"""Tests for resuming a failed translation: a re-run sends only the batches still missing."""

import json
import os
import tempfile
import threading
from copy import deepcopy
from pathlib import Path
from typing import Any
from unittest.mock import patch

from PySubtrans import init_options, init_translator
from PySubtrans.Helpers.TestCases import (
    DummyProvider,
    DummyTranslationClient,
    LoggedTestCase,
)
from PySubtrans.MKV.Config import MKVConfig, TranslationMode
from PySubtrans.SettingsType import SettingsType
from PySubtrans.SubtitleBatch import SubtitleBatch
from PySubtrans.SubtitleError import TranslationError, TranslationImpossibleError
from PySubtrans.SubtitleLine import SubtitleLine
from PySubtrans.Subtitles import Subtitles
from PySubtrans.SubtitleScene import SubtitleScene
from PySubtrans.Translation import Translation
from PySubtrans.TranslationClient import TranslationClient
from PySubtrans.TranslationPrompt import TranslationPrompt
from PySubtrans.TranslationRequest import TranslationRequest
from PySubtrans.TranslationResume import RESUME_STATE_SUFFIX, TranslationResume
from scripts import exsubs, transubs

LINE_COUNT = 12
BATCH_SIZE = 3
SCENE_GAP = 30


def _source_srt(
    edit: int | None = None, count: int = LINE_COUNT, gap_after: int | None = None
) -> str:
    """Twelve 2-second cues 1 second apart: four batches of three, one scene unless
    gap_after puts a long pause after that line."""
    blocks = []
    for number in range(1, count + 1):
        start = (number - 1) * 3
        if gap_after is not None and number > gap_after:
            start += SCENE_GAP
        text = f"Line number {number} is spoken here."
        if number == edit:
            text = f"Line number {number} was edited."
        blocks.append(
            f"{number}\n{_timestamp(start)} --> {_timestamp(start + 2)}\n{text}\n"
        )
    return "\n".join(blocks)


def _timestamp(seconds: float) -> str:
    millis = round(seconds * 1000)
    return f"00:{millis // 60000:02d}:{millis // 1000 % 60:02d},{millis % 1000:03d}"


def _realistic_srt() -> str:
    """Cues the source prep and the continuation pre-merge rewrite: odd milliseconds, a
    trailing space, a rapid-fire continuation pair and a sound description."""
    cues: list[tuple[float, float, str]] = []
    for number in range(1, 11):
        start = (number - 1) * 3 + 0.25
        cues.append((start, start + 2.1, f"Line number {number} is spoken here."))
    cues[2] = (cues[2][0], cues[2][1], "Line number 3 is spoken here. ")
    cues.append((11.4, 11.8, "And this sentence is"))
    cues.append((11.85, 12.2, "split in two."))
    cues.append((23.4, 24.1, "[MUSIC PLAYING]"))
    blocks = [
        f"{index}\n{_timestamp(start)} --> {_timestamp(end)}\n{text}\n"
        for index, (start, end, text) in enumerate(sorted(cues), start=1)
    ]
    return "\n".join(blocks)


class Recorder:
    """Records the line numbers of each request and fails the chosen ones."""

    def __init__(
        self,
        fail_on: set[int] | None = None,
        swallow_on: set[int] | None = None,
        fail_lines: int | None = None,
    ):
        self.fail_on = fail_on or set()
        self.swallow_on = swallow_on or set()
        self.fail_lines = fail_lines
        self.requests: list[list[int]] = []
        self.prompts: list[str] = []
        self.lines_by_prompt: dict[int, list[int]] = {}
        self.answered = 0
        self.others_done = threading.Event()
        self.lock = threading.Lock()

    def Respond(self, prompt: TranslationPrompt) -> Translation:
        numbers = self.lines_by_prompt[id(prompt)]
        with self.lock:
            self.requests.append(numbers)
            self.prompts.append(_prompt_text(prompt))
            index = len(self.requests)

        if self.fail_lines in numbers:
            # Parallel: fail only once every other batch is running past its request,
            # so cancelling the queued batches cannot drop one of them
            self.others_done.wait(timeout=10)
            raise TranslationImpossibleError("provider down")
        if self.fail_lines is not None:
            with self.lock:
                self.answered += 1
                if self.answered == LINE_COUNT // BATCH_SIZE - 1:
                    self.others_done.set()
        if index in self.fail_on:
            raise TranslationImpossibleError("provider down")
        if index in self.swallow_on:
            raise TranslationError("bad response")
        return Translation({"text": _response(numbers)})


def _prompt_text(prompt: TranslationPrompt) -> str:
    return "\n".join(str(message.get("content")) for message in prompt.messages)


def _response(numbers: list[int]) -> str:
    """Translate every line; merge lines 1 and 2 to prove [MERGED] survives a resume."""
    parts = []
    for number in numbers:
        if number == 1 and 2 in numbers:
            parts.append("#1\nTranslation>\n[MERGE 1+2] Rivit 1 ja 2\n")
        elif number == 2 and 1 in numbers:
            continue
        else:
            parts.append(f"#{number}\nTranslation>\nRivi {number}\n")
    # Summaries reach the prompts of later batches, so a resume must restore them too
    parts.append(f"<summary>Batch from line {numbers[0]}</summary>")
    parts.append(f"<scene>Scene up to line {numbers[-1]}</scene>")
    return "\n".join(parts)


class RecordingClient(DummyTranslationClient):
    def __init__(self, settings: SettingsType, recorder: Recorder):
        super().__init__(settings)
        self.recorder = recorder

    def BuildTranslationPrompt(
        self,
        user_prompt: str,
        instructions: str,
        lines: list[SubtitleLine],
        context: dict,
    ) -> TranslationPrompt:
        prompt = TranslationClient.BuildTranslationPrompt(
            self, user_prompt, instructions, lines, context
        )
        self.recorder.lines_by_prompt[id(prompt)] = [line.number for line in lines]
        return prompt

    def _request_translation(
        self, request: TranslationRequest, temperature: float | None = None
    ) -> Translation | None:
        return self.recorder.Respond(request.prompt)


class RecordingProvider(DummyProvider):
    def __init__(self, recorder: Recorder):
        super().__init__({})
        self.recorder = recorder

    def GetTranslationClient(self, settings: SettingsType) -> TranslationClient:
        client_settings = SettingsType(deepcopy(self.settings))
        client_settings.update(settings)
        return RecordingClient(client_settings, self.recorder)


class ResumeTestBase(LoggedTestCase):
    """Runs the real exsubs/transubs pipeline against a recording provider."""

    def setUp(self) -> None:
        super().setUp()
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.env: dict[str, str] = {
            "HOME": str(self.directory),
            "PATH": os.environ.get("PATH", ""),
            "VERTEX_PROJECT": "test-project",
            "LARGE_CONTEXT_MODE": "false",
            "MAX_BATCH_SIZE": str(BATCH_SIZE),
            "MIN_BATCH_SIZE": "1",
            # transubs runs Gemini on Vertex in parallel by default; keep request order fixed
            "PARALLEL_BATCHES": "0",
        }
        self.written_source: tuple[bool, dict[str, Any]] | None = None

    def WriteSource(self, name: str, realistic: bool = False, **kwargs: Any) -> Path:
        """Write the source once: transubs preps it in place, and a re-run must resume
        from the prepped file. A different fixture rewrites it."""
        source = self.directory / name
        wanted = (realistic, kwargs)
        if not source.exists() or self.written_source != wanted:
            text = _realistic_srt() if realistic else _source_srt(**kwargs)
            source.write_text(text, encoding="utf-8")
            self.written_source = wanted
        return source

    def RunExsubs(
        self,
        recorder: Recorder,
        parallel: bool = False,
        env: dict[str, str] | None = None,
    ) -> Path:
        source = self.WriteSource("Show.S01E01.srt")
        out_file = self.directory / "Show.S01E01.fi.srt"
        with self._patches(exsubs, recorder, env):
            exsubs.translate_subtitles(
                source,
                out_file,
                MKVConfig(),
                TranslationMode.GEMINI,
                show_progress=False,
                show_metrics=False,
                parallel_batches=parallel,
                max_batch_size_arg=BATCH_SIZE,
                min_batch_size_arg=1,
            )
        return out_file

    def RunTransubs(
        self, recorder: Recorder, env: dict[str, str] | None = None, **source: Any
    ) -> Path:
        sub_file = self.WriteSource("Show.S01E01.srt", **source)
        with (
            self._patches(transubs, recorder, env),
            patch.object(
                transubs, "_fix_finnish_subtitles", return_value=True
            ) as fixer,
        ):
            try:
                transubs.translate_srt_file(
                    sub_file, None, "gemini", show_progress=False, show_metrics=False
                )
            finally:
                self.fixer_calls = fixer.call_count
        return self.directory / "Show.S01E01.fi.srt"

    def _patches(self, module: Any, recorder: Recorder, env: dict[str, str] | None):
        provider = RecordingProvider(recorder)

        def fake_init_translator(options: Any):
            return init_translator(options, translation_provider=provider)

        stack = _PatchStack(
            patch.dict(os.environ, {**self.env, **(env or {})}, clear=True),
            patch.object(module, "init_translator", side_effect=fake_init_translator),
        )
        return stack

    def SideFiles(self, out_file: Path) -> tuple[Path, Path]:
        return (
            out_file.with_name(out_file.name + ".partial"),
            out_file.with_name(out_file.name + RESUME_STATE_SUFFIX),
        )


class _PatchStack:
    """Enters several patches as one context manager."""

    def __init__(self, *patches: Any):
        self.patches = patches

    def __enter__(self) -> None:
        for item in self.patches:
            item.__enter__()

    def __exit__(self, *exc: Any) -> None:
        for item in reversed(self.patches):
            item.__exit__(*exc)


BATCHES = [[1, 2, 3], [4, 5, 6], [7, 8, 9], [10, 11, 12]]


class TestResumeGate(ResumeTestBase):
    """A run that failed after 2 of 4 batches resumes and sends only batches 3 and 4."""

    def _check_gate(self, run: Any, fail_on: int = 3, **kwargs: Any) -> list[str]:
        clean = Recorder()
        out_file = run(clean, **kwargs)
        if not kwargs.get("realistic"):
            self.assertLoggedEqual("clean run requests", BATCHES, clean.requests)
        clean_output = out_file.read_text(encoding="utf-8")
        self.assertLoggedIn("merge applied", "Rivit 1 ja 2", clean_output)
        out_file.unlink()

        failed = Recorder(fail_on={fail_on})
        with self.assertRaises(TranslationImpossibleError):
            run(failed, **kwargs)
        partial, state = self.SideFiles(out_file)
        self.assertLoggedFalse("no final file after failure", out_file.exists())
        self.assertLoggedTrue("partial kept", partial.exists())
        self.assertLoggedTrue("resume state kept", state.exists())

        resumed = Recorder()
        with self.assertLogs(level="WARNING") as logs:
            run(resumed, **kwargs)
            exsubs.logging.warning("end of resumed run")
        missing = fail_on - 1
        self.assertLoggedEqual(
            "resumed requests", clean.requests[missing:], resumed.requests
        )
        self.assertLoggedEqual(
            "resumed prompts", clean.prompts[missing:], resumed.prompts
        )
        self.assertLoggedEqual(
            "resumed output", clean_output, out_file.read_text(encoding="utf-8")
        )
        self.assertLoggedFalse("partial removed", partial.exists())
        self.assertLoggedFalse("state removed", state.exists())
        self.assertLoggedFalse(
            "no state warning", any("resume state" in line for line in logs.output)
        )
        return resumed.prompts

    def test_exsubs_resumes_missing_batches(self):
        prompts = self._check_gate(self.RunExsubs)
        self.assertLoggedIn(
            "restored summary in prompt", "batch 2: from line 4", prompts[0]
        )

    def test_exsubs_large_context_resumes_missing_batches(self):
        self._check_gate(self.RunExsubs, env={"LARGE_CONTEXT_MODE": "true"})

    def test_transubs_resumes_from_prepped_source(self):
        """transubs rewrites its source in place; the re-run preps it a second time."""
        self._check_gate(self.RunTransubs, realistic=True)
        source = (self.directory / "Show.S01E01.srt").read_text(encoding="utf-8")
        self.assertLoggedFalse("trailing space removed", "here. \n" in source)

    def test_transubs_resumes_across_scenes(self):
        self.env["SCENE_THRESHOLD"] = "10"
        prompts = self._check_gate(self.RunTransubs, fail_on=4, gap_after=6)
        self.assertLoggedIn(
            "restored scene summary", "scene 1: up to line 6", prompts[0]
        )

    def test_transubs_resumes_missing_batches(self):
        self._check_gate(self.RunTransubs)

    def test_transubs_fixer_runs_once_on_resumed_output(self):
        with self.assertRaises(TranslationImpossibleError):
            self.RunTransubs(Recorder(fail_on={3}))
        self.assertLoggedEqual("fixer calls after failure", 0, self.fixer_calls)
        self.RunTransubs(Recorder())
        self.assertLoggedEqual("fixer calls after resume", 1, self.fixer_calls)


class TestResumeDiscarded(ResumeTestBase):
    """Saved batches are reused only for the same source and settings."""

    def _fail_then_run(self, env: dict[str, str] | None = None, **source: Any):
        with self.assertRaises(TranslationImpossibleError):
            self.RunTransubs(Recorder(fail_on={3}))
        recorder = Recorder()
        with self.assertLogs(level="WARNING") as logs:
            out_file = self.RunTransubs(recorder, env=env, **source)
        return recorder, logs.output, out_file

    def test_changed_model_translates_everything(self):
        recorder, logs, _out = self._fail_then_run(env={"GEMINI_MODEL": "gemini-other"})
        self.assertLoggedEqual("all batches sent", BATCHES, recorder.requests)
        self.assertLoggedTrue(
            "warning names model", any("changed: model" in line for line in logs)
        )

    def test_edited_untranslated_cue_translates_everything(self):
        recorder, logs, _out = self._fail_then_run(edit=11)
        self.assertLoggedEqual("all batches sent", BATCHES, recorder.requests)
        self.assertLoggedTrue(
            "warning names source", any("source subtitles" in line for line in logs)
        )

    def test_changed_line_count_translates_everything(self):
        recorder, _logs, _out = self._fail_then_run(count=LINE_COUNT - 1)
        sent = [number for request in recorder.requests for number in request]
        self.assertLoggedEqual("every line sent", list(range(1, LINE_COUNT)), sent)

    def test_deleted_partial_translates_everything(self):
        with self.assertRaises(TranslationImpossibleError):
            out_file = self.RunTransubs(Recorder(fail_on={3}))
        out_file = self.directory / "Show.S01E01.fi.srt"
        partial, state = self.SideFiles(out_file)
        partial.unlink()
        recorder = Recorder()
        self.RunTransubs(recorder)
        self.assertLoggedEqual("all batches sent", BATCHES, recorder.requests)
        self.assertLoggedFalse("state removed after success", state.exists())

    def test_corrupt_state_translates_everything(self):
        out_file = self.directory / "Show.S01E01.fi.srt"
        partial, state = self.SideFiles(out_file)
        for content in ("{not json", "null", "[]", '{"format": 99}', '{"format": 1}'):
            partial.write_text("x", encoding="utf-8")
            state.write_text(content, encoding="utf-8")
            recorder = Recorder()
            with self.assertLogs(level="WARNING") as logs:
                self.RunTransubs(recorder)
            self.assertLoggedEqual(f"{content}: all sent", BATCHES, recorder.requests)
            self.assertLoggedTrue(
                f"{content}: warned",
                any("cannot be read" in line for line in logs.output),
            )
            out_file.unlink()

    def test_wrong_shape_scenes_translate_everything(self):
        with self.assertRaises(TranslationImpossibleError):
            self.RunTransubs(Recorder(fail_on={3}))
        out_file = self.directory / "Show.S01E01.fi.srt"
        _partial, state = self.SideFiles(out_file)
        data = json.loads(state.read_text(encoding="utf-8"))
        data["scenes"] = [123]
        state.write_text(json.dumps(data), encoding="utf-8")
        recorder = Recorder()
        with self.assertLogs(level="WARNING"):
            self.RunTransubs(recorder)
        self.assertLoggedEqual("all batches sent", BATCHES, recorder.requests)


class TestResumeShapes(ResumeTestBase):
    """Failures in other places still resume only what is missing."""

    def test_second_failure_keeps_restored_batches(self):
        with self.assertRaises(TranslationImpossibleError):
            self.RunTransubs(Recorder(fail_on={3}))
        second = Recorder(fail_on={2})
        with self.assertRaises(TranslationImpossibleError):
            self.RunTransubs(second)
        self.assertLoggedEqual("second run requests", BATCHES[2:], second.requests)
        third = Recorder()
        self.RunTransubs(third)
        self.assertLoggedEqual("third run requests", BATCHES[3:], third.requests)

    def test_failure_with_no_new_batch_keeps_restored_batches(self):
        with self.assertRaises(TranslationImpossibleError):
            self.RunTransubs(Recorder(fail_on={3}))
        second = Recorder(fail_on={1})
        with self.assertRaises(TranslationImpossibleError):
            self.RunTransubs(second)
        third = Recorder()
        self.RunTransubs(third)
        self.assertLoggedEqual("third run requests", BATCHES[2:], third.requests)

    def test_swallowed_error_resends_only_that_batch(self):
        with self.assertRaises(transubs.IncompleteTranslationError):
            self.RunTransubs(Recorder(swallow_on={2}))
        recorder = Recorder()
        self.RunTransubs(recorder)
        self.assertLoggedEqual("only batch 2 sent", [BATCHES[1]], recorder.requests)

    def test_parallel_resends_only_failed_batch(self):
        failed = Recorder(fail_lines=4)
        with self.assertRaises(TranslationImpossibleError):
            self.RunExsubs(failed, parallel=True)
        recorder = Recorder()
        self.RunExsubs(recorder, parallel=True)
        self.assertLoggedEqual("only batch 2 sent", [BATCHES[1]], recorder.requests)


class TestTranslationResumeUnits(LoggedTestCase):
    """The helper on hand-built subtitles."""

    def setUp(self) -> None:
        super().setUp()
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.out_file = self.directory / "Show.fi.srt"
        self.partial = self.out_file.with_name(self.out_file.name + ".partial")
        self.partial.write_text("x", encoding="utf-8")
        self.options = init_options(
            provider="Gemini", model="gemini-test", api_key="secret-key-123"
        )

    def _subtitles(self) -> Subtitles:
        subtitles = Subtitles()
        lines = [
            SubtitleLine.Construct(
                n, f"00:00:{n:02d},000", f"00:00:{n:02d},500", f"Hi {n}"
            )
            for n in range(1, 5)
        ]
        scene = SubtitleScene({"number": 1})
        for number, chunk in ((1, lines[:2]), (2, lines[2:])):
            scene.AddBatch(
                SubtitleBatch({"scene": 1, "number": number, "originals": chunk})
            )
        subtitles.scenes = [scene]
        return subtitles

    def _translate(self, batch: SubtitleBatch) -> None:
        for line in batch.originals:
            line.translation = f"Moi {line.number}"
        batch.translated = [
            line.translated for line in batch.originals if line.translated
        ]

    def test_batch_without_event_is_not_saved(self):
        subtitles = self._subtitles()
        resume = TranslationResume(self.options, self.out_file)
        first, second = subtitles.scenes[0].batches
        self._translate(first)
        self._translate(second)
        resume._on_batch_translated(None, batch=first)
        resume.Save(subtitles)

        state = TranslationResume(self.options, self.out_file)
        fresh = self._subtitles()
        self.assertLoggedEqual("restored batches", 1, state.Restore(fresh))
        restored, missing = fresh.scenes[0].batches
        self.assertLoggedEqual(
            "restored text", "Moi 1", restored.originals[0].translation
        )
        self.assertLoggedFalse("unfinished batch untouched", missing.any_translated)
        self.assertLoggedEqual(
            "unfinished originals",
            [None, None],
            [line.translation for line in missing.originals],
        )

    def test_api_key_never_saved(self):
        subtitles = self._subtitles()
        resume = TranslationResume(self.options, self.out_file)
        first = subtitles.scenes[0].batches[0]
        self._translate(first)
        resume._on_batch_translated(None, batch=first)
        resume.Save(subtitles)
        text = resume.state_path.read_text(encoding="utf-8")
        self.assertLoggedFalse("api key absent", "secret-key-123" in text)

    def test_save_to_unwritable_path_warns(self):
        subtitles = self._subtitles()
        resume = TranslationResume(self.options, self.out_file)
        first = subtitles.scenes[0].batches[0]
        self._translate(first)
        resume._on_batch_translated(None, batch=first)
        resume.state_path.mkdir()
        with self.assertLogs(level="WARNING") as logs:
            resume.Save(subtitles)
        self.assertLoggedTrue(
            "warned", any("Could not save resume state" in line for line in logs.output)
        )
