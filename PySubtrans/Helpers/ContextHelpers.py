from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySubtrans.Helpers.Parse import ParseNames
from PySubtrans.SubtitleError import SubtitleError

if TYPE_CHECKING:
    from PySubtrans.Subtitles import Subtitles


def GetBatchContext(
    subtitles: Subtitles,
    scene_number: int,
    batch_number: int,
    max_lines: int | None = None,
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

    return context


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
    from PySubtrans.Helpers.Parse import ParseNames

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
