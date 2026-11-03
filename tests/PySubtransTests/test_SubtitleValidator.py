from PySubtrans.Helpers.TestCases import LoggedTestCase
from PySubtrans.Options import Options
from PySubtrans.SubtitleBatch import SubtitleBatch
from PySubtrans.SubtitleError import (
    EmptyLinesError,
    LeakedAnnotationError,
    LineTooLongError,
    ReadingSpeedError,
    TooManyNewlinesError,
    UnmatchedLinesError,
    UntranslatedLinesError,
)
from PySubtrans.SubtitleLine import SubtitleLine
from PySubtrans.SubtitleValidator import SubtitleValidator


class TestSubtitleValidator(LoggedTestCase):
    def test_ValidateTranslations_empty(self):
        validator = SubtitleValidator(Options())
        errors = validator.ValidateTranslations([])
        self.assertLoggedEqual("error_count", 1, len(errors))
        self.assertLoggedIsInstance("error type", errors[0], UntranslatedLinesError)

    def test_ValidateTranslations_detects_errors(self):
        options = Options({"max_characters": 10, "max_newlines": 1})
        validator = SubtitleValidator(options)

        line_no_number = SubtitleLine(
            {"start": "00:00:00,000", "end": "00:00:01,000", "text": "valid"}
        )
        line_no_text = SubtitleLine(
            {"number": 1, "start": "00:00:00,000", "end": "00:00:01,000"}
        )
        line_too_long = SubtitleLine(
            {
                "number": 2,
                "start": "00:00:00,000",
                "end": "00:00:01,000",
                "text": "abcdefghijklmnopqrstuvwxyz",
            }
        )
        line_too_many_newlines = SubtitleLine(
            {
                "number": 3,
                "start": "00:00:00,000",
                "end": "00:00:01,000",
                "text": "a\nb\nc",
            }
        )

        errors = validator.ValidateTranslations(
            [line_no_number, line_no_text, line_too_long, line_too_many_newlines]
        )
        expected_types = [
            UnmatchedLinesError,
            EmptyLinesError,
            LineTooLongError,
            TooManyNewlinesError,
        ]
        self.assertLoggedEqual("error_count", len(expected_types), len(errors))

        actual_error_types = {type(e) for e in errors}
        expected_error_types = set(expected_types)
        self.assertLoggedEqual("error types", expected_error_types, actual_error_types)

    def test_ValidateBatch_adds_untranslated_error(self):
        validator = SubtitleValidator(Options())

        orig1 = SubtitleLine(
            {
                "number": 1,
                "start": "00:00:00,000",
                "end": "00:00:01,000",
                "text": "original1",
            }
        )
        orig2 = SubtitleLine(
            {
                "number": 2,
                "start": "00:00:01,000",
                "end": "00:00:02,000",
                "text": "original2",
            }
        )
        trans1 = SubtitleLine(
            {
                "number": 1,
                "start": "00:00:00,000",
                "end": "00:00:01,000",
                "text": "translated1",
            }
        )
        batch = SubtitleBatch({"originals": [orig1, orig2], "translated": [trans1]})

        validator.ValidateBatch(batch)
        self.assertLoggedEqual("error_count", 1, len(batch.errors))
        self.assertLoggedIsInstance(
            "error type", batch.errors[0], UntranslatedLinesError
        )

    def test_ValidateBatch_includes_translation_errors(self):
        options = Options({"max_characters": 10})
        validator = SubtitleValidator(options)

        orig1 = SubtitleLine(
            {
                "number": 1,
                "start": "00:00:00,000",
                "end": "00:00:01,000",
                "text": "original1",
            }
        )
        orig2 = SubtitleLine(
            {
                "number": 2,
                "start": "00:00:01,000",
                "end": "00:00:02,000",
                "text": "original2",
            }
        )
        # This translated line is too long
        trans1 = SubtitleLine(
            {
                "number": 1,
                "start": "00:00:00,000",
                "end": "00:00:01,000",
                "text": "this is a very long translated line",
            }
        )
        batch = SubtitleBatch({"originals": [orig1, orig2], "translated": [trans1]})

        validator.ValidateBatch(batch)

        error_types = {type(e) for e in batch.errors}
        self.assertLoggedEqual(
            "batch error types",
            {LineTooLongError, UntranslatedLinesError},
            error_types,
        )
        self.assertIn(LineTooLongError, error_types)
        self.assertIn(UntranslatedLinesError, error_types)

    def test_ValidateTranslations_reading_speed(self):
        options = Options({"max_translation_cps": 25.0})
        validator = SubtitleValidator(options)

        # 42 raw characters in 1.1 seconds is ~38 cps - the signature of content
        # shifted onto the wrong line number
        line_too_fast = SubtitleLine(
            {
                "number": 1,
                "start": "00:00:00,000",
                "end": "00:00:01,100",
                "text": "Puolue on vapaa sijoittamaan minne haluaa.",
            }
        )
        # 40 characters in 2 seconds is 20 cps - within the limit
        line_ok = SubtitleLine(
            {
                "number": 2,
                "start": "00:00:02,000",
                "end": "00:00:04,000",
                "text": "0123456789012345678901234567890123456789",
            }
        )
        # Above the limit but within the 16-character grace allowance for short cues
        line_short_grace = SubtitleLine(
            {
                "number": 3,
                "start": "00:00:05,000",
                "end": "00:00:05,500",
                "text": "Hei sitten!",
            }
        )

        errors = validator.ValidateTranslations(
            [line_too_fast, line_ok, line_short_grace]
        )
        self.assertLoggedEqual("error_count", 1, len(errors))
        self.assertLoggedIsInstance("error type", errors[0], ReadingSpeedError)
        error = errors[0]
        assert isinstance(error, ReadingSpeedError)
        self.assertLoggedEqual("flagged lines", [line_too_fast], error.lines)

    def test_ValidateTranslations_reading_speed_disabled_by_default(self):
        validator = SubtitleValidator(Options())

        line_too_fast = SubtitleLine(
            {
                "number": 1,
                "start": "00:00:00,000",
                "end": "00:00:01,100",
                "text": "Puolue on vapaa sijoittamaan minne haluaa.",
            }
        )

        errors = validator.ValidateTranslations([line_too_fast])
        self.assertLoggedEqual("error_count", 0, len(errors))

class TestLeakedAnnotationValidation(LoggedTestCase):
    def _validate(self, text: str) -> list[Exception]:
        validator = SubtitleValidator(Options())
        line = SubtitleLine(
            {
                "number": 1,
                "start": "00:00:00,000",
                "end": "00:00:02,000",
                "text": text,
            }
        )
        return validator.ValidateTranslations([line])

    def test_detects_mangled_annotation(self):
        errors = self._validate("[2.0S, max 29 cars] teksti")
        self.assertLoggedEqual("error_count", 1, len(errors))
        self.assertLoggedIsInstance("error type", errors[0], LeakedAnnotationError)

    def test_ignores_sound_effects(self):
        errors = self._validate("[naurua] Hyvä juttu.")
        self.assertLoggedEqual("error_count", 0, len(errors))
