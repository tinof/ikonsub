import argparse
import logging
import os
import shutil
import statistics
import subprocess
import sys
import threading
import time
from pathlib import Path

import regex
from dotenv import load_dotenv
from rich.console import Console
from rich.logging import RichHandler

from PySubtrans import (
    batch_subtitles,
    init_options,
    init_translator,
    preprocess_subtitles,
)
from PySubtrans.Helpers.Localization import _
from PySubtrans.MKV import (
    filter_subtitles,
    postprocess_timecodes,
    preprocess_timecodes,
)
from PySubtrans.MKV.Config import (
    GEMINI_4_MODEL_PATTERN,
    MODE_TO_DEFAULT_MODEL,
    MODE_TO_RATE_LIMIT,
    MKVConfig,
    TranslationMode,
    claude_effort,
    gemini_thinking_level,
)
from PySubtrans.SubtitleProject import SubtitleProject
from PySubtrans.SubtitleTranslator import SubtitleTranslator
from PySubtrans.UsageLedger import USAGE_FILE

# Configure rich console and logging
console = Console()
logging.basicConfig(
    level=logging.WARNING,
    format="%(levelname)s: %(message)s",
    handlers=[RichHandler(console=console, rich_tracebacks=True)],
)
logger = logging.getLogger("transubs")

GEMINI_DEFAULT_MODEL = "gemini-4-hydrogen"
GEMINI_SCENE_THRESHOLD = 240.0
GEMINI_MIN_BATCH_SIZE = 80
GEMINI_MAX_BATCH_SIZE = 180
GEMINI_MAX_CONTEXT_SUMMARIES = 6
GEMINI_STANDARD_RATE_LIMIT = 150.0
GEMINI_FLASH_RATE_LIMIT = 1000.0
GEMINI_FLASH_MODEL_SUFFIX = "flash"
FLASH_SCENE_THRESHOLD = 300.0
FLASH_MIN_BATCH_SIZE = 100
FLASH_MAX_BATCH_SIZE = 220
# Gemini batches are sent concurrently by default. A blind A/B on four episodes
# (gemini-4-argon, 2026-10-23) scored 150-line parallel batches level with one sequential
# run of 600-line batches and 300-line parallel batches lower; 60-minute episodes went
# from ~9 minutes to ~2.
GEMINI_PARALLEL_MAX_BATCH_SIZE = 150
GEMINI_PARALLEL_WORKERS = 8

LANGUAGE_SUFFIX_PATTERN = regex.compile(
    r"^\.[a-z]{2,3}(?:-[a-z]{2,3})?$", regex.IGNORECASE
)

# Gemini 3.x and later should be left at the default temperature (Gemini 4 ignores
# temperature)
GEMINI_3_MODEL_PATTERN = regex.compile(r"gemini-[3-9]", regex.IGNORECASE)

# Readability limits handed to fix-finnish-subs, matching the translation prompt budget
# (same values as exsubs)
FINNISH_FIXER_WIDTH_LIMIT = 42
FINNISH_FIXER_MAX_CPS = 17.0
FINNISH_FIXER_TARGET_CPS = 15.0
FINNISH_FIXER_COMMAND = "fix-finnish-subs"

# A partial translation is moved to "<name>.fi.srt.partial". The extension is not in
# SUBTITLE_EXTENSIONS, so no run treats it as a source or as a finished translation.
PARTIAL_OUTPUT_SUFFIX = ".partial"

SEASON_EPISODE_PATTERN = regex.compile(
    r"S(\d{1,2})E(\d{1,3})"  # S01E02
    r"|(\d{1,2})x(\d{1,3})"  # 1x02
    r"|(\d{1,2})\.(\d{2})",  # 1.02
    regex.IGNORECASE,
)
SUBTITLE_EXTENSIONS = {".srt", ".ass", ".ssa", ".vtt"}


class IncompleteTranslationError(Exception):
    """The translation stopped early or left lines untranslated."""


def _parse_season_episode(filename: str) -> tuple[int, int]:
    """Extract (season, episode) from a filename, or (0, 0) if not found."""
    match = SEASON_EPISODE_PATTERN.search(filename)
    if match:
        groups = match.groups()
        for i in range(0, len(groups), 2):
            if groups[i] is not None:
                return int(groups[i]), int(groups[i + 1])
    return 0, 0


def _is_already_translated(sub_file: Path, lang_code: str) -> bool:
    """Check if a translated version of this subtitle file already exists.

    An empty file is a failed earlier run, so it does not count (same rule as exsubs).
    """
    translated = _build_translated_output_path(sub_file, lang_code)
    return translated.exists() and translated.stat().st_size > 0


def _is_translated_file(sub_file: Path, lang_code: str) -> bool:
    """Check if this file IS a translated file (e.g. Movie.fi.srt)."""
    stem = sub_file.stem
    return stem.endswith(f".{lang_code}")


def _looks_like_language_suffix(suffix: str) -> bool:
    """Return True if suffix resembles a language identifier."""
    return bool(LANGUAGE_SUFFIX_PATTERN.match(suffix))


def _build_translated_output_path(subtitle_file: Path, lang_code: str) -> Path:
    extension = subtitle_file.suffix or ".srt"
    filename = subtitle_file.name
    lang_code = lang_code.lower()

    if extension:
        base_name = filename[: -len(extension)]
    else:
        base_name = filename

    while True:
        dot_index = base_name.rfind(".")
        if dot_index == -1:
            break
        candidate = base_name[dot_index:]
        if not _looks_like_language_suffix(candidate):
            break
        base_name = base_name[:dot_index]

    return subtitle_file.parent / f"{base_name}.{lang_code}{extension}"


def _normalise_translated_output(
    subtitle_file: Path, desired_file: Path, target_language: str | None, lang_code: str
) -> Path:
    if desired_file.exists():
        return desired_file
    suffix = subtitle_file.suffix or ".srt"
    stem_with_lang = subtitle_file.stem
    candidates: list[Path] = [
        subtitle_file.with_name(f"{stem_with_lang}.translated{suffix}"),
        subtitle_file.with_name(f"{stem_with_lang}.{lang_code}{suffix}"),
    ]
    if target_language:
        candidates.append(
            subtitle_file.with_name(
                f"{stem_with_lang}.{target_language.lower()}{suffix}"
            )
        )
    seen: set[str] = set()
    ordered: list[Path] = []
    for c in candidates:
        s = str(c)
        if s not in seen:
            ordered.append(c)
            seen.add(s)
    for c in ordered:
        if c.exists():
            try:
                c.rename(desired_file)
                logger.info(
                    f"Renamed translated subtitles from {c.name} to {desired_file.name}"
                )
                return desired_file
            except Exception as exc:
                logger.error(
                    f"Failed to rename translated subtitles {c} -> {desired_file}: {exc}"
                )
                return c
    return desired_file


def _sisusub_ai_review_enabled() -> bool:
    """Whether fix-finnish-subs may run its AI review pass (off unless SISUSUB_AI_REVIEW is true).

    The AI pass made one Gemini call per flagged cue and took ~12 minutes and ~$1.50 per
    episode; see sisusub's docs/pipeline-review-2026-10-23.md. The deterministic fixes still run.
    """
    return (os.getenv("SISUSUB_AI_REVIEW") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _output_signature(path: Path) -> tuple[int, int] | None:
    """Return the size and mtime of an output file, or None when it does not exist."""
    try:
        stat = path.stat()
    except OSError:
        return None
    return (stat.st_size, stat.st_mtime_ns)


def _set_aside_partial_output(translated_file: Path) -> Path | None:
    """Move a partial translation away from the final path, so a later run retries the file."""
    if not translated_file.exists():
        return None

    partial_file = translated_file.with_name(
        translated_file.name + PARTIAL_OUTPUT_SUFFIX
    )
    try:
        translated_file.replace(partial_file)
    except OSError as e:
        logger.error(
            _("Could not move partial translation {file} aside: {error}").format(
                file=translated_file, error=e
            )
        )
        try:
            translated_file.unlink()
        except OSError:
            logger.error(
                _("Partial translation left at {file}").format(file=translated_file)
            )
        return None

    logger.warning(
        _("Partial translation kept as {file}").format(file=partial_file.name)
    )
    return partial_file


def _check_translation_complete(
    project: SubtitleProject, translator: SubtitleTranslator
) -> None:
    """Raise IncompleteTranslationError unless every line has a translation."""
    if translator.aborted:
        raise IncompleteTranslationError(_("Translation was aborted"))

    subtitles = project.subtitles
    if subtitles and subtitles.all_translated:
        return

    total = subtitles.linecount if subtitles else 0
    missing = (
        sum(
            len(batch.untranslated)
            for scene in subtitles.scenes
            for batch in scene.batches
        )
        if subtitles and subtitles.scenes
        else total
    )
    raise IncompleteTranslationError(
        _(
            "Translation incomplete: {missing} of {total} lines untranslated, {errors} errors"
        ).format(missing=missing, total=total, errors=len(translator.errors))
    )


def _fix_finnish_subtitles(translated_file: Path) -> bool:
    """Run fix-finnish-subs on the translated subtitle file. Return False if it failed.

    This runs exactly once: the fixer chain is not idempotent (merge fixers chain further and
    the gap fixer re-shifts timings), so re-running it on failure degrades the output. For the
    same reason it has no timeout: a killed fixer can leave a half-fixed file.
    """
    if not translated_file.exists():
        logger.warning(f"Cannot fix subtitles; {translated_file} does not exist")
        return False

    report_file = translated_file.with_stem(
        translated_file.stem + "_unfixed"
    ).with_suffix(".txt")

    command = [
        FINNISH_FIXER_COMMAND,
        str(translated_file),
        "--report",
        str(report_file),
        "--width-limit",
        str(FINNISH_FIXER_WIDTH_LIMIT),
        "--max-cps",
        str(FINNISH_FIXER_MAX_CPS),
        "--cps-target",
        str(FINNISH_FIXER_TARGET_CPS),
    ]
    if not _sisusub_ai_review_enabled():
        command.append("--no-ai-review")

    try:
        result = subprocess.run(command, check=False)
    except OSError as e:
        logger.error(_("Could not run fix-finnish-subs: {error}").format(error=e))
        return False

    if result.returncode == 0:
        logger.info(f"Fixed subtitles with fix-finnish-subs: {translated_file.name}")
    else:
        logger.error(
            _(
                "fix-finnish-subs returned non-zero exit status {status} for {file}"
            ).format(status=result.returncode, file=translated_file)
        )

    if report_file.exists():
        console.print(f"  Readability report: {report_file}")

    return result.returncode == 0


class TranslationMetrics:
    """Collect batched translation statistics for user feedback."""

    def __init__(self):
        self._lock = threading.Lock()
        self._attached = False
        self._batch_count = 0
        self._total_lines = 0
        self._line_counts: list[int] = []
        self._prompt_tokens: list[int] = []
        self._output_tokens: list[int] = []
        self._total_tokens: list[int] = []

    def attach(self, translator):
        if self._attached:
            return
        translator.events.batch_translated.connect(self._on_batch_translated)
        self._attached = True

    def detach(self, translator):
        if not self._attached:
            return
        translator.events.batch_translated.disconnect(self._on_batch_translated)
        self._attached = False

    def render(
        self,
        elapsed_seconds: float,
        expected_lines: int,
        expected_batches: int,
        rate_limit: float | None,
    ):
        if self._batch_count == 0:
            return

        avg_lines = statistics.fmean(self._line_counts) if self._line_counts else 0.0
        max_lines = max(self._line_counts) if self._line_counts else 0
        avg_prompt = (
            statistics.fmean(self._prompt_tokens) if self._prompt_tokens else 0.0
        )
        avg_output = (
            statistics.fmean(self._output_tokens) if self._output_tokens else 0.0
        )
        avg_total = statistics.fmean(self._total_tokens) if self._total_tokens else 0.0
        max_total = max(self._total_tokens) if self._total_tokens else 0

        lines_per_min = 0.0
        if elapsed_seconds > 0:
            lines_per_min = (self._total_lines / elapsed_seconds) * 60.0

        console.print("\n[bold]Gemini Translation Metrics[/bold]")
        console.print(
            f"Batches translated: {self._batch_count}/{expected_batches or self._batch_count}"
        )
        console.print(
            f"Lines processed: {self._total_lines}/{expected_lines or self._total_lines}"
        )
        console.print(f"Lines per batch (avg/max): {avg_lines:.1f}/{max_lines}")
        if self._total_tokens:
            console.print(
                f"Token usage avg (prompt/output/total): {avg_prompt:.0f}/{avg_output:.0f}/{avg_total:.0f}"
            )
            console.print(f"Peak total tokens: {max_total}")
        console.print(
            f"Throughput: {lines_per_min:.1f} lines/min ({elapsed_seconds:.1f}s elapsed)"
        )
        if rate_limit:
            console.print(f"Applied rate limit: {rate_limit:.0f} RPM")

    def _on_batch_translated(self, _sender, batch) -> None:
        with self._lock:
            self._batch_count += 1
            self._total_lines += batch.size or 0
            self._line_counts.append(batch.size or 0)
            tr = getattr(batch, "translation", None)
            if tr and isinstance(tr.content, dict):
                for key, bucket in (
                    ("prompt_tokens", self._prompt_tokens),
                    ("output_tokens", self._output_tokens),
                    ("total_tokens", self._total_tokens),
                ):
                    val = tr.content.get(key)
                    if isinstance(val, (int, float)):
                        bucket.append(int(val))


class TranslationProgress:
    def __init__(self, stream=None):
        self.stream = stream or sys.stdout
        self._file: Path | None = None
        self._total_scenes = 0
        self._done_scenes = 0
        self._total_batches = 0
        self._done_batches = 0
        self._total_lines = 0
        self._done_lines = 0
        self._last_len = 0

    def attach(self, translator, file_path: Path, total_lines: int):
        self._file = file_path
        self._total_lines = total_lines
        translator.events.preprocessed.connect(self._on_pre)
        translator.events.batch_translated.connect(self._on_batch)
        translator.events.scene_translated.connect(self._on_scene)

    def detach(self, translator, final: bool = False):
        try:
            translator.events.preprocessed.disconnect(self._on_pre)
            translator.events.batch_translated.disconnect(self._on_batch)
            translator.events.scene_translated.disconnect(self._on_scene)
        except Exception:
            pass
        self._render(final=True)

    def _on_pre(self, _s, scenes):
        self._total_scenes = len(scenes)
        self._total_batches = sum(len(sc.batches) for sc in scenes)
        self._render()

    def _on_batch(self, _s, batch):
        self._done_batches += 1
        self._done_lines += batch.size or 0
        self._render()

    def _on_scene(self, _s, **kwargs):
        # Accept keyword argument 'scene' from signal
        self._done_scenes += 1
        self._render()

    def _render(self, final: bool = False):
        if not self._file:
            return
        parts = [
            f"Translating {self._file.name}",
            f"scenes {self._done_scenes}/{self._total_scenes}",
            f"batches {self._done_batches}/{self._total_batches}",
        ]
        if self._total_lines:
            parts.append(f"lines {self._done_lines}/{self._total_lines}")
        msg = " | ".join(parts)
        pad = ""
        if len(msg) < self._last_len:
            pad = " " * (self._last_len - len(msg))
        self.stream.write(msg + pad + ("\n" if final else "\r"))
        self.stream.flush()
        self._last_len = len(msg)


def _normalise_model_name(model: str | None) -> str | None:
    if not model:
        return None
    return model.lower().split("/")[-1]


def _determine_gemini_rate_limit(model: str | None) -> float:
    normalised = _normalise_model_name(model)
    if normalised and GEMINI_FLASH_MODEL_SUFFIX in normalised:
        return GEMINI_FLASH_RATE_LIMIT
    return GEMINI_STANDARD_RATE_LIMIT


def _parallel_batches(mode: TranslationMode) -> bool:
    """PARALLEL_BATCHES, else on for Gemini on Vertex. Workers do not share the RPM pacing
    of the API-key path, so a rate-limited Gemini API key stays sequential by default."""
    value = os.getenv("PARALLEL_BATCHES")
    if value is not None:
        return value.lower() in {"1", "true", "yes", "on"}
    return mode == TranslationMode.GEMINI and _using_vertex()


def _using_vertex() -> bool:
    value = os.getenv("GEMINI_USE_VERTEX")
    if value is None:
        return True
    return value.lower() in {"1", "true", "yes", "on"}


def _detect_gcloud_project() -> str | None:
    for key in (
        "VERTEX_PROJECT",
        "GEMINI_VERTEX_PROJECT",
        "GOOGLE_CLOUD_PROJECT",
        "GCLOUD_PROJECT",
    ):
        value = os.getenv(key)
        if value:
            return value
    try:
        result = subprocess.run(
            ["gcloud", "config", "get-value", "project", "--quiet"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        proj = (result.stdout or "").strip()
        if proj and proj != "(unset)":
            return proj
    except Exception:
        pass
    return None


def _vertex_project() -> str | None:
    return _detect_gcloud_project()


def _vertex_location() -> str | None:
    return (
        os.getenv("VERTEX_LOCATION") or os.getenv("GEMINI_VERTEX_LOCATION") or "global"
    )


def _env_default_mode() -> TranslationMode | None:
    """Resolve translation mode defaults from environment for transubs."""
    raw = os.getenv("LLMSUBTRANS_DEFAULT_MODE") or os.getenv("TRANSUBS_DEFAULT_MODE")
    if not raw:
        return None
    key = raw.strip().lower()
    if key in {"gpt", "openai", "chatgpt"}:
        return TranslationMode.CHATGPT
    if key in {"gemini", "google", "vertex"}:
        return TranslationMode.GEMINI
    if key in {"claude", "anthropic"}:
        return TranslationMode.CLAUDE
    return None


def translate_srt_file(
    sub_file: Path,
    target_language: str | None,
    provider_flag: str | None,
    show_progress: bool = True,
    show_metrics: bool = True,
    proofread: bool = False,
    filter_subs: bool = True,
) -> bool:
    """Translate one subtitle file. Return False if fix-finnish-subs failed.

    A failed or incomplete translation raises, after moving any partial output aside.
    """
    # Preprocess the input file in place
    preprocess_timecodes(sub_file)
    if filter_subs:
        filter_subtitles(sub_file)
    postprocess_timecodes(sub_file)

    # Provider mapping
    provider_map = {
        TranslationMode.GEMINI: "Gemini",
        TranslationMode.CHATGPT: "OpenAI",
        TranslationMode.CLAUDE: "Claude",
    }

    mode = {
        "gemini": TranslationMode.GEMINI,
        "gpt": TranslationMode.CHATGPT,
        "claude": TranslationMode.CLAUDE,
    }.get(provider_flag or "gemini", TranslationMode.GEMINI)

    provider = provider_map.get(mode, "Gemini")
    model = MODE_TO_DEFAULT_MODEL[mode]

    # Tuned defaults and rate limits
    scene_threshold = float(os.getenv("SCENE_THRESHOLD") or 60.0)
    min_batch_size = int(os.getenv("MIN_BATCH_SIZE") or 10)
    max_batch_size = int(os.getenv("MAX_BATCH_SIZE") or 50)
    max_context_summaries = int(os.getenv("MAX_CONTEXT_SUMMARIES") or 10)
    rate_limit: float | None = None
    thinking_level: str | None = None

    # Check for large context mode
    # Default to True for Gemini, False for others, unless explicitly set
    env_large_context = os.getenv("LARGE_CONTEXT_MODE")
    if env_large_context is not None:
        large_context_mode = env_large_context.lower() in ("true", "1", "yes")
    else:
        large_context_mode = mode == TranslationMode.GEMINI

    if mode == TranslationMode.GEMINI:
        model = os.getenv("GEMINI_MODEL") or GEMINI_DEFAULT_MODEL
        thinking_level = gemini_thinking_level(model)

        # Only apply manual tuning if large_context_mode is NOT enabled
        if not large_context_mode:
            normalised = _normalise_model_name(model)
            if normalised and GEMINI_FLASH_MODEL_SUFFIX in normalised:
                scene_threshold = float(
                    os.getenv("SCENE_THRESHOLD") or FLASH_SCENE_THRESHOLD
                )
                min_batch_size = int(
                    os.getenv("MIN_BATCH_SIZE") or FLASH_MIN_BATCH_SIZE
                )
                max_batch_size = int(
                    os.getenv("MAX_BATCH_SIZE") or FLASH_MAX_BATCH_SIZE
                )
            else:
                scene_threshold = float(
                    os.getenv("SCENE_THRESHOLD") or GEMINI_SCENE_THRESHOLD
                )
                min_batch_size = int(
                    os.getenv("MIN_BATCH_SIZE") or GEMINI_MIN_BATCH_SIZE
                )
                max_batch_size = int(
                    os.getenv("MAX_BATCH_SIZE") or GEMINI_MAX_BATCH_SIZE
                )
        else:
            # If large context mode is on, let SubtitleBatcher handle defaults (or use env overrides)
            if not os.getenv("SCENE_THRESHOLD"):
                scene_threshold = 300.0
            if not os.getenv("MAX_BATCH_SIZE"):
                max_batch_size = (
                    GEMINI_PARALLEL_MAX_BATCH_SIZE if _parallel_batches(mode) else 600
                )
            if not os.getenv("MIN_BATCH_SIZE"):
                min_batch_size = 1

        max_context_summaries = int(
            os.getenv("MAX_CONTEXT_SUMMARIES") or GEMINI_MAX_CONTEXT_SUMMARIES
        )
        if _using_vertex():
            # Vertex uses dynamic shared quota - there is no fixed RPM to respect;
            # transient 429s are absorbed by the client's exponential backoff
            rate_limit = None
        else:
            rate_limit = _determine_gemini_rate_limit(model)
            os.environ.setdefault("GEMINI_RATE_LIMIT", str(int(rate_limit)))
    elif mode == TranslationMode.CHATGPT:
        model = os.getenv("OPENAI_MODEL") or "gpt-5-mini"
        scene_threshold = float(os.getenv("SCENE_THRESHOLD") or 120.0)
        min_batch_size = int(os.getenv("MIN_BATCH_SIZE") or 30)
        max_batch_size = int(os.getenv("MAX_BATCH_SIZE") or 100)
        max_context_summaries = int(os.getenv("MAX_CONTEXT_SUMMARIES") or 6)
        try:
            base_rate = os.getenv("OPENAI_RATE_LIMIT") or MODE_TO_RATE_LIMIT.get(
                mode, ""
            )
            rate_limit = float(base_rate) if base_rate else None
        except ValueError:
            rate_limit = None
    else:
        if mode == TranslationMode.CLAUDE:
            model = os.getenv("CLAUDE_MODEL") or model
        rate_value = (
            mode == TranslationMode.CLAUDE and os.getenv("CLAUDE_RATE_LIMIT")
        ) or MODE_TO_RATE_LIMIT.get(mode)
        if rate_value:
            try:
                rate_limit = float(rate_value)
            except ValueError:
                rate_limit = None

    # Vertex settings
    settings_vertex: dict[str, str | bool] = {}
    if mode == TranslationMode.GEMINI:
        use_vertex = _using_vertex()
        settings_vertex["use_vertex"] = use_vertex
        if use_vertex:
            project = _vertex_project()
            location = _vertex_location()
            if project:
                settings_vertex["vertex_project"] = project
            if location:
                settings_vertex["vertex_location"] = location

    # Determine temperature
    env_temp = os.getenv("LLM_TEMPERATURE")
    if mode == TranslationMode.GEMINI:
        env_temp = os.getenv("GEMINI_TEMPERATURE") or env_temp
    elif mode == TranslationMode.CHATGPT:
        env_temp = os.getenv("OPENAI_TEMPERATURE") or env_temp

    # Gemini 3.x is documented to degrade (repetition loops) at reduced temperature, so it
    # keeps the model default; other providers benefit from a low temperature on a
    # format-critical task. Same policy as exsubs.
    default_temperature = 1.0 if GEMINI_3_MODEL_PATTERN.search(model or "") else 0.3

    try:
        temperature = float(env_temp) if env_temp else default_temperature
    except ValueError:
        temperature = default_temperature

    # Resolve effective language and instruction file via MKVConfig (same as exsubs)
    effective_language = target_language or MKVConfig().target_language
    lang_code = MKVConfig.get_language_code(effective_language)
    config = MKVConfig(target_language=effective_language)

    # Build options
    settings = {
        "provider": provider,
        "target_language": effective_language,
        "temperature": temperature,
        "preprocess_subtitles": True,
        # postprocess_translation is the key SubtitleTranslator reads
        "postprocess_translation": True,
        "break_long_lines": True,
        "max_single_line_length": 42,
        "min_single_line_length": 8,
        "normalise_dialog_tags": True,
        "break_dialog_on_one_line": True,
        "whitespaces_to_newline": False,
        "remove_filler_words": False,
        "include_line_timings": True,
        "target_cps": 15.0,
        # Merge rapid-fire fragments of one sentence (text + timing atomically) before
        # translation, so each numbered line has a budget the model can actually meet.
        "merge_continuation_duration": 1.5,
        "merge_continuation_gap": 0.3,
        # Never split a cue by duration before translation (the Options default splits at
        # 4 s). It added ~30 lines per episode and handed the model sentence fragments,
        # without fixing any timing: text and time are divided in the same proportion.
        "max_line_duration": 0.0,
        # Reject batches where a translation blows its display-time budget - the signature
        # of content shifted onto the wrong line number - and retry them.
        "max_translation_cps": 25.0,
        "model": model,
        "scene_threshold": scene_threshold,
        "min_batch_size": min_batch_size,
        "max_batch_size": max_batch_size,
        "max_context_summaries": max_context_summaries,
        "large_context_mode": large_context_mode,
        # Batches sent concurrently, each with source-text context instead of the
        # translated summaries of earlier batches (SubtitleTranslator._translate_parallel).
        "parallel_batches": _parallel_batches(mode),
        "max_parallel_workers": int(
            os.getenv("MAX_PARALLEL_WORKERS") or GEMINI_PARALLEL_WORKERS
        ),
    }
    if rate_limit:
        settings["rate_limit"] = rate_limit
    settings.update(settings_vertex)
    if thinking_level:
        settings["thinking_level"] = thinking_level

    # Resolve instruction file (same logic as exsubs via MKVConfig)
    if config.instruction_file:
        settings["instruction_file"] = str(config.instruction_file)

    # Display configuration information

    console.print("\n[bold cyan]Translation Configuration:[/bold cyan]")
    console.print(f"  Provider: {provider}")
    console.print(f"  Model: {model}")
    console.print(f"  Target Language: {effective_language}")
    if mode == TranslationMode.CLAUDE:
        # The Claude API rejects temperature; effort is the setting that matters
        console.print(f"  Effort: {claude_effort()}")
    elif GEMINI_4_MODEL_PATTERN.search(model or ""):
        console.print(f"  Temperature: {temperature} (ignored by Gemini 4)")
    else:
        console.print(f"  Temperature: {temperature}")
    if thinking_level:
        console.print(f"  Thinking level: {thinking_level}")
    if settings["parallel_batches"]:
        console.print(
            f"  Batches: up to {max_batch_size} lines, {settings['max_parallel_workers']} in parallel"
        )
    if rate_limit:
        console.print(f"  Rate Limit: {rate_limit:.0f} RPM")
    elif settings_vertex.get("use_vertex"):
        console.print("  Rate Limit: none (Vertex dynamic shared quota)")

    console.print()

    # Create project on the input file
    project = SubtitleProject(persistent=False)

    if proofread:
        # Locate the proofread instructions file
        root_dir = Path(__file__).parent.parent
        inst_file = root_dir / "instructions" / "instructions (proofread).txt"
        if inst_file.exists():
            settings["instruction_file"] = str(inst_file)
            console.print(
                f"  [green]Proofreading mode enabled[/green] (using {inst_file.name})"
            )
        else:
            logger.warning(f"Proofread instructions not found at {inst_file}")

        # Override output language code to 'proofread' to avoid overwriting original/translated files
        lang_code = "proofread"

    # init_options loads the instruction file; Options() alone leaves instructions unset.
    # Must stay after the proofread block above, which overrides instruction_file.
    options = init_options(**settings)

    # Display instruction file info (if Options has one set)
    inst_file_raw = options.get("instruction_file")
    inst_file_path = Path(str(inst_file_raw)) if inst_file_raw else None
    if inst_file_path and inst_file_path.exists():
        console.print(f"  Instructions: {inst_file_path}")
    elif inst_file_path:
        console.print(f"  Instructions: {inst_file_path} [yellow](not found)[/yellow]")
    else:
        console.print("  Instructions: [dim]None[/dim]")

    console.print()

    desired_path = _build_translated_output_path(sub_file, lang_code)
    project.InitialiseProject(str(sub_file), str(desired_path))
    project.UpdateProjectSettings(options)

    # Preprocess explicitly: UpdateProjectSettings filters to DEFAULT_PROJECT_SETTINGS, so the
    # preprocess_subtitles flag never reaches anything that acts on it.
    if project.subtitles and project.subtitles.originals:
        preprocess_subtitles(project.subtitles, options)

    # Batch subtitles into scenes (required before translation)
    if project.subtitles:
        batch_subtitles(
            project.subtitles,
            scene_threshold=scene_threshold,
            min_batch_size=min_batch_size,
            max_batch_size=max_batch_size,
        )

    total_batches = (
        sum(len(scene.batches) for scene in project.subtitles.scenes)
        if project.subtitles and project.subtitles.scenes
        else 0
    )
    total_lines = project.subtitles.linecount if project.subtitles else 0

    translator = init_translator(options)
    metrics = (
        TranslationMetrics()
        if (show_metrics and mode == TranslationMode.GEMINI)
        else None
    )
    progress = TranslationProgress() if show_progress else None
    start = time.perf_counter()
    planned_output = Path(
        project.subtitles.outputpath
        if project.subtitles and project.subtitles.outputpath
        else desired_path
    )
    output_before = _output_signature(planned_output)
    try:
        if metrics:
            metrics.attach(translator)
        if progress:
            progress.attach(translator, sub_file, total_lines)
        project.TranslateSubtitles(translator)
        _check_translation_complete(project, translator)
    except Exception:
        # SubtitleProject saves partial results before it re-raises. Move them off the final
        # path, so the file is not skipped as done and fix-finnish-subs never sees them.
        # A file this run did not write (an earlier, finished translation) stays where it is.
        saved_path = project.subtitles.outputpath if project.subtitles else None
        partial_path = Path(saved_path) if saved_path else desired_path
        if (
            partial_path != planned_output
            or _output_signature(partial_path) != output_before
        ):
            _set_aside_partial_output(partial_path)
        raise
    finally:
        if progress:
            progress.detach(translator, final=True)
        if metrics:
            metrics.detach(translator)
            metrics.render(
                time.perf_counter() - start, total_lines, total_batches, rate_limit
            )
    # Normalise the file name, then run fix-finnish-subs once
    normalised = _normalise_translated_output(
        sub_file, desired_path, target_language, lang_code
    )
    if not normalised.exists():
        logger.error(f"Translation produced no output file: {normalised}")
        return False
    if lang_code == "fi":
        return _fix_finnish_subtitles(normalised)
    return True


def process_directory(
    target_language: str | None,
    provider_flag: str,
    *,
    show_progress: bool = True,
    show_metrics: bool = True,
    proofread: bool = False,
    filter_subs: bool = True,
) -> int:
    """Process all untranslated subtitle files in the current directory."""
    config = MKVConfig(target_language=target_language or MKVConfig().target_language)
    lang_code = MKVConfig.get_language_code(config.target_language).lower()

    # Discover subtitle files (flat, not recursive)
    all_subs: list[Path] = []
    for ext in SUBTITLE_EXTENSIONS:
        all_subs.extend(Path().glob(f"*{ext}"))

    # Filter out translated files and duplicates
    source_subs = sorted(
        {f for f in all_subs if f.is_file() and not _is_translated_file(f, lang_code)},
        key=lambda p: (_parse_season_episode(p.name), p.name),
    )

    if not source_subs:
        console.print("[yellow]No subtitle files found in current directory[/yellow]")
        return 0

    # Determine which still need translation
    pending: list[Path] = []
    for sub in source_subs:
        if _is_already_translated(sub, lang_code):
            console.print(
                f"[yellow]⏭ Skipping[/yellow] {sub.name} — translation exists"
            )
        else:
            pending.append(sub)

    if not pending:
        console.print("[green]✓ All subtitle files already translated[/green]")
        return 0

    console.print(
        f"\n[bold cyan]Starting subtitle processing[/bold cyan]"
        f" — {len(pending)} file(s) to translate, {len(source_subs) - len(pending)} skipped\n"
    )

    stats: dict[str, int] = {"processed": 0, "skipped": 0, "failed": 0}
    for sub in pending:
        console.print(f"[bold]⚙ Processing[/bold] {sub.name}")
        try:
            succeeded = translate_srt_file(
                sub,
                target_language,
                provider_flag,
                show_progress=show_progress,
                show_metrics=show_metrics,
                proofread=proofread,
                filter_subs=filter_subs,
            )
        except IncompleteTranslationError as e:
            stats["failed"] += 1
            logger.error(f"Failed to translate {sub.name}: {e}")
            console.print(f"[red]✗ Failed[/red] {sub.name}: {e}\n")
            continue
        except Exception as e:
            stats["failed"] += 1
            logger.exception(f"Failed to translate {sub.name}: {e}")
            console.print(f"[red]✗ Failed[/red] {sub.name}: {e}\n")
            continue

        if succeeded:
            stats["processed"] += 1
            console.print(f"[green]✓ Completed[/green] {sub.name}\n")
        else:
            stats["failed"] += 1
            console.print(
                f"[red]✗ Failed[/red] {sub.name}: fix-finnish-subs did not finish\n"
            )

    # Summary
    console.print("\n[bold cyan]Summary[/bold cyan]")
    console.print(f"  Processed: {stats['processed']}")
    if stats["failed"]:
        console.print(f"  [red]Failed: {stats['failed']}[/red]")
    console.print(f"  Skipped: {len(source_subs) - len(pending)}")

    return 1 if stats["failed"] else 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Translate subtitle files using tuned exsubs defaults. "
        "If no file is given, processes all untranslated subtitles in the current directory."
    )
    parser.add_argument(
        "file",
        nargs="?",
        default=None,
        help="Subtitle file to translate (.srt, .ass, .vtt). "
        "Omit to process all untranslated files in the current directory.",
    )
    # provider choice similar to exsubs
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--gemini", action="store_true", help="Use Gemini model (default)"
    )
    mode_group.add_argument("--gpt", action="store_true", help="Use ChatGPT model")
    mode_group.add_argument("--claude", action="store_true", help="Use Claude model")
    parser.add_argument(
        "-l", "--language", help="Target language (default from config)"
    )
    parser.add_argument(
        "--proofread",
        action="store_true",
        help="Proofread mode: fix flow and grammar without translating",
    )
    parser.add_argument(
        "--setup-vertex",
        action="store_true",
        help="Run Vertex setup assistant (delegates to exsubs --setup-vertex)",
    )
    parser.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="Answer yes to prompts for setup assistant",
    )
    parser.add_argument(
        "--no-filter",
        action="store_true",
        help="Disable subtitle filtering (preserve music cues, fonts, effects, etc.)",
    )
    parser.add_argument(
        "--no-progress", action="store_true", help="Disable translation progress line"
    )
    parser.add_argument(
        "--no-metrics",
        action="store_true",
        help="Do not print end-of-run translation metrics",
    )
    parser.add_argument(
        "--usage-json",
        metavar="PATH",
        help="Write the token usage of the run to PATH (Gemini per model and location, "
        "Claude per serving model), also when the run fails",
    )

    args = parser.parse_args()

    load_dotenv()

    if args.setup_vertex:
        # Reuse the setup assistant from exsubs
        try:
            from scripts.exsubs import setup_vertex

            return setup_vertex(yes=args.yes)
        except Exception as e:
            console.print(f"[red]Failed to run setup assistant:[/red] {e}")
            return 1

    # Determine provider from environment then CLI flags
    env_mode = _env_default_mode()
    provider_flag = "gemini"
    if env_mode == TranslationMode.CHATGPT:
        provider_flag = "gpt"
    elif env_mode == TranslationMode.CLAUDE:
        provider_flag = "claude"

    if args.gpt:
        provider_flag = "gpt"
    elif args.claude:
        provider_flag = "claude"

    # Fail before any API call if the Finnish post-processing tool is missing
    target_language = args.language or MKVConfig().target_language
    if (
        not args.proofread
        and MKVConfig.get_language_code(target_language) == "fi"
        and not shutil.which(FINNISH_FIXER_COMMAND)
    ):
        logger.error(
            _("{command} not found on PATH; Finnish output needs it").format(
                command=FINNISH_FIXER_COMMAND
            )
        )
        return 1

    if args.usage_json:
        _persist_usage(Path(args.usage_json))

    show_progress = not args.no_progress

    # Directory mode: process all untranslated subtitles in current directory
    if args.file is None:
        return process_directory(
            target_language=args.language,
            provider_flag=provider_flag,
            show_progress=show_progress,
            show_metrics=not args.no_metrics,
            proofread=args.proofread,
            filter_subs=not args.no_filter,
        )

    # Single-file mode
    sub_path = Path(args.file)
    if not sub_path.exists() or not sub_path.is_file():
        logger.error(f"Subtitle file not found: {sub_path}")
        return 1

    try:
        succeeded = translate_srt_file(
            sub_path,
            args.language,
            provider_flag,
            show_progress=show_progress,
            show_metrics=not args.no_metrics,
            proofread=args.proofread,
            filter_subs=not args.no_filter,
        )
        return 0 if succeeded else 1
    except IncompleteTranslationError as e:
        logger.error(f"Translation failed: {e}")
        return 1
    except Exception as e:
        logger.exception(f"Translation failed: {e}")
        return 1


def _persist_usage(path: Path) -> None:
    """Keep the run's Gemini and Claude usage in `path` for a caller that prices it.

    The ledgers rewrite the file after every response, so it holds what was billed even
    when the caller kills this process at its timeout. A failure here is only logged.
    """
    try:
        USAGE_FILE.persist_to(path)
    except Exception as e:
        logger.warning(f"Could not record usage to {path}: {e}")


if __name__ == "__main__":
    raise SystemExit(main())
