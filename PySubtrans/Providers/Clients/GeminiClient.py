import json
import logging
import os
import random
import re
import threading
import time
from pathlib import Path
from typing import Any

from google import genai
from google.genai import errors as genai_errors
from google.genai.types import (
    AutomaticFunctionCallingConfig,
    Candidate,
    Content,
    FinishReason,
    GenerateContentConfig,
    GenerateContentResponse,
    GenerateContentResponseUsageMetadata,
    HarmBlockThreshold,
    HarmCategory,
    HttpOptions,
    Part,
    SafetySetting,
    ThinkingConfig,
)

from PySubtrans.Helpers import FormatMessages
from PySubtrans.Helpers.Localization import _
from PySubtrans.Options import SettingsType
from PySubtrans.SubtitleError import (
    TranslationImpossibleError,
    TranslationResponseError,
)
from PySubtrans.Translation import Translation
from PySubtrans.TranslationClient import TranslationClient
from PySubtrans.TranslationPrompt import TranslationPrompt
from PySubtrans.TranslationRequest import TranslationRequest


def _is_gemini_3_or_later(model: str | None) -> bool:
    return bool(re.search(r"gemini-[3-9]", model or "", re.IGNORECASE))


class GeminiUsageLedger:
    """
    Token usage of every Gemini response in this process, per model and location.

    A response counts whether or not its translation is then accepted: a retried batch
    is billed for every attempt that came back. transubs writes it out with --usage-json;
    with `persist_to` set, the file is rewritten after every response, so a run killed by
    its caller's timeout still leaves what it was billed for.
    """

    _FIELDS = ("prompt_tokens", "cached_tokens", "output_tokens", "thoughts_tokens")

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._rows: dict[tuple[str, str], dict[str, int]] = {}
        self._path: Path | None = None

    def persist_to(self, path: Path | None) -> None:
        """Rewrite `path` with the snapshot after each response (None stops it)."""
        with self._lock:
            self._path = path
            self._write_locked()

    def _write_locked(self) -> None:
        if self._path is None:
            return
        payload = {
            "version": 1,
            "gemini": [
                {"model": model, "location": location, **row}
                for (model, location), row in self._rows.items()
            ],
        }
        try:
            partial = self._path.with_name(f"{self._path.name}.partial")
            partial.write_text(json.dumps(payload), encoding="utf-8")
            os.replace(partial, self._path)
        except OSError as e:
            logging.warning(f"Could not write Gemini usage to {self._path}: {e}")

    def record(self, model: str, location: str, usage: Any) -> None:
        if usage is None:
            return
        values = {
            "prompt_tokens": usage.prompt_token_count,
            "cached_tokens": usage.cached_content_token_count,
            "output_tokens": usage.candidates_token_count,
            "thoughts_tokens": usage.thoughts_token_count,
        }
        with self._lock:
            row = self._rows.setdefault(
                (model, location), {"requests": 0, **{f: 0 for f in self._FIELDS}}
            )
            row["requests"] += 1
            for field, value in values.items():
                row[field] += int(value or 0)
            self._write_locked()

    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                {"model": model, "location": location, **row}
                for (model, location), row in self._rows.items()
            ]

    def reset(self) -> None:
        with self._lock:
            self._rows.clear()
            self._write_locked()


USAGE_LEDGER = GeminiUsageLedger()


class GeminiClient(TranslationClient):
    """
    Handles communication with Google Gemini to request translations
    """

    RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})

    def __init__(self, settings: SettingsType):
        super().__init__(settings)

        self._gemini_client: genai.Client | None = None
        self._location_clients: dict[str, genai.Client] = {}
        self._client_lock = threading.Lock()

        self._emit_info(
            _("Translating with Gemini {model} model").format(
                model=self.model or _("default")
            )
        )

        if self.use_vertex:
            self._emit_info(
                _("Using Vertex AI project {project} in {location}").format(
                    project=self.vertex_project or _("(unspecified)"),
                    location=self.vertex_location or _("(unspecified)"),
                )
            )

        self.safety_settings: list[SafetySetting] = [
            SafetySetting(
                category=HarmCategory.HARM_CATEGORY_HARASSMENT,
                threshold=HarmBlockThreshold.OFF,
            ),
            SafetySetting(
                category=HarmCategory.HARM_CATEGORY_HATE_SPEECH,
                threshold=HarmBlockThreshold.OFF,
            ),
            SafetySetting(
                category=HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
                threshold=HarmBlockThreshold.OFF,
            ),
            SafetySetting(
                category=HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
                threshold=HarmBlockThreshold.OFF,
            ),
            SafetySetting(
                category=HarmCategory.HARM_CATEGORY_CIVIC_INTEGRITY,
                threshold=HarmBlockThreshold.OFF,
            ),
        ]

        self.automatic_function_calling: AutomaticFunctionCallingConfig = (
            AutomaticFunctionCallingConfig(disable=True, maximum_remote_calls=None)
        )

        # Configure reasoning parameters
        enable_thinking = settings.get_bool("enable_thinking", False)
        thinking_budget = settings.get_int("thinking_budget", -1)
        if thinking_budget is None:
            thinking_budget = -1
        thinking_level = settings.get_str("thinking_level")
        include_thoughts = False  # Gemini appends thoughts to the text if this is true, breaking the parser

        # Gemini 3.x models use the thinking_level enum (minimal/low/medium/high);
        # Gemini 2.5.x use the integer thinking_budget. They are mutually exclusive
        # (sending both returns HTTP 400), so thinking_level wins when set. An
        # explicit budget (0 turns thinking off on 2.5 Flash) is sent even without
        # enable_thinking. When neither is configured the model keeps its default
        # dynamic thinking.
        if thinking_level:
            self.thinking_config: ThinkingConfig | None = ThinkingConfig(
                include_thoughts=include_thoughts, thinking_level=thinking_level
            )
        elif enable_thinking or thinking_budget >= 0:
            self.thinking_config = ThinkingConfig(
                include_thoughts=include_thoughts, thinking_budget=thinking_budget
            )
        else:
            self.thinking_config = None

    @property
    def api_key(self) -> str | None:
        return self.settings.get_str("api_key")

    @property
    def model(self) -> str | None:
        return self.settings.get_str("model")

    @property
    def rate_limit(self) -> float | None:
        return self.settings.get_float("rate_limit")

    @property
    def use_vertex(self) -> bool:
        return self.settings.get_bool("use_vertex", False)

    @property
    def vertex_project(self) -> str | None:
        return self.settings.get_str("vertex_project")

    @property
    def vertex_location(self) -> str | None:
        return self.settings.get_str("vertex_location")

    @property
    def request_timeout(self) -> float:
        return self.settings.get_float("request_timeout") or 0.0

    @property
    def attempts_per_target(self) -> int:
        return max(1, self.settings.get_int("attempts_per_target") or 2)

    def _targets(self) -> list[tuple[str, str | None]]:
        """
        The configured model and location, then each "model@location" fallback in order.
        A fallback without "@" keeps the configured location; one without a model keeps the model.
        """
        targets: list[tuple[str, str | None]] = [
            (self.model or "", self.vertex_location)
        ]
        for item in (self.settings.get_str("fallbacks") or "").split(","):
            item = item.strip()
            if not item:
                continue
            model, _sep, location = item.partition("@")
            target = (
                model.strip() or targets[0][0],
                location.strip() or self.vertex_location,
            )
            if target not in targets:
                targets.append(target)
        return targets

    def _request_translation(
        self, request: TranslationRequest, temperature: float | None = None
    ) -> Translation | None:
        """
        Request a translation based on the provided prompt
        """
        prompt: TranslationPrompt = request.prompt
        logging.debug(f"Messages:\n{FormatMessages(prompt.messages)}")

        if not isinstance(prompt.system_prompt, str):
            raise TranslationImpossibleError(_("System prompt is required"))

        if not isinstance(prompt.content, str) or not prompt.content.strip():
            raise TranslationImpossibleError(_("No content provided for translation"))

        temperature = temperature or self.temperature

        response = self._send_messages(request, temperature)

        return Translation(response) if response else None

    def _abort(self) -> None:
        return super()._abort()

    def _send_messages(
        self, request: TranslationRequest, temperature: float
    ) -> dict[str, Any] | None:
        """
        Make a request to the Gemini API to provide a translation
        """
        if not self.model:
            raise TranslationImpossibleError(_("No model specified"))

        targets = self._targets()
        for retry in range(1 + self.max_retries):
            # Each target gets attempts_per_target tries; the last one keeps the rest.
            index = min(retry // self.attempts_per_target, len(targets) - 1)
            model, location = targets[index]
            if index and retry == index * self.attempts_per_target:
                self._emit_warning(
                    _("Gemini {model} unavailable, falling back to {fallback}").format(
                        model=targets[index - 1][0], fallback=f"{model}@{location}"
                    )
                )
            # The scripts pick a temperature for the configured model (Gemini 3+ must keep its
            # default); a fallback from another generation gets the model default instead.
            target_temperature = (
                temperature
                if _is_gemini_3_or_later(model) == _is_gemini_3_or_later(targets[0][0])
                else None
            )
            try:
                return self._get_gemini_response(
                    request, target_temperature, model, location
                )

            except TranslationImpossibleError:
                raise

            except Exception as e:
                if (
                    isinstance(e, genai_errors.APIError)
                    and e.code not in self.RETRYABLE_STATUS_CODES
                ):
                    raise TranslationImpossibleError(
                        _("Gemini request failed: {error}").format(error=str(e))
                    ) from e

                if retry == self.max_retries:
                    raise TranslationImpossibleError(
                        _(
                            "Failed to communicate with provider after {max_retries} retries"
                        ).format(max_retries=self.max_retries)
                    ) from e

                if not self.aborted:
                    # Truncated exponential backoff with jitter, per Google's 429 guidance.
                    # The backoff restarts on each target: a fallback is another capacity pool.
                    attempt_on_target = retry - index * self.attempts_per_target
                    base_delay = min(self.backoff_time * 2.0**attempt_on_target, 60.0)
                    sleep_time = round(
                        base_delay + random.uniform(0.0, base_delay * 0.5), 1
                    )
                    self._emit_warning(
                        _(
                            "Gemini request failure {error}, retrying in {sleep_time} seconds..."
                        ).format(error=str(e), sleep_time=sleep_time)
                    )
                    time.sleep(sleep_time)

    def _get_gemini_response(
        self,
        request: TranslationRequest,
        temperature: float | None,
        model: str | None = None,
        location: str | None = None,
    ) -> dict[str, Any] | None:
        """
        Handle both streaming and non-streaming Gemini API calls
        """
        model = model or self.model
        if not model:
            raise TranslationImpossibleError(_("No model specified"))

        prompt: TranslationPrompt = request.prompt
        if not isinstance(prompt.content, str):
            raise TranslationImpossibleError(_("Content must be a string for Gemini"))

        gemini_client = self._get_client(location)
        config = GenerateContentConfig(
            candidate_count=1,
            temperature=temperature,
            system_instruction=prompt.system_prompt,
            safety_settings=self.safety_settings,
            thinking_config=self.thinking_config,
            automatic_function_calling=self.automatic_function_calling,
            max_output_tokens=None,
            response_modalities=[],
        )

        if not request.is_streaming or not self.enable_streaming:
            # Non-streaming: single request
            gcr = gemini_client.models.generate_content(
                model=model,
                contents=Part.from_text(text=prompt.content),
                config=config,
            )

            return (
                self._process_gemini_response(gcr, model, location)
                if not self.aborted
                else None
            )

        # A retried request starts from scratch, not after the failed attempt's text
        request.ResetStreamingProgress()
        # Streaming: process chunks and create complete response
        stream = gemini_client.models.generate_content_stream(
            model=model,
            contents=Part.from_text(text=prompt.content),
            config=config,
        )

        # The final chunk may carry only finish_reason (no parts), so track the
        # finish reason and safety ratings from every candidate, whatever its content
        finish_reason: FinishReason | None = None
        safety_ratings = None
        last_usage: GenerateContentResponseUsageMetadata | None = None
        first_prompt_feedback = (
            None  # when blocked, this can be set only on the first chunk
        )
        accumulated_thoughts = ""

        for chunk in stream:
            if self.aborted:
                return None

            chunk_text = ""
            for candidate in chunk.candidates or []:
                finish_reason = candidate.finish_reason or finish_reason
                safety_ratings = candidate.safety_ratings or safety_ratings
                if candidate.content and candidate.content.parts:
                    for part in candidate.content.parts:
                        if part.text:
                            if part.thought:
                                accumulated_thoughts += part.text
                            else:
                                chunk_text += part.text

            if chunk_text:
                request.ProcessStreamingDelta(chunk_text)

            if chunk.usage_metadata:
                last_usage = chunk.usage_metadata
            if chunk.prompt_feedback and first_prompt_feedback is None:
                first_prompt_feedback = chunk.prompt_feedback

        # Build a synthetic response that keeps metadata but replaces content parts with accumulated text
        parts = [Part.from_text(text=request.accumulated_text)]

        if accumulated_thoughts:
            thought_part = Part.from_text(text=accumulated_thoughts)
            thought_part.thought = True
            parts.append(thought_part)

        synthetic_candidate = Candidate(
            content=Content(role="model", parts=parts),
            finish_reason=finish_reason,
            safety_ratings=safety_ratings,
        )

        response = GenerateContentResponse(
            candidates=[synthetic_candidate],
            usage_metadata=last_usage,
            prompt_feedback=first_prompt_feedback,
        )

        return (
            self._process_gemini_response(response, model, location)
            if not self.aborted
            else None
        )

    def _process_gemini_response(
        self,
        gcr: GenerateContentResponse,
        model: str | None = None,
        location: str | None = None,
    ) -> dict[str, Any]:
        """
        Process Gemini response and extract translation data
        """
        response = {}

        if not gcr:
            raise TranslationImpossibleError(_("No response from Gemini"))

        # Before any check that rejects the response: a rejected answer is still billed.
        USAGE_LEDGER.record(
            model or self.model or "",
            location or self.vertex_location or "",
            gcr.usage_metadata,
        )

        if gcr.prompt_feedback and gcr.prompt_feedback.block_reason:
            block_info = self._extract_block_info(gcr)
            raise TranslationImpossibleError(
                _("Request was blocked by Gemini: {block_info}").format(
                    block_info=block_info
                )
            )

        # Try to find a validate candidate
        candidates = (
            [candidate for candidate in gcr.candidates if candidate.content]
            if gcr.candidates
            else []
        )
        candidates = [
            candidate
            for candidate in candidates
            if candidate.finish_reason == FinishReason.STOP
        ] or candidates

        if not candidates:
            raise TranslationResponseError(
                _("No valid candidates returned in the response"), response=gcr
            )

        candidate = candidates[0]
        response["token_count"] = candidate.token_count

        finish_reason = candidate.finish_reason
        if finish_reason == "STOP" or finish_reason == FinishReason.STOP:
            response["finish_reason"] = "complete"
        elif finish_reason == "MAX_TOKENS" or finish_reason == FinishReason.MAX_TOKENS:
            response["finish_reason"] = "length"
            raise TranslationResponseError(
                _("Gemini response exceeded token limit"), response=candidate
            )
        elif finish_reason == "SAFETY" or finish_reason == FinishReason.SAFETY:
            response["finish_reason"] = "blocked"
            raise TranslationResponseError(
                _("Gemini response was blocked for safety reasons"), response=candidate
            )
        elif finish_reason == "RECITATION" or finish_reason == FinishReason.RECITATION:
            response["finish_reason"] = "recitation"
            raise TranslationResponseError(
                _("Gemini response was blocked for recitation"), response=candidate
            )
        elif (
            finish_reason == "FINISH_REASON_UNSPECIFIED"
            or finish_reason == FinishReason.FINISH_REASON_UNSPECIFIED
        ):
            response["finish_reason"] = "unspecified"
            raise TranslationResponseError(
                _("Gemini response was incomplete"), response=candidate
            )
        else:
            # Probably a failure
            response["finish_reason"] = finish_reason

        usage_metadata: GenerateContentResponseUsageMetadata | None = gcr.usage_metadata
        if usage_metadata:
            response["prompt_tokens"] = usage_metadata.prompt_token_count
            response["output_tokens"] = usage_metadata.candidates_token_count
            response["total_tokens"] = usage_metadata.total_token_count

        if not candidate or not candidate.content or not candidate.content.parts:
            raise TranslationResponseError(
                _("Gemini response has no valid content parts"), response=candidate
            )

        response_text = "\n".join(
            part.text for part in candidate.content.parts if part.text
        )

        if not response_text:
            raise TranslationResponseError(
                _("Gemini response is empty"), response=candidate
            )

        response["text"] = response_text

        thoughts = "\n".join(
            part.text for part in candidate.content.parts if part.thought and part.text
        )
        if thoughts:
            response["reasoning"] = thoughts

        return response

    def _create_client(self, location: str | None = None):
        # No SDK retries: _send_messages owns retries, backoff and fallbacks
        http_options = HttpOptions(timeout=int(self.request_timeout * 1000) or None)
        if self.use_vertex:
            location = location or self.vertex_location
            if not self.vertex_project or not location:
                raise TranslationImpossibleError(
                    _("Vertex AI project and location must be provided")
                )

            return genai.Client(
                vertexai=True,
                project=self.vertex_project,
                location=location,
                http_options=http_options,
            )

        if not self.api_key:
            raise TranslationImpossibleError(_("API Key is required"))

        http_options.api_version = "v1alpha"
        return genai.Client(api_key=self.api_key, http_options=http_options)

    def _get_client(self, location: str | None = None) -> genai.Client:
        """
        Create the genai client for a location on first use and reuse it for subsequent requests
        """
        location = location or self.vertex_location
        with self._client_lock:
            if location == self.vertex_location:
                if self._gemini_client is None:
                    self._gemini_client = self._create_client(location)
                return self._gemini_client
            client = self._location_clients.get(location or "")
            if client is None:
                client = self._create_client(location)
                self._location_clients[location or ""] = client
            return client

    def _extract_block_info(self, gcr: GenerateContentResponse) -> str:
        """
        Extract detailed blocking information from Gemini response
        """
        info_parts = []
        has_additional_info = False

        if gcr.prompt_feedback and gcr.prompt_feedback.block_reason:
            block_reason = gcr.prompt_feedback.block_reason
            reason_name = getattr(block_reason, "name", str(block_reason))
            info_parts.append(f"Reason: {reason_name}")

            # Add block reason message if available
            if gcr.prompt_feedback.block_reason_message:
                info_parts.append(
                    f"Message: {gcr.prompt_feedback.block_reason_message}"
                )
                has_additional_info = True

        # Add safety ratings if available
        if gcr.prompt_feedback and gcr.prompt_feedback.safety_ratings:
            safety_info = []
            for rating in gcr.prompt_feedback.safety_ratings:
                category = getattr(rating, "category", "Unknown")
                probability = getattr(rating, "probability", "Unknown")
                blocked = getattr(rating, "blocked", False)

                rating_str = f"{getattr(category, 'name', str(category))}={getattr(probability, 'name', str(probability))}"
                if blocked:
                    rating_str += " (BLOCKED)"
                safety_info.append(rating_str)

            if safety_info:
                info_parts.append(f"Safety: {', '.join(safety_info)}")
                has_additional_info = True

        # If it's just PROHIBITED_CONTENT with no additional details, add explanation
        if (
            not has_additional_info
            and len(info_parts) == 1
            and "PROHIBITED_CONTENT" in info_parts[0]
        ):
            info_parts.append(
                "Google content policy violation (copyright or censorship). Try another provider."
            )

        return "; ".join(info_parts) if info_parts else "Unknown blocking reason"
