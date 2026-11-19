"""Resume a failed translation from the batches an earlier run finished."""

import hashlib
import json
import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PySubtrans.Helpers.Localization import _
from PySubtrans.Options import Options
from PySubtrans.SubtitleBatch import SubtitleBatch
from PySubtrans.SubtitleError import SubtitleError
from PySubtrans.SubtitleLine import SubtitleLine
from PySubtrans.Subtitles import Subtitles
from PySubtrans.SubtitleScene import SubtitleScene
from PySubtrans.SubtitleSerialisation import SubtitleDecoder, SubtitleEncoder
from PySubtrans.SubtitleTranslator import SubtitleTranslator
from PySubtrans.version import __version__

# A failed run moves its output to "<name>.<lang>.srt.partial": no run treats it as a subtitle,
# so the episode is translated again. The batches it finished are kept beside it in
# "<name>.<lang>.srt.resume.json", and the next run sends only the batches still missing.
PARTIAL_OUTPUT_SUFFIX = ".partial"
RESUME_STATE_SUFFIX = ".resume.json"
RESUME_STATE_FORMAT = 1

# Settings that change the text of a finished batch or the prompts of the batches after it.
# A difference discards the saved batches. Temperature and thinking level are left out on
# purpose; batch sizes and the continuation pre-merge are covered by the layout check.
FINGERPRINT_KEYS = (
    "provider",
    "model",
    "target_language",
    "instructions",
    "retry_instructions",
    "prompt",
    "task_type",
    "movie_name",
    "description",
    "names",
    "substitutions",
    "substitution_mode",
    "parallel_batches",
    "large_context_mode",
    "max_context_summaries",
    "context_lines",
    "lookahead_lines",
    "include_line_timings",
    "target_cps",
    "max_translation_cps",
    "max_characters",
    "max_newlines",
    "postprocess_translation",
    "break_long_lines",
    "max_single_line_length",
    "min_single_line_length",
    "normalise_dialog_tags",
    "break_dialog_on_one_line",
    "whitespaces_to_newline",
    "remove_filler_words",
    "full_width_punctuation",
    "convert_wide_dashes",
)

_HASH_THRESHOLD = 80
_LOAD_ERRORS = (OSError, ValueError, KeyError, TypeError, AttributeError, SubtitleError)


@dataclass
class _SavedBatch:
    number: int
    summary: str | None
    originals: list[SubtitleLine]
    translated: list[SubtitleLine]

    @property
    def complete(self) -> bool:
        return bool(self.translated) and all(
            line.translation is not None for line in self.originals
        )


@dataclass
class _SavedScene:
    number: int
    summary: str | None
    batches: list[_SavedBatch]


class TranslationResume:
    """
    Keeps the batches a failed run finished and grafts them onto the next run.

    The next run loads, preprocesses and batches its source as usual. The saved batches are
    reused only when every batch's original lines and the settings fingerprint match, so the
    resumed lines sit on the same cue numbers as in an uninterrupted run.
    """

    def __init__(self, settings: Options, output_path: Path) -> None:
        self.settings = settings
        output_path = Path(output_path)
        self.state_path = output_path.with_name(output_path.name + RESUME_STATE_SUFFIX)
        self.partial_path = output_path.with_name(
            output_path.name + PARTIAL_OUTPUT_SUFFIX
        )
        self.restored: int = 0
        self._finished: set[tuple[int, int]] = set()
        self._lock = threading.Lock()

    def Restore(self, subtitles: Subtitles | None) -> int:
        """Graft the saved finished batches onto batched subtitles; return how many."""
        if not subtitles or not self.state_path.exists():
            return 0

        if not self.partial_path.exists():
            self._ignore(_("its partial translation is gone"))
            return 0

        try:
            fingerprint, saved_scenes = self._load_state()
        except _LOAD_ERRORS as e:
            self._ignore(_("it cannot be read ({error})").format(error=str(e)))
            return 0

        changed = self._changed_settings(fingerprint)
        if changed:
            self._ignore(
                _("these settings changed: {keys}").format(keys=", ".join(changed))
            )
            return 0

        if not _layout_matches(subtitles.scenes, saved_scenes):
            self._ignore(_("the source subtitles or their batches changed"))
            return 0

        self.restored = self._graft(subtitles.scenes, saved_scenes)
        return self.restored

    def Attach(self, translator: SubtitleTranslator) -> None:
        """Track finished batches, and skip the restored ones."""
        translator.events.batch_translated.connect(self._on_batch_translated)
        if self.restored:
            translator.resume = not (translator.reparse or translator.retranslate)

    def Save(self, subtitles: Subtitles | None) -> None:
        """Write the finished batches beside the partial translation. Never raises."""
        with self._lock:
            finished = set(self._finished)

        # Nothing new to keep: leave an earlier state file alone. It is reused only when the
        # source and settings match again, and a successful run removes it.
        if not subtitles or not finished or not self.partial_path.exists():
            return

        scenes = [_scene_state(scene, finished) for scene in subtitles.scenes]
        if not any(
            batch["translated"] for scene in scenes for batch in scene["batches"]
        ):
            return

        payload = {
            "format": RESUME_STATE_FORMAT,
            "fingerprint": self._fingerprint(),
            "scenes": scenes,
        }
        temp_path = self.state_path.with_name(self.state_path.name + ".tmp")
        try:
            with open(temp_path, "w", encoding="utf-8") as file:
                json.dump(payload, file, cls=SubtitleEncoder, ensure_ascii=False)
            temp_path.replace(self.state_path)
        except (OSError, TypeError, ValueError) as e:
            logging.warning(
                _("Could not save resume state {file}: {error}").format(
                    file=self.state_path.name, error=str(e)
                )
            )
            self._remove(temp_path)

    def Clear(self) -> None:
        """Remove the resume state and the partial translation after a successful run."""
        self._remove(self.state_path)
        self._remove(self.partial_path)

    def _on_batch_translated(self, _sender: Any, batch: SubtitleBatch, **_kwargs: Any):
        with self._lock:
            self._finished.add((batch.scene, batch.number))

    def _load_state(self) -> tuple[dict[str, Any], list[_SavedScene]]:
        with open(self.state_path, encoding="utf-8") as file:
            data = json.load(file, cls=SubtitleDecoder)

        if not isinstance(data, dict) or data.get("format") != RESUME_STATE_FORMAT:
            raise ValueError(_("unsupported format"))

        fingerprint = data["fingerprint"]
        if not isinstance(fingerprint, dict):
            raise TypeError(_("fingerprint is not an object"))

        scenes = [_parse_scene(scene) for scene in _as_list(data["scenes"])]
        return fingerprint, scenes

    def _fingerprint(self) -> dict[str, str]:
        values: dict[str, Any] = {
            key: self.settings.get(key) for key in FINGERPRINT_KEYS
        }
        if not values["model"]:
            provider_settings = self.settings.current_provider_settings
            values["model"] = (
                provider_settings.get("model") if provider_settings else None
            )
        values["version"] = __version__
        return {key: _fingerprint_value(value) for key, value in values.items()}

    def _changed_settings(self, saved: dict[str, Any]) -> list[str]:
        current = self._fingerprint()
        return sorted(key for key in current if saved.get(key) != current[key])

    def _graft(
        self, scenes: list[SubtitleScene], saved_scenes: list[_SavedScene]
    ) -> int:
        grafted = 0
        for scene, saved_scene in zip(scenes, saved_scenes, strict=True):
            restored_all = True
            for batch, saved in zip(scene.batches, saved_scene.batches, strict=True):
                if not saved.complete:
                    restored_all = False
                    continue

                for line, saved_line in zip(
                    batch.originals, saved.originals, strict=True
                ):
                    line.translation = saved_line.translation
                batch.translated = saved.translated
                batch.summary = saved.summary
                with self._lock:
                    self._finished.add((batch.scene, batch.number))
                grafted += 1

            if restored_all and saved_scene.summary:
                scene.summary = saved_scene.summary
        return grafted

    def _ignore(self, reason: str) -> None:
        logging.warning(
            _("Ignoring resume state {file}: {reason}; translating every batch").format(
                file=self.state_path.name, reason=reason
            )
        )

    @staticmethod
    def _remove(path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except OSError as e:
            logging.warning(
                _("Could not remove {file}: {error}").format(
                    file=path.name, error=str(e)
                )
            )


def _scene_state(
    scene: SubtitleScene, finished: set[tuple[int, int]]
) -> dict[str, Any]:
    return {
        "number": scene.number,
        "summary": scene.summary,
        "batches": [
            _batch_state(batch, (batch.scene, batch.number) in finished)
            for batch in scene.batches
        ],
    }


def _batch_state(batch: SubtitleBatch, finished: bool) -> dict[str, Any]:
    """Every batch keeps its originals for the layout check; only complete ones keep text."""
    complete = finished and batch.all_translated and bool(batch.translated)
    originals = (
        batch.originals
        if complete
        else [_untranslated(line) for line in batch.originals]
    )
    return {
        "number": batch.number,
        "summary": batch.summary if complete else None,
        "originals": originals,
        "translated": batch.translated if complete else [],
    }


def _untranslated(line: SubtitleLine) -> SubtitleLine:
    copy = line.copy()
    copy.translation = None
    return copy


def _layout_matches(
    scenes: list[SubtitleScene], saved_scenes: list[_SavedScene]
) -> bool:
    if len(scenes) != len(saved_scenes):
        return False

    for scene, saved_scene in zip(scenes, saved_scenes, strict=True):
        if scene.number != saved_scene.number or scene.size != len(saved_scene.batches):
            return False
        for batch, saved in zip(scene.batches, saved_scene.batches, strict=True):
            if batch.number != saved.number or batch.originals != saved.originals:
                return False
    return True


def _parse_scene(data: Any) -> _SavedScene:
    if not isinstance(data, dict):
        raise TypeError(_("scene is not an object"))
    return _SavedScene(
        number=int(data["number"]),
        summary=_optional_str(data.get("summary")),
        batches=[_parse_batch(batch) for batch in _as_list(data["batches"])],
    )


def _parse_batch(data: Any) -> _SavedBatch:
    if not isinstance(data, dict):
        raise TypeError(_("batch is not an object"))
    return _SavedBatch(
        number=int(data["number"]),
        summary=_optional_str(data.get("summary")),
        originals=_parse_lines(data["originals"]),
        translated=_parse_lines(data["translated"]),
    )


def _parse_lines(data: Any) -> list[SubtitleLine]:
    lines = _as_list(data)
    if not all(isinstance(line, SubtitleLine) for line in lines):
        raise TypeError(_("subtitle line is not a line"))
    return lines


def _as_list(data: Any) -> list[Any]:
    if not isinstance(data, list):
        raise TypeError(_("expected a list"))
    return data


def _optional_str(value: Any) -> str | None:
    if value is not None and not isinstance(value, str):
        raise TypeError(_("summary is not text"))
    return value


def _fingerprint_value(value: Any) -> str:
    """A readable value, or a hash for long ones such as the instructions."""
    text = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    if len(text) > _HASH_THRESHOLD:
        return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()
    return text
