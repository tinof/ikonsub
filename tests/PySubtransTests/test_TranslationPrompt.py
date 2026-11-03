import unittest

from PySubtrans.Helpers.TestCases import LoggedTestCase
from PySubtrans.SubtitleLine import SubtitleLine
from PySubtrans.TranslationPrompt import TranslationPrompt


def _line(number: int, start: str, end: str, text: str) -> SubtitleLine:
    return SubtitleLine.Construct(number, start, end, text)


class LineTimingAnnotationTests(LoggedTestCase):
    """
    With include_line_timings enabled each line header carries the display time and a
    character budget, so the model can tell which lines need condensing.
    """

    def setUp(self) -> None:
        super().setUp()
        self.lines = [
            _line(
                1,
                "00:00:01,000",
                "00:00:03,400",
                "This is a fairly long English sentence that must be condensed.",
            ),
            _line(2, "00:00:03,600", "00:00:04,300", "Sit."),
        ]

    def _prompt(self, include_timings: bool) -> TranslationPrompt:
        prompt = TranslationPrompt("Translate these subtitles", conversation=False)
        prompt.include_line_timings = include_timings
        prompt.max_single_line_length = 42
        prompt.target_cps = 15.0
        return prompt

    def test_annotation_is_absent_by_default(self):
        batch_prompt = self._prompt(False).GenerateBatchPrompt(self.lines)
        self.assertLoggedIn("plain header", "#1\nOriginal>", batch_prompt)
        self.assertLoggedNotIn("no annotation", "max ", batch_prompt)

    def test_annotation_reports_duration_and_budget(self):
        batch_prompt = self._prompt(True).GenerateBatchPrompt(self.lines)
        # 2.4s at 15 cps -> 36 characters
        self.assertLoggedIn("annotated header", "#1 [2.4s, max 36 chars]", batch_prompt)

    def test_budget_has_a_floor_for_very_short_lines(self):
        batch_prompt = self._prompt(True).GenerateBatchPrompt(self.lines)
        # 0.7s at 15 cps would be 10 characters, which is below the floor of 16
        self.assertLoggedIn("floored budget", "#2 [0.7s, max 16 chars]", batch_prompt)

    def test_budget_is_capped_at_two_lines(self):
        long_line = [_line(1, "00:00:00,000", "00:00:20,000", "Take your time.")]
        batch_prompt = self._prompt(True).GenerateBatchPrompt(long_line)
        # 20s at 15 cps would be 300 characters, capped at 2 x max_single_line_length
        self.assertLoggedIn("capped budget", "max 84 chars", batch_prompt)

    def test_zero_duration_line_is_not_annotated(self):
        zero = [_line(1, "00:00:01,000", "00:00:01,000", "Hi.")]
        batch_prompt = self._prompt(True).GenerateBatchPrompt(zero)
        self.assertLoggedIn("unannotated header", "#1\nOriginal>", batch_prompt)


if __name__ == "__main__":
    unittest.main()
