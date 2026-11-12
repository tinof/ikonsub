import subprocess
from pathlib import Path

import regex

from PySubtrans.Helpers.TestCases import LoggedTestCase

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACT = REPO_ROOT / ".claude" / "workflow.md"

# Files that carry the Finnish readability contract (docs/finnish-pipeline.md, linked from
# AGENTS.md): a change to any of them must get a Tier 2 review whatever its size.
FINNISH_PIPELINE_FILES = [
    "docs/finnish-pipeline.md",
    "instructions/instructions_fi.txt",
    "PySubtrans/Options.py",
    "PySubtrans/SubtitleProcessor.py",
    "PySubtrans/SubtitleTranslator.py",
    "PySubtrans/SubtitleValidator.py",
    "PySubtrans/TranslationParser.py",
    "PySubtrans/TranslationPrompt.py",
    "scripts/exsubs.py",
    "scripts/transubs.py",
]


def _section(name: str) -> str:
    """Return the body of a `## <name>` section of the workflow contract."""
    text = CONTRACT.read_text(encoding="utf-8")
    match = regex.search(
        rf"^## {regex.escape(name)}\n(.*?)(?=^## |\Z)", text, regex.M | regex.S
    )
    if match is None:
        raise AssertionError(f"workflow contract has no ## {name} section")
    return match.group(1)


def _tier2_globs() -> list[str]:
    """Read the fenced glob block of ## Tier 2 paths."""
    match = regex.search(r"```\n(.*?)```", _section("Tier 2 paths"), regex.S)
    if match is None:
        raise AssertionError("## Tier 2 paths has no fenced glob block")
    return [line.strip() for line in match.group(1).splitlines() if line.strip()]


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

    def test_tier2_paths_cover_the_finnish_pipeline_files(self) -> None:
        """Every file of the Finnish readability contract matches a Tier 2 glob.

        /ship adds `:(glob)` to each line and asks git which changed files match, so the same
        pathspecs are matched here against the tracked files.
        """
        globs = _tier2_globs()
        result = subprocess.run(
            ["git", "ls-files", "--", *[f":(glob){p}" for p in globs]],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        matched = set(result.stdout.splitlines())

        for path in FINNISH_PIPELINE_FILES:
            with self.subTest(path=path):
                self.assertLoggedIn("Tier 2 covers", path, matched)
