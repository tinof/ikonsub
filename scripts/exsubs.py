import argparse
import logging
import os
from pathlib import Path
import statistics
import shutil
import subprocess
import sys
import tempfile
import threading
import time

from dotenv import load_dotenv
from filelock import FileLock
import regex
from rich.console import Console
from rich.logging import RichHandler

from PySubtrans.MKV import (
    MKVConfig,
    TranslationMode,
    VideoFile,
    get_mkv_subtitle_tracks,
    select_track_interactively,
    select_best_track_with_fallback,
    extract_track_with_progress,
    preprocess_timecodes,
    postprocess_timecodes,
    filter_subtitles,
    run_diagnostics,
)
from PySubtrans.MKV.Config import MODE_TO_DEFAULT_MODEL, MODE_TO_ENV, MODE_TO_RATE_LIMIT

from PySubtrans import (
    batch_subtitles,
    init_options,
    init_translator,
    preprocess_subtitles,
)
from PySubtrans.Helpers.Localization import _
from PySubtrans.SubtitleProject import SubtitleProject
from PySubtrans.SubtitleTranslator import SubtitleTranslator

# Configure rich console and logging
console = Console()
logging.basicConfig(
    level=logging.WARNING,
    format="%(levelname)s: %(message)s",
    handlers=[RichHandler(console=console, rich_tracebacks=True)],
)
logger = logging.getLogger("exsubs")

GEMINI_DEFAULT_MODEL = "gemini-3.1-flash-lite"
GEMINI_SCENE_THRESHOLD = 240.0
GEMINI_MIN_BATCH_SIZE = 80
GEMINI_MAX_BATCH_SIZE = 180
GEMINI_STANDARD_RATE_LIMIT = 150.0
GEMINI_FLASH_RATE_LIMIT = 1000.0
GEMINI_FLASH_MODEL_SUFFIX = "flash"
GEMINI_MAX_CONTEXT_SUMMARIES = 6

# Flash preview tuned defaults
FLASH_SCENE_THRESHOLD = 300.0
FLASH_MIN_BATCH_SIZE = 100
FLASH_MAX_BATCH_SIZE = 220

# Large-context mode defaults, matching SubtitleBatcher
LARGE_CONTEXT_SCENE_THRESHOLD = 300.0
LARGE_CONTEXT_MIN_BATCH_SIZE = 1
LARGE_CONTEXT_MAX_BATCH_SIZE = 600

# Parallel batches default to 150 lines: in the 2026-10-23 A/B (transubs) 150-line parallel
# batches scored level with sequential 600-line batches.
PARALLEL_MAX_BATCH_SIZE = 150

LANGUAGE_SUFFIX_PATTERN = regex.compile(
    r"^\.[a-z]{2,3}(?:-[a-z]{2,3})?$", regex.IGNORECASE
)

# Gemini 3.x and later should be left at the default temperature
GEMINI_3_MODEL_PATTERN = regex.compile(r"gemini-[3-9]", regex.IGNORECASE)

# Readability limits handed to fix-finnish-subs, matching the translation prompt budget
FINNISH_FIXER_WIDTH_LIMIT = 42
FINNISH_FIXER_MAX_CPS = 17.0
FINNISH_FIXER_TARGET_CPS = 15.0
FINNISH_FIXER_COMMAND = "fix-finnish-subs"

# A partial translation is moved to "<name>.fi.srt.partial": no run treats it as a subtitle,
# so the next run translates the episode again instead of skipping it.
PARTIAL_OUTPUT_SUFFIX = ".partial"


class IncompleteTranslationError(Exception):
    """The translation stopped early or left lines untranslated."""


def _looks_like_language_suffix(suffix: str) -> bool:
    """Return True if suffix resembles a language identifier."""
    return bool(LANGUAGE_SUFFIX_PATTERN.match(suffix))


def _build_translated_output_path(subtitle_file: Path, lang_code: str) -> Path:
    """Generate output path replacing existing language suffix with target code."""
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
    """Ensure translated subtitles end up at the desired path."""
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
    for candidate in candidates:
        key = str(candidate)
        if key not in seen:
            ordered.append(candidate)
            seen.add(key)

    for candidate in ordered:
        if candidate.exists():
            try:
                candidate.rename(desired_file)
                logger.info(
                    f"Renamed translated subtitles from {candidate.name} to {desired_file.name}"
                )
                return desired_file
            except Exception as exc:
                logger.error(
                    f"Failed to rename translated subtitles {candidate} -> {desired_file}: {exc}"
                )
                return candidate

    return desired_file


def _sisusub_ai_review_enabled() -> bool:
    """Whether fix-finnish-subs may run its AI review pass (off unless SISUSUB_AI_REVIEW is true).

    The pass makes one Gemini call per flagged cue: about 12 minutes and $1.50 an episode in the
    2026-10-23 pipeline review (sisusub docs/pipeline-review-2026-10-23.md). Same rule as transubs.
    """
    return (os.getenv("SISUSUB_AI_REVIEW") or "").strip().lower() in {"1", "true", "yes", "on"}


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

    partial_file = translated_file.with_name(translated_file.name + PARTIAL_OUTPUT_SUFFIX)
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
            logger.error(_("Partial translation left at {file}").format(file=translated_file))
        return None

    logger.warning(_("Partial translation kept as {file}").format(file=partial_file.name))
    return partial_file


def _check_translation_complete(project: SubtitleProject, translator: SubtitleTranslator) -> None:
    """Raise IncompleteTranslationError unless every line has a translation."""
    if translator.aborted:
        raise IncompleteTranslationError(_("Translation was aborted"))

    subtitles = project.subtitles
    if subtitles and subtitles.all_translated:
        return

    total = subtitles.linecount if subtitles else 0
    missing = (
        sum(len(batch.untranslated) for scene in subtitles.scenes for batch in scene.batches)
        if subtitles and subtitles.scenes
        else total
    )
    raise IncompleteTranslationError(
        _("Translation incomplete: {missing} of {total} lines untranslated, {errors} errors").format(
            missing=missing, total=total, errors=len(translator.errors)
        )
    )


def _fix_finnish_subtitles(translated_file: Path) -> bool:
    """
    Run fix-finnish-subs on the translated subtitle file. Return False if it failed.

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

    if os.getenv("EXSUBS_FIXER_VERBOSE"):
        command.append("--verbose")

    try:
        result = subprocess.run(command, check=False)
    except OSError as e:
        logger.error(_("Could not run fix-finnish-subs: {error}").format(error=e))
        return False

    if result.returncode == 0:
        logger.info(f"Fixed subtitles with fix-finnish-subs: {translated_file.name}")
    else:
        logger.error(
            _("fix-finnish-subs returned non-zero exit status {status} for {file}").format(
                status=result.returncode, file=translated_file
            )
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

    def has_data(self) -> bool:
        return self._batch_count > 0

    def render(
        self,
        elapsed_seconds: float,
        expected_lines: int,
        expected_batches: int,
        rate_limit: float | None,
    ):
        if not self.has_data():
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

        lines_per_minute = 0.0
        if elapsed_seconds > 0:
            lines_per_minute = (self._total_lines / elapsed_seconds) * 60.0

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
            f"Throughput: {lines_per_minute:.1f} lines/min ({elapsed_seconds:.1f}s elapsed)"
        )
        if rate_limit:
            console.print(f"Applied rate limit: {rate_limit:.0f} RPM")

    def _on_batch_translated(self, _sender, batch) -> None:
        with self._lock:
            line_count = batch.size or 0
            self._batch_count += 1
            self._total_lines += line_count
            self._line_counts.append(line_count)

            translation = getattr(batch, "translation", None)
            if translation and isinstance(translation.content, dict):
                self._append_token(
                    self._prompt_tokens, translation.content.get("prompt_tokens")
                )
                self._append_token(
                    self._output_tokens, translation.content.get("output_tokens")
                )
                self._append_token(
                    self._total_tokens, translation.content.get("total_tokens")
                )

    def _append_token(self, bucket: list[int], value):
        if isinstance(value, (int, float)):
            bucket.append(int(value))


class TranslationProgress:
    """Lightweight progress line updated on translation events."""

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


def _resolve_batch_sizes(
    mode: TranslationMode,
    model: str | None,
    large_context_mode: bool,
    parallel_batches: bool,
    max_batch_size_arg: int | None,
    min_batch_size_arg: int | None,
) -> tuple[float, int, int]:
    """Return (scene_threshold, min_batch_size, max_batch_size).

    Precedence, lowest first: mode/model defaults, then the SCENE_THRESHOLD, MIN_BATCH_SIZE
    and MAX_BATCH_SIZE env vars, then the parallel default (when MAX_BATCH_SIZE is unset),
    then the --max-batch-size/--min-batch-size arguments.
    """
    if mode == TranslationMode.GEMINI and large_context_mode:
        # Large-context mode leaves batching to SubtitleBatcher's large defaults
        scene_threshold = LARGE_CONTEXT_SCENE_THRESHOLD
        min_batch_size = LARGE_CONTEXT_MIN_BATCH_SIZE
        max_batch_size = LARGE_CONTEXT_MAX_BATCH_SIZE
    elif mode == TranslationMode.GEMINI:
        normalised = _normalise_model_name(model)
        if normalised and GEMINI_FLASH_MODEL_SUFFIX in normalised:
            scene_threshold = FLASH_SCENE_THRESHOLD
            min_batch_size = FLASH_MIN_BATCH_SIZE
            max_batch_size = FLASH_MAX_BATCH_SIZE
        else:
            scene_threshold = GEMINI_SCENE_THRESHOLD
            min_batch_size = GEMINI_MIN_BATCH_SIZE
            max_batch_size = GEMINI_MAX_BATCH_SIZE
    elif mode == TranslationMode.CHATGPT:
        scene_threshold, min_batch_size, max_batch_size = 120.0, 30, 100
    else:
        scene_threshold, min_batch_size, max_batch_size = 60.0, 10, 50

    scene_threshold = float(os.getenv("SCENE_THRESHOLD") or scene_threshold)
    min_batch_size = int(os.getenv("MIN_BATCH_SIZE") or min_batch_size)
    max_batch_size = int(os.getenv("MAX_BATCH_SIZE") or max_batch_size)

    if parallel_batches and not os.getenv("MAX_BATCH_SIZE"):
        # Smaller batches keep the parallel workers busy
        max_batch_size = PARALLEL_MAX_BATCH_SIZE

    if max_batch_size_arg:
        max_batch_size = max_batch_size_arg

    if min_batch_size_arg:
        min_batch_size = min_batch_size_arg
    elif min_batch_size > max_batch_size:
        # SubtitleBatcher rejects min > max; only an explicit --min-batch-size may do that
        min_batch_size = max_batch_size

    return scene_threshold, min_batch_size, max_batch_size


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
        os.getenv("VERTEX_LOCATION")
        or os.getenv("GEMINI_VERTEX_LOCATION")
        or "europe-west1"
    )


def _env_default_mode() -> TranslationMode | None:
    """Resolve default mode from environment.

    Honors `LLMSUBTRANS_DEFAULT_MODE` (preferred) and `EXSUBS_DEFAULT_MODE`.
    Accepted values (case-insensitive): gemini, gpt/openai/chatgpt, claude.
    """
    raw = os.getenv("LLMSUBTRANS_DEFAULT_MODE") or os.getenv("EXSUBS_DEFAULT_MODE")
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


def verify_dependencies(lang_code: str | None = None):
    """Verify required command line tools are available before any API call"""
    for cmd in ("ffmpeg", "ffprobe"):
        if not shutil.which(cmd):
            logger.error(_("{command} not found. Please install ffmpeg package.").format(command=cmd))
            sys.exit(1)

    if lang_code == "fi" and not shutil.which(FINNISH_FIXER_COMMAND):
        logger.error(
            _("{command} not found on PATH; Finnish output needs it").format(
                command=FINNISH_FIXER_COMMAND
            )
        )
        sys.exit(1)


def _detect_shell_profile() -> Path:
    shell = os.getenv("SHELL", "").strip()
    home = Path.home()
    if shell.endswith("zsh"):
        return home / ".zshrc"
    if shell.endswith("bash"):
        return home / ".bashrc"
    # Fallbacks commonly loaded on login
    for candidate in [home / ".bashrc", home / ".profile", home / ".zshrc"]:
        return candidate
    return home / ".profile"


def _find_gcloud() -> str | None:
    path = shutil.which("gcloud")
    if path:
        return path
    # Try common locations
    candidates = [
        "/usr/bin/gcloud",
        "/snap/bin/gcloud",
        "/usr/local/bin/gcloud",
        "/opt/google-cloud-sdk/bin/gcloud",
    ]
    for c in candidates:
        if Path(c).exists():
            return c
    return None


def setup_vertex(yes: bool = False) -> int:
    """Interactive (or --yes) setup assistant for Vertex Gemini.

    - Verifies gcloud availability and ADC.
    - Detects default GCP project and proposes persistent exports.
    - Writes exports to the user's shell profile on confirmation.
    """
    console.print("\n[bold]Vertex setup assistant[/bold]")

    # 1) gcloud
    gcloud_path = _find_gcloud()
    if not gcloud_path:
        console.print(
            "[red]gcloud not found on PATH[/red]. Install Google Cloud CLI and re-run: https://cloud.google.com/sdk/docs/install"
        )
        return 1
    console.print(f"gcloud: [cyan]{gcloud_path}[/cyan]")

    # 2) ADC check (no token printed)
    adc_ok = False
    try:
        result = subprocess.run(
            [gcloud_path, "auth", "application-default", "print-access-token"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        adc_ok = result.returncode == 0 and bool((result.stdout or "").strip())
    except Exception:
        adc_ok = False
    if not adc_ok:
        console.print(
            "[yellow]No Application Default Credentials detected[/yellow]. Run: [bold]gcloud auth application-default login[/bold] and try again."
        )
        # continue; we can still set exports

    # 3) Detect project
    project = _detect_gcloud_project()
    if not project:
        console.print(
            "[yellow]No default GCP project detected[/yellow]. You can set one with: [bold]gcloud config set project <PROJECT_ID>[/bold]"
        )
        project = (
            console.input("Enter project id to use (or leave blank to skip): ").strip()
            if not yes
            else ""
        )

    # 4) Decide region/model
    region = os.getenv("VERTEX_LOCATION") or "europe-west1"
    model = os.getenv("GEMINI_MODEL") or GEMINI_DEFAULT_MODEL

    exports: list[str] = [
        "export GEMINI_USE_VERTEX=true",
        f"export VERTEX_LOCATION={region}",
        f"export GEMINI_MODEL={model}",
    ]
    if project:
        exports.append(f"export VERTEX_PROJECT={project}")

    console.print("\nProposed environment configuration:\n" + "\n".join(exports))

    do_write = yes
    if not do_write:
        choice = (
            console.input("Write these to your shell profile to persist? [y/N]: ")
            .strip()
            .lower()
        )
        do_write = choice in {"y", "yes"}

    if do_write:
        profile = _detect_shell_profile()
        try:
            profile.parent.mkdir(parents=True, exist_ok=True)
            with open(profile, "a", encoding="utf-8") as f:
                f.write("\n# llm-subtrans Vertex defaults\n")
                f.write("\n".join(exports) + "\n")
            console.print(f"[green]✓ Wrote exports to[/green] {profile}")
            console.print("Run 'exec $SHELL' or start a new shell to load them.")
        except Exception as e:
            console.print(f"[red]Failed to write profile:[/red] {e}")
            return 1

    # 5) Summary and next command
    console.print("\nYou can now run exsubs against an MKV file, e.g.:")
    console.print("  exsubs --gemini --no-progress your_video.mkv")
    return 0


def verify_api_key(mode: TranslationMode):
    """Verify required API key is set based on translation mode"""
    if mode == TranslationMode.GEMINI and _using_vertex():
        return

    required_key = MODE_TO_ENV[mode]
    if not os.getenv(required_key):
        logger.error(f"{required_key} not found in environment")
        sys.exit(1)


def process_video_file(
    video_file: Path,
    config: MKVConfig,
    mode: TranslationMode,
    interactive: bool,
    show_progress: bool,
    show_metrics: bool = True,
    copy_local: bool = False,
    parallel_batches: bool = False,
    parallel_workers: int = 4,
    max_batch_size: int | None = None,
    min_batch_size: int | None = None,
    filter_subs: bool = True,
) -> bool:
    """Process a single video file - extract and translate subtitles.

    Return True when the file is done (translated, fixed, or already translated) and False
    when it failed. A failed or incomplete translation raises.
    """
    logger.info(f"Processing {video_file}")

    # Only process MKV files
    if not video_file.suffix.lower() == ".mkv":
        logger.warning(f"Skipping {video_file} - only MKV files are supported")
        return False

    subtitle_file = video_file.with_suffix(".srt")
    lang_code = config.get_language_code(config.target_language)
    translated_file = _build_translated_output_path(subtitle_file, lang_code)

    # Check if translated file already exists. An empty one is a failed earlier run, and
    # skipping it would leave the episode without subtitles for good.
    if translated_file.exists():
        if translated_file.stat().st_size > 0:
            logger.info(f"Skipping {video_file} - translated subtitle already exists")
            return True
        logger.warning(
            f"Empty translated subtitle found, translating again: {translated_file.name}"
        )

    # Extract subtitles if needed
    if not subtitle_file.exists():
        extraction_source = video_file
        temp_file = None

        if copy_local:
            try:
                # Create temp file
                fd, temp_path = tempfile.mkstemp(suffix=".mkv")
                os.close(fd)
                temp_file = Path(temp_path)

                # Get file size for progress
                file_size = video_file.stat().st_size
                size_gb = file_size / (1024 * 1024 * 1024)

                console.print(
                    f"[blue]Copying to local temp ({size_gb:.1f} GB):[/blue] {temp_file}"
                )
                start_copy = time.perf_counter()
                shutil.copy2(video_file, temp_file)
                copy_time = time.perf_counter() - start_copy
                speed_mb = (file_size / (1024 * 1024)) / copy_time
                console.print(
                    f"[green]✓ Copied in {copy_time:.1f}s ({speed_mb:.1f} MB/s)[/green]"
                )

                extraction_source = temp_file

            except Exception as e:
                logger.error(f"Failed to copy to local temp: {e}")
                if temp_file and temp_file.exists():
                    temp_file.unlink()
                return False

        try:
            if not extract_subtitles(
                extraction_source, subtitle_file, interactive, show_progress
            ):
                return False
        finally:
            if temp_file and temp_file.exists():
                temp_file.unlink()
                console.print(f"[dim]Removed temp file: {temp_file}[/dim]")

    # Process subtitles: preprocess → filter → postprocess
    preprocess_timecodes(subtitle_file)
    if filter_subs:
        filter_subtitles(subtitle_file)
    postprocess_timecodes(subtitle_file)

    # Translate subtitles using PySubtrans API
    try:
        translate_subtitles(
            subtitle_file,
            translated_file,
            config,
            mode,
            show_progress=show_progress,
            show_metrics=show_metrics,
            parallel_batches=parallel_batches,
            parallel_workers=parallel_workers,
            max_batch_size_arg=max_batch_size,
            min_batch_size_arg=min_batch_size,
        )

        translated_file = _normalise_translated_output(
            subtitle_file, translated_file, config.target_language, lang_code
        )

        if not translated_file.exists():
            logger.error(f"Translation produced no output file: {translated_file}")
            return False

        fixed = _fix_finnish_subtitles(translated_file) if lang_code == "fi" else True

        # Clean up the original extracted subtitle if translation succeeded
        if subtitle_file.exists():
            subtitle_file.unlink()

        return fixed

    except Exception as e:
        logger.error(f"Translation failed: {e}")
        raise


def extract_subtitles(
    video_file: Path, subtitle_file: Path, interactive: bool, show_progress: bool
) -> bool:
    """Extract subtitles from MKV file using ffmpeg"""
    if not video_file.suffix.lower() == ".mkv":
        logger.error(f"Only MKV files are supported, got: {video_file.suffix}")
        return False

    # Get subtitle tracks from MKV file
    subtitle_tracks = get_mkv_subtitle_tracks(video_file)

    if not subtitle_tracks:
        logger.error(f"No subtitle tracks found in {video_file}")
        return False

    # Select track based on mode (interactive or automatic)
    if interactive:
        selected_track = select_track_interactively(subtitle_tracks, console)
    else:
        selected_track = select_best_track_with_fallback(subtitle_tracks)

    if not selected_track:
        if interactive:
            logger.info("No subtitle track selected")
        else:
            logger.error(
                f"No suitable subtitle track found in {video_file} (checked preferred language tiers)"
            )
            logger.info("Available tracks:")
            for track in subtitle_tracks:
                title = track.get("tags", {}).get("title", "No title")
                language = track.get("tags", {}).get("language", "Unknown")
                logger.info(f"  Track {track['id']}: {title} ({language})")
        return False

    # Extract using ffmpeg
    return extract_track_with_progress(
        video_file, subtitle_file, selected_track["id"], show_progress, console
    )


def translate_subtitles(
    sub_file: Path,
    out_file: Path,
    config: MKVConfig,
    mode: TranslationMode,
    show_progress: bool = True,
    show_metrics: bool = True,
    parallel_batches: bool = False,
    parallel_workers: int = 4,
    max_batch_size_arg: int | None = None,
    min_batch_size_arg: int | None = None,
):
    """Translate subtitles using PySubtrans API"""
    # Map mode to provider name
    provider_map = {
        TranslationMode.GEMINI: "Gemini",
        TranslationMode.CHATGPT: "OpenAI",
        TranslationMode.CLAUDE: "Claude",
    }

    provider = provider_map.get(mode, "Gemini")
    model = MODE_TO_DEFAULT_MODEL[mode]
    max_context_summaries = int(os.getenv("MAX_CONTEXT_SUMMARIES") or 10)
    rate_limit: float | None = None

    # Check for large context mode
    # Default to True for Gemini, False for others, unless explicitly set
    env_large_context = os.getenv("LARGE_CONTEXT_MODE")
    if env_large_context is not None:
        large_context_mode = env_large_context.lower() in ("true", "1", "yes")
    else:
        large_context_mode = mode == TranslationMode.GEMINI

    if mode == TranslationMode.GEMINI:
        model = os.getenv("GEMINI_MODEL") or GEMINI_DEFAULT_MODEL
    elif mode == TranslationMode.CHATGPT:
        model = os.getenv("OPENAI_MODEL") or "gpt-5-mini"

    scene_threshold, min_batch_size, max_batch_size = _resolve_batch_sizes(
        mode,
        model,
        large_context_mode,
        parallel_batches,
        max_batch_size_arg,
        min_batch_size_arg,
    )

    if mode == TranslationMode.GEMINI:
        max_context_summaries = int(
            os.getenv("MAX_CONTEXT_SUMMARIES") or GEMINI_MAX_CONTEXT_SUMMARIES
        )

        use_vertex = _using_vertex()
        if use_vertex:
            # Vertex uses dynamic shared quota - there is no fixed RPM to respect;
            # transient 429s are absorbed by the client's exponential backoff
            rate_limit = None
        else:
            rate_limit = _determine_gemini_rate_limit(model)
            os.environ.setdefault("GEMINI_RATE_LIMIT", str(int(rate_limit)))

        settings_vertex: dict[str, str | bool] = {"use_vertex": use_vertex}
        if use_vertex:
            project = _vertex_project()
            location = _vertex_location()
            if project:
                settings_vertex["vertex_project"] = project
            if location:
                settings_vertex["vertex_location"] = location
    elif mode == TranslationMode.CHATGPT:
        max_context_summaries = int(os.getenv("MAX_CONTEXT_SUMMARIES") or 6)
        settings_vertex = {}
        try:
            base_rate = os.getenv("OPENAI_RATE_LIMIT") or MODE_TO_RATE_LIMIT.get(
                mode, ""
            )
            rate_limit = float(base_rate) if base_rate else None
        except ValueError:
            rate_limit = None
    else:
        rate_value = MODE_TO_RATE_LIMIT.get(mode)
        if rate_value:
            try:
                rate_limit = float(rate_value)
            except ValueError:
                rate_limit = None
        settings_vertex = {}

    # Determine temperature
    env_temp = os.getenv("LLM_TEMPERATURE")
    if mode == TranslationMode.GEMINI:
        env_temp = os.getenv("GEMINI_TEMPERATURE") or env_temp
    elif mode == TranslationMode.CHATGPT:
        env_temp = os.getenv("OPENAI_TEMPERATURE") or env_temp
    elif mode == TranslationMode.CLAUDE:
        env_temp = os.getenv("CLAUDE_TEMPERATURE") or env_temp

    # Gemini 3.x is documented to degrade (repetition loops) at reduced temperature, so it keeps
    # the model default; other providers benefit from a low temperature on a format-critical task.
    default_temperature = 1.0 if GEMINI_3_MODEL_PATTERN.search(model or "") else 0.3

    try:
        temperature = float(env_temp) if env_temp else default_temperature
    except ValueError:
        temperature = default_temperature

    # Create PySubtrans options
    settings = {
        "provider": provider,
        "target_language": config.target_language,
        "temperature": temperature,
        # Preprocessing is invoked explicitly below; postprocess_translation is read by
        # SubtitleTranslator to enable the SubtitleProcessor pass over the translation.
        "preprocess_subtitles": True,
        "postprocess_translation": True,
        # Source cues are already well formed, so preprocessing does per-line cleanup plus
        # continuation merging only: blind short-line merging and duration-splitting would
        # change cue structure unsafely, and splitting re-creates short display times.
        "merge_line_duration": 0.0,
        "max_line_duration": 0.0,
        # Rapid-fire fragments of one sentence are merged (text + timing atomically) before
        # translation, so each numbered line has a budget the model can actually meet.
        # Otherwise the model condenses across lines itself and desynchronises the timings.
        "merge_continuation_duration": 1.5,
        "merge_continuation_gap": 0.3,
        # Reject batches where a translation blows its display-time budget - the signature
        # of content shifted onto the wrong line number - and retry them.
        "max_translation_cps": 25.0,
        "break_long_lines": True,
        "max_single_line_length": 42,
        "min_single_line_length": 8,
        "normalise_dialog_tags": True,
        "break_dialog_on_one_line": True,
        "whitespaces_to_newline": False,
        # standard_filler_words is English-only, so it would not fire on the translation
        "remove_filler_words": False,
        # Give the model the duration and character budget for each line so it knows which
        # lines must be condensed - it cannot compute CPS otherwise.
        "include_line_timings": True,
        "target_cps": 15.0,
        "model": model,
        "scene_threshold": scene_threshold,
        "min_batch_size": min_batch_size,
        "max_batch_size": max_batch_size,
        "max_context_summaries": max_context_summaries,
        "large_context_mode": large_context_mode,
        "parallel_batches": parallel_batches,
        "max_parallel_workers": parallel_workers,
    }

    if rate_limit:
        settings["rate_limit"] = rate_limit

    if settings_vertex:
        settings.update(settings_vertex)

    # Set instruction file if it exists
    if config.instruction_file and config.instruction_file.exists():
        settings["instruction_file"] = str(config.instruction_file)

    # init_options loads the instruction file - constructing Options directly leaves the
    # instructions unset, silently falling back to the generic English defaults.
    options = init_options(**settings)

    # Display model information
    console.print("\n[bold cyan]Translation Configuration:[/bold cyan]")
    console.print(f"  Provider: {provider}")
    console.print(f"  Model: {model}")
    console.print(f"  Target Language: {config.target_language}")
    console.print(f"  Temperature: {temperature}")
    if rate_limit:
        console.print(f"  Rate Limit: {rate_limit:.0f} RPM")
    elif settings_vertex.get("use_vertex"):
        console.print("  Rate Limit: none (Vertex dynamic shared quota)")

    # Display instruction file info, and confirm the instructions were actually loaded
    if config.instruction_file and config.instruction_file.exists():
        console.print(f"  Instructions: {config.instruction_file}")
        loaded_instructions = options.get_str("instructions") or ""
        summary = " ".join(loaded_instructions.split())[:60]
        if summary:
            console.print(f"  [dim]Loaded: {summary}...[/dim]")
        else:
            console.print("  [yellow]Warning: instructions failed to load[/yellow]")
    elif config.instruction_file:
        console.print(
            f"  Instructions: {config.instruction_file} [yellow](not found)[/yellow]"
        )
    else:
        console.print("  Instructions: [dim]None[/dim]")

    console.print()

    # Create subtitle project
    project = SubtitleProject(persistent=False)
    project.InitialiseProject(str(sub_file), str(out_file))
    project.UpdateProjectSettings(options)

    # Preprocess explicitly: UpdateProjectSettings filters to DEFAULT_PROJECT_SETTINGS, so the
    # preprocess_subtitles flag never reaches anything that acts on it.
    if project.subtitles and project.subtitles.originals:
        preprocess_subtitles(project.subtitles, options)

    # Batch subtitles for translation
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

    # Translate
    translator = init_translator(options)
    metrics = (
        TranslationMetrics()
        if (show_metrics and mode == TranslationMode.GEMINI)
        else None
    )
    progress = TranslationProgress() if show_progress else None
    start_time = time.perf_counter()
    planned_output = Path(
        project.subtitles.outputpath if project.subtitles and project.subtitles.outputpath else out_file
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
        # path, so the episode is not skipped as done and fix-finnish-subs never sees them.
        # A file this run did not write (an earlier, finished translation) stays where it is.
        saved_path = project.subtitles.outputpath if project.subtitles else None
        partial_path = Path(saved_path) if saved_path else out_file
        if partial_path != planned_output or _output_signature(partial_path) != output_before:
            _set_aside_partial_output(partial_path)
        raise
    finally:
        if progress:
            progress.detach(translator, final=True)
        if metrics:
            metrics.detach(translator)
            elapsed = time.perf_counter() - start_time
            metrics.render(elapsed, total_lines, total_batches, rate_limit)


def process_directory(
    config: MKVConfig,
    mode: TranslationMode,
    interactive: bool,
    show_progress: bool,
    copy_local: bool = False,
    parallel_batches: bool = False,
    parallel_workers: int = 4,
    max_batch_size: int | None = None,
    min_batch_size: int | None = None,
    filter_subs: bool = True,
    show_metrics: bool = True,
) -> int:
    """Process all MKV files in the current directory. Return 1 if any file failed."""
    # Get all MKV files and sort them
    video_files = list(Path().glob("*.mkv"))

    # Filter out files that don't actually exist (symbolic links, etc.)
    existing_files = [f for f in video_files if f.exists() and f.is_file()]

    # Convert to VideoFile objects and sort
    sorted_files = sorted([VideoFile(f) for f in existing_files])

    if not sorted_files:
        logger.warning("No MKV files found in current directory")
        return 0

    console.print("\n[bold]Starting subtitle processing[/bold]")

    # Track statistics
    stats = {"processed": 0, "skipped": 0, "failed": 0}

    for video_file in sorted_files:
        try:
            subtitle_file = video_file.path.with_suffix(".srt")
            lang_code = config.get_language_code(config.target_language)
            translated_file = _build_translated_output_path(subtitle_file, lang_code)

            # Same rule as process_video_file: an empty file is a failed earlier run
            if translated_file.exists() and translated_file.stat().st_size > 0:
                console.print(
                    f"[yellow]⏭ Skipping[/yellow] {video_file.path.name} - translation exists"
                )
                stats["skipped"] += 1
            else:
                console.print(f"[blue]⚙ Processing[/blue] {video_file.path.name}")
                succeeded = process_video_file(
                    video_file.path,
                    config,
                    mode,
                    interactive,
                    show_progress,
                    show_metrics=show_metrics,
                    copy_local=copy_local,
                    parallel_batches=parallel_batches,
                    parallel_workers=parallel_workers,
                    max_batch_size=max_batch_size,
                    min_batch_size=min_batch_size,
                    filter_subs=filter_subs,
                )
                if succeeded:
                    stats["processed"] += 1
                    console.print(f"[green]✓ Completed[/green] {video_file.path.name}")
                else:
                    stats["failed"] += 1
                    console.print(f"[red]✗ Failed[/red] {video_file.path.name}")

        except IncompleteTranslationError as e:
            stats["failed"] += 1
            logger.error(f"Error processing {video_file.path.name}: {e}")
        except Exception as e:
            stats["failed"] += 1
            logger.exception(f"Error processing {video_file.path.name}: {e}")

        console.print("─" * 40)

    # Print summary
    console.print("\n[bold]Processing Summary[/bold]")
    console.print(f"Processed: {stats['processed']}")
    console.print(f"Skipped: {stats['skipped']}")
    console.print(f"Failed: {stats['failed']}")

    return 1 if stats["failed"] else 0


def main():
    parser = argparse.ArgumentParser(
        description="Extract and translate subtitles from MKV files"
    )
    parser.add_argument("file", nargs="?", help="MKV file to process (optional)")

    # Create a mutually exclusive group for translation modes
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--gemini",
        action="store_const",
        dest="mode",
        const=TranslationMode.GEMINI,
        help="Use Gemini model",
    )
    mode_group.add_argument(
        "--gpt",
        action="store_const",
        dest="mode",
        const=TranslationMode.CHATGPT,
        help="Use ChatGPT model",
    )
    mode_group.add_argument(
        "--claude",
        action="store_const",
        dest="mode",
        const=TranslationMode.CLAUDE,
        help="Use Claude model",
    )

    parser.add_argument("-l", "--language", help="Target language")
    parser.add_argument(
        "--no-filter",
        action="store_true",
        help="Disable subtitle filtering (preserve music cues, fonts, effects, etc.)",
    )
    parser.add_argument(
        "--no-progress", action="store_true", help="Disable progress bars"
    )
    parser.add_argument(
        "--no-metrics",
        action="store_true",
        help="Do not print end-of-run translation metrics",
    )
    parser.add_argument(
        "-i",
        "--interactive",
        action="store_true",
        help="Interactive mode - manually select subtitle track to extract",
    )
    parser.add_argument(
        "--diagnose",
        action="store_true",
        help="Run system diagnostics to identify performance bottlenecks",
    )
    parser.add_argument(
        "--setup-vertex",
        action="store_true",
        help="Verify Google Cloud CLI + ADC and write persistent Vertex defaults to your shell profile",
    )
    parser.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="Answer yes to prompts during --setup-vertex",
    )
    parser.add_argument(
        "--copy-local",
        action="store_true",
        help="Copy MKV file to local temporary directory before processing (useful for network mounts)",
    )
    parser.add_argument(
        "--parallel",
        action="store_true",
        help="Enable parallel batch translation (faster but disables inter-batch context and streaming)",
    )
    parser.add_argument(
        "--no-parallel",
        action="store_true",
        help="Disable parallel batch translation (force sequential mode)",
    )
    parser.add_argument(
        "--parallel-workers",
        type=int,
        default=None,
        help="Number of parallel workers for batch translation (default: 4, or 8 for Gemini 3.0)",
    )
    parser.add_argument(
        "--max-batch-size",
        type=int,
        help="Maximum lines per batch (default: 150 in parallel mode, else the mode default, "
        "600 for Gemini large-context mode); overrides MAX_BATCH_SIZE",
    )
    parser.add_argument(
        "--min-batch-size",
        type=int,
        help="Minimum lines per batch; overrides MIN_BATCH_SIZE",
    )
    args = parser.parse_args()

    # Load environment variables
    load_dotenv()

    # Run diagnostics if requested
    if args.diagnose:
        run_diagnostics(console)
        return 0

    # Vertex setup assistant
    if args.setup_vertex:
        return setup_vertex(yes=args.yes)

    # Initialize config
    if args.language:
        if args.language not in MKVConfig.SUPPORTED_LANGUAGES:
            logger.error(f"Unsupported language: {args.language}")
            logger.info(
                f"Supported languages: {', '.join(MKVConfig.SUPPORTED_LANGUAGES)}"
            )
            sys.exit(1)
        config = MKVConfig(target_language=args.language)
    else:
        config = MKVConfig()

    # Determine translation mode
    env_mode = _env_default_mode()
    mode = args.mode or env_mode or config.default_translation_mode

    # Gemini on Vertex runs parallel batches by default, as in transubs. Vertex has no RPM
    # limit to pace; a Gemini API key keeps sequential mode unless --parallel is given.
    if mode == TranslationMode.GEMINI and not args.no_parallel and _using_vertex():
        args.parallel = True

    # Set defaults for parallel mode if enabled. The 150-line parallel batch default is
    # applied in translate_subtitles, below explicit --max-batch-size and MAX_BATCH_SIZE.
    if args.parallel and args.parallel_workers is None:
        # Optimize for high-concurrency models (Gemini 3)
        args.parallel_workers = 8

    # Fallback default for workers if not parallel or not set
    if args.parallel_workers is None:
        args.parallel_workers = 4

    # Verify dependencies
    verify_dependencies(config.get_language_code(config.target_language))
    verify_api_key(mode)

    # Use file lock with proper error handling
    lock_dir = os.path.expanduser("~/.cache")
    os.makedirs(lock_dir, exist_ok=True)
    lock = FileLock(os.path.join(lock_dir, "exsubs.lock"))

    try:
        with lock.acquire(timeout=5):
            try:
                # Create processor with progress control
                show_progress = not args.no_progress
                show_metrics = not args.no_metrics

                filter_subs = not args.no_filter

                if args.file:
                    # Process single file
                    succeeded = process_video_file(
                        Path(args.file),
                        config,
                        mode,
                        args.interactive,
                        show_progress,
                        show_metrics,
                        copy_local=args.copy_local,
                        parallel_batches=args.parallel,
                        parallel_workers=args.parallel_workers,
                        max_batch_size=args.max_batch_size,
                        min_batch_size=args.min_batch_size,
                        filter_subs=filter_subs,
                    )
                    return 0 if succeeded else 1

                # Process all MKV files in current directory
                return process_directory(
                    config,
                    mode,
                    args.interactive,
                    show_progress,
                    copy_local=args.copy_local,
                    parallel_batches=args.parallel,
                    parallel_workers=args.parallel_workers,
                    max_batch_size=args.max_batch_size,
                    min_batch_size=args.min_batch_size,
                    filter_subs=filter_subs,
                    show_metrics=show_metrics,
                )

            except IncompleteTranslationError as e:
                logger.error(f"Processing error: {e}")
                return 1
            except Exception as e:
                logger.exception(f"Processing error: {e}")
                return 1
    except TimeoutError:
        logger.error("Could not acquire lock - another instance might be running")
        return 1
    except Exception as e:
        logger.exception(f"Unexpected error: {e}")
        return 1
    finally:
        if lock.is_locked:
            lock.release()


if __name__ == "__main__":
    raise SystemExit(main())
