import json
import logging
import subprocess
import time
from pathlib import Path

from ffmpeg_progress_yield import FfmpegProgress
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)

logger = logging.getLogger("exsubs")

# ffprobe reads files on network mounts (nzbdav/rclone); a stalled read must not hang the run
FFPROBE_TIMEOUT_SECONDS = 120


def get_mkv_subtitle_tracks(video_file: Path) -> list[dict]:
    """Get subtitle tracks from MKV file using ffprobe"""
    try:
        if not video_file.exists():
            logger.error(f"File does not exist: {video_file}")
            return []

        # Use ffprobe to get stream info
        cmd = [
            "ffprobe",
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_streams",
            "-select_streams",
            "s",
            str(video_file),
        ]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True,
            timeout=FFPROBE_TIMEOUT_SECONDS,
        )

        try:
            info = json.loads(result.stdout)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse ffprobe JSON output: {e}")
            return []

        subtitle_tracks = []
        for stream in info.get("streams", []):
            # ffmpeg stream index
            idx = stream.get("index")

            track_info = {
                "id": idx,
                "codec": stream.get("codec_name"),
                "properties": {},  # kept for compatibility structure
                "tags": {},
                "disposition": {},
            }

            # Map tags
            tags = stream.get("tags", {})
            # ffprobe returns keys in lowercase usually, but let's be safe
            track_info["tags"]["language"] = tags.get("language", "und")
            track_info["tags"]["title"] = tags.get(
                "title", tags.get("handler_name", "")
            )

            # Map disposition
            disp = stream.get("disposition", {})
            track_info["disposition"]["forced"] = disp.get("forced") == 1
            track_info["disposition"]["hearing_impaired"] = (
                disp.get("hearing_impaired") == 1
            )

            subtitle_tracks.append(track_info)

        return subtitle_tracks

    except subprocess.CalledProcessError as e:
        logger.error(f"ffprobe command failed: {e}")
        return []
    except subprocess.TimeoutExpired:
        logger.error(
            f"ffprobe timed out after {FFPROBE_TIMEOUT_SECONDS}s reading {video_file}"
        )
        return []
    except Exception as e:
        logger.error(f"Failed to get subtitle tracks: {e}")
        return []


def select_track_interactively(tracks: list[dict], console) -> dict | None:
    """Allow user to manually select a subtitle track"""
    if not tracks:
        console.print("[red]No subtitle tracks found.[/red]")
        return None

    console.print("\n[bold]Available subtitle tracks:[/bold]")
    console.print()

    for i, track in enumerate(tracks, 1):
        track_id = track.get("id", "Unknown")
        title = track.get("tags", {}).get("title", "No title")
        language = track.get("tags", {}).get("language", "Unknown")
        disposition = track.get("disposition", {})

        flags = []
        if disposition.get("forced"):
            flags.append("Forced")
        if disposition.get("hearing_impaired"):
            flags.append("SDH")
        flag_str = f" [{', '.join(flags)}]" if flags else ""

        console.print(
            f"[cyan]{i:2d}.[/cyan] Stream {track_id}: [bold]{title}[/bold] ({language}){flag_str}"
        )

    console.print()

    while True:
        try:
            choice = console.input(
                f"Select track number (1-{len(tracks)}) or 'q' to quit: "
            )

            if choice.lower() == "q":
                console.print("[yellow]Cancelled by user.[/yellow]")
                return None

            track_num = int(choice)
            if 1 <= track_num <= len(tracks):
                selected_track = tracks[track_num - 1]
                return selected_track
            else:
                console.print(
                    f"[red]Please enter a number between 1 and {len(tracks)}[/red]"
                )

        except ValueError:
            console.print("[red]Please enter a valid number or 'q' to quit[/red]")
        except KeyboardInterrupt:
            console.print("\n[yellow]Cancelled by user.[/yellow]")
            return None


def select_best_track_with_fallback(tracks: list[dict]) -> dict | None:
    """
    Select best track based on robust priority list:
    1. Swedish (Nordic Gold)
    2. Norwegian & Danish
    3. German (Long Word)
    4. Dutch
    5. French (Romance)
    6. Spanish & Portuguese
    7. Italian
    8. Polish (Central European)
    9. Hungarian
    10. Turkish

    Fallback: English
    Avoid: Chinese, Japanese, Korean, Arabic, Hebrew (unless English is also missing, but user said stick to English)
    """

    # Priority tiers (ISO 639-1 and 639-2 codes)
    TIERS = [
        {"swe", "sv"},  # 1. Swedish
        {"nor", "no", "dan", "da"},  # 2. Norwegian & Danish
        {"ger", "deu", "de"},  # 3. German
        {"dut", "nld", "nl"},  # 4. Dutch
        {"fre", "fra", "fr"},  # 5. French
        {"spa", "es", "por", "pt"},  # 6. Spanish & Portuguese
        {"ita", "it"},  # 7. Italian
        {"pol", "pl"},  # 8. Polish
        {"hun", "hu"},  # 9. Hungarian
        {"tur", "tr"},  # 10. Turkish
        {"eng", "en"},  # Fallback
    ]

    def normalize_lang(lang: str) -> str:
        return lang.lower().strip()

    def is_text_track(track: dict) -> bool:
        codec = (track.get("codec") or "").lower()
        # ffmpeg codec names for text subs
        text_codecs = ["subrip", "srt", "ass", "ssa", "webvtt", "mov_text", "text"]
        return any(c in codec for c in text_codecs)

    # Filter for text tracks first
    text_tracks = [t for t in tracks if is_text_track(t)]

    if not text_tracks:
        logger.warning("No text-based subtitle tracks found (srt, ass, mov_text, etc).")
        return None

    candidate_tracks = text_tracks

    # Helper to find track in a specific language set
    def find_track_in_languages(langs: set[str]) -> dict | None:
        matches = []
        for track in candidate_tracks:
            lang = normalize_lang(track.get("tags", {}).get("language", ""))
            if lang in langs:
                matches.append(track)

        if not matches:
            return None

        # Sort matches:
        # Prioritize:
        # 1. Not Forced (Full dialogue)
        # 2. Not SDH (Cleaner)

        def score_track(t):
            disp = t.get("disposition", {})
            score = 10
            if disp.get("forced"):
                score -= 5
            if disp.get("hearing_impaired"):
                score -= 2
            return score

        matches.sort(key=score_track, reverse=True)
        return matches[0]

    # Iterate through tiers
    for i, tier in enumerate(TIERS, 1):
        match = find_track_in_languages(tier)
        if match:
            lang = match.get("tags", {}).get("language")
            logger.info(f"Selected track from Tier {i} ({lang}): Stream {match['id']}")
            return match

    logger.warning("No suitable track found in preferred tiers.")
    return None


def _get_video_duration(video_file: Path) -> float | None:
    """Get video duration in seconds using ffprobe."""
    try:
        cmd = [
            "ffprobe",
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_format",
            str(video_file),
        ]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True,
            timeout=FFPROBE_TIMEOUT_SECONDS,
        )
        info = json.loads(result.stdout)
        duration_str = info.get("format", {}).get("duration")
        if duration_str:
            return float(duration_str)
    except Exception as e:
        logger.debug(f"Could not get video duration: {e}")
    return None


def extract_track_with_progress(
    video_file: Path,
    subtitle_file: Path,
    track_id: int,
    show_progress: bool = True,
    console=None,
) -> bool:
    """Extract subtitle from MKV file using ffmpeg with progress bar.

    Uses ffmpeg-progress-yield for real-time progress feedback when a console is given,
    and a quiet subprocess run otherwise.
    """
    try:
        # Build ffmpeg command - note: we don't use -v error here to allow
        # ffmpeg-progress-yield to capture progress info
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(video_file),
            "-map",
            f"0:{track_id}",
            "-c:s",
            "subrip",
            str(subtitle_file),
        ]

        start_time = time.time()

        if show_progress and console:
            console.print(
                f"[blue]Extracting subtitle track {track_id} from {video_file.name}...[/blue]"
            )

            # Get video duration for progress override (subtitle streams may not report duration)
            duration = _get_video_duration(video_file)

            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
                TimeElapsedColumn(),
                console=console,
                transient=True,
            ) as progress:
                task = progress.add_task("Extracting", total=100)

                with FfmpegProgress(cmd) as ff:
                    for pct in ff.run_command_with_progress(duration_override=duration):
                        progress.update(task, completed=pct)

                    # Check for errors in stderr
                    stderr_output = ff.stderr or ""

            elapsed = time.time() - start_time

            # Verify file was created
            if not subtitle_file.exists() or subtitle_file.stat().st_size == 0:
                logger.error("Subtitle file was not created or is empty")
                if stderr_output:
                    logger.error(f"ffmpeg stderr: {stderr_output}")
                return False

            console.print(f"[green]✓ Extracted in {elapsed:.1f}s[/green]")
            return True

        # Simple extraction without progress
        # Use -v error to suppress output when not showing progress
        cmd_quiet = cmd.copy()
        cmd_quiet.insert(2, "-v")
        cmd_quiet.insert(3, "error")

        result = subprocess.run(cmd_quiet, capture_output=True, text=True)
        elapsed = time.time() - start_time

        if result.returncode != 0:
            logger.error(f"ffmpeg extraction failed: {result.stderr}")
            return False

        # Verify file
        if not subtitle_file.exists() or subtitle_file.stat().st_size == 0:
            logger.error("Subtitle file was not created or is empty")
            return False

        if show_progress and console:
            console.print(f"[green]✓ Extracted in {elapsed:.1f}s[/green]")

        return True

    except Exception as e:
        logger.error(f"Failed to extract subtitles with ffmpeg: {e}")
        return False
