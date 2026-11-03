import unittest
from datetime import timedelta

from PySubtrans.Options import Options
from PySubtrans.SubtitleBatcher import SubtitleBatcher
from PySubtrans.SubtitleLine import SubtitleLine


def _back_to_back(count: int) -> list[SubtitleLine]:
    """Cues that start when the previous one ends, as in many documentary captions."""
    return [
        SubtitleLine.Construct(i + 1, timedelta(seconds=2 * i), timedelta(seconds=2 * i + 2), f"Line {i + 1}")
        for i in range(count)
    ]


class SubtitleBatcherSplitTests(unittest.TestCase):
    def _sizes(self, lines: list[SubtitleLine], max_batch_size: int, min_batch_size: int = 1) -> list[int]:
        batcher = SubtitleBatcher(
            Options({"min_batch_size": min_batch_size, "max_batch_size": max_batch_size, "scene_threshold": 300.0})
        )
        return [len(batch) for batch in batcher._split_lines(lines)]

    def test_back_to_back_cues_split_evenly(self) -> None:
        """Equal gaps used to peel one-line batches off the front (533 lines became 194 batches)."""
        sizes = self._sizes(_back_to_back(533), max_batch_size=150)

        self.assertEqual(sum(sizes), 533)
        self.assertLessEqual(max(sizes), 150)
        self.assertGreaterEqual(min(sizes), 50)
        self.assertLessEqual(len(sizes), 6)

    def test_largest_gap_still_wins(self) -> None:
        """A real pause is still where a long run is cut."""
        lines = _back_to_back(200)
        for line in lines[120:]:
            line.start += timedelta(seconds=30)
            line.end += timedelta(seconds=30)

        self.assertEqual(self._sizes(lines, max_batch_size=150), [120, 80])
