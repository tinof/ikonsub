from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import MagicMock, patch

import httpx2
from google.genai import errors as genai_errors
from google.genai.types import (
    Candidate,
    Content,
    FinishReason,
    GenerateContentResponse,
    Part,
)

from PySubtrans.Helpers.TestCases import LoggedTestCase
from PySubtrans.Providers.Clients.GeminiClient import GeminiClient
from PySubtrans.SettingsType import SettingsType
from PySubtrans.SubtitleError import TranslationImpossibleError
from PySubtrans.TranslationPrompt import TranslationPrompt
from PySubtrans.TranslationRequest import TranslationRequest


def _api_error(code: int) -> genai_errors.APIError:
    return genai_errors.APIError(code, {"error": {"message": f"error {code}"}})


def _chunk(
    text: str, finish_reason: FinishReason | None = None
) -> GenerateContentResponse:
    content = Content(role="model", parts=[Part.from_text(text=text)])
    return GenerateContentResponse(
        candidates=[Candidate(content=content, finish_reason=finish_reason)]
    )


def _dropped_stream() -> Iterator[GenerateContentResponse]:
    """A stream that yields part of a translation, then loses its connection."""
    yield _chunk("#1\nHalf")
    raise httpx2.RemoteProtocolError("peer closed connection")


def _truncated_stream() -> Iterator[GenerateContentResponse]:
    """A stream that ends without an error and without a finish reason."""
    yield _chunk("#1\nHalf")


class GeminiClientRetryTests(LoggedTestCase):
    """Validate GeminiClient retry classification and client reuse."""

    def _make_client(self, **extra) -> GeminiClient:
        settings = SettingsType(
            {
                "instructions": "Verify retry behaviour.",
                "model": "gemini-3.1-flash-lite",
                "max_retries": 3,
                "backoff_time": 0.0,
                **extra,
            }
        )
        return GeminiClient(settings)

    def _make_request(self) -> TranslationRequest:
        prompt = TranslationPrompt("Translate this", False)
        prompt.content = "1. Hello"
        return TranslationRequest(prompt, None)

    def test_retries_transient_429_then_succeeds(self) -> None:
        """A 429 APIError is retried and a later success is returned."""
        client = self._make_client()
        responses = [_api_error(429), _api_error(503), {"text": "translated"}]

        def fake_response(request, temperature, *target):
            result = responses.pop(0)
            if isinstance(result, Exception):
                raise result
            return result

        with (
            patch.object(
                GeminiClient, "_get_gemini_response", side_effect=fake_response
            ),
            patch("PySubtrans.Providers.Clients.GeminiClient.time.sleep") as mock_sleep,
        ):
            result = client._send_messages(self._make_request(), temperature=0.0)

        self.assertLoggedEqual("result", {"text": "translated"}, result)
        self.assertLoggedEqual("sleep_count", 2, mock_sleep.call_count)

    def test_fails_fast_on_non_retryable_error(self) -> None:
        """A 404 APIError raises immediately without retrying."""
        client = self._make_client()

        with (
            patch.object(
                GeminiClient, "_get_gemini_response", side_effect=_api_error(404)
            ) as mock_response,
            patch("PySubtrans.Providers.Clients.GeminiClient.time.sleep") as mock_sleep,
        ):
            with self.assertRaises(TranslationImpossibleError):
                client._send_messages(self._make_request(), temperature=0.0)

        self.assertLoggedEqual("attempts", 1, mock_response.call_count)
        self.assertLoggedEqual("sleep_count", 0, mock_sleep.call_count)

    def test_gives_up_after_max_retries(self) -> None:
        """Persistent 429s raise TranslationImpossibleError after max_retries."""
        client = self._make_client()

        with (
            patch.object(
                GeminiClient, "_get_gemini_response", side_effect=_api_error(429)
            ) as mock_response,
            patch("PySubtrans.Providers.Clients.GeminiClient.time.sleep"),
        ):
            with self.assertRaises(TranslationImpossibleError):
                client._send_messages(self._make_request(), temperature=0.0)

        self.assertLoggedEqual("attempts", 4, mock_response.call_count)

    def test_client_is_created_once_and_reused(self) -> None:
        """_get_client creates the underlying genai client once and caches it."""
        client = self._make_client(api_key="test-key")

        with patch.object(
            GeminiClient, "_create_client", return_value=object()
        ) as mock_create:
            first = client._get_client()
            second = client._get_client()

        self.assertLoggedEqual("create_calls", 1, mock_create.call_count)
        self.assertLoggedIs("same_instance", first, second)

    def test_falls_back_after_attempts_per_target(self) -> None:
        """Each target gets attempts_per_target tries, then the next fallback is used."""
        client = self._make_client(
            vertex_location="global",
            fallbacks="gemini-4-argon@us, gemini-3.8-flash@us",
            attempts_per_target=2,
            max_retries=5,
        )
        calls: list[tuple[str | None, str | None]] = []

        def fake_response(request, temperature, model=None, location=None):
            calls.append((model, location))
            if len(calls) < 5:
                raise _api_error(429)
            return {"text": "translated"}

        with (
            patch.object(
                GeminiClient, "_get_gemini_response", side_effect=fake_response
            ),
            patch("PySubtrans.Providers.Clients.GeminiClient.time.sleep"),
        ):
            result = client._send_messages(self._make_request(), temperature=0.0)

        self.assertLoggedEqual("result", {"text": "translated"}, result)
        self.assertLoggedSequenceEqual(
            "targets",
            [
                ("gemini-3.1-flash-lite", "global"),
                ("gemini-3.1-flash-lite", "global"),
                ("gemini-4-argon", "us"),
                ("gemini-4-argon", "us"),
                ("gemini-3.8-flash", "us"),
            ],
            calls,
        )

    def test_fallback_defaults_to_configured_model_and_location(self) -> None:
        """A fallback names only what changes; duplicates of earlier targets are dropped."""
        client = self._make_client(
            vertex_location="global", fallbacks="@us,gemini-4-argon,@global"
        )

        self.assertLoggedSequenceEqual(
            "targets",
            [
                ("gemini-3.1-flash-lite", "global"),
                ("gemini-3.1-flash-lite", "us"),
                ("gemini-4-argon", "global"),
            ],
            client._targets(),
        )

    def test_one_client_per_location(self) -> None:
        """A fallback location gets its own cached client; the configured one is reused."""
        client = self._make_client(api_key="test-key", vertex_location="global")

        with patch.object(
            GeminiClient, "_create_client", side_effect=lambda location=None: object()
        ) as mock_create:
            main = client._get_client()
            us_first = client._get_client("us")
            us_second = client._get_client("us")
            main_again = client._get_client("global")

        self.assertLoggedEqual("create_calls", 2, mock_create.call_count)
        self.assertLoggedIs("us_cached", us_first, us_second)
        self.assertLoggedIs("main_cached", main, main_again)

    def test_request_timeout_reaches_the_http_client(self) -> None:
        """request_timeout (seconds) becomes the SDK's HttpOptions timeout (milliseconds)."""
        client = self._make_client(api_key="test-key", request_timeout=120.0)

        genai_client = client._create_client()

        self.assertLoggedEqual(
            "timeout_ms", 120000, genai_client._api_client._http_options.timeout
        )

    def test_fallback_from_another_generation_uses_model_default_temperature(
        self,
    ) -> None:
        """A 3.x fallback from a 2.x primary drops the primary's temperature; same-generation keeps it."""
        client = self._make_client(
            fallbacks="gemini-4-argon,gemini-2.5-flash", attempts_per_target=1
        )
        temperatures: list[float | None] = []

        def fake_response(request, temperature, model=None, location=None):
            temperatures.append(temperature)
            if len(temperatures) < 3:
                raise _api_error(429)
            return {"text": "translated"}

        with (
            patch.object(
                GeminiClient, "_get_gemini_response", side_effect=fake_response
            ),
            patch("PySubtrans.Providers.Clients.GeminiClient.time.sleep"),
        ):
            client._send_messages(self._make_request(), temperature=1.0)

        self.assertLoggedSequenceEqual("temperatures", [1.0, 1.0, None], temperatures)


class GeminiStreamRetryTests(LoggedTestCase):
    """Validate that a dropped Gemini stream is retried from a clean slate."""

    def _send(self, *streams: Iterator[GenerateContentResponse]) -> tuple:
        client = GeminiClient(
            SettingsType(
                {
                    "instructions": "Verify stream retries.",
                    "model": "gemini-3.1-flash-lite",
                    "max_retries": 3,
                    "backoff_time": 0.0,
                    "stream_responses": True,
                    "supports_streaming": True,
                }
            )
        )
        prompt = TranslationPrompt("Translate this", False)
        prompt.content = "1. Hello"
        request = TranslationRequest(prompt, lambda translation: None)
        sdk = MagicMock()
        sdk.models.generate_content_stream.side_effect = list(streams)
        with (
            patch.object(GeminiClient, "_get_client", return_value=sdk),
            patch("PySubtrans.Providers.Clients.GeminiClient.time.sleep"),
        ):
            result = client._send_messages(request, temperature=1.0)
        return result, sdk.models.generate_content_stream.call_count

    def test_mid_stream_drop_is_retried(self) -> None:
        """A stream dropped part way is retried and only the retry's text is kept."""
        complete = iter([_chunk("#1\nHei maailma", FinishReason.STOP)])
        result, calls = self._send(_dropped_stream(), complete)

        assert result is not None
        self.assertLoggedEqual("stream calls", 2, calls)
        self.assertLoggedEqual("text", "#1\nHei maailma", result["text"])
        self.assertLoggedEqual("finish_reason", "complete", result["finish_reason"])

    def test_stream_without_finish_reason_is_retried(self) -> None:
        """A stream that ends silently without a finish reason is not a translation."""
        complete = iter([_chunk("#1\nHei maailma", FinishReason.STOP)])
        result, calls = self._send(_truncated_stream(), complete)

        assert result is not None
        self.assertLoggedEqual("stream calls", 2, calls)
        self.assertLoggedEqual("text", "#1\nHei maailma", result["text"])


class GeminiParallelStreamTests(LoggedTestCase):
    """Validate that a parallel batch, which sends no callback, still streams."""

    def _request(self, sdk: MagicMock, stream_responses: bool = True):
        client = GeminiClient(
            SettingsType(
                {
                    "instructions": "Verify parallel streams.",
                    "model": "gemini-3.1-flash-lite",
                    "max_retries": 3,
                    "backoff_time": 0.0,
                    "stream_responses": stream_responses,
                    "supports_streaming": True,
                }
            )
        )
        prompt = TranslationPrompt("Translate this", False)
        prompt.content = "1. Hello"
        prompt.system_prompt = "Translate to Finnish."
        with (
            patch.object(GeminiClient, "_get_client", return_value=sdk),
            patch("PySubtrans.Providers.Clients.GeminiClient.time.sleep"),
        ):
            # SubtitleTranslator's parallel path passes no streaming callback
            return client.RequestTranslation(prompt)

    def test_parallel_batch_streams(self) -> None:
        """A request without a callback uses generate_content_stream."""
        sdk = MagicMock()
        sdk.models.generate_content_stream.return_value = iter(
            [_chunk("#1\nHei maailma", FinishReason.STOP)]
        )
        translation = self._request(sdk)

        assert translation is not None
        self.assertLoggedEqual(
            "stream calls", 1, sdk.models.generate_content_stream.call_count
        )
        self.assertLoggedEqual(
            "blocking calls", 0, sdk.models.generate_content.call_count
        )
        self.assertLoggedEqual("text", "#1\nHei maailma", translation.text)

    def test_parallel_stream_without_finish_reason_is_retried(self) -> None:
        """A parallel stream that ends without a finish reason is sent again."""
        sdk = MagicMock()
        sdk.models.generate_content_stream.side_effect = [
            _truncated_stream(),
            iter([_chunk("#1\nHei maailma", FinishReason.STOP)]),
        ]
        translation = self._request(sdk)

        assert translation is not None
        self.assertLoggedEqual(
            "stream calls", 2, sdk.models.generate_content_stream.call_count
        )
        self.assertLoggedEqual("text", "#1\nHei maailma", translation.text)

    def test_streaming_off_sends_one_blocking_request(self) -> None:
        """With stream_responses off, a parallel batch uses generate_content."""
        sdk = MagicMock()
        sdk.models.generate_content.return_value = _chunk(
            "#1\nHei maailma", FinishReason.STOP
        )
        translation = self._request(sdk, stream_responses=False)

        assert translation is not None
        self.assertLoggedEqual(
            "blocking calls", 1, sdk.models.generate_content.call_count
        )
        self.assertLoggedEqual(
            "stream calls", 0, sdk.models.generate_content_stream.call_count
        )
