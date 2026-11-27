#!/usr/bin/env python3
"""
Report readability metrics for a subtitle file, to measure translation quality objectively.

Line lengths are counted in raw characters, matching what Subtitle Edit and video players
measure - deliberately not the proportional width units used internally by fix-finnish-subs.

With --baseline, the tool compares one new translation with a baseline translation of the
same source and prints each metric for both and the delta. Counts are shown as percentages,
because the two files can have different numbers of cues and lines.

Usage:
    uv run python tools/subtitle_metrics.py translated.srt
    uv run python tools/subtitle_metrics.py translated.srt --source english.srt
    uv run python tools/subtitle_metrics.py new.fi.srt --baseline baseline.fi.srt \\
        --source english.srt
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import regex

TIMECODE_PATTERN = regex.compile(
    r"(\d+):(\d\d):(\d\d)[,.](\d{1,3})\s*-->\s*(\d+):(\d\d):(\d\d)[,.](\d{1,3})"
)
TAG_PATTERN = regex.compile(r"<[^>]+>|\{\\[^}]*\}")
DASH_PATTERN = regex.compile(r"^\s*[-–—]\s*")

LINE_LENGTH_LIMIT = 42
LINE_LENGTH_LIMITS = (LINE_LENGTH_LIMIT, LINE_LENGTH_LIMIT + 3)
LINES_PER_CUE = (1, 2, 3)
CPS_THRESHOLDS = (15.0, 17.0, 20.0, 24.0)
DURATION_THRESHOLDS_MS = (1000, 1200, 1500)
MIN_GAP_MS = 150


@dataclass
class Cue:
    start_ms: int
    end_ms: int
    lines: list[str]

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms

    @property
    def text(self) -> str:
        return " ".join(self.lines)

    @property
    def characters(self) -> int:
        return len(self.text)

    @property
    def cps(self) -> float:
        if self.duration_ms <= 0:
            return float("inf")
        return self.characters / (self.duration_ms / 1000.0)


@dataclass
class Metrics:
    """The numbers behind one report, so a report and a comparison read the same values"""

    cues: int
    display_lines: int
    max_length: int
    length_p50: float
    length_p90: float
    length_p99: float
    lines_over: dict[int, int]
    lines_per_cue: dict[int, int]
    more_than_two_lines: int
    has_cps: bool
    cps_max: float
    cps_p50: float
    cps_p90: float
    cues_over_cps: dict[float, int]
    min_duration_ms: int
    cues_under_ms: dict[int, int]
    gaps: int
    min_gap_ms: int
    short_gaps: int
    overlaps: int
    dash_cues: int
    two_speaker_cues: int


@dataclass
class DeltaRow:
    """One metric of a comparison: its baseline value, its new value and the delta"""

    label: str
    baseline: float
    new: float
    decimals: int


def _to_ms(hours: str, minutes: str, seconds: str, millis: str) -> int:
    return (
        int(hours) * 3600000
        + int(minutes) * 60000
        + int(seconds) * 1000
        + int(millis.ljust(3, "0"))
    )


def parse_subtitles(path: Path) -> list[Cue]:
    """Parse an SRT/VTT file into cues, stripping markup from the text"""
    content = path.read_text(encoding="utf-8-sig", errors="replace").replace(
        "\r\n", "\n"
    )

    cues: list[Cue] = []
    for block in regex.split(r"\n\s*\n", content.strip()):
        block_lines = block.split("\n")
        timecode_index = next(
            (i for i, line in enumerate(block_lines) if "-->" in line), None
        )
        if timecode_index is None:
            continue

        match = TIMECODE_PATTERN.search(block_lines[timecode_index])
        if not match:
            continue

        text_lines = [
            stripped
            for line in block_lines[timecode_index + 1 :]
            if (stripped := TAG_PATTERN.sub("", line).strip())
        ]
        if not text_lines:
            continue

        cues.append(
            Cue(
                start_ms=_to_ms(*match.groups()[:4]),
                end_ms=_to_ms(*match.groups()[4:]),
                lines=text_lines,
            )
        )

    return cues


def _percentile(values: Sequence[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(int(fraction * len(ordered)), len(ordered) - 1)
    return ordered[index]


def _percent(count: int, total: int) -> float:
    return (100.0 * count / total) if total else 0.0


def _count(label: str, count: int, total: int) -> str:
    return f"  {label:<34}{count:>6}  ({_percent(count, total):5.1f}%)"


def compute_metrics(cues: list[Cue]) -> Metrics:
    """Compute the readability metrics of a non-empty list of cues"""
    display_lines = [line for cue in cues for line in cue.lines]
    lengths = [len(line) for line in display_lines]
    cps_values = [cue.cps for cue in cues if cue.duration_ms > 0]
    durations = [cue.duration_ms for cue in cues]
    gaps = [cues[i].start_ms - cues[i - 1].end_ms for i in range(1, len(cues))]

    return Metrics(
        cues=len(cues),
        display_lines=len(display_lines),
        max_length=max(lengths),
        length_p50=_percentile(lengths, 0.5),
        length_p90=_percentile(lengths, 0.9),
        length_p99=_percentile(lengths, 0.99),
        lines_over={
            limit: sum(1 for length in lengths if length > limit)
            for limit in LINE_LENGTH_LIMITS
        },
        lines_per_cue={
            count: sum(1 for cue in cues if len(cue.lines) == count)
            for count in LINES_PER_CUE
        },
        more_than_two_lines=sum(1 for cue in cues if len(cue.lines) > 2),
        has_cps=bool(cps_values),
        cps_max=max(cps_values, default=0.0),
        cps_p50=_percentile(cps_values, 0.5),
        cps_p90=_percentile(cps_values, 0.9),
        cues_over_cps={
            threshold: sum(1 for value in cps_values if value > threshold)
            for threshold in CPS_THRESHOLDS
        },
        min_duration_ms=min(durations),
        cues_under_ms={
            threshold: sum(1 for duration in durations if duration < threshold)
            for threshold in DURATION_THRESHOLDS_MS
        },
        gaps=len(gaps),
        min_gap_ms=min(gaps, default=0),
        short_gaps=sum(1 for g in gaps if g < MIN_GAP_MS),
        overlaps=sum(1 for g in gaps if g < 0),
        dash_cues=sum(
            1 for cue in cues if any(DASH_PATTERN.match(line) for line in cue.lines)
        ),
        two_speaker_cues=sum(
            1
            for cue in cues
            if len(cue.lines) == 2
            and all(DASH_PATTERN.match(line) for line in cue.lines)
        ),
    )


def _source_delta(cues: int, source: list[Cue]) -> tuple[int, float]:
    delta = cues - len(source)
    return delta, _percent(delta, len(source))


def report(cues: list[Cue], source: list[Cue] | None, path: Path) -> None:
    if not cues:
        print(f"{path.name}: no cues found")
        return

    m = compute_metrics(cues)
    total = m.cues

    print(f"\n=== {path.name} ===")
    print(f"  cues: {total}   display lines: {m.display_lines}")

    print("\nLine length (raw characters)")
    print(
        f"  max {m.max_length}   p50 {m.length_p50:.0f}"
        f"   p90 {m.length_p90:.0f}"
        f"   p99 {m.length_p99:.0f}"
    )
    for limit, over in m.lines_over.items():
        print(_count(f"lines > {limit} chars", over, m.display_lines))

    print("\nLines per cue")
    for count, matching in m.lines_per_cue.items():
        label = f"{count} line" if count == 1 else f"{count} lines"
        print(_count(label, matching, total))
    print(_count("more than 2 lines", m.more_than_two_lines, total))

    print("\nReading speed (characters per second)")
    if m.has_cps:
        print(f"  max {m.cps_max:.1f}   p50 {m.cps_p50:.1f}   p90 {m.cps_p90:.1f}")
    for threshold, over in m.cues_over_cps.items():
        print(_count(f"cues > {threshold:g} cps", over, total))

    print("\nDisplay time")
    print(f"  min {m.min_duration_ms} ms")
    for threshold, under in m.cues_under_ms.items():
        print(_count(f"cues < {threshold} ms", under, total))

    print("\nGaps and overlaps")
    if m.gaps:
        print(f"  min gap {m.min_gap_ms} ms")
        print(_count(f"gaps < {MIN_GAP_MS} ms", m.short_gaps, m.gaps))
        print(_count("overlaps", m.overlaps, m.gaps))

    print("\nDialogue")
    print(_count("cues with any dash line", m.dash_cues, total))
    print(_count("two-speaker dash cues", m.two_speaker_cues, total))

    if source is not None:
        delta, percentage = _source_delta(total, source)
        print("\nAgainst source")
        print(f"  source cues: {len(source)}   delta: {delta:+d} ({percentage:+.1f}%)")
        if abs(percentage) > 3.0:
            print(
                "  note: a large delta means cues are being split or merged downstream"
            )


def compare_metrics(
    new: Metrics, baseline: Metrics, source_cues: int | None = None
) -> list[DeltaRow]:
    """Pair each metric of a new translation with its baseline value"""

    def rate(
        label: str, new_count: int, new_total: int, base_count: int, base_total: int
    ) -> DeltaRow:
        return DeltaRow(
            f"{label} (%)",
            _percent(base_count, base_total),
            _percent(new_count, new_total),
            1,
        )

    rows = [
        DeltaRow("cues", baseline.cues, new.cues, 0),
        DeltaRow("display lines", baseline.display_lines, new.display_lines, 0),
        DeltaRow("max line length", baseline.max_length, new.max_length, 0),
        DeltaRow("p90 line length", baseline.length_p90, new.length_p90, 0),
    ]
    rows += [
        rate(
            f"lines > {limit} chars",
            new.lines_over[limit],
            new.display_lines,
            baseline.lines_over[limit],
            baseline.display_lines,
        )
        for limit in LINE_LENGTH_LIMITS
    ]
    rows.append(
        rate(
            "cues with > 2 lines",
            new.more_than_two_lines,
            new.cues,
            baseline.more_than_two_lines,
            baseline.cues,
        )
    )
    rows += [
        DeltaRow("max cps", baseline.cps_max, new.cps_max, 1),
        DeltaRow("p50 cps", baseline.cps_p50, new.cps_p50, 1),
        DeltaRow("p90 cps", baseline.cps_p90, new.cps_p90, 1),
    ]
    rows += [
        rate(
            f"cues > {threshold:g} cps",
            new.cues_over_cps[threshold],
            new.cues,
            baseline.cues_over_cps[threshold],
            baseline.cues,
        )
        for threshold in CPS_THRESHOLDS
    ]
    rows += [
        rate(
            f"cues < {threshold} ms",
            new.cues_under_ms[threshold],
            new.cues,
            baseline.cues_under_ms[threshold],
            baseline.cues,
        )
        for threshold in DURATION_THRESHOLDS_MS
    ]
    rows += [
        rate(
            f"gaps < {MIN_GAP_MS} ms",
            new.short_gaps,
            new.gaps,
            baseline.short_gaps,
            baseline.gaps,
        ),
        rate("overlaps", new.overlaps, new.gaps, baseline.overlaps, baseline.gaps),
        rate(
            "cues with any dash line",
            new.dash_cues,
            new.cues,
            baseline.dash_cues,
            baseline.cues,
        ),
        rate(
            "two-speaker dash cues",
            new.two_speaker_cues,
            new.cues,
            baseline.two_speaker_cues,
            baseline.cues,
        ),
    ]
    if source_cues:
        rows.append(
            DeltaRow(
                "cue delta vs source (%)",
                _percent(baseline.cues - source_cues, source_cues),
                _percent(new.cues - source_cues, source_cues),
                1,
            )
        )
    return rows


def report_comparison(
    new_path: Path, baseline_path: Path, source: list[Cue] | None
) -> int:
    """Print each metric of a new translation next to its baseline and the delta"""
    new_cues = parse_subtitles(new_path)
    baseline_cues = parse_subtitles(baseline_path)
    for path, cues in ((new_path, new_cues), (baseline_path, baseline_cues)):
        if not cues:
            print(f"{path}: no cues found", file=sys.stderr)
            return 1

    rows = compare_metrics(
        compute_metrics(new_cues),
        compute_metrics(baseline_cues),
        len(source) if source else None,
    )

    print(f"\n=== {new_path.name} against {baseline_path.name} ===")
    print(f"  {'metric':<34}{'baseline':>10}{'new':>10}{'delta':>10}")
    for row in rows:
        delta = row.new - row.baseline
        print(
            f"  {row.label:<34}{row.baseline:>10.{row.decimals}f}"
            f"{row.new:>10.{row.decimals}f}{delta:>+10.{row.decimals}f}"
        )
    print("  rates (%) are shares of each file's own cues, lines or gaps")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "subtitles", type=Path, nargs="+", help="subtitle files to measure"
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=None,
        help="source subtitle file to compare cue counts against",
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        default=None,
        help="baseline translation to compare one new translation with",
    )
    args = parser.parse_args(argv)

    if args.baseline is not None and len(args.subtitles) != 1:
        parser.error("--baseline compares exactly one subtitle file")

    for path in [*args.subtitles, args.source, args.baseline]:
        if path is not None and not path.exists():
            print(f"{path}: not found", file=sys.stderr)
            return 1

    source_cues = parse_subtitles(args.source) if args.source else None

    if args.baseline is not None:
        return report_comparison(args.subtitles[0], args.baseline, source_cues)

    for path in args.subtitles:
        report(parse_subtitles(path), source_cues, path)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
