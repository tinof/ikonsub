import unittest
from datetime import timedelta
from enum import Enum

import regex

from PySubtrans.Helpers import GetValueFromName, GetValueName
from PySubtrans.Helpers.Parse import ParseDelayFromHeader, ParseNames
from PySubtrans.Helpers.TestCases import LoggedTestCase
from PySubtrans.Instructions import DEFAULT_TASK_TYPE
from PySubtrans.Options import Options
from PySubtrans.SubtitleLine import SubtitleLine
from PySubtrans.Translation import Translation
from PySubtrans.TranslationParser import (
    TranslationParser,
    default_pattern,
    fallback_patterns,
)


class TestParseDelayFromHeader(LoggedTestCase):
    test_cases = [
        ("5", 5.0),
        ("10s", 10.0),
        ("5m", 300.0),
        ("500ms", 1.0),
        ("1500ms", 1.5),
        ("abc", 32.1),
    ]

    def test_ParseDelayFromHeader(self):
        for value, expected in self.test_cases:
            with self.subTest(value=value):
                result = ParseDelayFromHeader(value)
                self.assertLoggedEqual(
                    f"delay parsed from {value}", expected, result, input_value=value
                )


class TestParseNames(LoggedTestCase):
    test_cases = [
        ("John, Jane, Alice", ["John", "Jane", "Alice"]),
        (["John", "Jane", "Alice"], ["John", "Jane", "Alice"]),
        ("Mike, Murray, Mabel, Marge", ["Mike", "Murray", "Mabel", "Marge"]),
        ("", []),
        ([], []),
        ([""], []),
    ]

    def test_ParseNames(self):
        for value, expected in self.test_cases:
            with self.subTest(value=value):
                result = ParseNames(value)
                self.assertLoggedSequenceEqual(
                    f"names parsed from {value}",
                    expected,
                    result,
                    input_value=value,
                )


class TestParseValues(LoggedTestCase):
    class TestEnum(Enum):
        Test1 = 1
        Test2 = 2
        TestValue = 4
        TestExample = 5

    class TestObject:
        def __init__(self, name):
            self.name = name

    get_value_name_cases = [
        (12345, "12345"),
        (True, "True"),
        ("Test", "Test"),
        ("TEST", "TEST"),
        ("TestName", "TestName"),
        (TestEnum.Test1, "Test1"),
        (TestEnum.Test2, "Test2"),
        (TestEnum.TestValue, "Test Value"),
        (TestEnum.TestExample, "Test Example"),
        (TestObject("Test Object"), "Test Object"),
    ]

    def test_GetValueName(self):
        for value, expected in self.get_value_name_cases:
            with self.subTest(value=value):
                result = GetValueName(value)
                self.assertLoggedEqual(
                    f"name for {value}", expected, result, input_value=value
                )

    get_value_from_name_cases = [
        (
            "Test Name",
            ["Test Name", "Another Name", "Yet Another Name"],
            None,
            "Test Name",
        ),
        (
            "Nonexistent Name",
            ["Test Name", "Another Name", "Yet Another Name"],
            "Default Value",
            "Default Value",
        ),
        (34567, [12345, 34567, 98765], None, 34567),
        ("12345", [12345, 34567, 98765], None, 12345),
        ("Test2", TestEnum, None, TestEnum.Test2),
    ]

    def test_GetValueFromName(self):
        for value, names, default, expected in self.get_value_from_name_cases:
            with self.subTest(value=value):
                result = GetValueFromName(value, names, default)
                self.assertLoggedEqual(
                    "value from name",
                    expected,
                    result,
                    input_value=(value, names, default),
                )


class TestTranslationLinePatterns(LoggedTestCase):
    """
    The line header may carry a trailing annotation (e.g. "#12 [2.4s, max 36 chars]") when
    include_line_timings is enabled, and models frequently echo it back in their response.
    """

    # (name, response, expected [(number, body)])
    match_cases = [
        ("plain", "#12\nOriginal>\nHi\nTranslation>\nHei", [("12", "Hei")]),
        ("no_original", "#12\nTranslation>\nHei", [("12", "Hei")]),
        ("annotated", "#12 [2.4s, max 36 chars]\nTranslation>\nHei", [("12", "Hei")]),
        (
            "annotated_with_original",
            "#12 [2.4s, max 36 chars]\nOriginal>\nHi\nTranslation>\nHei",
            [("12", "Hei")],
        ),
        (
            "annotated_multiple_lines",
            "#12 [2.4s]\nTranslation>\nEka\n#13 [1.8s]\nTranslation>\nToka",
            [("12", "Eka"), ("13", "Toka")],
        ),
        (
            "annotated_merge_tag",
            "#12 [2.4s]\nTranslation>\n[MERGE 12+13] Yhdistetty",
            [("12", "[MERGE 12+13] Yhdistetty")],
        ),
        (
            "annotated_dialog",
            "#12 [3.0s, max 45 chars]\nTranslation>\n- Huomenta.\n- Moi.",
            [("12", "- Huomenta.\n- Moi.")],
        ),
    ]

    def test_default_pattern_matches_annotated_headers(self):
        for name, response, expected in self.match_cases:
            with self.subTest(case=name):
                matches = [
                    (match.group("number"), match.group("body"))
                    for match in regex.finditer(
                        default_pattern, response, regex.MULTILINE
                    )
                ]
                self.assertLoggedSequenceEqual(
                    f"default_pattern matches for {name}",
                    expected,
                    matches,
                    input_value=response,
                )

    def test_fallback_patterns_tolerate_annotated_headers(self):
        """Every fallback except the last (bare number) must also survive an annotation"""
        with_original = "#12 [2.4s, max 36 chars]\nOriginal>\nHi\nTranslation>\nHei\n\n"
        # fallback_patterns[4] has no Original> branch, so it needs a bare response
        without_original = "#12 [2.4s, max 36 chars]\nTranslation>\nHei\n\n"
        for index, pattern in enumerate(fallback_patterns[:-1]):
            response = without_original if index == 4 else with_original
            with self.subTest(fallback=index):
                match = regex.search(pattern, response, regex.MULTILINE)
                self.assertLoggedIsNotNone(
                    f"fallback_patterns[{index}] matched", match, input_value=response
                )
                assert match is not None
                self.assertLoggedEqual(
                    f"fallback_patterns[{index}] number", "12", match.group("number")
                )

    def test_number_excludes_annotation(self):
        match = regex.search(
            default_pattern,
            "#7 [1.5s, max 22 chars]\nTranslation>\nHei",
            regex.MULTILINE,
        )
        self.assertLoggedIsNotNone("annotated header matched", match)
        assert match is not None
        self.assertLoggedEqual("captured number", "7", match.group("number"))


class TestTranslationParserSanitisation(LoggedTestCase):
    """Timing annotations echoed into the body must never reach the translation"""

    def _parse(self, response: str) -> dict[int, str | None]:
        parser = TranslationParser(DEFAULT_TASK_TYPE, Options())
        parser.ProcessTranslation(Translation({"text": response}), validate=False)
        return {sub.number: sub.text for sub in parser.translations.values()}

    def test_annotation_after_translation_marker(self):
        response = (
            "#123\nOriginal>\nThe other doctors died -\n"
            "Translation>\n[2.0s, max 29 chars]\nMuut lääkärit kuolivat -\n"
        )
        translations = self._parse(response)
        self.assertLoggedEqual(
            "sanitised body", "Muut lääkärit kuolivat -", translations[123]
        )

    def test_annotation_without_markers(self):
        response = "#123\n[2.0s, max 29 chars]\nMuut lääkärit kuolivat -\n"
        translations = self._parse(response)
        self.assertLoggedEqual(
            "sanitised body", "Muut lääkärit kuolivat -", translations[123]
        )

    def test_annotation_inline_with_text(self):
        response = "#7\nTranslation>\n[2.7s, max 40 chars] ja joudun\n"
        translations = self._parse(response)
        self.assertLoggedEqual("sanitised body", "ja joudun", translations[7])

    def test_escaped_newline_becomes_line_break(self):
        response = "#4\nTranslation>\nEi minun pitäisi teitä vahtia.\\nHain apua.\n"
        translations = self._parse(response)
        self.assertLoggedEqual(
            "unescaped body",
            "Ei minun pitäisi teitä vahtia.\nHain apua.",
            translations[4],
        )

    def test_annotation_on_header_line_is_ignored(self):
        response = "#7 [1.5s, max 22 chars]\nTranslation>\nHei maailma.\n"
        translations = self._parse(response)
        self.assertLoggedEqual("unaffected body", "Hei maailma.", translations[7])

    def test_repeated_translation_marker_is_removed(self):
        response = "#142\nTranslation>\nÄlä yritä.\nTranslation>\n\n#143\nTranslation>\nÄlä teeskentele.\n"
        translations = self._parse(response)
        self.assertLoggedEqual("marker line removed", "Älä yritä.", translations[142])
        self.assertLoggedEqual(
            "next line intact", "Älä teeskentele.", translations[143]
        )


class TestMergeTagMatching(LoggedTestCase):
    """A [MERGE a+b] tag either extends the line's timing or is dropped, never shipped as text"""

    def _match(self, response: str) -> dict[int, tuple[str | None, float]]:
        originals = [
            SubtitleLine.Construct(
                number,
                timedelta(seconds=number * 2),
                timedelta(seconds=number * 2 + 1.5),
                f"line {number}",
            )
            for number in (10, 11, 12, 13)
        ]
        parser = TranslationParser(DEFAULT_TASK_TYPE, Options())
        parser.ProcessTranslation(Translation({"text": response}), validate=False)
        matched, _ = parser.MatchTranslations(originals)
        return {line.number: (line.text, line.end.total_seconds()) for line in matched}

    def test_valid_merge_extends_timing_and_drops_merged_line(self):
        result = self._match(
            "#10\nTranslation>\nEka\n\n#11\nTranslation>\n[MERGE 11+12] Yhdistetty\n\n#13\nTranslation>\nVika\n"
        )
        self.assertLoggedEqual("merged text", ("Yhdistetty", 25.5), result[11])
        self.assertLoggedFalse("merged line dropped", 12 in result)

    def test_merge_that_cannot_apply_keeps_own_timing(self):
        result = self._match("#11\nTranslation>\n[MERGE 14+15] Väärä tunniste\n")
        self.assertLoggedEqual(
            "tag stripped, own end kept", ("Väärä tunniste", 23.5), result[11]
        )

    def test_merge_beyond_batch_keeps_own_timing(self):
        result = self._match("#13\nTranslation>\n[MERGE 13+14] Viimeinen\n")
        self.assertLoggedEqual(
            "tag stripped, own end kept", ("Viimeinen", 27.5), result[13]
        )

    def test_marker_only_body_is_unmatched_not_a_crash(self):
        result = self._match(
            "#11\nTranslation>\nTranslation>\n\n#12\nTranslation>\nToka\n"
        )
        self.assertLoggedFalse("empty line left unmatched", 11 in result)
        self.assertLoggedEqual("next line intact", "Toka", result[12][0])

    def test_tag_not_at_start_is_stripped(self):
        result = self._match("#11\nTranslation>\n- Kuka?\n- [MERGE 11+12] Niin.\n")
        self.assertLoggedEqual(
            "second-line tag removed", "- Kuka?\n- Niin.", result[11][0]
        )
        result = self._match("#11\nTranslation>\n<i>[MERGE 11+12] Niin.</i>\n")
        self.assertLoggedEqual("italic tag removed", "<i>Niin.</i>", result[11][0])

    def test_en_dash_before_tag_applies_merge(self):
        result = self._match("#11\nTranslation>\n– [MERGE 11+12] Niin.\n")
        self.assertLoggedEqual("merged with dash", ("– Niin.", 25.5), result[11])

    def test_dash_before_tag_is_kept_once(self):
        result = self._match(
            "#11\nTranslation>\n- [MERGE 11+12] Arvostettiin?\n- Niin.\n"
        )
        self.assertLoggedEqual("dash kept", "- Arvostettiin?\n- Niin.", result[11][0])
        result = self._match(
            "#11\nTranslation>\n- [MERGE 11+12] - Arvostettiin?\n- Niin.\n"
        )
        self.assertLoggedEqual(
            "dash not doubled", "- Arvostettiin?\n- Niin.", result[11][0]
        )


if __name__ == "__main__":
    unittest.main()
