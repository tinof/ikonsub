import logging
import regex as re
from pathlib import Path

from PySubtrans.Helpers.Text import markup_pattern

logger = logging.getLogger("exsubs")

MARKUP_PATTERN = markup_pattern
CUE_SEPARATOR_PATTERN = re.compile(r"\n\s*\n")


def detect_and_convert_encoding(sub_file: Path) -> bool:
    """Detect file encoding and convert to UTF-8 if needed"""
    try:
        # Try to detect encoding with chardet if available
        try:
            import chardet

            with open(sub_file, "rb") as f:
                raw_data = f.read()

            detected = chardet.detect(raw_data)
            encoding = detected.get("encoding") or "utf-8"
            confidence = detected.get("confidence", 0)

            logger.info(f"Detected encoding: {encoding} (confidence: {confidence:.2f})")

            # If already UTF-8 or very low confidence, try UTF-8 first
            if encoding.lower() in ["utf-8", "ascii"] or confidence < 0.7:
                try:
                    content = raw_data.decode("utf-8")
                    logger.info("File is already UTF-8 encoded")
                    return True
                except UnicodeDecodeError:
                    pass

            # Try the detected encoding
            if encoding and encoding.lower() != "utf-8":
                try:
                    content = raw_data.decode(encoding)
                    logger.info(f"Successfully decoded with {encoding}")
                except UnicodeDecodeError:
                    logger.warning(
                        f"Failed to decode with detected encoding {encoding}"
                    )
                    encoding = None

            # If detection failed, try common subtitle encodings
            if not encoding or encoding.lower() == "utf-8":
                common_encodings = [
                    "latin-1",
                    "windows-1252",
                    "cp1252",
                    "iso-8859-1",
                    "cp850",
                ]
                for enc in common_encodings:
                    try:
                        content = raw_data.decode(enc)
                        encoding = enc
                        logger.info(f"Successfully decoded with {enc}")
                        break
                    except UnicodeDecodeError:
                        continue
                else:
                    logger.error(
                        "Could not decode subtitle file with any known encoding"
                    )
                    return False

            # Convert to UTF-8 if needed
            if encoding.lower() != "utf-8":
                logger.info(f"Converting from {encoding} to UTF-8")
                with open(sub_file, "w", encoding="utf-8") as f:
                    f.write(content)

            return True

        except ImportError:
            # chardet not available, try manual fallback
            logger.warning("chardet not available, trying common encodings manually")
            return _manual_encoding_conversion(sub_file)
    except Exception as e:
        logger.error(f"Encoding detection failed: {e}")
        return _manual_encoding_conversion(sub_file)


def _manual_encoding_conversion(sub_file: Path) -> bool:
    """Fallback encoding conversion without chardet"""
    try:
        # Try UTF-8 first
        with open(sub_file, "r", encoding="utf-8") as f:
            content = f.read()
        return True
    except UnicodeDecodeError:
        pass

    # Try common subtitle encodings
    encodings = ["latin-1", "windows-1252", "cp1252", "iso-8859-1", "cp850", "utf-16"]

    for encoding in encodings:
        try:
            with open(sub_file, "r", encoding=encoding) as f:
                content = f.read()

            # Convert to UTF-8
            with open(sub_file, "w", encoding="utf-8") as f:
                f.write(content)

            logger.info(f"Converted subtitle from {encoding} to UTF-8")
            return True

        except (UnicodeDecodeError, UnicodeError):
            continue

    logger.error("Could not convert subtitle file to UTF-8")
    return False


def preprocess_timecodes(sub_file: Path):
    """Fix encoding and timecode formats before filtering"""
    # First, ensure the file is UTF-8 encoded
    if not detect_and_convert_encoding(sub_file):
        raise ValueError("Could not convert subtitle file to UTF-8 encoding")

    # Now read with UTF-8 encoding
    with open(sub_file, "r", encoding="utf-8") as f:
        content = f.read()

    # Replace first colon in timecodes with full-width colon to avoid splitting issues
    processed = re.sub(r"(\d{2}):((\d{2}:\d{2},\d{3}))", r"\1：\2", content)

    with open(sub_file, "w", encoding="utf-8") as f:
        f.write(processed)


def postprocess_timecodes(sub_file: Path):
    """Restore original timecode format after filtering"""
    with open(sub_file, "r", encoding="utf-8") as f:
        content = f.read()

    # Replace full-width colon back with regular colon
    processed = content.replace("：", ":")

    with open(sub_file, "w", encoding="utf-8") as f:
        f.write(processed)


def filter_subtitles(sub_file: Path):
    """Filter subtitles using subtitle_filter if available"""
    try:
        from subtitle_filter import Subtitles

        subs = Subtitles(str(sub_file))
        subs.filter(
            rm_fonts=True,
            rm_ast=True,
            rm_music=True,
            rm_effects=True,
            rm_names=True,
            rm_author=True,
            rm_style=True,
        )
        subs.save()  # Overwrites original file
        remove_markup_only_cues(sub_file)
    except ImportError:
        logger.warning("subtitle_filter not available, skipping subtitle filtering")
    except Exception as e:
        logger.error(f"Filtering failed: {e}")
        raise


def remove_markup_only_cues(sub_file: Path) -> int:
    """Drop SRT cues left with no visible text, and renumber the rest.

    Filtering removes sound descriptions such as "[MUSICA]" but keeps the cue, so a
    positioned description leaves a cue holding only "{\\an8}". Translated, that becomes an
    empty subtitle on screen. Returns the number of cues removed.
    """
    content = sub_file.read_text(encoding="utf-8")
    blocks = [block for block in CUE_SEPARATOR_PATTERN.split(content.strip()) if block.strip()]

    kept: list[list[str]] = []
    removed = 0
    for block in blocks:
        lines = block.split("\n")
        timing_index = next((i for i, line in enumerate(lines) if "-->" in line), None)
        if timing_index is None:
            kept.append(lines)
            continue
        text = "\n".join(lines[timing_index + 1 :])
        if not MARKUP_PATTERN.sub("", text).strip():
            removed += 1
            continue
        kept.append(lines[timing_index:])

    if not removed:
        return 0

    cues = []
    for number, lines in enumerate(kept, start=1):
        if lines and "-->" in lines[0]:
            cues.append("\n".join([str(number), *lines]))
        else:
            cues.append("\n".join(lines))
    sub_file.write_text("\n\n".join(cues) + "\n", encoding="utf-8")
    logger.info(f"Removed {removed} subtitle cue(s) with no visible text")
    return removed
