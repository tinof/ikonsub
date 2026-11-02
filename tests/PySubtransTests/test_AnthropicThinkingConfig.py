from __future__ import annotations

import anthropic

from PySubtrans.Helpers.TestCases import LoggedTestCase
from PySubtrans.Providers.Clients.AnthropicClient import AnthropicClient
from PySubtrans.SettingsType import SettingsType


class AnthropicThinkingConfigTests(LoggedTestCase):
    """Validate which thinking configuration AnthropicClient sends for each model."""

    def _thinking(self, model: str, thinking: bool = True) -> dict | anthropic.NotGiven:
        settings = SettingsType(
            {
                "instructions": "Verify thinking config.",
                "model": model,
                "thinking": thinking,
                "max_thinking_tokens": 2048,
            }
        )
        return AnthropicClient(settings).thinking

    def test_thinking_off_sends_nothing(self) -> None:
        """With thinking off the parameter is omitted for every model."""
        for model in ("claude-haiku-5-5", "claude-sonnet-4-5"):
            result = self._thinking(model, thinking=False)
            self.assertLoggedTrue(f"{model} omits thinking", result is anthropic.NOT_GIVEN)

    def test_current_models_use_adaptive_thinking(self) -> None:
        """Claude 4.6 and newer get adaptive thinking, never budget_tokens."""
        for model in (
            "claude-haiku-5-5",
            "claude-sonnet-5-5",
            "claude-opus-5-5",
            "claude-opus-4-8",
            "claude-opus-4-6",
            "claude-sonnet-4-6",
            "claude-fable-5-1",
        ):
            self.assertLoggedEqual(model, {"type": "adaptive"}, self._thinking(model))

    def test_older_models_keep_budget_thinking(self) -> None:
        """Claude 4.5 and older reject adaptive thinking and keep budget_tokens."""
        expected = {"type": "enabled", "budget_tokens": 2048}
        for model in (
            "claude-haiku-4-5",
            "claude-sonnet-4-5",
            "claude-sonnet-4-5-20250929",
            "claude-opus-4-1-20250805",
            "claude-opus-4-0",
            "claude-sonnet-4-20250514",
            "claude-3-7-sonnet-20250219",
        ):
            self.assertLoggedEqual(model, expected, self._thinking(model))
