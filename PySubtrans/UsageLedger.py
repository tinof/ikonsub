"""
Token usage ledgers for callers that price a run (transubs --usage-json).

One JSON file holds a list per provider beside "version": 1. Each ledger rewrites the
whole file after every response, so a run killed by its caller's timeout still leaves
what it was billed for. This module imports no provider SDK.
"""

import json
import logging
import os
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

USAGE_FILE_VERSION = 1

# Refusal categories Anthropic bills even when the refusal comes before any output
# (refusals-and-fallback docs, "How refusals are billed", checked 2026-11-06). A
# pre-output refusal in any other category, or with no category, is not billed.
BILLED_REFUSAL_CATEGORIES = frozenset({"bio", "frontier_llm", "reasoning_extraction"})


class UsageFile:
    """
    The usage JSON file: one section per registered ledger, rewritten atomically
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._path: Path | None = None
        self._sections: dict[str, Callable[[], list[dict[str, Any]]]] = {}

    def register(self, name: str, snapshot: Callable[[], list[dict[str, Any]]]) -> None:
        """Add a section, written from `snapshot()` on every write"""
        with self._lock:
            self._sections[name] = snapshot

    def persist_to(self, path: Path | None) -> None:
        """Rewrite `path` after every response from now on (None stops it)"""
        with self._lock:
            self._path = path
            self._write_locked()

    def write(self) -> None:
        with self._lock:
            self._write_locked()

    def _write_locked(self) -> None:
        if self._path is None:
            return
        payload: dict[str, Any] = {"version": USAGE_FILE_VERSION}
        for name, snapshot in self._sections.items():
            payload[name] = snapshot()
        try:
            partial = self._path.with_name(f"{self._path.name}.partial")
            partial.write_text(json.dumps(payload), encoding="utf-8")
            os.replace(partial, self._path)
        except OSError as e:
            logging.warning(f"Could not write usage to {self._path}: {e}")


class GeminiUsageLedger:
    """
    Token usage of every Gemini response in this process, per model and location.

    A response counts whether or not its translation is then accepted: a retried batch
    is billed for every attempt that came back.
    """

    _FIELDS = ("prompt_tokens", "cached_tokens", "output_tokens", "thoughts_tokens")

    def __init__(self, usage_file: UsageFile | None = None) -> None:
        self._lock = threading.Lock()
        self._rows: dict[tuple[str, str], dict[str, int]] = {}
        self._file = usage_file or UsageFile()
        self._file.register("gemini", self.snapshot)

    def persist_to(self, path: Path | None) -> None:
        """Rewrite `path` with the snapshot after each response (None stops it)."""
        self._file.persist_to(path)

    def record(self, model: str, location: str, usage: Any) -> None:
        if usage is None:
            return
        values = {
            "prompt_tokens": usage.prompt_token_count,
            "cached_tokens": usage.cached_content_token_count,
            "output_tokens": usage.candidates_token_count,
            "thoughts_tokens": usage.thoughts_token_count,
        }
        with self._lock:
            row = self._rows.setdefault(
                (model, location), {"requests": 0, **{f: 0 for f in self._FIELDS}}
            )
            row["requests"] += 1
            for field, value in values.items():
                row[field] += int(value or 0)
        self._file.write()

    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                {"model": model, "location": location, **row}
                for (model, location), row in self._rows.items()
            ]

    def reset(self) -> None:
        with self._lock:
            self._rows.clear()
        self._file.write()


class ClaudeUsageLedger:
    """
    Token usage of every Claude response in this process, per serving model.

    Every response that came back counts, including retried batches and refusals.
    With a server-side refusal fallback, usage.iterations splits the tokens per hop and
    each hop is counted against the model that ran it. output_tokens includes thinking.
    Only 5-minute cache writes are used, so cache writes are one field.
    """

    _FIELDS = (
        "input_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
        "output_tokens",
    )

    def __init__(self, usage_file: UsageFile | None = None) -> None:
        self._lock = threading.Lock()
        self._rows: dict[str, dict[str, int]] = {}
        self._file = usage_file or UsageFile()
        self._file.register("claude", self.snapshot)

    def persist_to(self, path: Path | None) -> None:
        """Rewrite `path` with the snapshot after each response (None stops it)."""
        self._file.persist_to(path)

    def record(
        self,
        requested_model: str,
        served_model: str | None,
        usage: Any,
        declined_categories: list[str | None] | None = None,
        refused: bool = False,
        refusal_category: str | None = None,
    ) -> None:
        """
        Count one response, per hop when a server-side fallback ran.

        declined_categories holds the trigger category of each fallback block, in
        order: one per hop that declined before the last. refused and
        refusal_category describe the last hop (stop_reason "refusal"). A refusal with
        no output in an unbilled category is counted with zero tokens.
        """
        if usage is None:
            return

        # usage.iterations splits a fallback turn per hop. A hop entry may omit its
        # model: the last hop then is the serving model, earlier ones the requested one.
        entries = [
            iteration
            for iteration in getattr(usage, "iterations", None) or []
            if getattr(iteration, "type", None) in ("message", "fallback_message")
        ]
        hops: list[tuple[str, Any]] = []
        for index, entry in enumerate(entries):
            fallback_model = (
                served_model if index == len(entries) - 1 else requested_model
            )
            hops.append(
                (
                    getattr(entry, "model", None) or fallback_model or requested_model,
                    entry,
                )
            )

        if not hops:
            hops = [(served_model or requested_model, usage)]

        declined = list(declined_categories or [])

        with self._lock:
            # One request per response, against the model that served it
            served = served_model or hops[-1][0]
            for index, (model, hop) in enumerate(hops):
                row = self._rows.setdefault(
                    model, {"requests": 0, **{f: 0 for f in self._FIELDS}}
                )
                if index < len(hops) - 1:
                    hop_refused = True
                    category = declined[index] if index < len(declined) else None
                else:
                    hop_refused, category = refused, refusal_category
                if hop_refused and self._unbilled_refusal(hop, category):
                    continue
                row["input_tokens"] += int(getattr(hop, "input_tokens", 0) or 0)
                row["cache_read_tokens"] += int(
                    getattr(hop, "cache_read_input_tokens", 0) or 0
                )
                row["cache_write_tokens"] += int(
                    getattr(hop, "cache_creation_input_tokens", 0) or 0
                )
                row["output_tokens"] += int(getattr(hop, "output_tokens", 0) or 0)
            self._rows.setdefault(
                served, {"requests": 0, **{f: 0 for f in self._FIELDS}}
            )["requests"] += 1
        self._file.write()

    @staticmethod
    def _unbilled_refusal(hop: Any, category: str | None) -> bool:
        """A refusal before any output is free unless its category is billed"""
        no_output = not int(getattr(hop, "output_tokens", 0) or 0)
        return no_output and category not in BILLED_REFUSAL_CATEGORIES

    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            return [{"model": model, **row} for model, row in self._rows.items()]

    def reset(self) -> None:
        with self._lock:
            self._rows.clear()
        self._file.write()


# The process-wide file and ledgers that transubs --usage-json writes
USAGE_FILE = UsageFile()
GEMINI_USAGE_LEDGER = GeminiUsageLedger(USAGE_FILE)
CLAUDE_USAGE_LEDGER = ClaudeUsageLedger(USAGE_FILE)
