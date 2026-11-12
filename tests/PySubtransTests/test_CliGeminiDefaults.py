"""Tests for the Gemini defaults of exsubs, transubs and gemini-subtrans: model, Vertex
location and the thinking level requested on Gemini 4."""

import contextlib
import io
import os
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from PySubtrans.Helpers.TestCases import LoggedTestCase
from PySubtrans.MKV.Config import MODE_TO_DEFAULT_MODEL, MKVConfig, TranslationMode
from scripts import exsubs, gemini_subtrans, transubs
from tests.PySubtransTests.test_CliPipeline import (
    BASE_ENV,
    CaptureExsubsSettings,
    CaptureTransubsSettings,
    _SettingsCaptured,
)

DEFAULT_MODEL = "gemini-4-hydrogen"
GEMINI_3: dict[str, str] = {"GEMINI_MODEL": "gemini-3.8-flash"}
HIGH_THINKING: dict[str, str] = {"GEMINI_THINKING_LEVEL": "high"}


def _printed(console_print: MagicMock) -> list[str]:
    return [
        str(call.args[0]).strip() for call in console_print.call_args_list if call.args
    ]


def CaptureGeminiSubtransSettings(env: dict[str, str] | None = None) -> dict[str, Any]:
    """Run gemini-subtrans up to CreateOptions and return the settings it passes"""
    captured: dict[str, Any] = {}

    def fake_create_options(_args: Any, provider: str, **kwargs: Any):
        captured.update(kwargs, provider=provider)
        raise _SettingsCaptured()

    with (
        patch.dict(os.environ, {**BASE_ENV, **(env or {})}, clear=True),
        patch.object(sys, "argv", ["gemini-subtrans", "episode.srt"]),
        patch.object(gemini_subtrans, "check_required_imports"),
        patch.object(gemini_subtrans, "InitLogger"),
        patch.object(gemini_subtrans, "CreateOptions", side_effect=fake_create_options),
        contextlib.redirect_stdout(io.StringIO()),
    ):
        gemini_subtrans.main()

    if not captured:
        raise AssertionError("CreateOptions was not called")
    return captured


class TestGeminiDefaults(LoggedTestCase):
    """gemini-4-hydrogen in location global, with thinking level low on Gemini 4."""

    def _both(self, env: dict[str, str] | None = None) -> dict[str, dict[str, Any]]:
        return {
            "exsubs": CaptureExsubsSettings(TranslationMode.GEMINI, env),
            "transubs": CaptureTransubsSettings("gemini", env),
        }

    def test_default_model(self):
        self.assertLoggedEqual(
            "MODE_TO_DEFAULT_MODEL",
            DEFAULT_MODEL,
            MODE_TO_DEFAULT_MODEL[TranslationMode.GEMINI],
        )
        for name, module in (("exsubs", exsubs), ("transubs", transubs)):
            self.assertLoggedEqual(
                f"{name} GEMINI_DEFAULT_MODEL",
                DEFAULT_MODEL,
                module.GEMINI_DEFAULT_MODEL,
            )
        for name, settings in self._both().items():
            self.assertLoggedEqual(f"{name} model", DEFAULT_MODEL, settings["model"])

    def test_vertex_location_defaults_to_global(self):
        for name, settings in self._both().items():
            self.assertLoggedEqual(
                f"{name} vertex_location", "global", settings["vertex_location"]
            )

    def test_vertex_location_env_wins(self):
        for name, settings in self._both({"VERTEX_LOCATION": "us"}).items():
            self.assertLoggedEqual(
                f"{name} vertex_location", "us", settings["vertex_location"]
            )

    def test_thinking_level_low_on_gemini_4(self):
        for name, settings in self._both().items():
            self.assertLoggedEqual(
                f"{name} thinking_level", "low", settings.get("thinking_level")
            )

    def test_no_thinking_level_on_gemini_3(self):
        for name, settings in self._both(GEMINI_3).items():
            self.assertLoggedNotIn(f"{name} thinking_level", "thinking_level", settings)

    def test_thinking_level_env_wins(self):
        for name, settings in self._both(HIGH_THINKING).items():
            self.assertLoggedEqual(
                f"{name} thinking_level", "high", settings.get("thinking_level")
            )

    def test_no_thinking_level_outside_gemini_mode(self):
        exsubs_gpt = CaptureExsubsSettings(TranslationMode.CHATGPT, HIGH_THINKING)
        transubs_gpt = CaptureTransubsSettings("gpt", HIGH_THINKING)
        self.assertLoggedNotIn("exsubs gpt", "thinking_level", exsubs_gpt)
        self.assertLoggedNotIn("transubs gpt", "thinking_level", transubs_gpt)

    def test_exsubs_prints_thinking_level(self):
        with (
            patch.dict(os.environ, BASE_ENV, clear=True),
            patch.object(exsubs, "SubtitleProject", side_effect=_SettingsCaptured),
            patch.object(exsubs.console, "print") as console_print,
        ):
            with self.assertRaises(_SettingsCaptured):
                exsubs.translate_subtitles(
                    Path("episode.srt"),
                    Path("episode.fi.srt"),
                    MKVConfig(),
                    TranslationMode.GEMINI,
                    show_progress=False,
                    show_metrics=False,
                )
        printed = _printed(console_print)
        self.assertLoggedIn("thinking line", "Thinking level: low", printed)
        self.assertLoggedIn(
            "temperature line", "Temperature: 1.0 (ignored by Gemini 4)", printed
        )

    def test_transubs_prints_thinking_level(self):
        with patch.object(transubs.console, "print") as console_print:
            CaptureTransubsSettings("gemini")
        printed = _printed(console_print)
        self.assertLoggedIn("thinking line", "Thinking level: low", printed)
        self.assertLoggedIn(
            "temperature line", "Temperature: 1.0 (ignored by Gemini 4)", printed
        )

    def test_gemini_subtrans_defaults(self):
        settings = CaptureGeminiSubtransSettings()
        self.assertLoggedEqual("model", DEFAULT_MODEL, settings["model"])
        self.assertLoggedEqual("thinking_level", "low", settings["thinking_level"])

    def test_gemini_subtrans_thinking_level(self):
        gemini_3 = CaptureGeminiSubtransSettings(GEMINI_3)
        high = CaptureGeminiSubtransSettings(HIGH_THINKING)
        self.assertLoggedIsNone("gemini 3 thinking_level", gemini_3["thinking_level"])
        self.assertLoggedEqual("env thinking_level", "high", high["thinking_level"])
