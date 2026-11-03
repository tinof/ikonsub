from __future__ import annotations

from unittest.mock import MagicMock, patch

from google.genai.types import (
    Candidate,
    Content,
    FinishReason,
    GenerateContentResponse,
    Part,
)

from PySubtrans.Helpers.TestCases import LoggedTestCase
from PySubtrans.Helpers.Tests import skip_if_debugger_attached
from PySubtrans.Providers.Clients.GeminiClient import GeminiClient
from PySubtrans.SettingsType import SettingsType
from PySubtrans.SubtitleError import TranslationResponseError
from PySubtrans.TranslationPrompt import TranslationPrompt
from PySubtrans.TranslationRequest import TranslationRequest


def _text_chunk(text: str) -> GenerateContentResponse:
    return GenerateContentResponse(
        candidates=[
            Candidate(content=Content(role="model", parts=[Part.from_text(text=text)]))
        ]
    )


def _finish_chunk(finish_reason: FinishReason) -> GenerateContentResponse:
    """A final chunk that carries only the finish reason, with no content parts."""
    return GenerateContentResponse(candidates=[Candidate(finish_reason=finish_reason)])


class GeminiStreamingFinishReasonTests(LoggedTestCase):
    """Validate that a streamed finish_reason reaches the response checks."""

    def _make_client(self) -> GeminiClient:
        settings = SettingsType(
            {
                "instructions": "Verify streaming.",
                "model": "gemini-3.1-flash-lite",
                "stream_responses": True,
                "supports_streaming": True,
            }
        )
        return GeminiClient(settings)

    def _make_request(self) -> TranslationRequest:
        prompt = TranslationPrompt("Translate this", False)
        prompt.content = "1. Hello"
        return TranslationRequest(prompt, lambda translation: None)

    def _stream(self, chunks: list[GenerateContentResponse]) -> dict | None:
        client = self._make_client()
        sdk = MagicMock()
        sdk.models.generate_content_stream.return_value = iter(chunks)
        with patch.object(GeminiClient, "_get_client", return_value=sdk):
            return client._get_gemini_response(self._make_request(), temperature=1.0)

    @skip_if_debugger_attached
    def test_finish_only_chunk_max_tokens_is_detected(self) -> None:
        """MAX_TOKENS on a final chunk without parts marks the response as truncated."""
        with self.assertRaises(TranslationResponseError):
            self._stream([_text_chunk("#1\nHei"), _finish_chunk(FinishReason.MAX_TOKENS)])

    @skip_if_debugger_attached
    def test_finish_only_chunk_safety_is_detected(self) -> None:
        """SAFETY on a final chunk without parts is not accepted as a translation."""
        with self.assertRaises(TranslationResponseError):
            self._stream([_text_chunk("#1\nHei"), _finish_chunk(FinishReason.SAFETY)])

    def test_finish_only_chunk_stop_is_complete(self) -> None:
        """STOP on a final chunk without parts completes the accumulated text."""
        result = self._stream([_text_chunk("#1\nHei"), _finish_chunk(FinishReason.STOP)])
        assert result is not None
        self.assertLoggedEqual("finish_reason", "complete", result.get("finish_reason"))
