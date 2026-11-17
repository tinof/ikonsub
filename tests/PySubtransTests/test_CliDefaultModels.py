"""Every CLI's default model is the shared constant in PySubtrans.MKV.Config, and the
OPENAI_MODEL, CLAUDE_MODEL and GEMINI_MODEL overrides still win."""

import contextlib
import io
import os
import sys
from types import ModuleType
from typing import Any
from unittest.mock import patch

from PySubtrans.Helpers.TestCases import LoggedTestCase
from PySubtrans.MKV.Config import (
    CLAUDE_DEFAULT_MODEL,
    GEMINI_DEFAULT_MODEL,
    MODE_TO_DEFAULT_MODEL,
    OPENAI_DEFAULT_MODEL,
    TranslationMode,
)
from scripts import (
    batch_translate,
    claude_subtrans,
    exsubs,
    gemini_subtrans,
    gpt_subtrans,
    transubs,
)
from tests.PySubtransTests.test_CliPipeline import (
    BASE_ENV,
    CaptureExsubsSettings,
    CaptureTransubsSettings,
    _SettingsCaptured,
)

# (mode, transubs provider flag, *_subtrans module, model env var, shared constant)
CASES: list[tuple[TranslationMode, str, ModuleType, str, str]] = [
    (
        TranslationMode.CHATGPT,
        "gpt",
        gpt_subtrans,
        "OPENAI_MODEL",
        OPENAI_DEFAULT_MODEL,
    ),
    (
        TranslationMode.CLAUDE,
        "claude",
        claude_subtrans,
        "CLAUDE_MODEL",
        CLAUDE_DEFAULT_MODEL,
    ),
    (
        TranslationMode.GEMINI,
        "gemini",
        gemini_subtrans,
        "GEMINI_MODEL",
        GEMINI_DEFAULT_MODEL,
    ),
]

OVERRIDE_MODEL = "override-model"


def CaptureSubtransModel(module: ModuleType, env: dict[str, str] | None = None) -> str:
    """Run a provider's *_subtrans CLI up to CreateOptions and return the model it passes"""
    captured: dict[str, Any] = {}

    def fake_create_options(_args: Any, _provider: str, **kwargs: Any):
        captured.update(kwargs)
        raise _SettingsCaptured()

    with (
        patch.dict(os.environ, {**BASE_ENV, **(env or {})}, clear=True),
        patch.object(sys, "argv", [module.__name__, "episode.srt"]),
        patch.object(module, "check_required_imports"),
        patch.object(module, "InitLogger"),
        patch.object(module, "CreateOptions", side_effect=fake_create_options),
        contextlib.redirect_stdout(io.StringIO()),
    ):
        module.main()

    if "model" not in captured:
        raise AssertionError(f"{module.__name__} passed no model to CreateOptions")
    return captured["model"]


def CaptureModels(
    mode: TranslationMode,
    flag: str,
    module: ModuleType,
    env: dict[str, str] | None = None,
) -> dict[str, str]:
    """The model exsubs, transubs and the provider's *_subtrans CLI pick under env"""
    with (
        patch.object(exsubs, "load_dotenv"),
        patch.object(transubs, "load_dotenv"),
    ):
        return {
            "exsubs": CaptureExsubsSettings(mode, env)["model"],
            "transubs": CaptureTransubsSettings(flag, env)["model"],
            module.__name__: CaptureSubtransModel(module, env),
        }


class TestCliDefaultModels(LoggedTestCase):
    """The CLIs read their default model from one place, so the defaults cannot drift."""

    def test_mode_map_uses_shared_constants(self):
        for mode, _flag, _module, _env_var, constant in CASES:
            self.assertLoggedEqual(
                f"MODE_TO_DEFAULT_MODEL[{mode.value}]",
                constant,
                MODE_TO_DEFAULT_MODEL[mode],
            )

    def test_cli_default_equals_shared_constant(self):
        for mode, flag, module, _env_var, constant in CASES:
            for cli, model in CaptureModels(mode, flag, module).items():
                self.assertLoggedEqual(f"{cli} {mode.value} model", constant, model)

    def test_model_env_var_wins(self):
        for mode, flag, module, env_var, _constant in CASES:
            env = {env_var: OVERRIDE_MODEL}
            for cli, model in CaptureModels(mode, flag, module, env).items():
                self.assertLoggedEqual(
                    f"{cli} {mode.value} model with {env_var}", OVERRIDE_MODEL, model
                )

    def test_batch_translate_default_equals_shared_constant(self):
        self.assertLoggedEqual(
            "batch_translate default model",
            GEMINI_DEFAULT_MODEL,
            batch_translate.DEFAULT_OPTIONS.get_str("model"),
        )
