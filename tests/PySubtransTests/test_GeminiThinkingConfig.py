from __future__ import annotations

import os
import warnings
from unittest.mock import MagicMock, patch

from google.genai import errors as genai_errors
from google.genai.types import ThinkingConfig

from PySubtrans import init_options, init_translator
from PySubtrans.Helpers.TestCases import LoggedTestCase
from PySubtrans.Providers.Clients.GeminiClient import GeminiClient, _resolve_thinking
from PySubtrans.Providers.Provider_Gemini import GeminiProvider
from PySubtrans.SettingsType import SettingsType
from PySubtrans.SubtitleError import TranslationImpossibleError
from PySubtrans.TranslationPrompt import TranslationPrompt
from PySubtrans.TranslationRequest import TranslationRequest

FALLBACK_CHAIN = "gemini-4-argon@us,gemini-3.8-flash@us"
CHAIN_MODELS = ["gemini-4-hydrogen", "gemini-4-argon", "gemini-3.8-flash"]


def _thinking(config: ThinkingConfig | None) -> tuple[str | None, int | None]:
    """The level (as the SDK value, e.g. "LOW") and budget a thinking config sends"""
    if config is None:
        return None, None
    level = getattr(config.thinking_level, "value", config.thinking_level)
    return level, config.thinking_budget


class GeminiThinkingConfigTests(LoggedTestCase):
    """Validate how GeminiClient resolves thinking_level vs thinking_budget."""

    def _make_client(self, **extra) -> GeminiClient:
        settings = SettingsType(
            {
                "instructions": "Verify thinking config.",
                "model": "gemini-3.5-flash",
                **extra,
            }
        )
        return GeminiClient(settings)

    def test_thinking_level_takes_precedence(self) -> None:
        """A configured thinking_level produces a level-based ThinkingConfig (Gemini 3.x)."""
        client = self._make_client(thinking_level="low")
        self.assertLoggedIsNotNone("thinking_config", client.thinking_config)
        assert client.thinking_config is not None
        # The SDK normalises the string into the ThinkingLevel enum (e.g. ThinkingLevel.LOW).
        level = getattr(
            client.thinking_config.thinking_level,
            "value",
            client.thinking_config.thinking_level,
        )
        self.assertLoggedEqual("thinking_level", "low", str(level).lower())
        # thinking_budget and thinking_level are mutually exclusive (HTTP 400 if both)
        self.assertLoggedIsNone(
            "thinking_budget", client.thinking_config.thinking_budget
        )

    def test_default_leaves_dynamic_thinking(self) -> None:
        """Without any thinking setting the config is None (model default dynamic thinking)."""
        client = self._make_client()
        self.assertLoggedIsNone("thinking_config", client.thinking_config)

    def test_budget_path_when_enabled(self) -> None:
        """enable_thinking without a level uses the integer budget (Gemini 2.5.x)."""
        client = self._make_client(enable_thinking=True, thinking_budget=1024)
        self.assertLoggedIsNotNone("thinking_config", client.thinking_config)
        assert client.thinking_config is not None
        self.assertLoggedEqual(
            "thinking_budget", 1024, client.thinking_config.thinking_budget
        )
        self.assertLoggedIsNone("thinking_level", client.thinking_config.thinking_level)

    def test_zero_budget_turns_thinking_off(self) -> None:
        """thinking_budget=0 reaches ThinkingConfig as 0, not -1 (dynamic)."""
        client = self._make_client(enable_thinking=True, thinking_budget=0)
        assert client.thinking_config is not None
        self.assertLoggedEqual(
            "thinking_budget", 0, client.thinking_config.thinking_budget
        )

    def test_explicit_budget_sent_without_enable_thinking(self) -> None:
        """An explicit budget is sent even when enable_thinking is not set."""
        client = self._make_client(thinking_budget=0)
        self.assertLoggedIsNotNone("thinking_config", client.thinking_config)
        assert client.thinking_config is not None
        self.assertLoggedEqual(
            "thinking_budget", 0, client.thinking_config.thinking_budget
        )

    def test_provider_keeps_zero_budget(self) -> None:
        """GEMINI_THINKING_BUDGET=0 survives the provider settings instead of becoming -1."""
        with patch.dict("os.environ", {"GEMINI_THINKING_BUDGET": "0"}):
            provider = GeminiProvider(SettingsType({}))
        self.assertLoggedEqual(
            "provider thinking_budget", 0, provider.settings.get_int("thinking_budget")
        )

    def test_provider_defaults_to_dynamic_budget(self) -> None:
        """Without any budget configured the provider keeps -1 (dynamic)."""
        with patch.dict("os.environ", {}) as environ:
            environ.pop("GEMINI_THINKING_BUDGET", None)
            provider = GeminiProvider(SettingsType({}))
        self.assertLoggedEqual(
            "provider thinking_budget", -1, provider.settings.get_int("thinking_budget")
        )

    def test_provider_location_defaults_to_global(self) -> None:
        """Without VERTEX_LOCATION the provider uses global, where gemini-4-hydrogen runs"""
        with patch.dict(os.environ, {"VERTEX_PROJECT": "p"}, clear=True):
            provider = GeminiProvider(SettingsType({}))
        self.assertLoggedEqual("vertex_location", "global", provider.vertex_location)

    def test_provider_location_env_wins(self) -> None:
        """VERTEX_LOCATION overrides the default location"""
        with patch.dict(
            os.environ, {"VERTEX_PROJECT": "p", "VERTEX_LOCATION": "us"}, clear=True
        ):
            provider = GeminiProvider(SettingsType({}))
        self.assertLoggedEqual("vertex_location", "us", provider.vertex_location)


class GeminiTargetThinkingTests(LoggedTestCase):
    """Each target model gets the thinking setting it accepts (Gemini 4 rules)."""

    def _make_client(self, **extra) -> GeminiClient:
        settings = SettingsType(
            {
                "instructions": "Verify per-target thinking.",
                "model": "gemini-4-hydrogen",
                "vertex_location": "global",
                "fallbacks": FALLBACK_CHAIN,
                "attempts_per_target": 1,
                "max_retries": 2,
                "backoff_time": 0.0,
                **extra,
            }
        )
        return GeminiClient(settings)

    def _make_request(self) -> TranslationRequest:
        prompt = TranslationPrompt("Translate this", False)
        prompt.content = "1. Hello"
        return TranslationRequest(prompt, None)

    def _sent_thinking(
        self, **extra
    ) -> list[tuple[str, tuple[str | None, int | None]]]:
        """Run the fallback chain against a failing SDK and return what each call sent"""
        client = self._make_client(**extra)
        sdk = MagicMock()
        sdk.models.generate_content.side_effect = genai_errors.APIError(
            429, {"error": {"message": "resource exhausted"}}
        )
        with (
            patch.object(GeminiClient, "_get_client", return_value=sdk),
            patch("PySubtrans.Providers.Clients.GeminiClient.time.sleep"),
        ):
            with self.assertRaises(TranslationImpossibleError):
                client._send_messages(self._make_request(), temperature=1.0)

        return [
            (call.kwargs["model"], _thinking(call.kwargs["config"].thinking_config))
            for call in sdk.models.generate_content.call_args_list
        ]

    def test_resolve_thinking_rules(self) -> None:
        """The rule table: (model, level, budget) -> (level, budget) sent"""
        cases = [
            ("gemini-4-hydrogen", "xhigh", None, ("xhigh", None)),
            ("gemini-4-argon", "xhigh", None, ("high", None)),
            ("gemini-3.8-flash", "xhigh", None, ("high", None)),
            ("gemini-2.5-flash", "xhigh", None, (None, None)),
            ("gemini-4-hydrogen", "minimal", None, ("low", None)),
            ("gemini-3.8-flash", "minimal", None, ("minimal", None)),
            ("gemini-4-argon", "medium", 1024, ("medium", None)),
            ("gemini-4-hydrogen", None, -1, (None, None)),
            ("gemini-4-hydrogen", None, 0, ("low", None)),
            ("gemini-4-hydrogen", None, 1024, ("low", None)),
            ("gemini-4-hydrogen", None, 1025, ("medium", None)),
            ("gemini-4-hydrogen", None, 8192, ("medium", None)),
            ("gemini-4-hydrogen", None, 8193, ("high", None)),
            ("gemini-3.8-flash", None, 1024, (None, 1024)),
            ("gemini-2.5-flash", "low", 0, (None, 0)),
            ("gemini-2.5-flash", "low", None, (None, None)),
            ("models/gemini-4-hydrogen", "xhigh", None, ("xhigh", None)),
            ("publishers/google/models/gemini-4-argon", "minimal", None, ("low", None)),
            ("models/gemini-4-argon", None, 2048, ("medium", None)),
            ("gemini-flash-latest", "minimal", None, ("minimal", None)),
            ("gemini-flash-latest", "xhigh", None, ("high", None)),
            ("gemini-flash-latest", None, 2048, (None, 2048)),
            ("gemini-4-hydrogen", None, None, (None, None)),
        ]
        for model, level, budget, expected in cases:
            with self.subTest(model=model, level=level, budget=budget):
                self.assertLoggedEqual(
                    f"{model} level={level} budget={budget}",
                    expected,
                    _resolve_thinking(model, level, budget),
                )

    def test_fallback_chain_xhigh(self) -> None:
        """xhigh reaches hydrogen only; the fallbacks get high"""
        sent = self._sent_thinking(thinking_level="xhigh")
        self.assertLoggedEqual(
            "sent per target",
            list(
                zip(
                    CHAIN_MODELS,
                    [("XHIGH", None), ("HIGH", None), ("HIGH", None)],
                    strict=True,
                )
            ),
            sent,
        )

    def test_fallback_chain_minimal(self) -> None:
        """minimal becomes low on Gemini 4 and stays minimal on Gemini 3"""
        sent = self._sent_thinking(thinking_level="minimal")
        self.assertLoggedEqual(
            "sent per target",
            list(
                zip(
                    CHAIN_MODELS,
                    [("LOW", None), ("LOW", None), ("MINIMAL", None)],
                    strict=True,
                )
            ),
            sent,
        )

    def test_fallback_chain_budget(self) -> None:
        """A budget becomes a level on Gemini 4 and stays a budget on Gemini 3"""
        sent = self._sent_thinking(thinking_budget=1024)
        self.assertLoggedEqual(
            "sent per target",
            list(
                zip(
                    CHAIN_MODELS,
                    [("LOW", None), ("LOW", None), (None, 1024)],
                    strict=True,
                )
            ),
            sent,
        )

    def test_primary_thinking_config_is_the_primary_target(self) -> None:
        """client.thinking_config stays the configured model's config"""
        client = self._make_client(thinking_level="xhigh")
        self.assertLoggedEqual(
            "primary thinking", ("XHIGH", None), _thinking(client.thinking_config)
        )

    def test_adjusted_targets_warn_once(self) -> None:
        """Each adjusted model warns once, when the client is built"""
        with patch.object(GeminiClient, "_emit_warning") as warn:
            client = self._make_client(thinking_level="xhigh")
            self.assertLoggedEqual("warnings at init", 2, warn.call_count)
            messages = [call.args[0] for call in warn.call_args_list]
            self.assertLoggedIn("argon warned", "gemini-4-argon", messages[0])
            self.assertLoggedIn("3.8-flash warned", "gemini-3.8-flash", messages[1])
            self.assertLoggedIn("describes the change", "using level high", messages[0])

            for model in CHAIN_MODELS:
                client._thinking_config_for(model)
            self.assertLoggedEqual("warnings after lookups", 2, warn.call_count)

    def test_accepted_level_does_not_warn(self) -> None:
        """low is valid on every target, so nothing warns"""
        with patch.object(GeminiClient, "_emit_warning") as warn:
            self._make_client(thinking_level="low")
        self.assertLoggedEqual("warnings", 0, warn.call_count)

    def test_level_with_budget_does_not_warn(self) -> None:
        """A level winning over a budget is the normal precedence, not an adjustment"""
        with patch.object(GeminiClient, "_emit_warning") as warn:
            self._make_client(
                thinking_level="low", enable_thinking=True, thinking_budget=1024
            )
        self.assertLoggedEqual("warnings", 0, warn.call_count)

    def test_xhigh_hides_the_sdk_warning(self) -> None:
        """The SDK's "XHIGH is not a valid ThinkingLevel" UserWarning is not shown"""
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            self._make_client(thinking_level="xhigh")
        self.assertLoggedEqual(
            "user warnings",
            [],
            [str(w.message) for w in caught if issubclass(w.category, UserWarning)],
        )

    def test_unlisted_model_is_built_on_first_use(self) -> None:
        """A model outside the targets gets its config on first lookup"""
        client = self._make_client(thinking_level="xhigh")
        self.assertLoggedEqual(
            "gemini-2.5-flash thinking",
            (None, None),
            _thinking(client._thinking_config_for("gemini-2.5-flash")),
        )

    def test_thinking_level_reaches_the_client(self) -> None:
        """A thinking_level setting reaches GeminiClient through init_translator"""
        with (
            patch.dict(os.environ, {"VERTEX_PROJECT": "p"}, clear=True),
            patch.object(GeminiProvider, "_get_gemini_models", return_value=[]),
        ):
            translator = init_translator(
                init_options(
                    provider="Gemini",
                    model="gemini-4-hydrogen",
                    thinking_level="low",
                    use_vertex=True,
                    vertex_project="p",
                    vertex_location="global",
                )
            )
        assert isinstance(translator.client, GeminiClient)
        self.assertLoggedEqual(
            "client thinking",
            ("LOW", None),
            _thinking(translator.client.thinking_config),
        )
