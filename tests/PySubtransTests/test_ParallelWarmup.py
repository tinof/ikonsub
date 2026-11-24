"""parallel_warmup: the first batch is sent ahead of the others (prompt-cache warm-up)."""

import threading
from datetime import timedelta
from functools import partial
from unittest.mock import MagicMock

import regex

from PySubtrans.Helpers.TestCases import DummyProvider, SubtitleTestCase
from PySubtrans.Options import Options
from PySubtrans.SubtitleBuilder import SubtitleBuilder
from PySubtrans.SubtitleError import TranslationImpossibleError
from PySubtrans.Subtitles import Subtitles
from PySubtrans.SubtitleTranslator import SubtitleTranslator
from PySubtrans.Translation import Translation
from PySubtrans.TranslationClient import TranslationClient
from PySubtrans.TranslationPrompt import TranslationPrompt

LINE_NUMBER = regex.compile(r"^#(\d+)", regex.MULTILINE)


def _subtitles(batches: int) -> Subtitles:
    """One scene of 3-line batches."""
    builder = SubtitleBuilder(max_batch_size=3, min_batch_size=3)
    builder.AddScene()
    for number in range(1, batches * 3 + 1):
        start = timedelta(seconds=number * 3)
        builder.BuildLine(start, start + timedelta(seconds=2), f"English {number}")
    return builder.Build()


class TestParallelWarmup(SubtitleTestCase):
    def _translator(self, warmup: bool, on_request) -> SubtitleTranslator:
        """A parallel translator whose client calls on_request(first_line_number)."""
        options = Options(self.options)
        options["parallel_batches"] = True
        options["max_parallel_workers"] = 4
        options["parallel_warmup"] = warmup
        translator = SubtitleTranslator(options, translation_provider=DummyProvider({}))

        def request(prompt: TranslationPrompt, *_args, **_kwargs) -> Translation:
            text = "\n".join(str(m.get("content")) for m in prompt.messages)
            numbers = [int(n) for n in LINE_NUMBER.findall(text)]
            on_request(numbers[0])
            reply = "".join(f"#{n}\nTranslation>\nSuomi {n}\n\n" for n in numbers)
            return Translation({"text": reply, "finish_reason": "end_turn"})

        client = MagicMock()
        client.RequestTranslation.side_effect = request
        client.GetParser.side_effect = translator.client.GetParser
        client.BuildTranslationPrompt.side_effect = partial(
            TranslationClient.BuildTranslationPrompt, translator.client
        )
        client.enable_streaming = False
        client.temperature = 0.0
        client.response_started = threading.Event()
        translator.client = client
        translator.instructions.instructions = "Translate."
        return translator

    def test_other_batches_wait_for_the_first_response(self) -> None:
        lock = threading.Lock()
        started: list[int] = []
        seen_before_signal: list[int] = []
        holder: dict[str, SubtitleTranslator] = {}

        def on_request(first_line: int) -> None:
            with lock:
                started.append(first_line)
            if first_line == 1:
                # Give the other workers time to start, were they queued already
                threading.Event().wait(0.3)
                with lock:
                    seen_before_signal.extend(started)
                holder["translator"].client.response_started.set()

        translator = self._translator(True, on_request)
        holder["translator"] = translator
        subtitles = _subtitles(4)

        translator.TranslateSubtitles(subtitles)

        self.assertLoggedEqual(
            "only batch 1 before the signal", [1], seen_before_signal
        )
        self.assertLoggedEqual("all batches sent", [1, 4, 7, 10], sorted(started))
        self.assertLoggedEqual("first batch first", 1, started[0])
        self.assertLoggedEqual("all translated", 12, len(subtitles.translated or []))

    def test_without_warmup_batches_start_together(self) -> None:
        second_started = threading.Event()
        overlapped: list[bool] = []

        def on_request(first_line: int) -> None:
            if first_line == 1:
                overlapped.append(second_started.wait(5.0))
            else:
                second_started.set()

        translator = self._translator(False, on_request)
        translator.TranslateSubtitles(_subtitles(2))

        self.assertLoggedEqual("batch 2 started during batch 1", [True], overlapped)

    def test_a_refused_first_batch_sends_nothing_else(self) -> None:
        requests: list[int] = []

        def on_request(first_line: int) -> None:
            requests.append(first_line)
            raise TranslationImpossibleError("refused")

        translator = self._translator(True, on_request)

        with self.assertRaises(TranslationImpossibleError):
            translator.TranslateSubtitles(_subtitles(3))

        self.assertLoggedEqual("only the first batch was sent", [1], requests)
