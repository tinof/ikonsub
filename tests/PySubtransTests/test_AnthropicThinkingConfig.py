from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from anthropic import omit

from PySubtrans.Helpers.TestCases import LoggedTestCase
from PySubtrans.Helpers.Tests import skip_if_debugger_attached
from PySubtrans.MKV.Config import CLAUDE_DEFAULT_EFFORT
from PySubtrans.Providers.Clients.AnthropicClient import (
    DEFAULT_EFFORT,
    FALLBACK_BETA,
    AnthropicClient,
)
from PySubtrans.SettingsType import SettingsType
from PySubtrans.SubtitleError import TranslationError
from PySubtrans.TranslationPrompt import TranslationPrompt
from PySubtrans.TranslationRequest import TranslationRequest


def _client(model: str, **extra: Any) -> AnthropicClient:
    settings = SettingsType(
        {
            "api_key": "sk-ant-test",
            "instructions": "Verify request config.",
            "model": model,
            "max_thinking_tokens": 2048,
            **extra,
        }
    )
    return AnthropicClient(settings)


def _block(block_type: str, **fields: Any) -> MagicMock:
    block = MagicMock()
    block.type = block_type
    for name, value in fields.items():
        setattr(block, name, value)
    return block


def _message(
    content: list[MagicMock], stop_reason: str = "end_turn", **fields: Any
) -> MagicMock:
    message = MagicMock()
    message.stop_reason = stop_reason
    message.usage = None
    message.content = content
    for name, value in fields.items():
        setattr(message, name, value)
    return message


def _send(client: AnthropicClient, message: MagicMock) -> tuple[Any, MagicMock]:
    """Run one request against a mocked SDK; returns the translation and the SDK mock"""
    stream = MagicMock()
    stream.text_stream = iter([])
    stream.get_final_message.return_value = message
    context = MagicMock()
    context.__enter__.return_value = stream
    context.__exit__.return_value = False

    sdk = MagicMock()
    sdk.messages.stream.return_value = context
    sdk.beta.messages.stream.return_value = context
    client.client = sdk

    prompt = TranslationPrompt("Translate this", True)
    prompt.content = [{"role": "user", "content": "1. Hello"}]
    prompt.system_prompt = "Translate."
    translation = client._request_translation(TranslationRequest(prompt, None))
    return translation, sdk


class AnthropicThinkingConfigTests(LoggedTestCase):
    """Validate the thinking and effort configuration sent for each model."""

    def test_thinking_off_omits_the_parameter(self) -> None:
        """With thinking off the parameter is omitted (current models still think)."""
        for model in ("claude-haiku-5-5", "claude-sonnet-4-5"):
            client = _client(model, thinking=False)
            self.assertLoggedTrue(f"{model} omits thinking", client.thinking is omit)

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
            client = _client(model, thinking=True)
            self.assertLoggedEqual(model, {"type": "adaptive"}, client.thinking)

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
            client = _client(model, thinking=True)
            self.assertLoggedEqual(model, expected, client.thinking)

    def test_effort_defaults_to_medium(self) -> None:
        """Without a setting, effort is pinned to medium on every current model."""
        self.assertLoggedEqual(
            "scripts show the same default", DEFAULT_EFFORT, CLAUDE_DEFAULT_EFFORT
        )
        for model in ("claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5"):
            self.assertLoggedEqual(
                model, {"effort": "medium"}, _client(model).output_config
            )

    def test_effort_setting_is_sent(self) -> None:
        """A configured effort is sent as output_config.effort."""
        client = _client("claude-sonnet-5-5", effort="low")
        self.assertLoggedEqual("low effort", {"effort": "low"}, client.output_config)

    def test_effort_default_leaves_model_default(self) -> None:
        """effort=default sends no output_config."""
        client = _client("claude-opus-5-5", effort="default")
        self.assertLoggedTrue("omitted", client.output_config is omit)

    def test_effort_not_sent_to_models_without_it(self) -> None:
        """Haiku 4.5 and Sonnet 4.5 reject effort; Opus 4.5 accepts it."""
        for model in ("claude-haiku-4-5", "claude-sonnet-4-5"):
            self.assertLoggedTrue(model, _client(model).output_config is omit)
        self.assertLoggedEqual(
            "claude-opus-4-5",
            {"effort": "medium"},
            _client("claude-opus-4-5").output_config,
        )


class AnthropicEffortValidationTests(LoggedTestCase):
    """Validate that bad or unsupported effort values never reach the API."""

    def test_unknown_effort_is_dropped(self) -> None:
        """A misspelled effort sends no output_config instead of failing every batch."""
        client = _client("claude-sonnet-5-5", effort="hgih")
        self.assertLoggedTrue("omitted", client.output_config is omit)

    def test_effort_is_clamped_to_model_levels(self) -> None:
        """Levels a model lacks are lowered to its highest supported level."""
        cases = [
            ("claude-opus-4-5", "max", "high"),
            ("claude-opus-4-5", "xhigh", "high"),
            ("claude-opus-4-6", "xhigh", "high"),
            ("claude-sonnet-4-6", "max", "max"),
            ("claude-opus-5-5", "xhigh", "xhigh"),
        ]
        for model, requested, expected in cases:
            client = _client(model, effort=requested)
            self.assertLoggedEqual(
                f"{model} {requested}", {"effort": expected}, client.output_config
            )


class AnthropicRequestTests(LoggedTestCase):
    """Validate the request shape and how responses are interpreted."""

    def test_system_prompt_is_cached(self) -> None:
        """The system prompt carries the cache breakpoint; messages stay uncached."""
        _, sdk = _send(
            _client("claude-haiku-5-5"), _message([_block("text", text="Hei")])
        )
        kwargs = sdk.messages.stream.call_args.kwargs
        self.assertLoggedEqual(
            "system block",
            [
                {
                    "type": "text",
                    "text": "Translate.",
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            kwargs["system"],
        )
        self.assertLoggedEqual("effort", {"effort": "medium"}, kwargs["output_config"])
        self.assertLoggedNotIn("no temperature", "temperature", kwargs)
        self.assertLoggedNotIn("no fallbacks on Haiku", "fallbacks", kwargs)

    def test_fallbacks_on_supported_models(self) -> None:
        """Sonnet 5.5 and Opus 5.5 use the beta endpoint with fallbacks=default."""
        for model in ("claude-sonnet-5-5", "claude-opus-5-5"):
            _, sdk = _send(_client(model), _message([_block("text", text="Hei")]))
            kwargs = sdk.beta.messages.stream.call_args.kwargs
            self.assertLoggedEqual(f"{model} fallbacks", "default", kwargs["fallbacks"])
            self.assertLoggedEqual(f"{model} beta", [FALLBACK_BETA], kwargs["betas"])
            self.assertLoggedEqual(
                f"{model} plain endpoint unused", 0, sdk.messages.stream.call_count
            )

    def test_fallbacks_can_be_turned_off(self) -> None:
        """fallbacks=False uses the regular endpoint."""
        client = _client("claude-opus-5-5", fallbacks=False)
        _, sdk = _send(client, _message([_block("text", text="Hei")]))
        self.assertLoggedEqual("regular endpoint", 1, sdk.messages.stream.call_count)

    def test_fallback_text_is_joined(self) -> None:
        """Text before and after a mid-stream fallback forms one translation."""
        fallback = _block(
            "fallback",
            from_=MagicMock(model="claude-sonnet-5-5"),
            to=MagicMock(model="claude-opus-4-8"),
        )
        message = _message(
            [
                _block("text", text="#1\nHei\n\n#2\nMo"),
                fallback,
                _block("text", text="i\n"),
            ]
        )
        translation, _ = _send(_client("claude-sonnet-5-5"), message)
        self.assertLoggedEqual("joined text", "#1\nHei\n\n#2\nMoi", translation.text)

    @skip_if_debugger_attached
    def test_refusal_raises_with_category(self) -> None:
        """A refusal is a batch error that names the category, not an empty result."""
        details = MagicMock(category="general_harms", explanation=None)
        message = _message([], stop_reason="refusal", stop_details=details)
        with self.assertRaises(TranslationError) as raised:
            _send(_client("claude-haiku-5-5"), message)
        self.assertLoggedIn("category", "general_harms", str(raised.exception))

    def test_token_limit_returns_translation(self) -> None:
        """max_tokens is returned as a length finish, so the translator can split."""
        message = _message([_block("text", text="#1\nHei")], stop_reason="max_tokens")
        translation, _ = _send(_client("claude-haiku-5-5"), message)
        self.assertLoggedTrue("reached token limit", translation.reached_token_limit)

    def test_empty_thinking_is_not_stored(self) -> None:
        """Omitted thinking text (the default display) is not kept as reasoning."""
        message = _message(
            [_block("thinking", thinking=""), _block("text", text="Hei")]
        )
        translation, _ = _send(_client("claude-haiku-5-5"), message)
        self.assertLoggedIsNone("no reasoning", translation.reasoning)
