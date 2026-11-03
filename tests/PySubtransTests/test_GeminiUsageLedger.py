from __future__ import annotations

from types import SimpleNamespace
from typing import cast

from google.genai.types import GenerateContentResponse

from PySubtrans.Helpers.TestCases import LoggedTestCase
from PySubtrans.Providers.Clients.GeminiClient import (
    USAGE_LEDGER,
    GeminiClient,
    GeminiUsageLedger,
)
from PySubtrans.SettingsType import SettingsType
from PySubtrans.SubtitleError import TranslationResponseError


def _usage(
    prompt: int, output: int, thoughts: int | None = None, cached: int | None = None
):
    return SimpleNamespace(
        prompt_token_count=prompt,
        candidates_token_count=output,
        thoughts_token_count=thoughts,
        cached_content_token_count=cached,
    )


class GeminiUsageLedgerTests(LoggedTestCase):
    """Every billed Gemini response is counted per model and location."""

    def test_ledger_sums_per_model_and_location(self) -> None:
        ledger = GeminiUsageLedger()
        ledger.record("gemini-4-argon", "global", _usage(100, 40, 10, 20))
        ledger.record("gemini-4-argon", "global", _usage(50, 5))
        ledger.record("gemini-4-argon", "us", _usage(7, 3, 1))
        ledger.record("gemini-4-argon", "us", None)

        rows = {(r["model"], r["location"]): r for r in ledger.snapshot()}
        self.assertLoggedEqual(
            "global row",
            {
                "model": "gemini-4-argon",
                "location": "global",
                "requests": 2,
                "prompt_tokens": 150,
                "cached_tokens": 20,
                "output_tokens": 45,
                "thoughts_tokens": 10,
            },
            rows[("gemini-4-argon", "global")],
        )
        self.assertLoggedEqual(
            "us requests", 1, rows[("gemini-4-argon", "us")]["requests"]
        )

    def test_a_rejected_response_is_still_counted(self) -> None:
        USAGE_LEDGER.reset()
        client = GeminiClient(
            SettingsType(
                {
                    "instructions": "x",
                    "model": "gemini-3.8-flash",
                    "vertex_location": "global",
                }
            )
        )
        gcr = SimpleNamespace(
            prompt_feedback=None, candidates=[], usage_metadata=_usage(9, 0, 4)
        )

        with self.assertRaises(TranslationResponseError):
            client._process_gemini_response(
                cast(GenerateContentResponse, gcr), "gemini-3.5-flash-lite", "us"
            )

        rows = USAGE_LEDGER.snapshot()
        USAGE_LEDGER.reset()
        self.assertLoggedEqual("model", "gemini-3.5-flash-lite", rows[0]["model"])
        self.assertLoggedEqual("location", "us", rows[0]["location"])
        self.assertLoggedEqual("thoughts", 4, rows[0]["thoughts_tokens"])

    def test_a_persisted_ledger_is_on_disk_after_every_response(self) -> None:
        import json
        import tempfile
        from pathlib import Path

        ledger = GeminiUsageLedger()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "usage.json"
            ledger.persist_to(path)
            self.assertLoggedEqual(
                "empty at start", [], json.loads(path.read_text())["gemini"]
            )

            ledger.record("gemini-4-argon", "global", _usage(10, 2, 1))
            rows = json.loads(path.read_text())["gemini"]
            self.assertLoggedEqual("written on record", 10, rows[0]["prompt_tokens"])
            self.assertLoggedEqual(
                "no partial file left",
                ["usage.json"],
                sorted(p.name for p in Path(tmp).iterdir()),
            )
            ledger.persist_to(None)
