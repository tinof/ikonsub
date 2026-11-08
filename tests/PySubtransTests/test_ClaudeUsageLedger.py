from __future__ import annotations

import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from PySubtrans.Helpers.TestCases import LoggedTestCase
from PySubtrans.Helpers.Tests import skip_if_debugger_attached
from PySubtrans.Providers.Clients.AnthropicClient import AnthropicClient
from PySubtrans.SettingsType import SettingsType
from PySubtrans.SubtitleError import TranslationError
from PySubtrans.TranslationPrompt import TranslationPrompt
from PySubtrans.TranslationRequest import TranslationRequest
from PySubtrans.UsageLedger import (
    CLAUDE_USAGE_LEDGER,
    ClaudeUsageLedger,
    GeminiUsageLedger,
    UsageFile,
)


def _usage(
    input_tokens: int,
    output_tokens: int,
    cache_read: int | None = None,
    cache_write: int | None = None,
    iterations: list | None = None,
):
    return SimpleNamespace(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_input_tokens=cache_read,
        cache_creation_input_tokens=cache_write,
        iterations=iterations,
    )


def _hop(kind: str, model: str | None, input_tokens: int, output_tokens: int):
    return SimpleNamespace(
        type=kind,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_input_tokens=0,
        cache_creation_input_tokens=0,
    )


class ClaudeUsageLedgerTests(LoggedTestCase):
    """Every billed Claude response is counted against the model that ran it."""

    def test_ledger_sums_per_model(self) -> None:
        ledger = ClaudeUsageLedger()
        ledger.record("claude-haiku-5-5", "claude-haiku-5-5", _usage(100, 50, 3000, 0))
        ledger.record("claude-haiku-5-5", "claude-haiku-5-5", _usage(120, 40, 0, 3000))
        ledger.record("claude-haiku-5-5", "claude-haiku-5-5", None)
        self.assertLoggedEqual(
            "rows",
            [
                {
                    "model": "claude-haiku-5-5",
                    "requests": 2,
                    "input_tokens": 220,
                    "cache_read_tokens": 3000,
                    "cache_write_tokens": 3000,
                    "output_tokens": 90,
                }
            ],
            ledger.snapshot(),
        )

    def test_fallback_hops_are_billed_to_each_model(self) -> None:
        """A declined hop counts for the requested model, the rescue for the fallback."""
        ledger = ClaudeUsageLedger()
        usage = _usage(
            900,
            400,
            iterations=[
                _hop("message", None, 1000, 20),
                _hop("fallback_message", "claude-opus-4-8", 900, 400),
            ],
        )
        ledger.record("claude-sonnet-5-5", "claude-opus-4-8", usage)
        rows = {row["model"]: row for row in ledger.snapshot()}
        self.assertLoggedEqual(
            "declined hop input", 1000, rows["claude-sonnet-5-5"]["input_tokens"]
        )
        self.assertLoggedEqual(
            "declined hop requests", 0, rows["claude-sonnet-5-5"]["requests"]
        )
        self.assertLoggedEqual(
            "served output", 400, rows["claude-opus-4-8"]["output_tokens"]
        )
        self.assertLoggedEqual(
            "served requests", 1, rows["claude-opus-4-8"]["requests"]
        )

    def test_unbilled_pre_output_refusal_has_no_tokens(self) -> None:
        """A refusal before any output is free unless its category is billed."""
        ledger = ClaudeUsageLedger()
        ledger.record(
            "claude-haiku-5-5",
            "claude-haiku-5-5",
            _usage(800, 0),
            refused=True,
            refusal_category="general_harms",
        )
        ledger.record(
            "claude-haiku-5-5",
            "claude-haiku-5-5",
            _usage(800, 0),
            refused=True,
            refusal_category=None,
        )
        row = ledger.snapshot()[0]
        self.assertLoggedEqual("requests", 2, row["requests"])
        self.assertLoggedEqual("input", 0, row["input_tokens"])

    def test_billed_category_and_mid_stream_refusals_keep_tokens(self) -> None:
        """bio/frontier_llm/reasoning_extraction and mid-stream refusals are billed."""
        ledger = ClaudeUsageLedger()
        ledger.record(
            "claude-haiku-5-5",
            "claude-haiku-5-5",
            _usage(800, 0),
            refused=True,
            refusal_category="bio",
        )
        ledger.record(
            "claude-haiku-5-5",
            "claude-haiku-5-5",
            _usage(800, 30),
            refused=True,
            refusal_category="general_harms",
        )
        row = ledger.snapshot()[0]
        self.assertLoggedEqual("input", 1600, row["input_tokens"])
        self.assertLoggedEqual("output", 30, row["output_tokens"])

    def test_declined_hop_uses_its_trigger_category(self) -> None:
        """A pre-output declined hop in an unbilled category adds no tokens."""
        ledger = ClaudeUsageLedger()
        usage = _usage(
            900,
            400,
            iterations=[
                _hop("message", "claude-sonnet-5-5", 1000, 0),
                _hop("fallback_message", "claude-opus-4-8", 900, 400),
            ],
        )
        ledger.record(
            "claude-sonnet-5-5",
            "claude-opus-4-8",
            usage,
            declined_categories=["cyber"],
        )
        rows = {row["model"]: row for row in ledger.snapshot()}
        self.assertLoggedEqual(
            "declined hop input", 0, rows["claude-sonnet-5-5"]["input_tokens"]
        )
        self.assertLoggedEqual(
            "served input", 900, rows["claude-opus-4-8"]["input_tokens"]
        )

    def test_single_hop_without_model_is_the_serving_model(self) -> None:
        """A sticky fallback turn reports one hop; it belongs to the serving model."""
        ledger = ClaudeUsageLedger()
        usage = _usage(500, 100, iterations=[_hop("message", None, 500, 100)])
        ledger.record("claude-sonnet-5-5", "claude-opus-4-8", usage)
        self.assertLoggedEqual(
            "models", ["claude-opus-4-8"], [row["model"] for row in ledger.snapshot()]
        )

    def test_one_file_holds_both_providers(self) -> None:
        """The file keeps version 1 and the gemini list beside the claude list."""
        usage_file = UsageFile()
        GeminiUsageLedger(usage_file)
        claude = ClaudeUsageLedger(usage_file)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "usage.json"
            usage_file.persist_to(path)
            self.assertLoggedEqual(
                "empty at start",
                {"version": 1, "gemini": [], "claude": []},
                json.loads(path.read_text()),
            )
            claude.record("claude-opus-5-5", "claude-opus-5-5", _usage(10, 5))
            payload = json.loads(path.read_text())
            self.assertLoggedEqual("version", 1, payload["version"])
            self.assertLoggedEqual("gemini unchanged", [], payload["gemini"])
            self.assertLoggedEqual("claude row", 1, payload["claude"][0]["requests"])
            usage_file.persist_to(None)

    @skip_if_debugger_attached
    def test_client_records_a_refused_response(self) -> None:
        """A refusal is billed, so it is recorded before the client raises."""
        CLAUDE_USAGE_LEDGER.reset()
        self.addCleanup(CLAUDE_USAGE_LEDGER.reset)

        message = MagicMock()
        message.stop_reason = "refusal"
        message.stop_details = None
        message.model = "claude-haiku-5-5"
        message.usage = _usage(800, 12)
        message.content = []
        stream = MagicMock()
        stream.text_stream = iter([])
        stream.get_final_message.return_value = message
        context = MagicMock()
        context.__enter__.return_value = stream
        context.__exit__.return_value = False

        client = AnthropicClient(
            SettingsType(
                {
                    "api_key": "sk-ant-test",
                    "instructions": "x",
                    "model": "claude-haiku-5-5",
                }
            )
        )
        client.client = MagicMock()
        client.client.messages.stream.return_value = context

        prompt = TranslationPrompt("Translate this", True)
        prompt.content = [{"role": "user", "content": "1. Hello"}]
        prompt.system_prompt = "Translate."
        with self.assertRaises(TranslationError):
            client._request_translation(TranslationRequest(prompt, None))

        rows = CLAUDE_USAGE_LEDGER.snapshot()
        self.assertLoggedEqual("recorded", 1, len(rows))
        self.assertLoggedEqual("input", 800, rows[0]["input_tokens"])
