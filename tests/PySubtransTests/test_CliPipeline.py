"""Tests for the exsubs/transubs CLI wiring: batch-size precedence, the Finnish pipeline
settings, partial translations, exit codes and the fix-finnish-subs invocation."""

import contextlib
import os
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

from PySubtrans.Helpers.TestCases import LoggedTestCase
from PySubtrans.MKV import MKVExtractor
from PySubtrans.MKV.Config import MKVConfig, TranslationMode
from scripts import exsubs, transubs

# Every test runs with exactly this environment plus its own overrides, so a developer's
# shell (or .env, loaded by PySubtrans.Options at import) cannot change the outcome.
BASE_ENV: dict[str, str] = {
    "HOME": os.environ.get("HOME", tempfile.gettempdir()),
    "PATH": os.environ.get("PATH", ""),
    # Stops _detect_gcloud_project from shelling out to gcloud
    "VERTEX_PROJECT": "test-project",
}

NO_VERTEX: dict[str, str] = {"GEMINI_USE_VERTEX": "0"}
SMALL_BATCHES: dict[str, str] = {"LARGE_CONTEXT_MODE": "false"}
# The flash-tuned batch sizes apply only to flash models; the default model is not one
FLASH_MODEL: dict[str, str] = {"GEMINI_MODEL": "gemini-3.8-flash"}


class _SettingsCaptured(Exception):
    """Raised by the init_options stub to stop a run once the settings are known."""


def _capture_init_options(captured: dict[str, Any]):
    def fake_init_options(**settings: Any):
        captured.update(settings)
        raise _SettingsCaptured()

    return fake_init_options


def CaptureExsubsSettings(
    mode: TranslationMode, env: dict[str, str] | None = None, **kwargs: Any
) -> dict[str, Any]:
    """Run exsubs.translate_subtitles up to init_options and return the settings it gets."""
    captured: dict[str, Any] = {}
    with (
        patch.dict(os.environ, {**BASE_ENV, **(env or {})}, clear=True),
        patch.object(
            exsubs, "init_options", side_effect=_capture_init_options(captured)
        ),
    ):
        try:
            exsubs.translate_subtitles(
                Path("episode.srt"),
                Path("episode.fi.srt"),
                MKVConfig(),
                mode,
                show_progress=False,
                show_metrics=False,
                **kwargs,
            )
        except _SettingsCaptured:
            return captured
    raise AssertionError("init_options was not called")


def CaptureTransubsSettings(
    provider_flag: str, env: dict[str, str] | None = None
) -> dict[str, Any]:
    """Run transubs.translate_srt_file up to init_options and return the settings it gets."""
    captured: dict[str, Any] = {}
    with (
        patch.dict(os.environ, {**BASE_ENV, **(env or {})}, clear=True),
        patch.object(
            transubs, "init_options", side_effect=_capture_init_options(captured)
        ),
        patch.object(transubs, "preprocess_timecodes"),
        patch.object(transubs, "filter_subtitles"),
        patch.object(transubs, "postprocess_timecodes"),
    ):
        try:
            transubs.translate_srt_file(
                Path("episode.srt"),
                None,
                provider_flag,
                show_progress=False,
                show_metrics=False,
            )
        except _SettingsCaptured:
            return captured
    raise AssertionError("init_options was not called")


def _chdir(directory: Path) -> contextlib.chdir:
    return contextlib.chdir(directory)


def _batch_sizes(settings: dict[str, Any]) -> tuple[int, int]:
    return settings["max_batch_size"], settings["min_batch_size"]


class TestExsubsBatchSizePrecedence(LoggedTestCase):
    """Model/mode defaults < env vars < parallel default < explicit CLI args."""

    def test_cli_args_win_in_large_context_mode(self):
        settings = CaptureExsubsSettings(
            TranslationMode.GEMINI,
            NO_VERTEX,
            parallel_batches=True,
            max_batch_size_arg=40,
            min_batch_size_arg=5,
        )
        self.assertLoggedEqual("max/min batch size", (40, 5), _batch_sizes(settings))

    def test_cli_args_win_over_model_defaults(self):
        settings = CaptureExsubsSettings(
            TranslationMode.GEMINI,
            {**NO_VERTEX, **SMALL_BATCHES},
            max_batch_size_arg=40,
            min_batch_size_arg=5,
        )
        self.assertLoggedEqual("max/min batch size", (40, 5), _batch_sizes(settings))

    def test_cli_args_win_over_env(self):
        settings = CaptureExsubsSettings(
            TranslationMode.GEMINI,
            {**NO_VERTEX, "MAX_BATCH_SIZE": "300", "MIN_BATCH_SIZE": "20"},
            parallel_batches=True,
            max_batch_size_arg=40,
            min_batch_size_arg=5,
        )
        self.assertLoggedEqual("max/min batch size", (40, 5), _batch_sizes(settings))

    def test_parallel_default_in_large_context_mode(self):
        settings = CaptureExsubsSettings(TranslationMode.GEMINI, parallel_batches=True)
        self.assertLoggedEqual("max/min batch size", (150, 1), _batch_sizes(settings))
        self.assertLoggedTrue("parallel_batches", settings["parallel_batches"])

    def test_sequential_large_context_default(self):
        settings = CaptureExsubsSettings(TranslationMode.GEMINI, NO_VERTEX)
        self.assertLoggedEqual("max/min batch size", (600, 1), _batch_sizes(settings))
        self.assertLoggedEqual("scene_threshold", 300.0, settings["scene_threshold"])

    def test_parallel_default_beats_model_default(self):
        settings = CaptureExsubsSettings(
            TranslationMode.GEMINI,
            {**NO_VERTEX, **SMALL_BATCHES, **FLASH_MODEL},
            parallel_batches=True,
        )
        # flash model: min 100 from the tuned defaults, max from the parallel default
        self.assertLoggedEqual("max/min batch size", (150, 100), _batch_sizes(settings))

    def test_sequential_model_default(self):
        settings = CaptureExsubsSettings(
            TranslationMode.GEMINI, {**NO_VERTEX, **SMALL_BATCHES, **FLASH_MODEL}
        )
        self.assertLoggedEqual("max/min batch size", (220, 100), _batch_sizes(settings))

    def test_env_beats_parallel_default(self):
        settings = CaptureExsubsSettings(
            TranslationMode.GEMINI, {"MAX_BATCH_SIZE": "300"}, parallel_batches=True
        )
        self.assertLoggedEqual("max batch size", 300, settings["max_batch_size"])

    def test_env_min_batch_size_beats_mode_default(self):
        settings = CaptureExsubsSettings(
            TranslationMode.GEMINI, {"MIN_BATCH_SIZE": "20"}
        )
        self.assertLoggedEqual("min batch size", 20, settings["min_batch_size"])

    def test_explicit_max_below_default_min_clamps_min(self):
        # SubtitleBatcher rejects min > max, so a small --max-batch-size lowers the default min
        settings = CaptureExsubsSettings(
            TranslationMode.GEMINI,
            {**NO_VERTEX, **SMALL_BATCHES},
            max_batch_size_arg=40,
        )
        self.assertLoggedEqual("max/min batch size", (40, 40), _batch_sizes(settings))

    def test_openai_parallel_default(self):
        parallel = CaptureExsubsSettings(TranslationMode.CHATGPT, parallel_batches=True)
        sequential = CaptureExsubsSettings(TranslationMode.CHATGPT)
        explicit = CaptureExsubsSettings(TranslationMode.CHATGPT, max_batch_size_arg=70)
        self.assertLoggedEqual("parallel max", 150, parallel["max_batch_size"])
        self.assertLoggedEqual("sequential max", 100, sequential["max_batch_size"])
        self.assertLoggedEqual("explicit max", 70, explicit["max_batch_size"])


class TestFinnishPipelineSettings(LoggedTestCase):
    """The settings contract from AGENTS.md, as passed to init_options by both scripts."""

    def _assert_contract(self, settings: dict[str, Any]) -> None:
        self.assertLoggedTrue(
            "postprocess_translation", settings["postprocess_translation"]
        )
        self.assertLoggedEqual(
            "merge_continuation_duration", 1.5, settings["merge_continuation_duration"]
        )
        self.assertLoggedEqual(
            "merge_continuation_gap", 0.3, settings["merge_continuation_gap"]
        )
        self.assertLoggedEqual(
            "max_translation_cps", 25.0, settings["max_translation_cps"]
        )
        self.assertLoggedTrue("include_line_timings", settings["include_line_timings"])
        self.assertLoggedEqual("target_cps", 15.0, settings["target_cps"])
        self.assertLoggedEqual("max_line_duration", 0.0, settings["max_line_duration"])
        self.assertLoggedIn("instruction_file", "instruction_file", settings)

    def test_exsubs_contract(self):
        self._assert_contract(CaptureExsubsSettings(TranslationMode.GEMINI))

    def test_transubs_contract(self):
        self._assert_contract(CaptureTransubsSettings("gemini"))

    def test_exsubs_temperature_policy(self):
        gemini = CaptureExsubsSettings(TranslationMode.GEMINI)
        openai = CaptureExsubsSettings(TranslationMode.CHATGPT)
        self.assertLoggedEqual("gemini temperature", 1.0, gemini["temperature"])
        self.assertLoggedEqual("openai temperature", 0.3, openai["temperature"])

    def test_transubs_temperature_policy(self):
        gemini = CaptureTransubsSettings("gemini")
        openai = CaptureTransubsSettings("gpt")
        self.assertLoggedEqual("gemini temperature", 1.0, gemini["temperature"])
        self.assertLoggedEqual("openai temperature", 0.3, openai["temperature"])

    def test_transubs_batch_defaults(self):
        vertex = CaptureTransubsSettings("gemini")
        api_key = CaptureTransubsSettings("gemini", NO_VERTEX)
        self.assertLoggedTrue("vertex runs parallel", vertex["parallel_batches"])
        self.assertLoggedEqual("vertex max batch", 150, vertex["max_batch_size"])
        self.assertLoggedFalse("api key runs sequential", api_key["parallel_batches"])
        self.assertLoggedEqual("api key max batch", 600, api_key["max_batch_size"])


class TestOutputPaths(LoggedTestCase):
    def test_build_translated_output_path_replaces_language_suffix(self):
        for module in (exsubs, transubs):
            result = module._build_translated_output_path(
                Path("/m/Show.S01E02.en.srt"), "FI"
            )
            self.assertLoggedEqual(
                f"{module.__name__} output path", Path("/m/Show.S01E02.fi.srt"), result
            )

    def test_normalise_renames_translated_suffix(self):
        for module in (exsubs, transubs):
            with tempfile.TemporaryDirectory() as directory:
                source = Path(directory) / "Show.srt"
                desired = Path(directory) / "Show.fi.srt"
                (Path(directory) / "Show.translated.srt").write_text(
                    "x", encoding="utf-8"
                )
                result = module._normalise_translated_output(
                    source, desired, "Finnish", "fi"
                )
                self.assertLoggedEqual(f"{module.__name__} result", desired, result)
                self.assertLoggedTrue("desired exists", desired.exists())


class FakeProject:
    """Stands in for SubtitleProject: TranslateSubtitles writes the output file like a save."""

    def __init__(
        self,
        all_translated: bool = True,
        untranslated: int = 0,
        error: Exception | None = None,
        writes_output: bool = True,
    ):
        self.error = error
        self.writes_output = writes_output
        batch = SimpleNamespace(untranslated=[None] * untranslated)
        self.subtitles = SimpleNamespace(
            originals=["line"],
            scenes=[SimpleNamespace(batches=[batch])],
            linecount=10,
            all_translated=all_translated,
            outputpath=None,
        )

    def InitialiseProject(self, _source: str, output: str) -> None:
        self.subtitles.outputpath = output

    def UpdateProjectSettings(self, _options: Any) -> None:
        pass

    def TranslateSubtitles(self, _translator: Any) -> None:
        if self.writes_output:
            Path(self.subtitles.outputpath).write_text(
                "1\n00:00:01,000 --> 00:00:02,000\nHei\n"
            )
        if self.error:
            raise self.error


def _fake_translator(aborted: bool = False) -> SimpleNamespace:
    return SimpleNamespace(aborted=aborted, errors=[])


def _pipeline_patches(module: Any, project: FakeProject, translator: SimpleNamespace):
    options = MagicMock()
    options.get_str.return_value = "Translate to Finnish"
    options.get.return_value = None
    return (
        patch.dict(os.environ, BASE_ENV, clear=True),
        patch.object(module, "init_options", return_value=options),
        patch.object(module, "SubtitleProject", return_value=project),
        patch.object(module, "preprocess_subtitles"),
        patch.object(module, "batch_subtitles"),
        patch.object(module, "init_translator", return_value=translator),
    )


class TestPartialTranslations(LoggedTestCase):
    """A partial result must never stay at the final path, where later runs skip it."""

    def _run_exsubs(self, project: FakeProject, translator: SimpleNamespace) -> Path:
        directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        out_file = directory / "Show.S01E01.fi.srt"
        p1, p2, p3, p4, p5, p6 = _pipeline_patches(exsubs, project, translator)
        with p1, p2, p3, p4, p5, p6:
            exsubs.translate_subtitles(
                directory / "Show.S01E01.srt",
                out_file,
                MKVConfig(),
                TranslationMode.GEMINI,
                show_progress=False,
                show_metrics=False,
            )
        return out_file

    def _run_transubs(self, project: FakeProject, translator: SimpleNamespace) -> bool:
        directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.transubs_out = directory / "Show.S01E01.fi.srt"
        p1, p2, p3, p4, p5, p6 = _pipeline_patches(transubs, project, translator)
        with (
            p1,
            p2,
            p3,
            p4,
            p5,
            p6,
            patch.object(transubs, "preprocess_timecodes"),
            patch.object(transubs, "filter_subtitles"),
            patch.object(transubs, "postprocess_timecodes"),
        ):
            return transubs.translate_srt_file(
                directory / "Show.S01E01.srt",
                None,
                "gemini",
                show_progress=False,
                show_metrics=False,
            )

    def _assert_set_aside(self, out_file: Path) -> None:
        partial = out_file.with_name(out_file.name + ".partial")
        self.assertLoggedFalse("final path is free", out_file.exists())
        self.assertLoggedTrue("partial result kept aside", partial.exists())

    def test_exsubs_untranslated_lines_raise_and_set_output_aside(self):
        directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        out_file = directory / "Show.S01E01.fi.srt"
        project = FakeProject(all_translated=False, untranslated=3)
        p1, p2, p3, p4, p5, p6 = _pipeline_patches(exsubs, project, _fake_translator())
        with (
            p1,
            p2,
            p3,
            p4,
            p5,
            p6,
            self.assertRaises(exsubs.IncompleteTranslationError) as ctx,
        ):
            exsubs.translate_subtitles(
                directory / "Show.S01E01.srt",
                out_file,
                MKVConfig(),
                TranslationMode.GEMINI,
                show_progress=False,
                show_metrics=False,
            )
        self.assertLoggedIn("message counts lines", "3 of 10", str(ctx.exception))
        self._assert_set_aside(out_file)

    def test_exsubs_failed_translation_sets_saved_partial_aside(self):
        project = FakeProject(error=RuntimeError("provider down"))
        with self.assertRaises(RuntimeError):
            self._run_exsubs(project, _fake_translator())
        self._assert_set_aside(Path(project.subtitles.outputpath))

    def test_exsubs_aborted_translation_is_incomplete(self):
        project = FakeProject()
        with self.assertRaises(exsubs.IncompleteTranslationError):
            self._run_exsubs(project, _fake_translator(aborted=True))
        self._assert_set_aside(Path(project.subtitles.outputpath))

    def test_exsubs_complete_translation_stays(self):
        out_file = self._run_exsubs(FakeProject(), _fake_translator())
        self.assertLoggedTrue("output at final path", out_file.exists())

    def test_transubs_partial_skips_fixer(self):
        project = FakeProject(all_translated=False, untranslated=2)
        with (
            patch.object(transubs, "_fix_finnish_subtitles") as fixer,
            self.assertRaises(transubs.IncompleteTranslationError),
        ):
            self._run_transubs(project, _fake_translator())
        self.assertLoggedEqual("fixer calls", 0, fixer.call_count)
        self._assert_set_aside(self.transubs_out)

    def test_transubs_failure_keeps_earlier_finished_output(self):
        """A re-run that fails before saving must not move an earlier, finished translation."""
        directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        out_file = directory / "Show.S01E01.fi.srt"
        out_file.write_text("1\n00:00:01,000 --> 00:00:02,000\nValmis\n")
        project = FakeProject(
            error=RuntimeError("401 unauthorized"), writes_output=False
        )
        p1, p2, p3, p4, p5, p6 = _pipeline_patches(
            transubs, project, _fake_translator()
        )
        with (
            p1,
            p2,
            p3,
            p4,
            p5,
            p6,
            patch.object(transubs, "preprocess_timecodes"),
            patch.object(transubs, "filter_subtitles"),
            patch.object(transubs, "postprocess_timecodes"),
            self.assertRaises(RuntimeError),
        ):
            transubs.translate_srt_file(
                directory / "Show.S01E01.srt",
                None,
                "gemini",
                show_progress=False,
                show_metrics=False,
            )
        self.assertLoggedTrue("finished output stays", out_file.exists())
        self.assertLoggedFalse(
            "nothing set aside", out_file.with_name(out_file.name + ".partial").exists()
        )

    def test_transubs_complete_runs_fixer_once_and_reports_it(self):
        with patch.object(
            transubs, "_fix_finnish_subtitles", return_value=False
        ) as fixer:
            result = self._run_transubs(FakeProject(), _fake_translator())
        self.assertLoggedEqual("fixer calls", 1, fixer.call_count)
        self.assertLoggedFalse("fixer failure is reported", result)
        self.assertLoggedTrue("output at final path", self.transubs_out.exists())

    def test_exsubs_process_video_file_skips_fixer_on_partial(self):
        directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        video = directory / "Show.S01E01.mkv"
        video.write_bytes(b"")
        video.with_suffix(".srt").write_text("1\n00:00:01,000 --> 00:00:02,000\nHi\n")
        with (
            patch.dict(os.environ, BASE_ENV, clear=True),
            patch.object(exsubs, "preprocess_timecodes"),
            patch.object(exsubs, "filter_subtitles"),
            patch.object(exsubs, "postprocess_timecodes"),
            patch.object(
                exsubs,
                "translate_subtitles",
                side_effect=exsubs.IncompleteTranslationError("incomplete"),
            ),
            patch.object(exsubs, "_fix_finnish_subtitles") as fixer,
            self.assertRaises(exsubs.IncompleteTranslationError),
        ):
            exsubs.process_video_file(
                video, MKVConfig(), TranslationMode.GEMINI, False, False
            )
        self.assertLoggedEqual("fixer calls", 0, fixer.call_count)
        self.assertLoggedTrue(
            "extracted source kept", video.with_suffix(".srt").exists()
        )


class TestExsubsExitCodes(LoggedTestCase):
    def _make_videos(self, *names: str) -> Path:
        directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        for name in names:
            (directory / name).write_bytes(b"")
        self.enterContext(_chdir(directory))
        return directory

    def _process_directory(self, results: list[Any]) -> tuple[int, MagicMock]:
        with (
            patch.dict(os.environ, BASE_ENV, clear=True),
            patch.object(exsubs, "process_video_file", side_effect=results) as process,
        ):
            code = exsubs.process_directory(
                MKVConfig(), TranslationMode.GEMINI, False, False, show_metrics=False
            )
        return code, process

    def test_directory_failure_returns_non_zero(self):
        directory = self._make_videos("Show.S01E01.mkv", "Show.S01E02.mkv")
        # An empty translation is a failed earlier run, so it is retried, not skipped
        (directory / "Show.S01E01.fi.srt").write_text("")
        code, process = self._process_directory([True, False])
        self.assertLoggedEqual("exit code", 1, code)
        self.assertLoggedEqual("files processed", 2, process.call_count)
        self.assertLoggedFalse(
            "show_metrics passed by keyword", process.call_args.kwargs["show_metrics"]
        )

    def test_directory_exception_returns_non_zero(self):
        self._make_videos("Show.S01E01.mkv")
        code, _process = self._process_directory(
            [exsubs.IncompleteTranslationError("x")]
        )
        self.assertLoggedEqual("exit code", 1, code)

    def test_directory_success_returns_zero_and_skips_done(self):
        directory = self._make_videos("Show.S01E01.mkv", "Show.S01E02.mkv")
        (directory / "Show.S01E01.fi.srt").write_text(
            "1\n00:00:01,000 --> 00:00:02,000\nHei\n"
        )
        code, process = self._process_directory([True])
        self.assertLoggedEqual("exit code", 0, code)
        self.assertLoggedEqual("files processed", 1, process.call_count)

    def _run_main(self, argv: list[str], **patches: Any) -> int:
        with (
            patch.dict(os.environ, BASE_ENV, clear=True),
            patch.object(exsubs.sys, "argv", ["exsubs", *argv]),
            patch.object(exsubs, "load_dotenv"),
            patch.object(exsubs, "verify_dependencies"),
            patch.object(exsubs, "verify_api_key"),
            patch.object(exsubs, "FileLock"),
            patch.multiple(exsubs, **patches),
        ):
            return exsubs.main()

    def test_main_single_file_failure_returns_non_zero(self):
        self.assertLoggedEqual(
            "failed file",
            1,
            self._run_main(["x.mkv"], process_video_file=MagicMock(return_value=False)),
        )
        self.assertLoggedEqual(
            "done file",
            0,
            self._run_main(["x.mkv"], process_video_file=MagicMock(return_value=True)),
        )
        incomplete = MagicMock(side_effect=exsubs.IncompleteTranslationError("x"))
        self.assertLoggedEqual(
            "incomplete file",
            1,
            self._run_main(["x.mkv"], process_video_file=incomplete),
        )

    def test_main_directory_failure_returns_non_zero(self):
        process_directory = MagicMock(return_value=1)
        code = self._run_main(["--no-metrics"], process_directory=process_directory)
        self.assertLoggedEqual("exit code", 1, code)
        self.assertLoggedFalse(
            "--no-metrics reaches directory mode",
            process_directory.call_args.kwargs["show_metrics"],
        )

    def test_main_leaves_parallel_batch_size_to_translate_subtitles(self):
        process = MagicMock(return_value=True)
        self._run_main(["x.mkv"], process_video_file=process)
        kwargs = process.call_args.kwargs
        self.assertLoggedTrue("vertex default is parallel", kwargs["parallel_batches"])
        self.assertLoggedIsNone("no implicit max batch size", kwargs["max_batch_size"])

    def test_verify_dependencies_requires_fixer_for_finnish(self):
        def which(command: str) -> str | None:
            return None if command == "fix-finnish-subs" else f"/usr/bin/{command}"

        with patch.object(exsubs.shutil, "which", side_effect=which):
            with self.assertRaises(SystemExit):
                exsubs.verify_dependencies("fi")
            exsubs.verify_dependencies("de")


class TestTransubsExitCodes(LoggedTestCase):
    def _run_main(
        self,
        argv: list[str],
        which: str | None = "/usr/bin/fix-finnish-subs",
        **patches: Any,
    ) -> int:
        with (
            patch.dict(os.environ, BASE_ENV, clear=True),
            patch.object(transubs.sys, "argv", ["transubs", *argv]),
            patch.object(transubs, "load_dotenv"),
            patch.object(transubs.shutil, "which", return_value=which),
            patch.multiple(transubs, **patches),
        ):
            return transubs.main()

    def test_missing_fixer_fails_before_translation(self):
        translate = MagicMock(return_value=True)
        code = self._run_main(["episode.srt"], which=None, translate_srt_file=translate)
        self.assertLoggedEqual("exit code", 1, code)
        self.assertLoggedEqual("translations started", 0, translate.call_count)

    def test_directory_mode_records_usage_and_progress_flag(self):
        process_directory = MagicMock(return_value=0)
        persist = MagicMock()
        self._run_main(
            ["--usage-json", "usage.json", "--no-progress"],
            process_directory=process_directory,
            _persist_usage=persist,
        )
        self.assertLoggedEqual("usage recorded", 1, persist.call_count)
        self.assertLoggedFalse(
            "--no-progress reaches directory mode",
            process_directory.call_args.kwargs["show_progress"],
        )

    def test_single_file_fixer_failure_returns_non_zero(self):
        with tempfile.NamedTemporaryFile(suffix=".srt") as source:
            translate = MagicMock(return_value=False)
            code = self._run_main(
                [source.name, "--no-progress"], translate_srt_file=translate
            )
        self.assertLoggedEqual("exit code", 1, code)
        self.assertLoggedFalse(
            "--no-progress reaches the file",
            translate.call_args.kwargs["show_progress"],
        )

    def test_directory_skips_done_and_partial_files(self):
        directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        for name in ("Show.S01E01.srt", "Show.S01E02.srt", "Show.S01E03.srt"):
            (directory / name).write_text("x")
        (directory / "Show.S01E01.fi.srt").write_text(
            "1\n00:00:01,000 --> 00:00:02,000\nHei\n"
        )
        (directory / "Show.S01E02.fi.srt").write_text("")
        (directory / "Show.S01E03.fi.srt.partial").write_text("x")
        self.enterContext(_chdir(directory))
        with (
            patch.dict(os.environ, BASE_ENV, clear=True),
            patch.object(
                transubs, "translate_srt_file", side_effect=[True, False]
            ) as translate,
        ):
            code = transubs.process_directory(None, "gemini", show_progress=False)
        translated = [call.args[0].name for call in translate.call_args_list]
        self.assertLoggedEqual("exit code", 1, code)
        self.assertLoggedSequenceEqual(
            "files translated", ["Show.S01E02.srt", "Show.S01E03.srt"], translated
        )


class TestFinnishFixerInvocation(LoggedTestCase):
    """fix-finnish-subs runs exactly once, and its failure is reported, never retried."""

    def _run(self, module: Any, result: Any) -> tuple[bool, MagicMock]:
        directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        translated = directory / "Show.fi.srt"
        translated.write_text("x")
        with (
            patch.dict(os.environ, BASE_ENV, clear=True),
            patch.object(module.subprocess, "run", side_effect=[result]) as run,
        ):
            ok = module._fix_finnish_subtitles(translated)
        self.translated = translated
        return ok, run

    def test_argv_and_single_run(self):
        for module in (exsubs, transubs):
            ok, run = self._run(module, subprocess.CompletedProcess([], 0))
            report = self.translated.with_name("Show.fi_unfixed.txt")
            self.assertLoggedTrue(f"{module.__name__} success", ok)
            self.assertLoggedEqual("runs", 1, run.call_count)
            self.assertLoggedSequenceEqual(
                "argv",
                [
                    "fix-finnish-subs",
                    str(self.translated),
                    "--report",
                    str(report),
                    "--width-limit",
                    "42",
                    "--max-cps",
                    "17.0",
                    "--cps-target",
                    "15.0",
                    "--no-ai-review",
                ],
                run.call_args.args[0],
            )
            self.assertLoggedNotIn("no timeout", "timeout", run.call_args.kwargs)

    def test_non_zero_exit_is_reported_without_retry(self):
        for module in (exsubs, transubs):
            ok, run = self._run(module, subprocess.CompletedProcess([], 3))
            self.assertLoggedFalse(f"{module.__name__} failure reported", ok)
            self.assertLoggedEqual("runs", 1, run.call_count)

    def test_missing_binary_is_reported(self):
        for module in (exsubs, transubs):
            ok, run = self._run(module, FileNotFoundError("fix-finnish-subs"))
            self.assertLoggedFalse(f"{module.__name__} failure reported", ok)
            self.assertLoggedEqual("runs", 1, run.call_count)


class TestFfprobeTimeout(LoggedTestCase):
    def test_timeout_returns_no_tracks(self):
        with tempfile.NamedTemporaryFile(suffix=".mkv") as video:
            with patch.object(
                MKVExtractor.subprocess,
                "run",
                side_effect=subprocess.TimeoutExpired(
                    "ffprobe", MKVExtractor.FFPROBE_TIMEOUT_SECONDS
                ),
            ) as run:
                tracks = MKVExtractor.get_mkv_subtitle_tracks(Path(video.name))
        self.assertLoggedEqual("tracks", [], tracks)
        self.assertLoggedEqual(
            "timeout passed",
            MKVExtractor.FFPROBE_TIMEOUT_SECONDS,
            run.call_args.kwargs["timeout"],
        )
