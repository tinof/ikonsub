import regex

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

# Deliberately looser than the sanitiser in Helpers.Text, which strips the well-formed
# annotation: this only needs the prefix, so mangled repetitions are caught too. No natural
# subtitle text contains "[<number>s, max <number>".
leaked_annotation_pattern = regex.compile(
    r"\[\s*\d+(?:[.,]\d+)?\s*s\s*,\s*max\s+\d+", regex.IGNORECASE
)


class SubtitleValidator:
    def __init__(self, options: Options) -> None:
        self.options: Options = options

    def ValidateBatch(self, batch: SubtitleBatch):
        """
        Check if the batch seems at least plausible
        """
        self.errors = []

        if batch.translated:
            errors = self.ValidateTranslations(batch.translated)
            if errors:
                self.errors.extend(errors)

        if batch.any_translated and not batch.all_translated:
            self.errors.append(
                UntranslatedLinesError(
                    f"No translation found for {len(batch.originals) - len(batch.translated)} lines",
                    translation=batch.translation,
                )
            )

        batch.errors = self.errors

    def ValidateTranslations(self, translated: list[SubtitleLine]) -> list[Exception]:
        """
        Check if the translation seems at least plausible
        """
        if not translated:
            return [UntranslatedLinesError("Failed to extract any translations")]

        max_characters: int = self.options.get_int("max_characters") or 1000
        max_newlines: int = self.options.get_int("max_newlines") or 10
        no_number: list[SubtitleLine] = []
        no_text: list[SubtitleLine] = []
        too_long: list[SubtitleLine] = []
        too_many_newlines: list[SubtitleLine] = []
        leaked_annotations: list[SubtitleLine] = []

        for line in translated:
            if not line.number:
                no_number.append(line)

            if not line.text:
                no_text.append(line)
                continue

            if len(line.text) > max_characters:
                too_long.append(line)

            if line.text.count("\n") > max_newlines:
                too_many_newlines.append(line)

            if leaked_annotation_pattern.search(line.text):
                leaked_annotations.append(line)

        errors = []

        if no_number:
            errors.append(
                UnmatchedLinesError(
                    f"{len(no_number)} translations could not be matched with a source line",
                    lines=no_number,
                )
            )

        if no_text:
            errors.append(
                EmptyLinesError(
                    f"{len(no_text)} translations returned a blank line", lines=no_text
                )
            )

        if too_long:
            errors.append(
                LineTooLongError(
                    f"One or more lines exceeded {max_characters} characters",
                    lines=too_long,
                )
            )

        if too_many_newlines:
            errors.append(
                TooManyNewlinesError(
                    f"One or more lines contain more than {max_newlines} newlines",
                    lines=too_many_newlines,
                )
            )

        if leaked_annotations:
            errors.append(
                LeakedAnnotationError(
                    f"{len(leaked_annotations)} translations repeat the timing annotation "
                    "from the prompt - it is guidance only and must never appear in a translation",
                    lines=leaked_annotations,
                )
            )

        errors.extend(self.ValidateReadingSpeed(translated))

        return errors

    def ValidateReadingSpeed(
        self, translated: list[SubtitleLine]
    ) -> list[ReadingSpeedError]:
        """
        Find translations too long to read in their display time (max_translation_cps).

        Lines without a display time are skipped. The parser's lines have none until
        MatchTranslations copies the source timings, so SubtitleTranslator calls this
        again once a batch is matched.
        """
        max_translation_cps: float = (
            self.options.get_float("max_translation_cps") or 0.0
        )
        if max_translation_cps <= 0.0:
            return []

        too_fast: list[SubtitleLine] = []
        for line in translated:
            if not line.text:
                continue

            # Raw character count (spaces included, line breaks not) is the metric
            # subtitle players and downstream fixers use for reading speed
            char_count = len(line.text.replace("\n", ""))
            seconds = line.duration.total_seconds() if line.duration else 0.0
            # Very short cues get a grace allowance, matching the prompt annotation
            # budget floor of 16 characters
            if seconds > 0.0 and char_count > 16:
                if char_count / seconds > max_translation_cps:
                    too_fast.append(line)

        if not too_fast:
            return []

        # The retry prompt shows this message, so it names the lines to shorten
        numbers = ", ".join(f"#{line.number}" for line in too_fast)
        return [
            ReadingSpeedError(
                f"{len(too_fast)} translations exceed {max_translation_cps:.0f} "
                f"characters per second for their display time: {numbers}",
                lines=too_fast,
            )
        ]
