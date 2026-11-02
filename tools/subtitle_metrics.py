#!/usr/bin/env python3
"""
Report readability metrics for a subtitle file, to measure translation quality objectively.

Line lengths are counted in raw characters, matching what Subtitle Edit and video players
measure - deliberately not the proportional width units used internally by fix-finnish-subs.

Usage:
    uv run python tools/subtitle_metrics.py translated.srt
    uv run python tools/subtitle_metrics.py translated.srt --source english.srt
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

import regex

TIMECODE_PATTERN = regex.compile(
    r"(\d+):(\d\d):(\d\d)[,.](\d{1,3})\s*-->\s*(\d+):(\d\d):(\d\d)[,.](\d{1,3})"
)
TAG_PATTERN = regex.compile(r"<[^>]+>|\{\\[^}]*\}")
DASH_PATTERN = regex.compile(r"^\s*[-–—]\s*")

LINE_LENGTH_LIMIT = 42
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


def _to_ms(hours: str, minutes: str, seconds: str, millis: str) -> int:
    return (
        int(hours) * 3600000
        + int(minutes) * 60000
        + int(seconds) * 1000
        + int(millis.ljust(3, "0"))
    )


def parse_subtitles(path: Path) -> list[Cue]:
    """Parse an SRT/VTT file into cues, stripping markup from the text"""
    content = path.read_text(encoding="utf-8-sig", errors="replace").replace("\r\n", "\n")

    cues: list[Cue] = []
    for block in regex.split(r"\n\s*\n", content.strip()):
        block_lines = block.split("\n")
        timecode_index = next((i for i, line in enumerate(block_lines) if "-->" in line), None)
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


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(int(fraction * len(ordered)), len(ordered) - 1)
    return ordered[index]


def _count(label: str, count: int, total: int) -> str:
    percentage = (100.0 * count / total) if total else 0.0
    return f"  {label:<34}{count:>6}  ({percentage:5.1f}%)"


def report(cues: list[Cue], source: list[Cue] | None, path: Path) -> None:
    total = len(cues)
    if not total:
        print(f"{path.name}: no cues found")
        return

    display_lines = [line for cue in cues for line in cue.lines]
    lengths = [len(line) for line in display_lines]
    cps_values = [cue.cps for cue in cues if cue.duration_ms > 0]
    durations = [cue.duration_ms for cue in cues]
    gaps = [cues[i].start_ms - cues[i - 1].end_ms for i in range(1, total)]

    print(f"\n=== {path.name} ===")
    print(f"  cues: {total}   display lines: {len(display_lines)}")

    print("\nLine length (raw characters)")
    print(
        f"  max {max(lengths)}   p50 {_percentile(lengths, 0.5):.0f}"
        f"   p90 {_percentile(lengths, 0.9):.0f}"
        f"   p99 {_percentile(lengths, 0.99):.0f}"
    )
    for limit in (LINE_LENGTH_LIMIT, LINE_LENGTH_LIMIT + 3):
        over = sum(1 for length in lengths if length > limit)
        print(_count(f"lines > {limit} chars", over, len(display_lines)))

    print("\nLines per cue")
    for count in (1, 2, 3):
        matching = sum(1 for cue in cues if len(cue.lines) == count)
        label = f"{count} line" if count == 1 else f"{count} lines"
        print(_count(label, matching, total))
    over_two = sum(1 for cue in cues if len(cue.lines) > 2)
    print(_count("more than 2 lines", over_two, total))

    print("\nReading speed (characters per second)")
    if cps_values:
        print(
            f"  max {max(cps_values):.1f}   p50 {_percentile(cps_values, 0.5):.1f}"
            f"   p90 {_percentile(cps_values, 0.9):.1f}"
        )
    for threshold in CPS_THRESHOLDS:
        over = sum(1 for value in cps_values if value > threshold)
        print(_count(f"cues > {threshold:g} cps", over, total))

    print("\nDisplay time")
    print(f"  min {min(durations)} ms")
    for threshold in DURATION_THRESHOLDS_MS:
        under = sum(1 for duration in durations if duration < threshold)
        print(_count(f"cues < {threshold} ms", under, total))

    print("\nGaps and overlaps")
    if gaps:
        print(f"  min gap {min(gaps)} ms")
        print(_count(f"gaps < {MIN_GAP_MS} ms", sum(1 for g in gaps if g < MIN_GAP_MS), len(gaps)))
        print(_count("overlaps", sum(1 for g in gaps if g < 0), len(gaps)))

    print("\nDialogue")
    any_dash = sum(1 for cue in cues if any(DASH_PATTERN.match(line) for line in cue.lines))
    two_speaker = sum(
        1
        for cue in cues
        if len(cue.lines) == 2 and all(DASH_PATTERN.match(line) for line in cue.lines)
    )
    print(_count("cues with any dash line", any_dash, total))
    print(_count("two-speaker dash cues", two_speaker, total))

    if source is not None:
        delta = total - len(source)
        percentage = (100.0 * delta / len(source)) if source else 0.0
        print("\nAgainst source")
        print(f"  source cues: {len(source)}   delta: {delta:+d} ({percentage:+.1f}%)")
        if abs(percentage) > 3.0:
            print("  note: a large delta means cues are being split or merged downstream")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("subtitles", type=Path, nargs="+", help="subtitle files to measure")
    parser.add_argument(
        "--source",
        type=Path,
        default=None,
        help="source subtitle file to compare cue counts against",
    )
    args = parser.parse_args(argv)

    source_cues = parse_subtitles(args.source) if args.source else None

    for path in args.subtitles:
        if not path.exists():
            print(f"{path}: not found", file=sys.stderr)
            return 1
        report(parse_subtitles(path), source_cues, path)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
