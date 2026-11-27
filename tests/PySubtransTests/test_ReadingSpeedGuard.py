"""The max_translation_cps guard on the real path: parser, MatchTranslations, retry."""

from datetime import timedelta
from functools import partial
from unittest.mock import MagicMock

from PySubtrans.Helpers.TestCases import DummyProvider, SubtitleTestCase
from PySubtrans.Options import Options
from PySubtrans.SubtitleBuilder import SubtitleBuilder
from PySubtrans.SubtitleError import ReadingSpeedError
from PySubtrans.Subtitles import Subtitles
from PySubtrans.SubtitleTranslator import SubtitleTranslator
from PySubtrans.Translation import Translation
from PySubtrans.TranslationClient import TranslationClient
from PySubtrans.TranslationPrompt import TranslationPrompt

# 30 characters: 30 cps on a 1-second cue, 15 cps on two merged 1-second cues
THIRTY_CHARS = "Tämä käännös on liian pitkä nyt"[:30]
SHORT = "Selvä."


def _subtitles() -> Subtitles:
    """One batch of three 1-second cues, back to back."""
    builder = SubtitleBuilder(max_batch_size=10, min_batch_size=1)
    builder.AddScene()
    for number in range(3):
        start = timedelta(seconds=number)
        builder.BuildLine(start, start + timedelta(seconds=1), f"English {number + 1}")
    return builder.Build()


def _response(texts: dict[int, str]) -> Translation:
    text = "".join(f"#{n}\nTranslation>\n{body}\n\n" for n, body in texts.items())
    return Translation({"text": text, "finish_reason": "end_turn"})


class TestReadingSpeedGuard(SubtitleTestCase):
    def _translator(
        self, responses: list[Translation], max_cps: float | None = 25.0
    ) -> SubtitleTranslator:
        options = Options(self.options)
        # The test base turns retries off; exsubs and transubs leave them on
        options["retry_on_error"] = True
        if max_cps is not None:
            options["max_translation_cps"] = max_cps
        translator = SubtitleTranslator(options, translation_provider=DummyProvider({}))
        self.prompts: list[str] = []

        def request(prompt: TranslationPrompt, *_args, **_kwargs) -> Translation:
            self.prompts.append(
                "\n".join(str(m.get("content")) for m in prompt.messages)
            )
            return responses.pop(0)

        client = MagicMock()
        client.RequestTranslation.side_effect = request
        client.GetParser.side_effect = translator.client.GetParser
        client.BuildTranslationPrompt.side_effect = partial(
            TranslationClient.BuildTranslationPrompt, translator.client
        )
        client.enable_streaming = False
        client.temperature = 0.0
        translator.client = client
        translator.instructions.instructions = "Translate."
        translator.instructions.retry_instructions = "Fix the errors."
        return translator

    def test_fast_line_fails_the_batch_after_matching(self) -> None:
        subtitles = _subtitles()
        batch = subtitles.scenes[0].batches[0]
        translator = self._translator([])

        translator.ProcessBatchTranslation(
            batch, _response({1: THIRTY_CHARS, 2: SHORT, 3: SHORT}), None
        )

        self.assertLoggedEqual("error count", 1, len(batch.errors))
        error = batch.errors[0]
        self.assertLoggedIsInstance("error type", error, ReadingSpeedError)
        assert isinstance(error, ReadingSpeedError)
        self.assertLoggedEqual(
            "flagged line numbers", [1], [line.number for line in error.lines]
        )
        self.assertLoggedIn("message names the line", "#1", str(error))

    def test_merged_cue_is_measured_over_its_extended_time(self) -> None:
        subtitles = _subtitles()
        batch = subtitles.scenes[0].batches[0]
        translator = self._translator([])

        translator.ProcessBatchTranslation(
            batch, _response({1: f"[MERGE 1+2] {THIRTY_CHARS}", 3: SHORT}), None
        )

        self.assertLoggedEqual("no errors", [], batch.errors)
        merged = (batch.translated or [])[0]
        self.assertLoggedEqual("merged duration", 2.0, merged.duration.total_seconds())

    def test_guard_is_off_without_the_setting(self) -> None:
        subtitles = _subtitles()
        batch = subtitles.scenes[0].batches[0]
        translator = self._translator([], max_cps=None)

        translator.ProcessBatchTranslation(
            batch, _response({1: THIRTY_CHARS, 2: SHORT, 3: SHORT}), None
        )

        self.assertLoggedEqual("no errors", [], batch.errors)

    def test_fast_line_triggers_one_retry_that_names_it(self) -> None:
        subtitles = _subtitles()
        scene = subtitles.scenes[0]
        translator = self._translator(
            [
                _response({1: THIRTY_CHARS, 2: SHORT, 3: SHORT}),
                _response({1: "Lyhyempi käännös", 2: SHORT, 3: SHORT}),
            ]
        )

        translator.TranslateScene(subtitles, scene)

        self.assertLoggedEqual("requests", 2, len(self.prompts))
        self.assertLoggedIn(
            "retry prompt names the line",
            "characters per second for their display time: #1",
            self.prompts[1],
        )
        batch = scene.batches[0]
        self.assertLoggedEqual("retry passed", [], batch.errors)
        self.assertLoggedEqual(
            "retried text kept",
            "Lyhyempi käännös",
            (batch.translated or [])[0].text,
        )

    def test_selected_lines_ignore_a_fast_line_outside_the_selection(self) -> None:
        subtitles = _subtitles()
        scene = subtitles.scenes[0]
        batch = scene.batches[0]
        translator = self._translator(
            [
                _response({1: THIRTY_CHARS, 2: "Uusi", 3: SHORT}),
                _response({1: THIRTY_CHARS, 2: "Uusi", 3: SHORT}),
            ]
        )
        # An earlier pass left a too-fast #1 that this pass may not replace
        translator.ProcessBatchTranslation(
            batch, _response({1: THIRTY_CHARS, 2: SHORT, 3: SHORT}), None
        )

        translator.TranslateScene(subtitles, scene, line_numbers=[2])

        self.assertLoggedEqual("requests", 1, len(self.prompts))
        self.assertLoggedEqual("no errors", [], batch.errors)
        self.assertLoggedEqual(
            "selected line replaced", "Uusi", (batch.translated or [])[1].text
        )

    def test_batch_within_budget_is_not_retried(self) -> None:
        subtitles = _subtitles()
        scene = subtitles.scenes[0]
        translator = self._translator([_response({1: SHORT, 2: SHORT, 3: SHORT})])

        translator.TranslateScene(subtitles, scene)

        self.assertLoggedEqual("requests", 1, len(self.prompts))
        self.assertLoggedEqual("no errors", [], scene.batches[0].errors)
