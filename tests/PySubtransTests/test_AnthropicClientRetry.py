from __future__ import annotations

from unittest.mock import MagicMock, patch

import anthropic
import httpx2

from PySubtrans.Helpers.TestCases import LoggedTestCase
from PySubtrans.Helpers.Tests import skip_if_debugger_attached
from PySubtrans.Providers.Clients.AnthropicClient import (
    DEFAULT_MAX_TOKENS,
    AnthropicClient,
)
from PySubtrans.Providers.Provider_Claude import Provider_Claude
from PySubtrans.SettingsType import SettingsType
from PySubtrans.SubtitleError import TranslationImpossibleError
from PySubtrans.TranslationPrompt import TranslationPrompt
from PySubtrans.TranslationRequest import TranslationRequest

REQUEST = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


def _status_error(
    error_class: type[anthropic.APIStatusError], status: int, error_type: str
) -> anthropic.APIStatusError:
    body = {"type": "error", "error": {"type": error_type, "message": error_type}}
    return error_class(error_type, response=httpx2.Response(status, request=REQUEST), body=body)


def _message(text: str = "translated") -> MagicMock:
    message = MagicMock()
    message.stop_reason = "end_turn"
    message.usage = None
    piece = MagicMock()
    piece.type = "text"
    piece.text = text
    message.content = [piece]
    return message


class AnthropicClientRetryTests(LoggedTestCase):
    """Validate which Anthropic errors are retried and that the client is reused."""

    def _make_client(self, **extra) -> AnthropicClient:
        settings = SettingsType(
            {
                "api_key": "sk-ant-test",
                "instructions": "Verify retry behaviour.",
                "model": "claude-haiku-5-5",
                "max_retries": 3,
                "backoff_time": 0.0,
                **extra,
            }
        )
        return AnthropicClient(settings)

    def _make_request(self) -> TranslationRequest:
        prompt = TranslationPrompt("Translate this", True)
        prompt.content = [{"role": "user", "content": "1. Hello"}]
        prompt.system_prompt = "Translate."
        return TranslationRequest(prompt, None)

    def _send(self, client: AnthropicClient, side_effect: list) -> MagicMock:
        sdk = MagicMock()
        sdk.messages.create.side_effect = side_effect
        client.client = sdk
        with patch("PySubtrans.Providers.Clients.AnthropicClient.time.sleep"):
            client._request_translation(self._make_request())
        return sdk

    def test_transient_errors_are_retried(self) -> None:
        """Overload, 5xx and connection errors are retried and a later success is used."""
        client = self._make_client()
        sdk = self._send(
            client,
            [
                _status_error(anthropic.OverloadedError, 529, "overloaded_error"),
                _status_error(anthropic.InternalServerError, 500, "api_error"),
                anthropic.APIConnectionError(request=REQUEST),
                _message(),
            ],
        )
        self.assertLoggedEqual("attempts", 4, sdk.messages.create.call_count)

    def test_mid_stream_overload_is_retried(self) -> None:
        """An overloaded_error event in a 200 stream (plain APIStatusError) is retried."""
        client = self._make_client()
        sdk = self._send(
            client,
            [_status_error(anthropic.APIStatusError, 200, "overloaded_error"), _message()],
        )
        self.assertLoggedEqual("attempts", 2, sdk.messages.create.call_count)

    def test_mid_stream_connection_drop_is_retried(self) -> None:
        """A dropped stream (raw httpx2 error, not APIError) is retried from a clean slate."""

        def dropped_text_stream():
            yield "#1\nHei\n\n"
            yield "#2\nPa"
            raise httpx2.RemoteProtocolError("peer closed connection")

        dropped = MagicMock()
        dropped.text_stream = dropped_text_stream()
        complete = MagicMock()
        complete.text_stream = iter(["#1\nHei\n\n", "#2\nMoi\n\n"])
        complete.get_final_message.return_value = _message()

        streams = []
        for stream in (dropped, complete):
            context = MagicMock()
            context.__enter__.return_value = stream
            context.__exit__.return_value = False
            streams.append(context)

        client = self._make_client(stream_responses=True, supports_streaming=True)
        sdk = MagicMock()
        sdk.messages.stream.side_effect = streams
        client.client = sdk
        updates: list[str] = []
        request = TranslationRequest(
            self._make_request().prompt, lambda update: updates.append(update.text)
        )
        with patch("PySubtrans.Providers.Clients.AnthropicClient.time.sleep"):
            client._request_translation(request)

        self.assertLoggedEqual("stream attempts", 2, sdk.messages.stream.call_count)
        self.assertLoggedEqual("non-streaming calls", 0, sdk.messages.create.call_count)
        self.assertLoggedEqual(
            "retry does not append to the dropped attempt",
            "#1\nHei\n\n#2\nMoi\n\n",
            request.accumulated_text,
        )
        # One update from the dropped attempt, then one per section of the retry: a stale
        # cursor would swallow the retry's first section
        self.assertLoggedEqual(
            "partial updates",
            ["#1\nHei", "#1\nHei", "#1\nHei\n\n#2\nMoi"],
            updates,
        )

    @skip_if_debugger_attached
    def test_request_errors_fail_fast(self) -> None:
        """A bad request is not retried and ends the run."""
        client = self._make_client()
        error = _status_error(anthropic.BadRequestError, 400, "invalid_request_error")
        with self.assertRaises(TranslationImpossibleError):
            self._send(client, [error, _message()])
        assert isinstance(client.client, MagicMock)
        self.assertLoggedEqual("attempts", 1, client.client.messages.create.call_count)

    def test_client_is_created_once(self) -> None:
        """The SDK client is built on first use and reused for later batches."""
        client = self._make_client()
        with patch("PySubtrans.Providers.Clients.AnthropicClient.anthropic.Anthropic") as factory:
            factory.return_value.messages.create.side_effect = [_message(), _message()]
            client._request_translation(self._make_request())
            client._request_translation(self._make_request())
        self.assertLoggedEqual("clients created", 1, factory.call_count)

    def test_abort_closes_client(self) -> None:
        """Aborting closes the shared SDK client."""
        client = self._make_client()
        sdk = MagicMock()
        client.client = sdk
        client.AbortTranslation()
        self.assertLoggedEqual("close calls", 1, sdk.close.call_count)

    def test_default_max_tokens(self) -> None:
        """Client and provider share one max_tokens default."""
        client = self._make_client()
        self.assertLoggedEqual("client max_tokens", DEFAULT_MAX_TOKENS, client.max_tokens)
        with patch.dict("os.environ", {}, clear=False) as environ:
            environ.pop("CLAUDE_MAX_TOKENS", None)
            provider = Provider_Claude(SettingsType({"api_key": "sk-ant-test"}))
        self.assertLoggedEqual(
            "provider max_tokens", DEFAULT_MAX_TOKENS, provider.settings.get_int("max_tokens")
        )
        self.assertLoggedEqual("provider property", DEFAULT_MAX_TOKENS, provider.max_tokens)


class ClaudeModelLookupTests(LoggedTestCase):
    """Validate how Provider_Claude resolves the configured model name."""

    def _make_provider(self) -> Provider_Claude:
        return Provider_Claude(SettingsType({"api_key": "sk-ant-test"}))

    def test_unlisted_model_is_used_as_id(self) -> None:
        """A failed or incomplete model listing falls back to the configured name."""
        provider = self._make_provider()
        with patch.object(Provider_Claude, "_get_claude_models", return_value=[]):
            model_id = provider._get_model_id("claude-new-model-9")
        self.assertLoggedEqual("model id", "claude-new-model-9", model_id)

    def test_display_name_maps_to_id(self) -> None:
        """A listed display name still maps to its model id."""
        provider = self._make_provider()
        listed = MagicMock()
        listed.id = "claude-haiku-5-5"
        listed.display_name = "Claude Haiku 5.5"
        with patch.object(Provider_Claude, "_get_claude_models", return_value=[listed]):
            model_id = provider._get_model_id("Claude Haiku 5.5")
        self.assertLoggedEqual("model id", "claude-haiku-5-5", model_id)
