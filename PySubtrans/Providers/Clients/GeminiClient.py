import logging
import random
import threading
import time
import warnings
from typing import Any

import regex
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
    ThinkingLevel,
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
from PySubtrans.UsageLedger import GEMINI_USAGE_LEDGER
from PySubtrans.UsageLedger import GeminiUsageLedger as GeminiUsageLedger


def _is_gemini_3_or_later(model: str | None) -> bool:
    return bool(regex.search(r"gemini-[3-9]", model or "", regex.IGNORECASE))


def _gemini_generation(model: str | None) -> int | None:
    """The major Gemini version in a model name, or None when the name has none"""
    match = regex.search(r"gemini-(\d+)", model or "", regex.IGNORECASE)
    return int(match.group(1)) if match else None


# The only models that accept thinking level xhigh
_XHIGH_MODEL_PATTERN = regex.compile(r"gemini-4-hydrogen", regex.IGNORECASE)

# Upper budget bound for each thinking level on Gemini 4, which rejects thinking_budget
_GEMINI_4_BUDGET_LEVELS = ((1024, "low"), (8192, "medium"))


def _resolve_thinking(
    model: str | None, level: str | None, budget: int | None
) -> tuple[str | None, int | None]:
    """
    The thinking level and budget a model accepts for the requested level and budget.
    The level is lower case; the budget is None unless one was requested.
    """
    generation = _gemini_generation(model)
    gemini_4 = generation is not None and generation >= 4
    if level:
        if level == "xhigh" and not _XHIGH_MODEL_PATTERN.search(model or ""):
            level = "high"
        if gemini_4 and level == "minimal":
            level = "low"
        if generation is None or generation >= 3:
            return level, None
        # Gemini 2.x has no thinking levels: fall through to the budget

    if budget is None:
        return None, None

    if gemini_4:
        if budget < 0:
            return None, None
        for limit, budget_level in _GEMINI_4_BUDGET_LEVELS:
            if budget <= limit:
                return budget_level, None
        return "high", None

    return None, budget


def _effective_thinking(
    level: str | None, budget: int | None
) -> tuple[str | None, int | None]:
    """What a request asks for: a level wins over a budget, and budget -1 is the default"""
    if level:
        return level, None
    return None, budget if budget is not None and budget >= 0 else None


def _describe_thinking(level: str | None, budget: int | None) -> str:
    if level:
        return _("level {level}").format(level=level)
    if budget is not None and budget >= 0:
        return _("budget {budget}").format(budget=budget)
    return _("the model default")


def _thinking_level(level: str) -> ThinkingLevel:
    if level == "xhigh":
        # google-genai 2.34 has no XHIGH member and warns on every construction
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            return ThinkingLevel("XHIGH")
    return ThinkingLevel(level.upper())


# Kept under its old names for existing imports
USAGE_LEDGER = GEMINI_USAGE_LEDGER


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
        self._thinking_level: str | None = (
            settings.get_str("thinking_level") or ""
        ).strip().lower() or None
        # An explicit budget (0 turns thinking off on 2.5 Flash) is sent even without
        # enable_thinking
        self._thinking_budget: int | None = (
            thinking_budget if enable_thinking or thinking_budget >= 0 else None
        )

        # Each target model gets the thinking it accepts, so a fallback from another
        # generation does not fail with HTTP 400 (_resolve_thinking):
        # - thinking_level (minimal/low/medium/high) and thinking_budget are mutually
        #   exclusive, so the level wins when both are set.
        # - xhigh exists only on gemini-4-hydrogen; other models get high.
        # - Gemini 4 rejects minimal (low instead) and any budget, which maps to the
        #   nearest level (up to 1024 low, up to 8192 medium, above that high).
        # - Gemini 2.x has no levels, so it gets the budget alone.
        # Without either setting the model keeps its default dynamic thinking.
        self._thinking_configs: dict[str, ThinkingConfig | None] = {}
        for target_model, _location in self._targets():
            if target_model not in self._thinking_configs:
                self._thinking_configs[target_model] = self._build_thinking_config(
                    target_model
                )
        self.thinking_config: ThinkingConfig | None = self._thinking_configs[
            self.model or ""
        ]

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

    def _build_thinking_config(self, model: str) -> ThinkingConfig | None:
        """
        The thinking config for a model, with a warning when the model needs another
        setting than the one requested
        """
        level, budget = _resolve_thinking(
            model, self._thinking_level, self._thinking_budget
        )
        requested = _effective_thinking(self._thinking_level, self._thinking_budget)
        if _effective_thinking(level, budget) != requested:
            self._emit_warning(
                _(
                    "Gemini {model} does not accept thinking {requested}; using {used}"
                ).format(
                    model=model,
                    requested=_describe_thinking(*requested),
                    used=_describe_thinking(level, budget),
                )
            )

        # Gemini appends thoughts to the text if include_thoughts is true, breaking the parser
        if level:
            return ThinkingConfig(
                include_thoughts=False, thinking_level=_thinking_level(level)
            )
        if budget is not None:
            return ThinkingConfig(include_thoughts=False, thinking_budget=budget)
        return None

    def _thinking_config_for(self, model: str) -> ThinkingConfig | None:
        """
        The thinking config for a target model, built on first use for a model that is
        not a configured target
        """
        if model in self._thinking_configs:
            return self._thinking_configs[model]
        with self._client_lock:
            if model not in self._thinking_configs:
                self._thinking_configs[model] = self._build_thinking_config(model)
            return self._thinking_configs[model]

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
            thinking_config=self._thinking_config_for(model),
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
