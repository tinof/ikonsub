import logging
import threading
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import anthropic
import httpx2
import regex
from anthropic import Omit, omit

from PySubtrans.Helpers import FormatMessages
from PySubtrans.Helpers.Localization import _
from PySubtrans.Options import SettingsType
from PySubtrans.SubtitleError import TranslationError, TranslationImpossibleError
from PySubtrans.Translation import Translation
from PySubtrans.TranslationClient import TranslationClient
from PySubtrans.TranslationPrompt import TranslationPrompt
from PySubtrans.TranslationRequest import TranslationRequest
from PySubtrans.UsageLedger import CLAUDE_USAGE_LEDGER

linesep = "\n"

# One default for the provider and the client. Adaptive thinking spends output tokens,
# so a small limit truncates large batches. Requests always stream, so the SDK's
# non-streaming limit (about 21K) does not apply to larger values.
DEFAULT_MAX_TOKENS = 16000

# Effort is the thinking control on current models. Pinned for the whole run: changing it
# between requests invalidates the prompt cache.
DEFAULT_EFFORT = "medium"
EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")

# Connection and stream timeouts. read is the longest gap between two stream events, so a
# stalled stream fails in minutes instead of hanging for the SDK default of 10 minutes.
REQUEST_TIMEOUT = anthropic.Timeout(600.0, connect=10.0, read=300.0)

# Longest wait between attempts, from backoff or a retry-after header. Without a cap
# the exponential backoff reaches 320 s on the 7th retry.
MAX_RETRY_WAIT = 120.0

# Transient failures that a retry with backoff can recover from. The SDK's own retries
# are off (max_retries=0), so this loop is the only retry layer and mirrors the SDK's
# rules: 408, 409, 429, 5xx, connection errors and the x-should-retry header.
RETRYABLE_ERRORS = (
    anthropic.APIConnectionError,  # includes APITimeoutError
    anthropic.RequestTimeoutError,
    anthropic.ConflictError,
    anthropic.RateLimitError,
    anthropic.InternalServerError,  # includes 503, 504 and 529 overloaded
)

# The SDK's TypeError when no API key, token or profile can be found
MISSING_CREDENTIALS_MESSAGE = "Could not resolve authentication method"

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

# Opus 4.5 is the one budget-thinking model that also accepts effort (low to high)
EFFORT_BUDGET_MODEL_PATTERN = regex.compile(
    r"^claude-opus-4-5(?:-\d{8})?$", regex.IGNORECASE
)

# Models with a reduced set of effort levels: a higher request is lowered to the
# highest level the model accepts (xhigh arrived with Opus 4.7)
LIMITED_EFFORT_MODELS = (
    (EFFORT_BUDGET_MODEL_PATTERN, ("low", "medium", "high")),
    (
        regex.compile(r"^claude-(?:opus|sonnet)-4-6$", regex.IGNORECASE),
        ("low", "medium", "high", "max"),
    ),
)

# Models that think even when the thinking parameter is omitted: effort is the control
ALWAYS_THINKING_MODEL_PATTERN = regex.compile(
    r"^claude-(?:haiku-5-5|sonnet-5(?:-5)?|opus-5(?:-5)?|fable-5(?:-1)?)$",
    regex.IGNORECASE,
)

# Models with server-side refusal fallback on the Claude API: a declined batch is
# re-run on the model Anthropic picks for the refusal category, in the same request
FALLBACK_MODEL_PATTERN = regex.compile(
    r"^claude-(?:sonnet-5-5|opus-5(?:-5)?|fable-5(?:-1)?)$", regex.IGNORECASE
)
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class AnthropicClient(TranslationClient):
    """
    Handles communication with Claude via the anthropic SDK
    """

    def __init__(self, settings: SettingsType):
        super().__init__(settings)
        self.client: anthropic.Anthropic | None = None
        self._client_lock = threading.Lock()

        self._emit_info(
            _("Translating with Anthropic {model} ({config})").format(
                model=self.model or _("default model"), config=self._describe_config()
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
    def configured_effort(self) -> str:
        return (self.settings.get_str("effort") or DEFAULT_EFFORT).strip().lower()

    @property
    def effort(self) -> str | None:
        """
        The effort level to send, or None to leave it to the model. An unknown value
        is dropped (with a warning at startup) rather than failing every request.
        """
        value = self.configured_effort
        if value in ("", "default", "none") or value not in EFFORT_LEVELS:
            return None

        for pattern, levels in LIMITED_EFFORT_MODELS:
            if pattern.match(self.model or "") and value not in levels:
                requested = EFFORT_LEVELS.index(value)
                return next(
                    level
                    for level in reversed(levels)
                    if EFFORT_LEVELS.index(level) <= requested
                )

        return value

    @property
    def uses_fallbacks(self) -> bool:
        return self.settings.get_bool("fallbacks", True) and bool(
            FALLBACK_MODEL_PATTERN.match(self.model or "")
        )

    @property
    def thinking(self) -> anthropic.types.ThinkingConfigParam | Omit:
        """
        Thinking configuration for the model.

        With thinking off the parameter is omitted. Current models (see
        ALWAYS_THINKING_MODEL_PATTERN) still think adaptively then, so effort is the
        real control there.
        """
        if not self.allow_thinking:
            return omit

        if MANUAL_THINKING_MODEL_PATTERN.match(self.model or ""):
            budget = self.settings.get_int("max_thinking_tokens") or 1024
            return {"type": "enabled", "budget_tokens": budget}

        return {"type": "adaptive"}

    @property
    def output_config(self) -> anthropic.types.OutputConfigParam | Omit:
        """
        Effort setting, sent only to models that accept it
        """
        effort = self.effort
        if effort is None:
            return omit

        model = self.model or ""
        if MANUAL_THINKING_MODEL_PATTERN.match(
            model
        ) and not EFFORT_BUDGET_MODEL_PATTERN.match(model):
            return omit

        return {"effort": effort}  # type: ignore[typeddict-item]

    def _describe_config(self) -> str:
        """
        One-line summary of the thinking, effort and fallback settings
        """
        model = self.model or ""
        effort = self.effort
        configured = self.configured_effort
        if configured not in (*EFFORT_LEVELS, "", "default", "none"):
            self._emit_warning(
                _("Unknown effort '{effort}' ignored, expected one of {levels}").format(
                    effort=configured, levels=", ".join(EFFORT_LEVELS)
                )
            )
        elif effort is not None and effort != configured:
            self._emit_warning(
                _(
                    "{model} does not support effort {configured}, using {effort}"
                ).format(model=model, configured=configured, effort=effort)
            )

        parts: list[str] = []
        if self.allow_thinking or ALWAYS_THINKING_MODEL_PATTERN.match(model):
            parts.append(_("adaptive thinking"))
        else:
            parts.append(_("no thinking"))

        if isinstance(self.output_config, dict):
            parts.append(_("effort {effort}").format(effort=effort))

        if self.uses_fallbacks:
            parts.append(_("refusal fallback on"))

        return ", ".join(parts)

    def _request_translation(
        self, request: TranslationRequest, temperature: float | None = None
    ) -> Translation | None:
        """
        Request a translation based on the provided prompt.

        temperature is ignored: API version 2026-10-21 rejects temperature, top_p and
        top_k on every model.
        """
        del temperature

        prompt: TranslationPrompt = request.prompt
        logging.debug(f"Messages:\n{FormatMessages(prompt.messages)}")

        if prompt.system_prompt is None:
            raise TranslationError(_("System prompt is required"))

        if not prompt.content or not isinstance(prompt.content, list):
            raise TranslationError(_("No content provided for translation"))

        response = self._send_messages(request)

        translation = Translation(response) if response else None

        if translation and translation.quota_reached:
            raise TranslationImpossibleError(
                _(
                    "Anthropic account quota reached, please upgrade your plan or wait until it renews"
                )
            )

        # A translation that hit max_tokens is returned, not raised: SubtitleTranslator
        # splits the batch in half and retries it
        return translation

    def _send_messages(self, request: TranslationRequest) -> dict[str, Any] | None:
        """
        Make a request to the LLM to provide a translation
        """
        api_response = self._get_client_response(request)

        if self.aborted or not api_response:
            return None

        # Before any check that rejects the response: a refused answer is still billed.
        # response.model is the model that served it, a fallback model included.
        refused = api_response.stop_reason == "refusal"
        stop_details = getattr(api_response, "stop_details", None)
        CLAUDE_USAGE_LEDGER.record(
            self.model or "",
            api_response.model,
            api_response.usage,
            declined_categories=[
                getattr(getattr(piece, "trigger", None), "category", None)
                for piece in api_response.content
                if piece.type == "fallback"
            ],
            refused=refused,
            refusal_category=getattr(stop_details, "category", None)
            if refused
            else None,
        )

        if api_response.stop_reason == "refusal":
            raise TranslationError(self._get_refusal_message(api_response))

        result: dict[str, Any] = {}

        if api_response.stop_reason == "max_tokens":
            result["finish_reason"] = "length"
        else:
            result["finish_reason"] = api_response.stop_reason

        usage = api_response.usage
        if usage:
            result["prompt_tokens"] = usage.input_tokens
            result["output_tokens"] = usage.output_tokens
            result["cache_read_tokens"] = usage.cache_read_input_tokens
            result["cache_write_tokens"] = usage.cache_creation_input_tokens
            logging.info(
                _(
                    "Anthropic usage: {input} input ({cached} cached, {written} written to cache), {output} output tokens"
                ).format(
                    input=usage.input_tokens,
                    cached=usage.cache_read_input_tokens or 0,
                    written=usage.cache_creation_input_tokens or 0,
                    output=usage.output_tokens,
                )
            )

        # A server-side fallback continues the declined model's partial text in a new
        # text block, so the translation is all text blocks joined in order
        texts: list[str] = []
        for piece in api_response.content:
            if piece.type == "thinking" and piece.thinking:
                result["reasoning"] = piece.thinking
            elif piece.type == "redacted_thinking":
                result["reasoning"] = "Reasoning redacted by API"
            elif piece.type == "text":
                texts.append(piece.text)
            elif piece.type == "fallback":
                self._emit_warning(
                    _("{source} declined the batch, {target} translated it").format(
                        source=piece.from_.model, target=piece.to.model
                    )
                )

        if texts:
            result["text"] = "".join(texts)

        return result

    def _get_client_response(self, request: TranslationRequest):
        """
        Stream the request, with retry logic.

        Every request streams, so large max_tokens values work in parallel mode too.
        Text is forwarded to the progress callback only when the request asks for it.
        """
        if self.model is None:
            raise TranslationError(_("No model specified"))

        client = self._get_client()
        prompt: TranslationPrompt = request.prompt
        if prompt.system_prompt is None:
            raise TranslationError(_("System prompt is required"))

        forward_text = request.is_streaming and self.enable_streaming
        last_error: Exception | None = None

        for retry in range(self.max_retries + 1):
            if self.aborted:
                return None

            try:
                if forward_text:
                    request.ResetStreamingProgress()

                with self._open_stream(client, prompt) as stream:
                    for text in stream.text_stream:
                        if self.aborted:
                            return None
                        if forward_text:
                            request.ProcessStreamingDelta(text)

                    return stream.get_final_message()

            except anthropic.APIError as e:
                if self.aborted:
                    return None

                if not self._is_retryable(e):
                    raise TranslationImpossibleError(
                        self._get_error_message(e), error=e
                    ) from e

                last_error = e
                message = _("Anthropic API error: {error}").format(
                    error=self._get_error_message(e)
                )
                self._wait_before_retry(retry, message, self._get_retry_after(e))

            except httpx2.TransportError as e:
                # A connection that drops mid-stream reaches us as a raw transport error,
                # not an anthropic.APIError
                if self.aborted:
                    return None

                last_error = e
                message = _("Anthropic connection error: {error}").format(
                    error=str(e) or type(e).__name__
                )
                self._wait_before_retry(retry, message, None)

            except anthropic.CredentialsError as e:
                # A profile or identity token that cannot be loaded fails every batch
                raise TranslationImpossibleError(
                    _("Unable to load Anthropic credentials: {error}").format(
                        error=str(e)
                    ),
                    error=e,
                ) from e

            except TypeError as e:
                if MISSING_CREDENTIALS_MESSAGE not in str(e):
                    raise TranslationError(
                        _("Error communicating with provider"), error=e
                    ) from e

                raise TranslationImpossibleError(
                    _(
                        "Anthropic credentials not found: set CLAUDE_API_KEY or ANTHROPIC_API_KEY"
                    ),
                    error=e,
                ) from e

            except Exception as e:
                raise TranslationError(
                    _("Error communicating with provider"), error=e
                ) from e

        if self.aborted:
            return None

        error_text = (
            self._get_error_message(last_error)
            if isinstance(last_error, anthropic.APIError)
            else str(last_error)
        )
        raise TranslationImpossibleError(
            _(
                "Failed to communicate with provider after {max_retries} retries: {error}"
            ).format(max_retries=self.max_retries, error=error_text),
            error=last_error,
        )

    def _open_stream(self, client: anthropic.Anthropic, prompt: TranslationPrompt):
        """
        Open a message stream. The system prompt is byte-stable across batches, so it
        carries the cache breakpoint; the per-batch user message stays uncached.
        """
        system: list[anthropic.types.TextBlockParam] = [
            {
                "type": "text",
                "text": prompt.system_prompt or "",
                "cache_control": {"type": "ephemeral"},
            }
        ]

        if self.uses_fallbacks:
            return client.beta.messages.stream(
                model=self.model or "",
                max_tokens=self.max_tokens,
                system=system,
                messages=prompt.content,  # type: ignore[arg-type]
                thinking=self.thinking,  # type: ignore[arg-type]
                output_config=self.output_config,  # type: ignore[arg-type]
                fallbacks="default",
                betas=[FALLBACK_BETA],
            )

        return client.messages.stream(
            model=self.model or "",
            max_tokens=self.max_tokens,
            system=system,
            messages=prompt.content,  # type: ignore[arg-type]
            thinking=self.thinking,
            output_config=self.output_config,
        )

    def _wait_before_retry(
        self, retry: int, message: str, retry_after: float | None
    ) -> None:
        """
        Warn and wait before the next attempt. The wait follows the server's retry-after
        when it sends one, and an abort ends it at once.
        """
        if retry >= self.max_retries:
            return

        sleep_time = min(self.backoff_time * 2.0**retry, MAX_RETRY_WAIT)
        if retry_after is not None:
            sleep_time = min(max(retry_after, 0.0), MAX_RETRY_WAIT)

        self._emit_warning(
            _("{message}, retrying in {sleep_time:.0f} seconds...").format(
                message=message, sleep_time=sleep_time
            )
        )
        self._abort_event.wait(sleep_time)

    def _abort(self) -> None:
        # Closing the client ends any request in flight
        with self._client_lock:
            if self.client:
                self.client.close()
        return super()._abort()

    def _get_client(self) -> anthropic.Anthropic:
        """
        Create the client once and reuse it for every batch (it is thread-safe).
        The SDK's own retries are off: _get_client_response retries every request.
        """
        with self._client_lock:
            if self.client is None:
                try:
                    proxy = self.settings.get_str("proxy")
                    http_client = (
                        anthropic.DefaultHttpxClient(proxy=proxy) if proxy else None
                    )
                    self.client = anthropic.Anthropic(
                        api_key=self.api_key,
                        http_client=http_client,
                        max_retries=0,
                        timeout=REQUEST_TIMEOUT,
                    )
                except Exception as e:
                    raise TranslationImpossibleError(
                        _("Failed to initialize Anthropic client"), error=e
                    ) from e
            return self.client

    def _is_retryable(self, e: anthropic.APIError) -> bool:
        """
        True for connection, timeout, conflict, rate limit, overload and server errors,
        or whatever the server says in x-should-retry
        """
        if isinstance(e, anthropic.APIStatusError):
            should_retry = e.response.headers.get("x-should-retry")
            if should_retry == "true":
                return True
            if should_retry == "false":
                return False

        if isinstance(e, RETRYABLE_ERRORS):
            return True

        if isinstance(e, anthropic.APIStatusError) and isinstance(e.body, dict):
            error = e.body.get("error")
            if isinstance(error, dict):
                return error.get("type") in RETRYABLE_ERROR_TYPES

        return False

    def _get_retry_after(self, e: anthropic.APIError) -> float | None:
        """
        Seconds to wait from the retry-after header, if the server sent one
        """
        if not isinstance(e, anthropic.APIStatusError):
            return None

        headers = e.response.headers
        try:
            if milliseconds := headers.get("retry-after-ms"):
                return float(milliseconds) / 1000.0
        except ValueError:
            pass

        value = headers.get("retry-after")
        if not value:
            return None

        try:
            return float(value)
        except ValueError:
            pass

        # retry-after can also be an HTTP date
        try:
            retry_at = parsedate_to_datetime(value)
            return (retry_at - datetime.now(UTC)).total_seconds()
        except TypeError, ValueError:
            return None

    def _get_error_message(self, e: anthropic.APIError) -> str:
        """
        Extract a user-friendly error message from the API error
        """
        if isinstance(e, anthropic.APIStatusError) and e.api_message:
            return e.api_message

        if isinstance(e.body, dict) and "message" in e.body:
            return str(e.body["message"])

        return str(e)

    def _get_refusal_message(self, api_response: Any) -> str:
        """
        Describe a refusal, with the category and explanation when the API gives them
        """
        details = getattr(api_response, "stop_details", None)
        category = getattr(details, "category", None) if details else None
        explanation = getattr(details, "explanation", None) if details else None

        message = _("Claude declined to translate this batch")
        if category:
            message += _(" (category: {category})").format(category=category)
        if explanation:
            message += f": {explanation}"
        return message
