import unittest

from PySubtrans.Helpers.Localization import _, tr
from PySubtrans.Helpers.TestCases import LoggedTestCase


class TestLocalization(LoggedTestCase):
    def test_gettext_passthrough(self):
        text = "Cancel"
        self.assertLoggedEqual("passthrough translation", text, _(text))

    def test_context_passthrough(self):
        text = "Cancel"
        self.assertLoggedEqual(
            "context passthrough",
            text,
            tr("dialog", text),
            input_value=("dialog", text),
        )

    def test_placeholder_preserved(self):
        msgid = "Executing LoadSubtitleFile {file}"
        formatted = _(msgid).format(file="ABC.srt")
        self.assertLoggedEqual(
            "formatted placeholder",
            "Executing LoadSubtitleFile ABC.srt",
            formatted,
            input_value=msgid,
        )


if __name__ == "__main__":
    unittest.main()
