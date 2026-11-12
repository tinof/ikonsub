# ikonsub Development Guide

**Note:** ikonsub is a CLI-only app for Linux/macOS, derived from LLM-Subtrans (machinewrapped/llm-subtrans) and now developed separately. Nothing is merged from or sent to that project.

`CLAUDE.md` is a symlink to this file — edit `AGENTS.md`; both names resolve to the same content.

Project requires Python 3.14+. NEVER import or use deprecated typing members like List, Union or Iterator.

Secrets are stored in a .env file - NEVER read the contents of the file.

## Package Management (STRICT)

- STRICTLY use `uv` for dependency management, environment management, and command execution.
- Do not use `pip`, `pipx`, `pipxu`, or manual virtual environment activation flows for normal project work.
- Use `uv sync` to install dependencies and `uv run` to execute project commands.
- Use `uv tool install` to install CLI commands system-wide.

## Installation

### Development (in the repo checkout)

```bash
uv sync --all-extras --dev
```

This installs all optional provider dependencies (Gemini, OpenAI, Claude, etc.) and dev tools (ruff, basedpyright, pytest).

### System-Wide CLI (via `uv tool`)

Install the CLI commands (`exsubs`, `transubs`, etc.) globally:

```bash
uv tool install "ikonsub[openai,gemini,claude] @ git+https://github.com/tinof/ikonsub.git"
```

Or for editable/local development:

```bash
uv tool install --editable /path/to/ikonsub --with "google-genai" --with "openai" --with "anthropic"
```

### Optional Extras

Provider-specific dependencies are gated behind extras in `pyproject.toml`:

| Extra | Dependencies | Required For |
| --- | --- | --- |
| `openai` | `openai` | OpenAI (GPT) provider |
| `gemini` | `google-genai` | Gemini / Vertex AI provider |
| `claude` | `anthropic` | Claude provider |

The MKV dependencies are core dependencies. The empty `mkv` extra is kept only so old `ikonsub[mkv]` install commands still work.

**If a provider extra is not installed, the provider silently won't register and you'll get `Unknown translation provider` errors at runtime.**

## Command Reference

| Task | Command |
| --- | --- |
| Install deps (dev) | `uv sync --all-extras --dev` |
| Format | `uv run ruff check --fix && uv run ruff format` |
| Checks | `uv run ruff check && uv run ruff format --check && uv run basedpyright` |
| Tests | `uv run pytest` |

### Makefile Targets

All of the above are also available as `make` targets:

| Target | Command | Description |
| --- | --- | --- |
| `make` (default) | `install check test` | Full pipeline: install, lint, test |
| `make install` | `uv sync --all-extras` | Install all deps including all provider extras |
| `make fmt` | ruff check --fix + ruff format | Auto-fix lint issues and reformat |
| `make check` | ruff check + format --check + basedpyright | Lint, format verify, and type check |
| `make test` | `uv run pytest` | Run test suite |
| `make build` | `uv build` | Build distribution package |
| `make clean` | rm -rf dist, caches, venv | Clean all artifacts |

## Project Structure Overview

- `PySubtrans/`: Core subtitle translation engine, providers, parsing, and helpers.
  - `Providers/`: Translation provider implementations. This fork keeps only Gemini, OpenAI and Claude.
  - `Formats/`: Subtitle format handlers (SRT, ASS/SSA, VTT) with auto-discovery registry.
  - `Helpers/`: Shared utilities, test base classes (`LoggedTestCase`, `DummyProvider`).
- `scripts/`: CLI entrypoints and supporting automation scripts.
  - `exsubs.py`: Extract subtitles from MKV files and translate (primary workflow).
  - `transubs.py`: Translate existing subtitle files (.srt, .ass, .vtt).
  - `batch_translate.py`: Batch translation of entire directories.
  - `subtrans_common.py`: Shared CLI infrastructure (arg parsing, options, project creation).
  - `check_imports.py`: Validates provider dependencies are importable before running.
  - Provider-specific scripts: `gemini_subtrans.py`, `gpt_subtrans.py`, `claude_subtrans.py`, etc.
- `tests/`: Unit test suites and test harnesses.
  - `PySubtransTests/`: Core unit tests extending `LoggedTestCase`.
  - `functional/`: Functional tests (preprocessor, batcher).
  - `TestData/`: Realistic test data fixtures.
- `instructions/`: Translation/system instruction templates shipped with the project.
- `tools/`: Analysis utilities — `subtitle_metrics.py` reports line lengths, CPS, and cue-count deltas, and is how translation-quality changes are validated.
- `docs/`: Architecture and contributor documentation.
- `.github/workflows/`: CI and automation workflows.

## Code Style Guidelines

**CRITICAL RULE: NEVER add imports in the middle of functions or methods - ALL imports MUST be at the top of the file.**

The few existing exceptions are deliberate: the module-level `try`/`except ImportError` guards in `Provider_*.py` (a missing extra must not break import), the lazy `PySubtrans.Providers`/`PySubtrans.Formats` registry imports (circular imports), and one lazy import in `scripts/transubs.py` (`setup_vertex` from exsubs). Do not add more.

- **Naming**: PascalCase for classes and methods, snake_case for variables
- **Imports**: Standard lib -> third-party -> local, alphabetical within groups
- **Class structure**: Docstring -> constants -> init -> properties -> public methods -> private methods
- **Type Hints**: Use type hints for parameters, return values, and class variables
  - Follow PEP 8/604 style: no space before colon, space after colon, spaces around `|`
  - Example: `def func(self, param: str) -> str | None:`
- **Docstrings**: Triple-quoted concise descriptions for classes and methods
- **Error handling**: Custom exceptions, specific except blocks, input validation, logging.warning/error
  - Wrap user-facing error messages in `_()` (`PySubtrans/Helpers/Localization.py`; a passthrough today, no catalogs ship)
- **Threading safety**: Use locks (`threading.RLock`) for thread-safe operations
- **Regular Expressions**: Use the `regex` module rather than standard `re`
- **Unit Tests**: Extend `LoggedTestCase` from `PySubtrans.Helpers.TestCases` and prefer `assertLogged*` methods

## Testing and Validation

- Always run `make check` and `make test` before finishing a task.
- `make check` lints, format-checks and type-checks `PySubtrans/`, `scripts/`, `tools/` and `tests/`: ruff with a pinned `select` list (ruff 0.17 widened its defaults, so do not switch back to `extend-select`) at 88 columns, and basedpyright in standard mode, which also fails on unneeded `# type: ignore` comments.
- If needed for compatibility validation, run `uv run python tests/unit_tests.py`.

## Finnish Readability Pipeline

Read `docs/finnish-pipeline.md` before changing `exsubs`/`transubs`, the parser,
validator, prompt builder, `Options` or `instructions/`. Its binding contracts:
`init_options`, never `Options(settings)`; the `postprocess_translation` key;
register new `Options` keys; timing annotations and parser patterns as a pair;
one readability budget in three places; line correspondence and its three
guards; `fix-finnish-subs` exactly once; the temperature and Gemini thinking
policy (`GEMINI_3_MODEL_PATTERN`); measure with `tools/subtitle_metrics.py`.

## Information

Consult `docs/architecture.md` for detailed information on the project architecture and components.

## Repository Hygiene

- Never print or read `.env` contents.
- Keep the app CLI-only: do not reintroduce GUI or Windows-specific code.
- Keep only the Gemini, OpenAI and Claude providers.
- Keep the `LLMSUBTRANS_*` environment variables and the existing config directories, so installed setups keep working.
- New files or scripts must not add imports in the middle of functions.
- Use `regex` (not `re`) for regular expressions.
