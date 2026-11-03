import logging
from datetime import timedelta
from typing import Any

import regex

from PySubtrans.Helpers.Localization import _
from PySubtrans.Helpers.SubtitleHelpers import MergeTranslations
from PySubtrans.Helpers.Text import (
    ConvertEscapedNewlines,
    IsTextContentEqual,
    RemovePromptMarkers,
    RemoveTimingAnnotations,
)
from PySubtrans.Instructions import DEFAULT_TASK_TYPE
from PySubtrans.Options import Options
from PySubtrans.SubtitleError import (
    NoTranslationError,
    TranslationError,
    UntranslatedLinesError,
)
from PySubtrans.SubtitleLine import SubtitleLine
from PySubtrans.SubtitleValidator import SubtitleValidator
from PySubtrans.Translation import Translation

# Line headers may carry trailing annotations (e.g. "#12 [2.4s, max 36 chars]") which the model
# often echoes back, so everything after the number up to the end of the line is ignored.
line_number_pattern = r"#(?P<number>\d+)[^\r\n]*"

default_pattern = (
    line_number_pattern + r"(?:[\s\r\n]+Original>[\s\r\n]+(?P<original>[\s\S]*?))?"
    r"[\s\r\n]+Translation>"
    r"(?:[\s\r\n]?(?P<body>[\s\S]*?))?"
    r"(?=\n#\d|\Z)"
)

# A merge tag the model wrote anywhere in a line; never subtitle text
merge_tag_pattern = regex.compile(r"\[MERGE\s+\d+\+\d+\]\s*")

fallback_patterns = [
    line_number_pattern
    + r"(?:[\s\r\n]+Original>[\s\r\n]+(?P<original>[\s\S]*?))?[\s\r\n]*(?:Translation>(?:[\s\r\n]+(?P<body>[\s\S]*?))?(?:(?=\n{2,})|\Z))",
    line_number_pattern
    + r"(?:[\s\r\n]+Original[>:][\s\r\n]+(?P<original>[\s\S]*?))?[\s\r\n]*(?:Translation[>:](?:[\s\r\n]+(?P<body>[\s\S]*?))?(?:(?=\n{2,})|\Z))",
    line_number_pattern
    + r"(?:[\s\r\n]+Original[>:][\s\r\n]+(?P<original>[\s\S]*?))?[\s\r\n]*Translation[>:][\s\r\n]+(?P<body>[\s\S]*?)(?=(?:\n{2,}#)|\Z)",
    line_number_pattern
    + r"(?:[\s\r\n]*Original[>:][\s\r\n]*(?P<original>[\s\S]*?))?[\s\r\n]*Translation[>:][\s\r\n]*(?P<body>[\s\S]*?)(?=(?:\n{2,}#)|\Z)",
    line_number_pattern
    + r"[\s\r\n]+Translation[>:][\s\r\n]+(?P<body>[\s\S]*?)(?=(?:\n{2,}#)|\Z)",
    # Just the number and translation - deliberately NOT relaxed, since anything after the
    # number on the same line is the only translation this pattern can rescue.
    r"#(?P<number>\d+)(?:[\s\r\n]+(?P<body>[\s\S]*?))?(?:(?=\n{2,})|\Z)",
]


class TranslationParser:
    """
    Extract translated subtitles from the AI translation response
    """

    def __init__(self, task_type: str, options: Options):
        self.options: Options = options
        self.text: str | None = None
        self.translations: dict[int | str, SubtitleLine] = {}
        self.translated: list[SubtitleLine] = []
        self.errors: list[Exception] = []
        self.warnings: list[str] = []
        self.metatags: list[str] = ["summary", "scene"]
        self.task_type: str = task_type
        self.regex_patterns: list[regex.Pattern[Any]] = (
            self.GetRegularExpressionPatterns(task_type)
        )

    def GetRegularExpressionPatterns(
        self, task_type: str = DEFAULT_TASK_TYPE
    ) -> list[regex.Pattern[Any]]:
        """
        Returns a list of regular expressions to try for extracting translations
        """
        # Use the current default pattern, and fall back on alternative/older patterns if no matches are found
        patterns = [
            regex.compile(
                pattern.replace(DEFAULT_TASK_TYPE, task_type), regex.MULTILINE
            )
            for pattern in [default_pattern] + fallback_patterns
        ]
        return patterns

    def ProcessTranslation(
        self, translation: Translation, validate: bool = True
    ) -> list[SubtitleLine] | None:
        """
        Extract lines from a batched translation, using the
        pre-defined pattern to match each line, or a list of fallbacks
        if the match fails.
        """
        self.text = (
            translation.text
            if isinstance(translation, Translation)
            else str(translation)
        )

        if not self.text:
            raise TranslationError(
                "No translated text provided", translation=translation
            )

        matches: list[dict[str, str | None]] = []

        for template in self.regex_patterns:
            matches = self.FindMatches(f"{self.text}\n\n", template)

            if matches:
                break

        if not matches:
            raise TranslationError(
                f"No matches found in translation text using patterns: {self.regex_patterns}",
                translation=translation,
            )

        logging.debug(f"Matches: {str(matches)}")

        subs = [SubtitleLine(match) for match in matches]
        self.translations = {sub.key: sub for sub in subs}

        if not self.translations:
            return None

        self.translated = MergeTranslations(
            self.translated, list(self.translations.values())
        )

        if validate:
            self.errors = self.ValidateTranslations()

            if self.errors and self.translated:
                self._fix_unclosed_tags()
                self.errors = self.ValidateTranslations()

        return self.translated

    def FindMatches(self, text, template) -> list[dict[str, str | None]]:
        """
        re.findall has some very unhelpful behaviour, so we use finditer instead.

        Timing annotations are stripped here so that every pattern benefits, including the
        fallbacks that capture anything following the line number.
        """
        return [
            {
                "body": RemovePromptMarkers(
                    RemoveTimingAnnotations(ConvertEscapedNewlines(match.group("body")))
                ),
                "number": match.groupdict().get("number"),
                "start": match.groupdict().get("start"),
                "end": match.groupdict().get("end"),
                "original": RemoveTimingAnnotations(match.groupdict().get("original")),
            }
            for match in template.finditer(text)
        ]

    def MatchTranslations(
        self, originals: list[SubtitleLine]
    ) -> tuple[list[SubtitleLine], list[SubtitleLine]]:
        """
        Match lines in the translation with the original subtitles
        """
        if not originals:
            raise ValueError("Original subtitles not provided")

        matched = []
        unmatched = []
        merged_ids = set()

        # Create a map for quick lookup of original lines
        original_map = {line.number: line for line in originals}

        for item in originals:
            # Skip if this line was part of a merge
            if item.number in merged_ids:
                continue

            translation: SubtitleLine | None = self.translations.get(item.key)

            # A body that sanitising left empty counts as untranslated, so it is retried
            if translation and translation.text:
                # Check for MERGE tag
                # Format: [MERGE startID+endID]
                merge_match = regex.match(
                    r"^(?P<dash>[-–—]\s*)?\[MERGE\s+(?P<start>\d+)\+(?P<end>\d+)\]\s*(?P<text>.*)",
                    translation.text,
                    regex.DOTALL,
                )
                merge_applied = False

                if merge_match:
                    start_id = int(merge_match.group("start"))
                    end_id = int(merge_match.group("end"))
                    # The tag is never subtitle text, whether or not the merge can be applied.
                    # A dialog dash written before the tag is kept unless the text repeats it.
                    merged_text = merge_match.group("text")
                    if merge_match.group("dash") and not merged_text.startswith(
                        ("-", "–", "—")
                    ):
                        merged_text = merge_match.group("dash") + merged_text
                    translation.text = merged_text

                    if (
                        start_id == item.number
                        and end_id in original_map
                        and end_id > start_id
                    ):
                        merge_applied = True
                        # Calculate new end time
                        end_line = original_map[end_id]
                        translation.end = end_line.end

                        # Mark intermediate lines as merged
                        # We assume sequential IDs for now, but we should probably handle gaps if possible
                        # For now, let's just mark the specific end_id and any IDs in between if they exist in the map
                        for i in range(start_id + 1, end_id + 1):
                            if i in original_map:
                                merged_ids.add(i)
                                # Optionally mark the original line as having a "merged" translation for debugging
                                original_map[i].translation = "[MERGED]"
                    else:
                        logging.warning(
                            f"Ignoring merge tag [MERGE {start_id}+{end_id}] on line {item.number}: "
                            "it does not start at this line or its last line is not in the batch"
                        )

                # A tag anywhere else (a second dialog line, inside italics) cannot apply a merge
                if translation.text and "[MERGE" in translation.text:
                    translation.text = merge_tag_pattern.sub(
                        "", translation.text
                    ).strip()

                translation.number = item.number
                translation.start = item.start or timedelta(seconds=0)
                # Unless a merge was applied, the line keeps its own end time
                if not merge_applied:
                    translation.end = item.end or timedelta(seconds=0)

                translation.metadata = item.metadata

                if translation.original and IsTextContentEqual(
                    translation.text, item.text
                ):
                    # Check for swapped original & translation
                    translation.text = translation.original
                    translation.original = item.text

                item.translation = translation.text
                matched.append(translation)

            else:
                item.translation = None
                unmatched.append(item)

        if unmatched:
            self.TryFuzzyMatches(unmatched)

        if unmatched:
            self.errors.append(
                UntranslatedLinesError(
                    f"No translation found for {len(unmatched)} lines", lines=unmatched
                )
            )

        return matched, unmatched

    def TryFuzzyMatches(self, unmatched: list[SubtitleLine]) -> None:
        """
        Try to match translations to their source lines using heuristics
        """
        possible_matches: list[tuple[SubtitleLine, SubtitleLine]] = []
        for item in (item for item in unmatched if item.number is not None):
            for translation in self.translations.values():
                if translation.original:
                    if IsTextContentEqual(translation.original, item.text):
                        # A match on the original text is pretty compelling
                        possible_matches.append((item, translation))
                        continue
                    elif IsTextContentEqual(translation.text, item.text):
                        # LLMs sometimes swap the original and translated text - swap them back
                        translation.text = translation.original
                        translation.original = item.text
                        possible_matches.append((item, translation))
                        continue

        if possible_matches:
            for item, translation in possible_matches:
                self.warnings.append(
                    _("Found fuzzy match for line {number} in translations").format(
                        number=item.number
                    )
                )
                item.translation = f"#Fuzzy: {translation.text}"
                # unmatched.remove(item)

    def ValidateTranslations(self) -> list[Exception]:
        """
        Check if the translation seems at least plausible
        """
        if not self.translated:
            return [
                NoTranslationError(
                    "Failed to extract translations from response",
                    translation=self.text,
                )
            ]

        validator = SubtitleValidator(self.options)
        return validator.ValidateTranslations(self.translated)

    def _fix_unclosed_tags(self):
        """
        Check if the last line of the translation picked up a summary without a closing tag
        """
        last_line: SubtitleLine = self.translated[-1]

        if not last_line.text:
            return

        # Use a regex to check for opening metatags and ensure there is a matching close tag. If not, truncate the text at the tag.
        re_opening = regex.compile(rf"<({'|'.join(self.metatags)})>", regex.IGNORECASE)

        for match in re_opening.finditer(last_line.text):
            tag = match.group(1)
            if not regex.search(rf"</{tag}>", last_line.text):
                self.warnings.append(
                    _("Found unclosed tag {tag} in translation").format(tag=tag)
                )
                last_line.text = last_line.text[: match.start()]
                break
