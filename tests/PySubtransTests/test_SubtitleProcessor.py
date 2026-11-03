from datetime import timedelta

from PySubtrans.Helpers.TestCases import LoggedTestCase
from PySubtrans.SettingsType import SettingsType
from PySubtrans.SubtitleLine import SubtitleLine
from PySubtrans.SubtitleProcessor import SubtitleProcessor


def _line(number: int, start: str, end: str, text: str) -> SubtitleLine:
    return SubtitleLine({"number": number, "start": start, "end": end, "text": text})


class TestMergeContinuationLines(LoggedTestCase):
    """
    Tests for the rapid-fire continuation merge that runs before translation
    """

    def _processor(self, **overrides) -> SubtitleProcessor:
        settings = SettingsType(
            {
                "merge_continuation_duration": 1.5,
                "merge_continuation_gap": 0.3,
                "max_single_line_length": 42,
            }
        )
        settings.update(overrides)
        return SubtitleProcessor(settings)

    def test_merges_short_continuation(self):
        lines = [
            _line(
                1,
                "00:00:00,000",
                "00:00:01,900",
                "you would recommend your father-in-law's fund",
            ),
            _line(2, "00:00:01,900", "00:00:03,000", "in Shanghai?"),
        ]
        result = self._processor().PreprocessSubtitles(lines)

        self.assertLoggedEqual("line count", 1, len(result))
        self.assertLoggedEqual(
            "merged text",
            "you would recommend your father-in-law's fund in Shanghai?",
            result[0].text,
        )
        self.assertLoggedEqual(
            "merged duration", timedelta(seconds=3), result[0].duration
        )

    def test_does_not_merge_after_sentence_end(self):
        lines = [
            _line(
                1, "00:00:00,000", "00:00:02,000", "China is a partner, not a threat."
            ),
            _line(2, "00:00:02,000", "00:00:03,100", "15 million?"),
        ]
        result = self._processor().PreprocessSubtitles(lines)
        self.assertLoggedEqual("line count", 2, len(result))

    def test_does_not_merge_across_large_gap(self):
        lines = [
            _line(1, "00:00:00,000", "00:00:01,900", "So I'll hide it -"),
            _line(2, "00:00:02,900", "00:00:04,000", "somewhere safe"),
        ]
        result = self._processor().PreprocessSubtitles(lines)
        self.assertLoggedEqual("line count", 2, len(result))

    def test_does_not_merge_long_following_line(self):
        lines = [
            _line(
                1, "00:00:00,000", "00:00:01,900", "As I said, I'm here to negotiate"
            ),
            _line(
                2,
                "00:00:01,900",
                "00:00:04,500",
                "this nuclear contract between our great nations",
            ),
        ]
        result = self._processor().PreprocessSubtitles(lines)
        self.assertLoggedEqual("line count", 2, len(result))

    def test_does_not_merge_dialogue_cues(self):
        lines = [
            _line(1, "00:00:00,000", "00:00:01,900", "- Are you ready\n- Almost there"),
            _line(2, "00:00:01,900", "00:00:03,000", "for the meeting"),
        ]
        result = self._processor().PreprocessSubtitles(lines)
        self.assertLoggedEqual("line count", 2, len(result))

    def test_respects_combined_length_cap(self):
        lines = [
            _line(1, "00:00:00,000", "00:00:01,900", "a" * 70 + " and then some more"),
            _line(2, "00:00:01,900", "00:00:03,000", "words that will not fit"),
        ]
        result = self._processor().PreprocessSubtitles(lines)
        self.assertLoggedEqual("line count", 2, len(result))

    def test_disabled_by_default(self):
        lines = [
            _line(1, "00:00:00,000", "00:00:01,900", "you would recommend"),
            _line(2, "00:00:01,900", "00:00:03,000", "his fund in Shanghai?"),
        ]
        processor = SubtitleProcessor(SettingsType({}))
        result = processor.PreprocessSubtitles(lines)
        self.assertLoggedEqual("line count", 2, len(result))

    def test_renumbers_after_merge(self):
        lines = [
            _line(1, "00:00:00,000", "00:00:01,900", "you would recommend"),
            _line(2, "00:00:01,900", "00:00:03,000", "his fund in Shanghai?"),
            _line(3, "00:00:04,000", "00:00:06,000", "The party invests freely."),
        ]
        result = self._processor().PreprocessSubtitles(lines)
        self.assertLoggedEqual("line count", 2, len(result))
        self.assertLoggedEqual("line numbers", [1, 2], [line.number for line in result])

    def test_does_not_merge_after_sentence_end_inside_markup(self):
        for text, continuation in (
            ("<i>No, call your cousin.</i>", "<i>I'll do it</i>"),
            ('"We are done here."', "I'll do it"),
            ("{\\an8}<i>Did you hear that?</i>", "<i>I'll do it</i>"),
            ('<font color="#ffff00">That\'s enough!</font>', "I'll do it"),
        ):
            lines = [
                _line(1, "00:00:00,000", "00:00:02,000", text),
                _line(2, "00:00:02,000", "00:00:03,000", continuation),
            ]
            result = self._processor().PreprocessSubtitles(lines)
            self.assertLoggedEqual(f"line count after {text!r}", 2, len(result))

    def test_does_not_merge_italic_with_roman(self):
        lines = [
            _line(1, "00:00:00,000", "00:00:02,000", "<i>If you can hear me,</i>"),
            _line(2, "00:00:02,000", "00:00:03,000", "call me back"),
        ]
        result = self._processor().PreprocessSubtitles(lines)
        self.assertLoggedEqual("roman after italic", 2, len(result))

        lines = [
            _line(1, "00:00:00,000", "00:00:02,000", "If you can hear me,"),
            _line(2, "00:00:02,000", "00:00:03,000", "<i>call me back</i>"),
        ]
        result = self._processor().PreprocessSubtitles(lines)
        self.assertLoggedEqual("italic after roman", 2, len(result))

    def test_merges_italic_cues_into_one_span(self):
        lines = [
            _line(1, "00:00:00,000", "00:00:02,000", "<i>If you can hear me,</i>"),
            _line(2, "00:00:02,000", "00:00:03,000", "<i>call me back</i>"),
        ]
        result = self._processor().PreprocessSubtitles(lines)
        self.assertLoggedEqual("line count", 1, len(result))
        self.assertLoggedEqual(
            "merged text", "<i>If you can hear me, call me back</i>", result[0].text
        )

    def test_merged_text_has_no_inner_line_breaks(self):
        lines = [
            _line(
                1,
                "00:00:00,000",
                "00:00:02,000",
                "you would recommend\nyour father-in-law's",
            ),
            _line(2, "00:00:02,000", "00:00:03,000", "fund\nin Shanghai?"),
        ]
        result = self._processor().PreprocessSubtitles(lines)
        self.assertLoggedEqual("line count", 1, len(result))
        self.assertLoggedEqual(
            "merged text",
            "you would recommend your father-in-law's fund in Shanghai?",
            result[0].text,
        )

    def test_length_cap_counts_visible_text(self):
        first = "a" * 40 + ","
        second = "b" * 40
        lines = [
            _line(1, "00:00:00,000", "00:00:02,000", f"<i>{first}</i>"),
            _line(2, "00:00:02,000", "00:00:03,000", f"<i>{second}</i>"),
        ]
        result = self._processor().PreprocessSubtitles(lines)
        self.assertLoggedEqual("line count", 1, len(result))
        self.assertLoggedEqual(
            "merged text", f"<i>{first} {second}</i>", result[0].text
        )

    def test_length_cap_rejects_long_visible_text(self):
        lines = [
            _line(1, "00:00:00,000", "00:00:02,000", "a" * 45 + ","),
            _line(2, "00:00:02,000", "00:00:03,000", "b" * 45),
        ]
        result = self._processor().PreprocessSubtitles(lines)
        self.assertLoggedEqual("line count", 2, len(result))

    def test_sound_tag_is_not_merged_with_speech(self):
        lines = [
            _line(1, "00:00:00,000", "00:00:02,000", "[door slams]"),
            _line(2, "00:00:02,000", "00:00:03,000", "Who's there?"),
        ]
        result = self._processor().PreprocessSubtitles(lines)
        self.assertLoggedEqual("line count", 2, len(result))

    def test_unmerged_cues_keep_their_line_breaks(self):
        lines = [
            _line(
                1, "00:00:00,000", "00:00:02,000", "China is a partner,\nnot a threat."
            ),
            _line(2, "00:00:02,000", "00:00:03,000", "15 million?"),
        ]
        result = self._processor().PreprocessSubtitles(lines)
        self.assertLoggedEqual("line count", 2, len(result))
        self.assertLoggedEqual(
            "first text", "China is a partner,\nnot a threat.", result[0].text
        )
