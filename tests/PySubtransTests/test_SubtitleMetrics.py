"""Tests for tools/subtitle_metrics.py: the report and the --baseline comparison (E3.2).

The fixtures are synthetic cues; the reference episodes are copyrighted and stay outside the
repository."""

import io
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from PySubtrans.Helpers.TestCases import LoggedTestCase
from tools import subtitle_metrics

BASELINE_SRT = """1
00:00:01,000 --> 00:00:03,000
Hello there.

2
00:00:03,050 --> 00:00:04,000
This line is far too fast to read.

3
00:00:05,000 --> 00:00:07,000
- Yes.
- No.

4
00:00:08,000 --> 00:00:10,000
A line that is much longer than forty-two characters
"""

NEW_SRT = """1
00:00:01,000 --> 00:00:03,000
Hello there.

2
00:00:03,200 --> 00:00:05,000
Now it reads fine.

3
00:00:05,000 --> 00:00:07,000
- Yes.
- No.
"""

SOURCE_SRT = """1
00:00:01,000 --> 00:00:03,000
One.

2
00:00:03,050 --> 00:00:04,000
Two.

3
00:00:05,000 --> 00:00:07,000
Three.

4
00:00:08,000 --> 00:00:10,000
Four.
"""


class TestSubtitleMetrics(LoggedTestCase):
    """Metrics of fixture files, and the deltas between a new file and its baseline"""

    def setUp(self) -> None:
        super().setUp()
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        root = Path(self._directory.name)
        self.baseline = root / "baseline.srt"
        self.new = root / "new.srt"
        self.source = root / "source.srt"
        self.baseline.write_text(BASELINE_SRT, encoding="utf-8")
        self.new.write_text(NEW_SRT, encoding="utf-8")
        self.source.write_text(SOURCE_SRT, encoding="utf-8")

    def _run(self, argv: list[str]) -> tuple[int, str]:
        output = io.StringIO()
        with redirect_stdout(output):
            result = subtitle_metrics.main(argv)
        return result, output.getvalue()

    def test_compute_metrics(self):
        metrics = subtitle_metrics.compute_metrics(
            subtitle_metrics.parse_subtitles(self.baseline)
        )
        self.assertLoggedEqual("cues", 4, metrics.cues)
        self.assertLoggedEqual("display lines", 5, metrics.display_lines)
        self.assertLoggedEqual("lines over 42", 1, metrics.lines_over[42])
        self.assertLoggedEqual("cues over 24 cps", 2, metrics.cues_over_cps[24.0])
        self.assertLoggedEqual("gaps", 3, metrics.gaps)
        self.assertLoggedEqual("gaps under 150 ms", 1, metrics.short_gaps)
        self.assertLoggedEqual("two-speaker cues", 1, metrics.two_speaker_cues)

    def test_compare_metrics_uses_rates(self):
        baseline = subtitle_metrics.compute_metrics(
            subtitle_metrics.parse_subtitles(self.baseline)
        )
        new = subtitle_metrics.compute_metrics(
            subtitle_metrics.parse_subtitles(self.new)
        )
        rows = {
            row.label: (row.baseline, row.new)
            for row in subtitle_metrics.compare_metrics(new, baseline, 4)
        }
        self.assertLoggedEqual("cues", (4, 3), rows["cues"])
        self.assertLoggedEqual(
            "lines > 42 chars", (20.0, 0.0), rows["lines > 42 chars (%)"]
        )
        self.assertLoggedEqual("cues > 24 cps", (50.0, 0.0), rows["cues > 24 cps (%)"])
        self.assertLoggedEqual(
            "gaps < 150 ms", (100.0 / 3, 50.0), rows["gaps < 150 ms (%)"]
        )
        self.assertLoggedEqual(
            "cue delta vs source", (0.0, -25.0), rows["cue delta vs source (%)"]
        )

    def test_compare_metrics_covers_every_report_metric(self):
        baseline = subtitle_metrics.compute_metrics(
            subtitle_metrics.parse_subtitles(self.baseline)
        )
        new = subtitle_metrics.compute_metrics(
            subtitle_metrics.parse_subtitles(self.new)
        )
        rows = {
            row.label: (row.baseline, row.new)
            for row in subtitle_metrics.compare_metrics(new, baseline)
        }
        self.assertLoggedEqual("p50 line length", (12, 12), rows["p50 line length"])
        self.assertLoggedEqual("p99 line length", (52, 18), rows["p99 line length"])
        self.assertLoggedEqual(
            "1-line cues", (75.0, 200.0 / 3), rows["cues with 1 line (%)"]
        )
        self.assertLoggedEqual(
            "2-line cues", (25.0, 100.0 / 3), rows["cues with 2 lines (%)"]
        )
        self.assertLoggedEqual("3-line cues", (0.0, 0.0), rows["cues with 3 lines (%)"])
        self.assertLoggedEqual(
            "min display time", (950, 1800), rows["min display time (ms)"]
        )
        self.assertLoggedEqual("min gap", (50, 0), rows["min gap (ms)"])

    def test_compare_metrics_without_source(self):
        metrics = subtitle_metrics.compute_metrics(
            subtitle_metrics.parse_subtitles(self.new)
        )
        labels = [
            row.label for row in subtitle_metrics.compare_metrics(metrics, metrics)
        ]
        self.assertLoggedNotIn("source row", "cue delta vs source (%)", labels)

    def test_main_with_baseline_prints_deltas(self):
        result, output = self._run(
            [
                str(self.new),
                "--baseline",
                str(self.baseline),
                "--source",
                str(self.source),
            ]
        )
        self.assertLoggedEqual("exit code", 0, result)
        self.assertLoggedIn("header", "=== new.srt against baseline.srt ===", output)
        # Each row is a 34-column label after two spaces, then three numbers
        rows = {
            line[2:36].strip(): line[36:].split()
            for line in output.splitlines()
            if line.startswith("  ")
        }
        self.assertLoggedEqual("cues row", ["4", "3", "-1"], rows["cues"])
        self.assertLoggedEqual(
            "cps row", ["50.0", "0.0", "-50.0"], rows["cues > 24 cps (%)"]
        )
        self.assertLoggedEqual(
            "source row", ["0.0", "-25.0", "-25.0"], rows["cue delta vs source (%)"]
        )

    def test_report_without_baseline(self):
        result, output = self._run([str(self.baseline), "--source", str(self.source)])
        self.assertLoggedEqual("exit code", 0, result)
        self.assertLoggedIn("over-length count", "1  ( 20.0%)", output)
        self.assertLoggedIn(
            "source delta", "source cues: 4   delta: +0 (+0.0%)", output
        )
        self.assertLoggedNotIn("no comparison", "metric", output)

    def test_baseline_needs_exactly_one_file(self):
        errors = io.StringIO()
        with redirect_stderr(errors), self.assertRaises(SystemExit) as raised:
            subtitle_metrics.main(
                [str(self.new), str(self.source), "--baseline", str(self.baseline)]
            )
        self.assertLoggedEqual("exit code", 2, raised.exception.code)
        self.assertLoggedIn("message", "exactly one subtitle file", errors.getvalue())

    def test_missing_baseline_fails(self):
        missing = self.baseline.with_name("missing.srt")
        errors = io.StringIO()
        with redirect_stderr(errors):
            result, _ = self._run([str(self.new), "--baseline", str(missing)])
        self.assertLoggedEqual("exit code", 1, result)
        self.assertLoggedIn("message", "missing.srt: not found", errors.getvalue())
