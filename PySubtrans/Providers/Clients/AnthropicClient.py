import logging
import threading
import time
from typing import Any

import anthropic
import httpx2
import regex

from PySubtrans.Helpers import FormatMessages
from PySubtrans.Helpers.Localization import _
from PySubtrans.Options import SettingsType
from PySubtrans.SubtitleError import TranslationError, TranslationImpossibleError
from PySubtrans.TranslationClient import TranslationClient
from PySubtrans.Translation import Translation
from PySubtrans.TranslationPrompt import TranslationPrompt
from PySubtrans.TranslationRequest import TranslationRequest

linesep = "\n"

# One default for the provider and the client. Adaptive thinking spends output tokens,
# so a small limit truncates large batches.
DEFAULT_MAX_TOKENS = 16000

# Transient failures that a retry with backoff can recover from. The SDK retries these
# itself before the response starts, but not when they arrive mid-stream.
RETRYABLE_ERRORS = (
    anthropic.APIConnectionError,  # includes APITimeoutError
    anthropic.RateLimitError,
    anthropic.InternalServerError,
    anthropic.OverloadedError,
    anthropic.ServiceUnavailableError,
    anthropic.DeadlineExceededError,
)

# Error types in the body of a mid-stream "error" event, which the SDK raises as a
# plain APIStatusError carrying the stream's 200 status
RETRYABLE_ERROR_TYPES = {
    "api_error",
    "overloaded_error",
    "rate_limit_error",
    "timeout_error",
}

# Claude 4.5 and older take a fixed thinking budget and reject adaptive thinking.
# Claude 4.6 and newer take adaptive thinking; 4.7 and newer reject budget_tokens.
MANUAL_THINKING_MODEL_PATTERN = regex.compile(
    r"^claude-(?:3-.*|(?:opus|sonnet|haiku)-4(?:-[015])?(?:-\d{8})?)$", regex.IGNORECASE
)


class AnthropicClient(TranslationClient):
    """
    Handles communication with Claude via the anthropic SDK
    """

    def __init__(self, settings: SettingsType):
        super().__init__(settings)
        self.client: anthropic.Anthropic | None = None
        self._client_lock = threading.Lock()

        self._emit_info(
            _("Translating with Anthropic {model}").format(
                model=self.model or _("default model")
            )
        )

    @property
    def api_key(self) -> str | None:
        return self.settings.get_str("api_key")

    @property
    def model(self) -> str | None:
        return self.settings.get_str("model")

    @property
    def max_tokens(self) -> int:
        return self.settings.get_int("max_tokens") or DEFAULT_MAX_TOKENS

    @property
    def allow_thinking(self) -> bool:
        return self.settings.get_bool("thinking", False)

    @property
    def thinking(self) -> dict | anthropic.NotGiven:
        if not self.allow_thinking:
            return anthropic.NOT_GIVEN

        if MANUAL_THINKING_MODEL_PATTERN.match(self.model or ""):
            return {
                "type": "enabled",
                "budget_tokens": self.settings.get_int("max_thinking_tokens", 1024),
            }

        return {"type": "adaptive"}

    def _request_translation(
        self, request: TranslationRequest, temperature: float | None = None
    ) -> Translation | None:
        """
        Request a translation based on the provided prompt
        """
        self._get_client()

        prompt: TranslationPrompt = request.prompt
        logging.debug(f"Messages:\n{FormatMessages(prompt.messages)}")

        temperature = temperature or self.temperature

        if prompt.system_prompt is None:
            raise TranslationError(_("System prompt is required"))

        if not prompt.content or not isinstance(prompt.content, list):
            raise TranslationError(_("No content provided for translation"))

        if not isinstance(prompt.content, list):
            raise TranslationError(_("Content must be a list of messages"))

        response = self._send_messages(request, temperature)

        translation = Translation(response) if response else None

        if translation:
            if translation.quota_reached:
                raise TranslationImpossibleError(
                    _(
                        "Anthropic account quota reached, please upgrade your plan or wait until it renews"
                    )
                )

            if translation.reached_token_limit:
                raise TranslationError(
                    _("Too many tokens in translation"), translation=translation
                )

        return translation

    def _send_messages(
        self, request: TranslationRequest, temperature: float
    ) -> dict[str, Any] | None:
        """
        Make a request to the LLM to provide a translation
        """
        api_response = self._get_client_response(request, temperature)

        if self.aborted or not api_response:
            return None

        # Process the response
        result = {}

        if api_response.stop_reason == "max_tokens":
            result["finish_reason"] = "length"
        else:
            result["finish_reason"] = api_response.stop_reason

        if api_response.usage:
            result["prompt_tokens"] = api_response.usage.input_tokens
            result["output_tokens"] = api_response.usage.output_tokens

        for piece in api_response.content:
            if piece.type == "thinking":
                result["reasoning"] = piece.thinking
            elif piece.type == "redacted_thinking":
                result["reasoning"] = "Reasoning redacted by API"
            elif piece.type == "text":
                result["text"] = piece.text
                break

        return result

    def _get_client_response(self, request: TranslationRequest, temperature: float):
        """
        Handle both streaming and non-streaming API calls with retry logic.

        temperature is not sent: API version 2026-10-21 (anthropic SDK 2.x) rejects
        temperature, top_p and top_k on every model.
        """
        if self.model is None:
            raise TranslationError(_("No model specified"))

        client = self._get_client()

        for retry in range(self.max_retries + 1):
            if self.aborted:
                return None

            try:
                prompt: TranslationPrompt = request.prompt
                if prompt.system_prompt is None:
                    raise TranslationError(_("System prompt is required"))

                if request.is_streaming and self.enable_streaming:
                    with client.messages.stream(
                        model=self.model,
                        thinking=self.thinking,  # type: ignore
                        messages=prompt.content,  # type: ignore
                        system=prompt.system_prompt,
                        max_tokens=self.max_tokens,
                    ) as stream:
                        for text in stream.text_stream:
                            if self.aborted:
                                return None
                            request.ProcessStreamingDelta(text)

                        return stream.get_final_message()
                else:
                    return client.messages.create(
                        model=self.model,
                        thinking=self.thinking,  # type: ignore
                        messages=prompt.content,  # type: ignore
                        system=prompt.system_prompt,
                        max_tokens=self.max_tokens,
                    )

            except anthropic.APIError as e:
                if self.aborted:
                    return None

                if not self._is_retryable(e):
                    raise TranslationImpossibleError(
                        self._get_error_message(e), error=e
                    ) from e

                if retry < self.max_retries:
                    sleep_time = self.backoff_time * 2.0**retry
                    self._emit_warning(
                        _(
                            "Anthropic API error: {error}, retrying in {sleep_time} seconds..."
                        ).format(
                            error=self._get_error_message(e), sleep_time=sleep_time
                        )
                    )
                    time.sleep(sleep_time)
                    continue

            except httpx2.TransportError as e:
                # A connection that drops mid-stream reaches us as a raw transport error,
                # not an anthropic.APIError, so the SDK's own retries never see it
                if self.aborted:
                    return None

                if retry < self.max_retries:
                    sleep_time = self.backoff_time * 2.0**retry
                    self._emit_warning(
                        _(
                            "Anthropic connection error: {error}, retrying in {sleep_time} seconds..."
                        ).format(error=str(e) or type(e).__name__, sleep_time=sleep_time)
                    )
                    time.sleep(sleep_time)
                    continue

            except Exception as e:
                raise TranslationError(
                    _("Error communicating with provider"), error=e
                ) from e

        raise TranslationImpossibleError(
            _("Failed to communicate with provider after {max_retries} retries").format(
                max_retries=self.max_retries
            )
        )

    def _abort(self) -> None:
        # Closing the client ends any request in flight
        with self._client_lock:
            if self.client:
                self.client.close()
        return super()._abort()

    def _get_client(self) -> anthropic.Anthropic:
        """
        Create the client once and reuse it for every batch (it is thread-safe)
        """
        with self._client_lock:
            if self.client is None:
                try:
                    proxy = self.settings.get_str("proxy")
                    http_client = (
                        anthropic.DefaultHttpxClient(proxy=proxy) if proxy else None
                    )
                    self.client = anthropic.Anthropic(
                        api_key=self.api_key, http_client=http_client
                    )
                except Exception as e:
                    raise TranslationImpossibleError(
                        _("Failed to initialize Anthropic client"), error=e
                    ) from e
            return self.client

    def _is_retryable(self, e: anthropic.APIError) -> bool:
        """
        True for connection, rate limit, overload and server errors
        """
        if isinstance(e, RETRYABLE_ERRORS):
            return True

        if isinstance(e, anthropic.APIStatusError) and isinstance(e.body, dict):
            error = e.body.get("error")
            if isinstance(error, dict):
                return error.get("type") in RETRYABLE_ERROR_TYPES

        return False

    def _get_error_message(self, e: anthropic.APIError) -> str:
        """
        Extract a user-friendly error message from the API error
        """
        if hasattr(e, "body") and isinstance(e.body, dict):
            if "error" in e.body and isinstance(e.body["error"], dict):
                return str(e.body["error"].get("message", str(e)))
            elif "message" in e.body:
                return str(e.body["message"])

        return str(e)
