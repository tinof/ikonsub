import tempfile
from pathlib import Path

from PySubtrans.Helpers.TestCases import LoggedTestCase
from PySubtrans.MKV.SubtitleFilter import (
    detect_and_convert_encoding,
    remove_caps_sound_lines,
    remove_markup_only_cues,
)


class TestRemoveMarkupOnlyCues(LoggedTestCase):
    """
    Tests for dropping cues that filtering left with nothing a viewer would see
    """

    def _run(self, content: str) -> tuple[int, str]:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source.srt"
            path.write_text(content, encoding="utf-8")
            removed = remove_markup_only_cues(path)
            return removed, path.read_text(encoding="utf-8")

    def test_removes_positioning_code_only_cue_and_renumbers(self):
        removed, result = self._run(
            "1\n00:00:01,000 --> 00:00:02,000\nHola.\n\n"
            "2\n00:01:41,735 --> 00:01:46,439\n{\\an8}\n\n"
            "3\n00:02:02,025 --> 00:02:04,758\n{\\an8}Adiós.\n"
        )

        self.assertLoggedEqual("cues removed", 1, removed)
        self.assertLoggedEqual(
            "remaining cues",
            "1\n00:00:01,000 --> 00:00:02,000\nHola.\n\n"
            "2\n00:02:02,025 --> 00:02:04,758\n{\\an8}Adiós.\n",
            result,
        )

    def test_removes_cue_with_only_empty_html_tags(self):
        removed, _ = self._run(
            "1\n00:00:01,000 --> 00:00:02,000\n<i></i>\n\n2\n00:00:03,000 --> 00:00:04,000\nHola.\n"
        )

        self.assertLoggedEqual("cues removed", 1, removed)

    def test_leaves_file_untouched_when_every_cue_has_text(self):
        content = (
            "1\n00:00:01,000 --> 00:00:02,000\n{\\an8}Hola.\n\n"
            "2\n00:00:03,000 --> 00:00:04,000\n<i>Adiós.</i>\n"
        )
        removed, result = self._run(content)

        self.assertLoggedEqual("cues removed", 0, removed)
        self.assertLoggedEqual("file content", content, result)


class TestDetectAndConvertEncoding(LoggedTestCase):
    """
    Tests for the chardet-based encoding conversion
    """

    def test_undetectable_encoding_does_not_raise_into_fallback(self):
        # chardet reports encoding None for this input; it must not reach .lower()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "binary.srt"
            path.write_bytes(bytes(range(256)) * 4)

            with self.assertNoLogs("exsubs", level="ERROR"):
                result = detect_and_convert_encoding(path)

            self.assertLoggedTrue("conversion succeeds", result)
            path.read_text(encoding="utf-8")


class TestRemoveCapsSoundLines(LoggedTestCase):
    """
    Tests for dropping BBC-style all-caps sound descriptions ("GUNSHOTS")
    """

    def _run(self, *texts: str) -> tuple[int, str]:
        content = "\n\n".join(
            f"{n}\n00:00:{n:02d},000 --> 00:00:{n:02d},900\n{text}"
            for n, text in enumerate(texts, start=1)
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source.srt"
            path.write_text(content + "\n", encoding="utf-8")
            removed = remove_caps_sound_lines(path)
            return removed, path.read_text(encoding="utf-8")

    def test_removes_sound_lines_and_keeps_dialogue(self):
        removed, result = self._run(
            "No, no, no!\nGUNSHOTS",
            "SIREN WAILS",
            "Shots fired.",
            "SHE GASPS, SHRIEKS",
            "Annie?!",
        )

        self.assertLoggedEqual("lines removed", 3, removed)
        self.assertLoggedIn("dialogue kept", "No, no, no!", result)
        self.assertLoggedNotIn("sound removed", "GUNSHOTS", result)
        self.assertLoggedNotIn("sound removed", "SHRIEKS", result)

    def test_keeps_shouts_acronyms_and_short_words(self):
        removed, result = self._run(
            "GUNSHOTS",
            "SIREN WAILS",
            "DOOR SLAMS",
            "STOP RIGHT THERE!",
            "FBI",
            "OK.",
            "Get down.",
            "Where is she?",
            "Over there.",
            "Go, go.",
            "Now!",
            "Stay with me.",
            "I'm here.",
        )

        self.assertLoggedEqual("lines removed", 3, removed)
        for kept in ("STOP RIGHT THERE!", "FBI", "OK."):
            self.assertLoggedIn("kept", kept, result)

    def test_leaves_an_all_caps_file_alone(self):
        removed, _ = self._run(
            "WE NEED MORE RESOURCES", "WHY THERE WAS A CAR", "TOOK OFF"
        )

        self.assertLoggedEqual("lines removed", 0, removed)

    def test_markup_letters_do_not_make_an_all_caps_file_mixed_case(self):
        removed, result = self._run(
            "<i>WE NEED MORE RESOURCES</i>",
            "<i>WHY THERE WAS A CAR</i>",
            "{\\an8}TOOK OFF FAST",
            "<i>THEY SAW US</i>",
        )

        self.assertLoggedEqual("lines removed", 0, removed)
        self.assertLoggedIn("dialogue kept", "WE NEED MORE RESOURCES", result)

    def test_leaves_a_file_with_one_or_two_caps_lines_alone(self):
        removed, _ = self._run("GUNSHOTS", "Shots fired.", "Get down.")

        self.assertLoggedEqual("lines removed", 0, removed)
