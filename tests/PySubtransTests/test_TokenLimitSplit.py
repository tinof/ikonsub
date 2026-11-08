from datetime import timedelta
from functools import partial
from unittest.mock import MagicMock

from PySubtrans.Helpers.TestCases import DummyProvider, SubtitleTestCase
from PySubtrans.Helpers.Tests import skip_if_debugger_attached
from PySubtrans.SubtitleBatch import SubtitleBatch
from PySubtrans.SubtitleBuilder import SubtitleBuilder
from PySubtrans.SubtitleError import TranslationError
from PySubtrans.SubtitleTranslator import SubtitleTranslator
from PySubtrans.Translation import Translation
from PySubtrans.TranslationClient import TranslationClient


def _response(numbers: list[int], finish_reason: str = "end_turn") -> Translation:
    """A well-formed translation of the given line numbers"""
    text = "".join(
        f"#{n}\nOriginal>\nLine {n}\nTranslation>\nRivi {n}\n\n" for n in numbers
    )
    return Translation({"text": text, "finish_reason": finish_reason})


class TokenLimitSplitTests(SubtitleTestCase):
    """A batch that hits the token limit is split once, and never silently dropped."""

    def _batch(self, line_count: int) -> SubtitleBatch:
        builder = SubtitleBuilder(max_batch_size=10).AddScene(summary="Scene")
        for i in range(line_count):
            start = timedelta(seconds=i * 3 + 1)
            builder.BuildLine(start, start + timedelta(seconds=2), f"Line {i + 1}")
        subtitles = builder.Build()
        return subtitles.scenes[0].batches[0]

    def _translator(self, *responses: Translation) -> SubtitleTranslator:
        translator = SubtitleTranslator(
            self.options, translation_provider=DummyProvider({})
        )
        client = MagicMock()
        client.RequestTranslation.side_effect = list(responses)
        client.GetParser.side_effect = translator.client.GetParser
        # The base prompt builder: the dummy client's insists on test metadata
        client.BuildTranslationPrompt.side_effect = partial(
            TranslationClient.BuildTranslationPrompt, translator.client
        )
        client.enable_streaming = False
        client.temperature = 0.0
        translator.client = client
        translator.instructions.instructions = "Translate."
        return translator

    @skip_if_debugger_attached
    def test_single_line_batch_raises(self) -> None:
        """A one-line batch that hits the limit is an error, not an empty success."""
        batch = self._batch(1)
        translator = self._translator()
        with self.assertRaises(TranslationError):
            translator._translate_split_batch(batch, batch.originals, {}, "Translate.")

    @skip_if_debugger_attached
    def test_half_that_still_hits_limit_raises(self) -> None:
        """A half that also hits the limit raises instead of keeping partial text."""
        batch = self._batch(4)
        translator = self._translator(_response([1], finish_reason="length"))
        with self.assertRaises(TranslationError) as raised:
            translator._translate_split_batch(batch, batch.originals, {}, "Translate.")
        self.assertLoggedIn("token limit", "token limit", str(raised.exception))

    @skip_if_debugger_attached
    def test_truncated_empty_half_reports_token_limit(self) -> None:
        """A half truncated before any text names the token limit."""
        batch = self._batch(4)
        empty = Translation({"text": "", "finish_reason": "length"})
        translator = self._translator(empty)
        with self.assertRaises(TranslationError) as raised:
            translator._translate_split_batch(batch, batch.originals, {}, "Translate.")
        self.assertLoggedIn("token limit", "token limit", str(raised.exception))

    def test_token_limit_splits_and_merges(self) -> None:
        """A truncated batch is retried as two halves and every line is translated."""
        batch = self._batch(4)
        translator = self._translator(
            _response([1, 2], finish_reason="length"),
            _response([1, 2]),
            _response([3, 4]),
        )
        translator.TranslateBatch(batch, None, {})
        assert isinstance(translator.client, MagicMock)
        self.assertLoggedEqual(
            "requests", 3, translator.client.RequestTranslation.call_count
        )
        self.assertLoggedTrue("all translated", batch.all_translated)
        self.assertLoggedEqual(
            "texts",
            ["Rivi 1", "Rivi 2", "Rivi 3", "Rivi 4"],
            [line.text for line in batch.translated],
        )

    @skip_if_debugger_attached
    def test_streamed_lines_are_dropped_when_split_fails(self) -> None:
        """Lines streamed from a truncated response do not survive a failed split."""
        batch = self._batch(4)
        translator = self._translator()
        assert isinstance(translator.client, MagicMock)
        calls: list[int] = []

        def request(*args, **kwargs) -> Translation:
            calls.append(1)
            if len(calls) == 1:
                # Stands in for the streaming callback writing lines into the batch
                translator.ProcessBatchTranslation(batch, _response([1, 2, 3, 4]), None)
                return _response([1, 2, 3, 4], finish_reason="length")
            return _response([1], finish_reason="length")

        translator.client.RequestTranslation.side_effect = request

        with self.assertRaises(TranslationError):
            translator.TranslateBatch(batch, None, {})
        self.assertLoggedFalse("streamed lines dropped", batch.any_translated)

    @skip_if_debugger_attached
    def test_retranslation_token_limit_raises(self) -> None:
        """A retranslation that hits the limit is an error, not a partial result."""
        batch = self._batch(2)
        translator = self._translator(
            _response([1, 2]), _response([1], finish_reason="length")
        )
        translator.TranslateBatch(batch, None, {})
        translator.instructions.retry_instructions = "Fix it."
        batch.errors = [TranslationError("bad")]
        with self.assertRaises(TranslationError) as raised:
            translator.RequestRetranslation(batch)
        self.assertLoggedIn("token limit", "token limit", str(raised.exception))

    def test_split_half_with_errors_is_retried(self) -> None:
        """A split half that fails validation gets the same retry as a whole batch."""
        batch = self._batch(4)
        translator = self._translator(
            _response([1, 2], finish_reason="length"),
            _response([1]),  # line 2 missing: validation error
            _response([3, 4]),
        )
        translator.retry_on_error = True
        retried: list[SubtitleBatch] = []
        translator.RequestRetranslation = (  # type: ignore[method-assign]
            lambda half, **kwargs: retried.append(half)
        )
        translator.TranslateBatch(batch, None, {})
        self.assertLoggedEqual("halves retried", 1, len(retried))
        self.assertLoggedEqual(
            "retried half", [1, 2], [line.number for line in retried[0].originals]
        )
