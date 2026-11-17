"""Characterization tests for BatchProcessor.run in scripts/batch_translate.py: file
discovery, per-file translation, failures, skips, preview mode and the statistics."""

import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from PySubtrans import Options, SubtitleError
from PySubtrans.Helpers.TestCases import LoggedTestCase
from PySubtrans.SubtitleLine import SubtitleLine
from PySubtrans.Subtitles import Subtitles
from PySubtrans.TranslationEvents import TranslationEvents
from scripts import batch_translate

BASE_ENV: dict[str, str] = {
    "HOME": os.environ.get("HOME", tempfile.gettempdir()),
    "PATH": os.environ.get("PATH", ""),
}

SRT_CONTENT = """1
00:00:01,000 --> 00:00:03,000
Hello there.

2
00:00:04,000 --> 00:00:06,000
How are you?
"""


class FakeTranslator:
    """Stands in for SubtitleTranslator: prefixes every line, or fails for one file."""

    def __init__(self, fail_marker: str, preview: bool):
        self.events = TranslationEvents()
        self.preview = preview
        self.errors: list[str] = []
        self.fail_marker = fail_marker

    def TranslateSubtitles(self, subtitles: Subtitles) -> None:
        if self.fail_marker in (subtitles.sourcepath or ""):
            raise SubtitleError("simulated provider failure")
        for scene in subtitles.scenes:
            for batch in scene.batches:
                translated: list[SubtitleLine] = []
                for line in batch.originals:
                    copy = SubtitleLine(line)
                    copy.text = f"TR {line.text}"
                    translated.append(copy)
                batch.translated = translated


class BatchProcessorRunTests(LoggedTestCase):
    def setUp(self) -> None:
        super().setUp()
        self._tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tempdir.name)
        self.source = self.root / "source"
        self.destination = self.root / "translated"
        (self.source / "season").mkdir(parents=True)
        for name in ("alpha.srt", "broken.srt", "season/gamma.srt"):
            (self.source / name).write_text(SRT_CONTENT, encoding="utf-8")
        (self.source / "notes.txt").write_text("not a subtitle", encoding="utf-8")

    def tearDown(self) -> None:
        self._tempdir.cleanup()
        super().tearDown()

    def _run(self, preview: bool = False) -> batch_translate.BatchStatistics:
        options = Options(
            {
                "source_path": str(self.source),
                "destination_path": str(self.destination),
                "provider": "Dummy",
                "target_language": "Finnish",
                "prompt": "Translate these subtitles into Finnish",
                "instruction_file": None,
                "preview": preview,
            }
        )
        config = batch_translate.BatchJobConfig(options)
        provider = MagicMock()
        provider.name = "Dummy"
        provider.selected_model = "dummy-model"

        def fake_init_translator(_options, translation_provider=None):
            return FakeTranslator("broken", preview)

        with (
            patch.dict(os.environ, BASE_ENV, clear=True),
            patch.object(
                batch_translate, "init_translation_provider", return_value=provider
            ),
            patch.object(
                batch_translate, "init_translator", side_effect=fake_init_translator
            ),
        ):
            processor = batch_translate.BatchProcessor(config)
            processor.progress_display = batch_translate.ProgressDisplay(
                stream=MagicMock()
            )
            return processor.run()

    def test_translates_skips_and_counts_failures(self):
        existing = self.destination / "season" / "gamma.finnish.srt"
        existing.parent.mkdir(parents=True)
        existing.write_text("already translated", encoding="utf-8")

        with self.assertLogs(batch_translate.__name__, level="INFO") as logs:
            stats = self._run()

        self.assertLoggedEqual("discovered", 3, stats.discovered_files)
        self.assertLoggedEqual("translated", 1, stats.translated_files)
        self.assertLoggedEqual("failed", 1, stats.failed_files)
        self.assertLoggedEqual("skipped", 1, stats.skipped_files)
        self.assertLoggedEqual("previewed", 0, stats.previewed_files)
        self.assertLoggedEqual(
            "summary",
            "Processed 3 file(s): 1 translated, 0 previewed, 1 skipped, 1 failed",
            stats.as_message(),
        )

        output = self.destination / "alpha.finnish.srt"
        self.assertLoggedTrue("alpha output written", output.exists())
        self.assertLoggedIn("translated text", "TR Hello there.", output.read_text())
        self.assertLoggedFalse(
            "no output for the failed file",
            (self.destination / "broken.finnish.srt").exists(),
        )
        self.assertLoggedEqual(
            "existing output untouched", "already translated", existing.read_text()
        )

        messages = "\n".join(logs.output)
        self.assertLoggedIn("failure logged", "Translation failed for", messages)
        self.assertLoggedIn("skip logged", "already exists", messages)
        self.assertLoggedIn("save logged", "Saved translation to", messages)

    def test_preview_saves_nothing(self):
        stats = self._run(preview=True)

        self.assertLoggedEqual("discovered", 3, stats.discovered_files)
        self.assertLoggedEqual("previewed", 2, stats.previewed_files)
        self.assertLoggedEqual("failed", 1, stats.failed_files)
        self.assertLoggedEqual("translated", 0, stats.translated_files)
        self.assertLoggedEqual(
            "no files written",
            [],
            [path for path in self.destination.rglob("*") if path.is_file()],
        )

    def test_no_subtitle_files_returns_empty_statistics(self):
        for path in self.source.rglob("*.srt"):
            path.unlink()

        stats = self._run()

        self.assertLoggedEqual("discovered", 0, stats.discovered_files)
        self.assertLoggedEqual("translated", 0, stats.translated_files)

    def test_missing_source_directory_raises(self):
        self.source = self.root / "missing"

        with self.assertRaises(SubtitleError):
            self._run()

    def test_save_failure_aborts_the_batch(self):
        with (
            patch.object(
                Subtitles, "SaveTranslation", side_effect=OSError("disk full")
            ),
            self.assertRaisesRegex(SubtitleError, "Failed to save translation"),
        ):
            self._run()
