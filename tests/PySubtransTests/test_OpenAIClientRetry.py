from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import MagicMock, patch

import httpx2
import openai
from openai.types import responses as responses_types
from openai.types.chat import ChatCompletion
from openai.types.responses import (
    ResponseCompletedEvent,
    ResponseOutputMessage,
    ResponseOutputText,
    ResponseTextDeltaEvent,
)

from PySubtrans.Helpers.TestCases import LoggedTestCase
from PySubtrans.Helpers.Tests import skip_if_debugger_attached
from PySubtrans.Providers.Clients.ChatGPTClient import ChatGPTClient
from PySubtrans.Providers.Clients.OpenAIReasoningClient import OpenAIReasoningClient
from PySubtrans.SettingsType import SettingsType
from PySubtrans.SubtitleError import TranslationError, TranslationImpossibleError
from PySubtrans.TranslationPrompt import TranslationPrompt
from PySubtrans.TranslationRequest import TranslationRequest

REQUEST = httpx2.Request("POST", "https://api.openai.com/v1/responses")
SLEEP = "PySubtrans.Providers.Clients.OpenAIClient.time.sleep"


def _settings() -> SettingsType:
    return SettingsType(
        {
            "api_key": "sk-test",
            "instructions": "Verify retry behaviour.",
            "model": "gpt-test",
            "max_retries": 3,
        }
    )


def _make_request(updates: list[str] | None = None) -> TranslationRequest:
    prompt = TranslationPrompt("Translate this", True)
    prompt.content = [{"role": "user", "content": "1. Hello"}]
    prompt.system_prompt = "Translate."
    callback = (
        (lambda update: updates.append(update.text or ""))
        if updates is not None
        else None
    )
    return TranslationRequest(prompt, callback)


def _chat_completion(text: str) -> ChatCompletion:
    return ChatCompletion.model_validate(
        {
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "created": 1700000000,
            "model": "gpt-test",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": text},
                }
            ],
        }
    )


def _response(text: str) -> responses_types.Response:
    return responses_types.Response.model_construct(
        id="resp_test",
        created_at=1700000000.0,
        model="gpt-test",
        object="response",
        status="completed",
        incomplete_details=None,
        output=[
            ResponseOutputMessage.model_construct(
                id="msg_test",
                type="message",
                role="assistant",
                status="completed",
                content=[
                    ResponseOutputText.model_construct(type="output_text", text=text)
                ],
            )
        ],
        usage=None,
    )


def _delta(text: str) -> ResponseTextDeltaEvent:
    return ResponseTextDeltaEvent.model_construct(
        delta=text, type="response.output_text.delta"
    )


def _completed(text: str) -> ResponseCompletedEvent:
    return ResponseCompletedEvent.model_construct(
        response=_response(text), type="response.completed"
    )


def _dropped_stream(error: Exception) -> Iterator:
    yield _delta("#1\nHei\n\n")
    yield _delta("#2\nPa")
    raise error


class ChatClientRetryTests(LoggedTestCase):
    """Connection errors in the chat client are retried with backoff."""

    def _send(self, side_effect: list) -> tuple[str | None, MagicMock, MagicMock]:
        client = ChatGPTClient(_settings())
        sdk = MagicMock()
        sdk.chat.completions.create.side_effect = side_effect
        client.client = sdk
        with patch(SLEEP) as sleep:
            translation = client._request_translation(_make_request())
        text = translation.text if translation else None
        return text, sdk.chat.completions.create, sleep

    def test_connection_error_is_retried(self) -> None:
        """A connection error before the response is retried and the retry is used."""
        text, create, sleep = self._send(
            [openai.APIConnectionError(request=REQUEST), _chat_completion("Moi")]
        )
        self.assertLoggedEqual("attempts", 2, create.call_count)
        self.assertLoggedEqual("backoff waits", 1, sleep.call_count)
        self.assertLoggedEqual("translation", "Moi", text)

    def test_raw_transport_error_is_retried(self) -> None:
        """A transport error the SDK did not wrap is retried, not fatal."""
        text, create, _ = self._send(
            [
                httpx2.RemoteProtocolError("peer closed connection"),
                _chat_completion("Moi"),
            ]
        )
        self.assertLoggedEqual("attempts", 2, create.call_count)
        self.assertLoggedEqual("translation", "Moi", text)

    @skip_if_debugger_attached
    def test_exhausted_connection_errors_fail_the_batch(self) -> None:
        """When every attempt fails, the batch fails but the run is not ended."""
        errors = [openai.APIConnectionError(request=REQUEST) for _ in range(4)]
        with self.assertRaises(TranslationError) as raised:
            self._send(errors)
        self.assertLoggedFalse(
            "not fatal", isinstance(raised.exception, TranslationImpossibleError)
        )


class ResponsesClientRetryTests(LoggedTestCase):
    """Connection errors in the Responses client are retried from a clean slate."""

    def _send(
        self, side_effect: list, updates: list[str]
    ) -> tuple[TranslationRequest, str | None, MagicMock]:
        client = OpenAIReasoningClient(_settings())
        sdk = MagicMock()
        sdk.responses.create.side_effect = side_effect
        client.client = sdk
        request = _make_request(updates)
        with patch(SLEEP):
            translation = client._request_translation(request)
        text = translation.text if translation else None
        return request, text, sdk.responses.create

    def test_connection_error_is_retried(self) -> None:
        """A connection error before the stream opens is retried."""
        updates: list[str] = []
        _, text, create = self._send(
            [
                openai.APIConnectionError(request=REQUEST),
                iter([_delta("#1\nMoi\n\n"), _completed("#1\nMoi")]),
            ],
            updates,
        )
        self.assertLoggedEqual("attempts", 2, create.call_count)
        self.assertLoggedEqual("translation", "#1\nMoi", text)

    def test_mid_stream_drop_is_retried(self) -> None:
        """A stream dropped part way is retried and only the retry's text is kept."""
        drops: list[Exception] = [
            # The SDK wraps a transport failure while reading the stream
            openai.APIConnectionError(request=REQUEST),
            httpx2.RemoteProtocolError("peer closed connection"),
        ]
        for error in drops:
            with self.subTest(error=type(error).__name__):
                updates: list[str] = []
                request, text, create = self._send(
                    [
                        _dropped_stream(error),
                        iter(
                            [
                                _delta("#1\nHei\n\n"),
                                _delta("#2\nMoi\n\n"),
                                _completed("#1\nHei\n\n#2\nMoi"),
                            ]
                        ),
                    ],
                    updates,
                )
                self.assertLoggedEqual("attempts", 2, create.call_count)
                self.assertLoggedEqual("translation", "#1\nHei\n\n#2\nMoi", text)
                self.assertLoggedEqual(
                    "retry does not append to the dropped attempt",
                    "#1\nHei\n\n#2\nMoi\n\n",
                    request.accumulated_text,
                )
                self.assertLoggedEqual(
                    "partial updates",
                    ["#1\nHei", "#1\nHei", "#1\nHei\n\n#2\nMoi"],
                    updates,
                )
