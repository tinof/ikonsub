"""Previous and upcoming lines as context for sequential batches (context_lines, lookahead_lines)."""

from datetime import timedelta
from functools import partial
from unittest.mock import MagicMock

import regex

from PySubtrans.Helpers.ContextHelpers import (
    PREVIOUS_PAIRS_KEY,
    CollectLinePairs,
    FormatPreviousLines,
    FormatUpcomingLines,
    GetBatchContext,
    PrecomputeAllBatchContexts,
)
from PySubtrans.Helpers.TestCases import DummyProvider, SubtitleTestCase
from PySubtrans.Options import Options
from PySubtrans.SubtitleBuilder import SubtitleBuilder
from PySubtrans.SubtitleLine import SubtitleLine
from PySubtrans.Subtitles import Subtitles
from PySubtrans.SubtitleTranslator import SubtitleTranslator
from PySubtrans.Translation import Translation
from PySubtrans.TranslationClient import TranslationClient
from PySubtrans.TranslationParser import TranslationParser
from PySubtrans.TranslationPrompt import TranslationPrompt
from PySubtrans.TranslationResume import FINGERPRINT_KEYS

LINE_NUMBER = regex.compile(r"^#(\d+)", regex.MULTILINE)


def _subtitles(*scene_sizes: int) -> Subtitles:
    """Scenes of 3-line batches; line N says "English N"."""
    builder = SubtitleBuilder(max_batch_size=3, min_batch_size=3)
    number = 0
    for size in scene_sizes:
        builder.AddScene()
        for _ in range(size):
            number += 1
            start = timedelta(seconds=number * 3)
            builder.BuildLine(start, start + timedelta(seconds=2), f"English {number}")
    return builder.Build()


def _user_text(prompt: TranslationPrompt) -> str:
    return "\n".join(
        str(m.get("content")) for m in prompt.messages if m.get("role") == "user"
    )


def _response(numbers: list[int], finish_reason: str = "end_turn") -> Translation:
    text = "".join(f"#{n}\nTranslation>\nSuomi {n}\n\n" for n in numbers)
    return Translation({"text": text, "finish_reason": finish_reason})


class ContextLinesTestBase(SubtitleTestCase):
    def _translator(
        self, context_lines: int, lookahead_lines: int, responses=None
    ) -> SubtitleTranslator:
        options = Options(self.options)
        options["context_lines"] = context_lines
        options["lookahead_lines"] = lookahead_lines
        translator = SubtitleTranslator(options, translation_provider=DummyProvider({}))
        self.prompts: list[TranslationPrompt] = []

        def request(prompt: TranslationPrompt, streaming_callback=None) -> Translation:
            self.prompts.append(prompt)
            if responses:
                return responses.pop(0)
            numbers = [int(n) for n in LINE_NUMBER.findall(_user_text(prompt))]
            return _response(numbers)

        self.real_client = translator.client
        client = MagicMock()
        client.RequestTranslation.side_effect = request
        client.GetParser.side_effect = translator.client.GetParser
        # The base prompt builder: the dummy client's drops every context tag
        client.BuildTranslationPrompt.side_effect = partial(
            TranslationClient.BuildTranslationPrompt, translator.client
        )
        client.enable_streaming = False
        client.temperature = 0.0
        translator.client = client
        translator.instructions.instructions = "Translate."
        return translator

    def _run(
        self, subtitles: Subtitles, context_lines: int, lookahead_lines: int
    ) -> list[str]:
        translator = self._translator(context_lines, lookahead_lines)
        for scene in subtitles.scenes:
            translator.TranslateScene(subtitles, scene)
        return [_user_text(prompt) for prompt in self.prompts]


class TestContextLinesOff(ContextLinesTestBase):
    def test_default_settings_add_nothing(self) -> None:
        self.assertLoggedEqual(
            "context_lines default", 0, Options().get_int("context_lines")
        )
        self.assertLoggedEqual(
            "lookahead_lines default", 0, Options().get_int("lookahead_lines")
        )
        subtitles = _subtitles(9)
        context = GetBatchContext(subtitles, 1, 2, 10)
        self.assertLoggedNotIn("no previous", "previous_lines", context)
        self.assertLoggedNotIn("no upcoming", "upcoming_lines", context)

    def test_prompts_at_zero_carry_only_the_old_tags(self) -> None:
        prompts = self._run(_subtitles(6), 0, 0)
        for prompt in prompts:
            context = prompt.split("<context>")[1].split("</context>")[0]
            tags = set(regex.findall(r"^<(\w+)>", context, regex.MULTILINE))
            self.assertLoggedTrue(
                "old tags only", tags <= {"scene", "batch", "history"}
            )

    def test_parallel_contexts_are_unchanged(self) -> None:
        subtitles = _subtitles(6)
        subtitles.settings["context_lines"] = 20
        subtitles.settings["lookahead_lines"] = 5
        contexts = PrecomputeAllBatchContexts(subtitles)
        for context in contexts.values():
            self.assertLoggedNotIn("no previous", "previous_lines", context)
            self.assertLoggedNotIn("no upcoming", "upcoming_lines", context)


class TestContextLinesOn(ContextLinesTestBase):
    def test_later_batches_see_earlier_pairs_and_next_lines(self) -> None:
        subtitles = _subtitles(9)
        prompts = self._run(subtitles, 4, 2)
        batches = subtitles.scenes[0].batches
        self.assertLoggedTrue("several batches", len(batches) >= 3)

        self.assertLoggedNotIn(
            "first batch has no previous", "<previous_lines>", prompts[0]
        )
        for index, batch in enumerate(batches):
            first = batch.originals[0].number
            last = batch.originals[-1].number
            if index > 0:
                expected = "\n\n".join(
                    f"Source: English {n}\nTranslated: Suomi {n}"
                    for n in range(max(1, first - 4), first)
                )
                self.assertLoggedIn("last 4 pairs in order", expected, prompts[index])
                if first - 5 >= 1:
                    self.assertLoggedNotIn(
                        "capped at 4", f"English {first - 5}\n", prompts[index]
                    )
            if last < 9:
                upcoming = "\n".join(
                    f"English {n}" for n in range(last + 1, min(last + 2, 9) + 1)
                )
                self.assertLoggedIn(
                    "next lines", upcoming + "\n</upcoming_lines>", prompts[index]
                )
        self.assertLoggedNotIn(
            "last batch has no upcoming", "<upcoming_lines>", prompts[-1]
        )

    def test_previous_lines_cross_scenes_and_upcoming_stops_at_scene_end(self) -> None:
        prompts = self._run(_subtitles(3, 3), 2, 5)
        self.assertLoggedNotIn("scene end", "<upcoming_lines>", prompts[0])
        self.assertLoggedIn(
            "previous scene's lines",
            "Source: English 2\nTranslated: Suomi 2\n\nSource: English 3\nTranslated: Suomi 3",
            prompts[1],
        )

    def test_merged_lines_join_their_source(self) -> None:
        subtitles = _subtitles(3)
        batch = subtitles.scenes[0].batches[0]
        batch.translated = [
            SubtitleLine.Construct(
                1, timedelta(seconds=3), timedelta(seconds=8), "Yhdessä"
            ),
            SubtitleLine.Construct(
                3, timedelta(seconds=9), timedelta(seconds=11), "Kolme"
            ),
        ]
        pairs = CollectLinePairs([batch])
        self.assertLoggedEqual(
            "merge pair",
            [("English 1 English 2", "Yhdessä"), ("English 3", "Kolme")],
            pairs,
        )
        rendered = FormatPreviousLines(pairs, 10) or ""
        for marker in ("[MERGE", "[MERGED]", "#Fuzzy", "Translation>", "#1"):
            self.assertLoggedNotIn(f"no {marker}", marker, rendered)

    def test_format_keeps_the_most_recent_pairs(self) -> None:
        rendered = FormatPreviousLines([("A", "a"), ("B", "b"), ("C", "c")], 2) or ""
        self.assertLoggedNotIn("oldest dropped", "Source: A", rendered)
        self.assertLoggedIn(
            "newest kept", "Source: B\nTranslated: b\n\nSource: C", rendered
        )

    def test_parser_reads_nothing_from_the_blocks(self) -> None:
        subtitles = _subtitles(3)
        previous = FormatPreviousLines(
            [("English 1", "Suomi 1"), ("English 2", "Suomi 2")], 5
        )
        upcoming = FormatUpcomingLines(subtitles.scenes[0].batches[0].originals, 5)
        parser = TranslationParser("Translation", self.options)
        for block in (previous, upcoming):
            for pattern in parser.regex_patterns:
                self.assertLoggedEqual(
                    "no matches", [], parser.FindMatches(f"<x>{block}</x>", pattern)
                )

    def test_split_halves_get_each_others_lines(self) -> None:
        subtitles = _subtitles(4)
        builder_batch = subtitles.scenes[0].batches[0]
        translator = self._translator(
            5,
            5,
            responses=[_response([1, 2]), _response([3, 4])],
        )
        context = GetBatchContext(
            subtitles, 1, 1, 10, context_lines=5, lookahead_lines=5
        )
        originals = (
            builder_batch.originals[:4]
            if len(builder_batch.originals) >= 4
            else [
                line
                for batch in subtitles.scenes[0].batches
                for line in batch.originals
            ][:4]
        )
        translator._translate_split_batch(
            builder_batch, originals, context, "Translate."
        )
        first, second = (_user_text(p) for p in self.prompts)
        upcoming_a = first.split("<upcoming_lines>")[1].split("</upcoming_lines>")[0]
        self.assertLoggedIn("first half sees the second half", "English 3", upcoming_a)
        self.assertLoggedIn(
            "second half sees the first half",
            "Source: English 1\nTranslated: Suomi 1",
            second,
        )

    def test_retry_prompt_keeps_the_context(self) -> None:
        subtitles = _subtitles(6)
        context = GetBatchContext(
            subtitles, 1, 2, 10, context_lines=5, lookahead_lines=5
        )
        subtitles.scenes[0].batches[0].translated = [
            SubtitleLine.Construct(
                n, timedelta(seconds=n * 3), timedelta(seconds=n * 3 + 2), f"Suomi {n}"
            )
            for n in (1, 2, 3)
        ]
        context = GetBatchContext(
            subtitles, 1, 2, 10, context_lines=5, lookahead_lines=5
        )
        translator = self._translator(5, 5)
        prompt = TranslationClient.BuildTranslationPrompt(
            self.real_client,
            translator.user_prompt,
            "Translate.",
            subtitles.scenes[0].batches[1].originals,
            context,
        )
        prompt.GenerateRetryPrompt("#4\nTranslation>\nX", "Retry.", None)
        self.assertLoggedIn(
            "previous kept", "<previous_lines>", str(prompt.messages[0]["content"])
        )
        self.assertLoggedNotIn(
            "pairs list not rendered", PREVIOUS_PAIRS_KEY, _user_text(prompt)
        )


class TestContextLinesSettings(SubtitleTestCase):
    def test_settings_survive_and_are_fingerprinted(self) -> None:
        options = Options({"context_lines": 20, "lookahead_lines": 5})
        settings = options.GetSettings()
        self.assertLoggedEqual("context_lines kept", 20, settings.get("context_lines"))
        self.assertLoggedEqual(
            "lookahead_lines kept", 5, settings.get("lookahead_lines")
        )
        self.assertLoggedIn("fingerprint context", "context_lines", FINGERPRINT_KEYS)
        self.assertLoggedIn(
            "fingerprint lookahead", "lookahead_lines", FINGERPRINT_KEYS
        )
