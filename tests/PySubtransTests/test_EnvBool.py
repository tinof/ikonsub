"""Tests for env_bool: every script and provider reads 1/true/yes/on, in any case, as
true (owner decision D2.1, 2026-11-17)."""

import os
from unittest.mock import patch

from PySubtrans.Helpers.TestCases import LoggedTestCase
from PySubtrans.MKV.Config import TranslationMode
from PySubtrans.Options import env_bool
from PySubtrans.Providers.Provider_Gemini import GeminiProvider
from PySubtrans.SettingsType import SettingsType
from scripts import exsubs, transubs
from tests.PySubtransTests.test_CliPipeline import (
    BASE_ENV,
    CaptureExsubsSettings,
    CaptureTransubsSettings,
)

TRUE_SPELLINGS = ["1", "true", "TRUE", "True", "yes", "YES", "on", "On", " true "]
FALSE_SPELLINGS = ["0", "false", "False", "no", "off", "nope"]


class TestEnvBool(LoggedTestCase):
    """The helper itself, under a cleared environment."""

    def _read(self, value: str | None, default: bool) -> bool:
        env = {} if value is None else {"D22_FLAG": value}
        with patch.dict(os.environ, env, clear=True):
            return env_bool("D22_FLAG", default)

    def test_true_spellings(self):
        for value in TRUE_SPELLINGS:
            with self.subTest(value=value):
                self.assertLoggedTrue(f"env_bool({value!r})", self._read(value, False))

    def test_false_spellings(self):
        for value in FALSE_SPELLINGS:
            with self.subTest(value=value):
                self.assertLoggedFalse(f"env_bool({value!r})", self._read(value, True))

    def test_unset_or_empty_returns_default(self):
        for value in (None, "", "  "):
            for default in (True, False):
                with self.subTest(value=value, default=default):
                    self.assertLoggedEqual(
                        f"env_bool({value!r}, {default})",
                        default,
                        self._read(value, default),
                    )


class TestScriptsUseVertex(LoggedTestCase):
    """GEMINI_USE_VERTEX as exsubs and transubs parse it (unset means Vertex)."""

    def _use_vertex(self, value: str | None) -> dict[str, bool]:
        env = {} if value is None else {"GEMINI_USE_VERTEX": value}
        with (
            patch.object(exsubs, "load_dotenv"),
            patch.object(transubs, "load_dotenv"),
        ):
            return {
                "exsubs": CaptureExsubsSettings(TranslationMode.GEMINI, env)[
                    "use_vertex"
                ],
                "transubs": CaptureTransubsSettings("gemini", env)["use_vertex"],
            }

    def test_true_spellings(self):
        for value in TRUE_SPELLINGS:
            with self.subTest(value=value):
                self.assertLoggedEqual(
                    f"use_vertex for {value!r}",
                    {"exsubs": True, "transubs": True},
                    self._use_vertex(value),
                )

    def test_false_spellings(self):
        for value in FALSE_SPELLINGS:
            with self.subTest(value=value):
                self.assertLoggedEqual(
                    f"use_vertex for {value!r}",
                    {"exsubs": False, "transubs": False},
                    self._use_vertex(value),
                )

    def test_unset_defaults_to_vertex(self):
        self.assertLoggedEqual(
            "use_vertex unset",
            {"exsubs": True, "transubs": True},
            self._use_vertex(None),
        )


class TestGeminiProviderUseVertex(LoggedTestCase):
    """GEMINI_USE_VERTEX as Provider_Gemini parses it (unset means the API key)."""

    def _use_vertex(self, value: str | None) -> bool:
        env = {} if value is None else {"GEMINI_USE_VERTEX": value}
        # BASE_ENV sets VERTEX_PROJECT, so the provider does not shell out to gcloud
        with patch.dict(os.environ, {**BASE_ENV, **env}, clear=True):
            return GeminiProvider(SettingsType({})).use_vertex

    def test_true_spellings(self):
        for value in TRUE_SPELLINGS:
            with self.subTest(value=value):
                self.assertLoggedTrue(
                    f"provider use_vertex for {value!r}", self._use_vertex(value)
                )

    def test_false_spellings(self):
        for value in FALSE_SPELLINGS:
            with self.subTest(value=value):
                self.assertLoggedFalse(
                    f"provider use_vertex for {value!r}", self._use_vertex(value)
                )

    def test_unset_defaults_to_api_key(self):
        self.assertLoggedFalse("provider use_vertex unset", self._use_vertex(None))
