from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySubtrans.Helpers.Parse import ParseNames
from PySubtrans.SubtitleError import SubtitleError

if TYPE_CHECKING:
    from PySubtrans.SubtitleBatch import SubtitleBatch
    from PySubtrans.SubtitleLine import SubtitleLine
    from PySubtrans.Subtitles import Subtitles

# Notes that open the two context blocks. The lines carry no "#N" or "Translation>": every
# parser pattern starts at "#<number>", so a block can never be read back as a translation.
PREVIOUS_LINES_NOTE = (
    "Earlier lines with the translation already delivered. Context only: stay consistent "
    "with their names, terms and register. Do not translate them again."
)
UPCOMING_LINES_NOTE = (
    "The lines that follow this batch. Context only: do not translate them."
)

# The source and translation pairs behind "previous_lines", kept so a split batch can add
# its first half's pairs for the second half. Not a prompt tag: it is never rendered.
PREVIOUS_PAIRS_KEY = "previous_line_pairs"


def GetBatchContext(
    subtitles: Subtitles,
    scene_number: int,
    batch_number: int,
    max_lines: int | None = None,
    *,
    context_lines: int = 0,
    lookahead_lines: int = 0,
) -> dict[str, Any]:
    """
    Get context for a batch of subtitles, by extracting summaries from previous scenes and batches
    """
    with subtitles.lock:
        scene = subtitles.GetScene(scene_number)
        if not scene:
            raise SubtitleError(f"Failed to find scene {scene_number}")

        batch = subtitles.GetBatch(scene_number, batch_number)
        if not batch:
            raise SubtitleError(
                f"Failed to find batch {batch_number} in scene {scene_number}"
            )

        context: dict[str, Any] = {
            "scene_number": scene.number,
            "batch_number": batch.number,
            "scene": f"Scene {scene.number}: {scene.summary}"
            if scene.summary
            else f"Scene {scene.number}",
            "batch": f"Batch {batch.number}: {batch.summary}"
            if batch.summary
            else f"Batch {batch.number}",
        }

        if "movie_name" in subtitles.settings:
            context["movie_name"] = subtitles.settings.get_str("movie_name")

        if "description" in subtitles.settings:
            context["description"] = subtitles.settings.get_str("description")

        if "names" in subtitles.settings:
            context["names"] = ParseNames(subtitles.settings.get("names", []))

        if subtitles.settings.get_bool("large_context_mode", False):
            max_history_tokens = subtitles.settings.get_int(
                "max_context_history_tokens", 10000
            )
            history_lines = GetDetailedHistory(
                subtitles,
                scene_number,
                batch_number,
                max_history_tokens if max_history_tokens is not None else 10000,
            )
        else:
            history_lines = GetHistory(subtitles, scene_number, batch_number, max_lines)

        if history_lines:
            context["history"] = history_lines

        if context_lines > 0:
            earlier = _BatchesBefore(subtitles, scene_number, batch_number)
            context = WithPreviousLines(context, earlier, context_lines)

        if lookahead_lines > 0:
            following = [
                line
                for later in scene.batches
                if later.number > batch_number
                for line in later.originals
            ]
            context = WithUpcomingLines(context, following, lookahead_lines)

    return context


def _BatchesBefore(
    subtitles: Subtitles, scene_number: int, batch_number: int
) -> list[SubtitleBatch]:
    """Every batch before the given one, across scene boundaries, in order"""
    return [
        batch
        for scene in subtitles.scenes
        if scene.number <= scene_number
        for batch in scene.batches
        if scene.number < scene_number or batch.number < batch_number
    ]


def _OneLine(text: str | None) -> str:
    return " ".join((text or "").split())


def CollectLinePairs(batches: list[SubtitleBatch]) -> list[tuple[str, str]]:
    """
    Source and translation pairs of translated batches, in order.

    The translation is the delivered text of `batch.translated`, never `line.translation`,
    which can hold "[MERGED]" or a "#Fuzzy" marker. A source line with no translation of its
    own (the second line of a [MERGE N+M]) joins the previous pair's source.
    """
    pairs: list[tuple[str, str]] = []
    for batch in batches:
        if not batch.translated:
            continue
        translations = {line.number: line.text for line in batch.translated}
        for original in batch.originals:
            source = _OneLine(original.text)
            if not source:
                continue
            translated = _OneLine(translations.get(original.number))
            if translated:
                pairs.append((source, translated))
            elif pairs:
                last_source, last_translation = pairs[-1]
                pairs[-1] = (f"{last_source} {source}", last_translation)
    return pairs


def FormatPreviousLines(pairs: list[tuple[str, str]], max_lines: int) -> str | None:
    """Render the last `max_lines` pairs as the <previous_lines> block, or None"""
    if max_lines <= 0 or not pairs:
        return None
    blocks = [
        f"Source: {source}\nTranslated: {translated}"
        for source, translated in pairs[-max_lines:]
    ]
    return "\n" + PREVIOUS_LINES_NOTE + "\n" + "\n\n".join(blocks) + "\n"


def FormatUpcomingLines(lines: list[SubtitleLine], max_lines: int) -> str | None:
    """Render the next `max_lines` source lines as the <upcoming_lines> block, or None"""
    if max_lines <= 0:
        return None
    texts = [text for line in lines if (text := _OneLine(line.text))][:max_lines]
    if not texts:
        return None
    return "\n" + UPCOMING_LINES_NOTE + "\n" + "\n".join(texts) + "\n"


def WithPreviousLines(
    context: dict[str, Any], batches: list[SubtitleBatch], max_lines: int
) -> dict[str, Any]:
    """
    A copy of the context whose previous lines also hold the pairs of `batches`.

    Returns the context unchanged when `max_lines` is 0.
    """
    if max_lines <= 0:
        return context
    pairs = list(context.get(PREVIOUS_PAIRS_KEY) or []) + CollectLinePairs(batches)
    pairs = pairs[-max_lines:]
    updated = dict(context)
    updated[PREVIOUS_PAIRS_KEY] = pairs
    rendered = FormatPreviousLines(pairs, max_lines)
    if rendered:
        updated["previous_lines"] = rendered
    else:
        updated.pop("previous_lines", None)
    return updated


def WithUpcomingLines(
    context: dict[str, Any], lines: list[SubtitleLine], max_lines: int
) -> dict[str, Any]:
    """
    A copy of the context whose upcoming lines are the first of `lines`.

    Returns the context unchanged when `max_lines` is 0.
    """
    if max_lines <= 0:
        return context
    updated = dict(context)
    rendered = FormatUpcomingLines(lines, max_lines)
    if rendered:
        updated["upcoming_lines"] = rendered
    else:
        updated.pop("upcoming_lines", None)
    return updated


def GetHistory(
    subtitles: Subtitles,
    scene_number: int,
    batch_number: int,
    max_lines: int | None = None,
) -> list[str]:
    """
    Get a list of historical summaries up to a given scene and batch number
    """
    history_lines: list[str] = []
    last_summary: str = ""

    scenes = [
        scene
        for scene in subtitles.scenes
        if scene.number and scene.number < scene_number
    ]
    for scene in [scene for scene in scenes if scene.summary]:
        if scene.summary != last_summary:
            history_lines.append(f"scene {scene.number}: {scene.summary}")
            last_summary = scene.summary or ""

    batches = [
        batch
        for batch in subtitles.GetScene(scene_number).batches
        if batch.number is not None and batch.number < batch_number
    ]
    for batch in [batch for batch in batches if batch.summary]:
        if batch.summary != last_summary:
            history_lines.append(
                f"scene {batch.scene} batch {batch.number}: {batch.summary}"
            )
            last_summary = batch.summary or ""

    if max_lines:
        history_lines = history_lines[-max_lines:]

    return history_lines


def GetDetailedHistory(
    subtitles: Subtitles, scene_number: int, batch_number: int, max_tokens: int = 10000
) -> list[str]:
    """
    Get a list of actual translated lines from previous batches up to a token limit (approximate)
    """

    # Iterate backwards through batches to collect lines until we hit the limit
    collected_lines = []
    current_tokens = 0

    # We need to flatten the structure to iterate backwards easily
    all_batches = []
    for scene in subtitles.scenes:
        if scene.number > scene_number:
            break

        for batch in scene.batches:
            if scene.number == scene_number and batch.number >= batch_number:
                break
            all_batches.append(batch)

    for batch in reversed(all_batches):
        if not batch.translated:
            continue

        batch_lines = []
        for line in reversed(batch.translated):
            text = f"{line.number}. {line.text}"
            # Rough approximation: 1 token ~= 4 chars
            tokens = len(text) / 4

            if current_tokens + tokens > max_tokens:
                break

            batch_lines.insert(0, text)
            current_tokens += tokens

        if batch_lines:
            batch_lines.insert(0, f"--- Batch {batch.number} ---")
            collected_lines = batch_lines + collected_lines

        if current_tokens >= max_tokens:
            break

    return collected_lines


def GetSourceBasedContext(
    subtitles: Subtitles, scene_number: int, batch_number: int, max_tokens: int = 10000
) -> dict[str, Any]:
    """
    Get context for a batch using SOURCE TEXT only (not translated output).
    This enables parallel batch translation by pre-computing all context upfront.
    """
    with subtitles.lock:
        scene = subtitles.GetScene(scene_number)
        if not scene:
            raise SubtitleError(f"Failed to find scene {scene_number}")

        batch = subtitles.GetBatch(scene_number, batch_number)
        if not batch:
            raise SubtitleError(
                f"Failed to find batch {batch_number} in scene {scene_number}"
            )

        context: dict[str, Any] = {
            "scene_number": scene.number,
            "batch_number": batch.number,
            "scene": f"Scene {scene.number}",
            "batch": f"Batch {batch.number}",
        }

        if "movie_name" in subtitles.settings:
            context["movie_name"] = subtitles.settings.get_str("movie_name")

        if "description" in subtitles.settings:
            context["description"] = subtitles.settings.get_str("description")

        if "names" in subtitles.settings:
            context["names"] = ParseNames(subtitles.settings.get("names", []))

        # Use source text history instead of translated history
        history_lines = _GetSourceHistory(
            subtitles, scene_number, batch_number, max_tokens
        )
        if history_lines:
            context["history"] = history_lines

    return context


def _GetSourceHistory(
    subtitles: Subtitles, scene_number: int, batch_number: int, max_tokens: int = 10000
) -> list[str]:
    """
    Get a list of SOURCE lines (original text) from previous batches up to a token limit.
    This allows context to be pre-computed before translation starts.
    """
    collected_lines: list[str] = []
    current_tokens = 0

    # Flatten all batches before the current one
    all_batches = []
    for scene in subtitles.scenes:
        if scene.number > scene_number:
            break

        for batch in scene.batches:
            if scene.number == scene_number and batch.number >= batch_number:
                break
            all_batches.append(batch)

    # Iterate backwards to get most recent context first
    for batch in reversed(all_batches):
        if not batch.originals:
            continue

        batch_lines = []
        for line in reversed(batch.originals):
            if not line.text:
                continue
            text = f"{line.number}. {line.text}"
            # Rough approximation: 1 token ~= 4 chars
            tokens = len(text) / 4

            if current_tokens + tokens > max_tokens:
                break

            batch_lines.insert(0, text)
            current_tokens += tokens

        if batch_lines:
            batch_lines.insert(
                0, f"--- Scene {batch.scene} Batch {batch.number} (source) ---"
            )
            collected_lines = batch_lines + collected_lines

        if current_tokens >= max_tokens:
            break

    return collected_lines


def PrecomputeAllBatchContexts(
    subtitles: Subtitles, max_tokens: int = 10000
) -> dict[tuple[int, int], dict[str, Any]]:
    """
    Pre-compute source-based context for ALL batches upfront.
    Returns a dict mapping (scene_number, batch_number) -> context dict.
    This enables fully parallel batch translation.
    """
    contexts: dict[tuple[int, int], dict[str, Any]] = {}

    for scene in subtitles.scenes:
        for batch in scene.batches:
            key = (scene.number, batch.number)
            contexts[key] = GetSourceBasedContext(
                subtitles, scene.number, batch.number, max_tokens
            )

    return contexts
