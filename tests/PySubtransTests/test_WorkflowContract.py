import subprocess
from pathlib import Path

import regex

from PySubtrans.Helpers.TestCases import LoggedTestCase

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACT = REPO_ROOT / ".claude" / "workflow.md"


def _section(name: str) -> str:
    """Return the body of a `## <name>` section of the workflow contract."""
    text = CONTRACT.read_text(encoding="utf-8")
    match = regex.search(
        rf"^## {regex.escape(name)}\n(.*?)(?=^## |\Z)", text, regex.M | regex.S
    )
    if match is None:
        raise AssertionError(f"workflow contract has no ## {name} section")
    return match.group(1)


def _unattended_value(key: str) -> str:
    """Read a `- `key`: `value`` bullet of ## Unattended, as gate-proven.sh does."""
    match = regex.search(
        rf"^- `{regex.escape(key)}`:\s*`(.*)`\s*$", _section("Unattended"), regex.M
    )
    if match is None:
        raise AssertionError(f"## Unattended has no {key}")
    return match.group(1)


class WorkflowContractTests(LoggedTestCase):
    """Guard the parts of .claude/workflow.md that decide whether a change skips review."""

    def test_move_gate_fails_when_the_checker_cannot_be_loaded(self) -> None:
        """A move gate that cannot load its checker must fail, not pass on empty stdin.

        gate-proven.sh runs the gate as `bash -c "$gate --step <ID>"`; a pass there skips every
        reviewer, so a missing tools/check_pure_move.py must make the command exit non-zero.
        """
        gate = _unattended_value("move-gate")
        missing = gate.replace(
            "tools/check_pure_move.py", "tools/__missing_move_checker__.py"
        )
        self.assertLoggedIn(
            "checker path substituted", "__missing_move_checker__", missing
        )

        result = subprocess.run(
            ["bash", "-c", f"{missing} --step E5.2"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=120,
        )

        self.assertLoggedTrue(
            f"move gate fails (exit {result.returncode})", result.returncode != 0
        )
