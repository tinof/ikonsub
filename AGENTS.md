# LLM-Subtrans Development Guide (CLI Edition)

**Note:** This is a CLI-only fork for Linux/macOS. All GUI and Windows-specific components have been removed.

`CLAUDE.md` is a symlink to this file — edit `AGENTS.md`; both names resolve to the same content.

Project uses Python 3.11+ (developed and installed on 3.14). NEVER import or use deprecated typing members like List, Union or Iterator.

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
uv tool install "llm-subtrans[openai,gemini,claude]" @ git+https://github.com/tinof/llm-subtrans.git
```

Or for editable/local development:

```bash
uv tool install --editable /path/to/llm-subtrans --with "google-genai" --with "google-api-core" --with "openai" --with "anthropic"
```

### Optional Extras

Provider-specific dependencies are gated behind extras in `pyproject.toml`:

| Extra | Dependencies | Required For |
| --- | --- | --- |
| `openai` | `openai` | OpenAI (GPT) provider |
| `gemini` | `google-genai`, `google-api-core` | Gemini / Vertex AI provider |
| `claude` | `anthropic` | Claude provider |
| `mkv` | `subtitle_filter`, `chardet`, `filelock`, `rich` | MKV subtitle extraction |

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
- `locales/`: gettext catalogs for user-facing (`_()`) strings.
- `docs/`: Architecture and contributor documentation.
- `.github/workflows/`: CI and automation workflows.

## Code Style Guidelines

**CRITICAL RULE: NEVER add imports in the middle of functions or methods - ALL imports MUST be at the top of the file.**

- **Naming**: PascalCase for classes and methods, snake_case for variables
- **Imports**: Standard lib -> third-party -> local, alphabetical within groups
- **Class structure**: Docstring -> constants -> init -> properties -> public methods -> private methods
- **Type Hints**: Use type hints for parameters, return values, and class variables
  - Follow PEP 8/604 style: no space before colon, space after colon, spaces around `|`
  - Example: `def func(self, param: str) -> str | None:`
- **Docstrings**: Triple-quoted concise descriptions for classes and methods
- **Error handling**: Custom exceptions, specific except blocks, input validation, logging.warning/error
  - User-facing error messages should be localizable, using `_()`
- **Threading safety**: Use locks (RLock/QRecursiveMutex) for thread-safe operations
- **Regular Expressions**: Use the `regex` module rather than standard `re`
- **Unit Tests**: Extend `LoggedTestCase` from `PySubtrans.Helpers.TestCases` and prefer `assertLogged*` methods

## Testing and Validation

- Always run `make check` and `make test` before finishing a task.
- If needed for compatibility validation, run `uv run python tests/unit_tests.py`.

## Finnish Readability Pipeline (development notes)

The `exsubs`/`transubs` → `fix-finnish-subs` pipeline enforces a readability
budget end-to-end. The wiring is subtle and has silently broken before — keep
these contracts intact:

- **`init_options(**settings)`, never `Options(settings)`, in scripts.** Only
  `init_options` calls `LoadInstructions`; constructing `Options` directly
  silently falls back to the generic English instructions while the console
  still prints the Finnish path. Both scripts print a `Loaded:` excerpt of the
  resolved instructions — keep that visible.
- **The postprocess key is `postprocess_translation`** (read by
  `SubtitleTranslator`), not `postprocess_subtitles`. Preprocessing must be
  invoked explicitly via `preprocess_subtitles(project.subtitles, options)` —
  the project-settings flag is filtered out by `UpdateProjectSettings`.
- **New `Options` keys must be registered in `Options.default_settings`** —
  `GetSettings()` filters to known keys, so an unregistered key never reaches
  the translation client. (`include_line_timings` and `target_cps` exist for
  this reason.)
- **Timing annotations and the parser are a matched pair.** The line template
  emits `#123 [2.4s, max 36 chars]`; `TranslationParser`'s `default_pattern`
  and `fallback_patterns[0..4]` tolerate trailing text after `#N` because
  models echo the header. `fallback_patterns[5]` is deliberately strict — do
  not relax it (it rescues translations placed on the number line).
- **Keep the budget consistent in three places:** the prompt annotation
  (15 CPS, cap `2 × 42` chars), `instructions/instructions_fi.txt` (which
  explains the annotation, the 2-line/42-char limits, and dialogue-dash
  inference), and the `fix-finnish-subs` flags in `exsubs.py`
  (`--width-limit 42 --max-cps 17 --cps-target 15`).
- **Line correspondence is a hard invariant.** In fast-paced scenes the model
  used to condense across lines and silently shift content onto earlier cue
  numbers, desyncing text from audio by up to ~35 s. Three guards keep it
  aligned — keep all three intact: (1) `instructions_fi.txt` rule 6b mandates
  explicit `[MERGE N+M]` (handled by `TranslationParser.MatchTranslations`,
  which extends the cue's end time) and forbids moving content between
  numbers; (2) `merge_continuation_duration`/`merge_continuation_gap`
  (1.5/0.3 in both scripts) pre-merge rapid-fire continuation fragments —
  text and timing atomically — in `SubtitleProcessor` before the model sees
  them; (3) `max_translation_cps` (25.0 in both scripts) makes
  `SubtitleValidator` raise `ReadingSpeedError` so over-budget lines (the
  drift signature) trigger a batch retry. Lines of ≤16 raw chars are exempt,
  matching the prompt annotation's budget floor.
- **Run `fix-finnish-subs` exactly once** — its fixer chain is not idempotent
  (merges chain further, gap fixing re-shifts timings). Do not reintroduce a
  retry loop.
- **Temperature policy:** Gemini 3.x stays at 1.0 (documented repetition-loop
  risk when lowered); other providers default to 0.3. `GEMINI_3_MODEL_PATTERN`
  in both scripts — change both or neither.
- **Measure, don't eyeball:** `tools/subtitle_metrics.py` reports raw-character
  line lengths, CPS, display times, dialogue-dash counts, and cue-count delta
  vs the source. Run it before/after on a real episode for any change touching
  translation quality, and compare against the kept baseline artifacts.

## Information

Consult `docs/architecture.md` for detailed information on the project architecture and components.

## Upstream Sync (Fork Maintenance)

- `upstream` should point at the original repo, `https://github.com/machinewrapped/llm-subtrans.git`, but it is **not configured by default** — this checkout has only `origin`. Verify with `git remote -v` and add it before syncing:
  `git remote add upstream https://github.com/machinewrapped/llm-subtrans.git`
- Preferred workflow for public main: merge (not rebase) to preserve history and avoid force-pushes.
- Keep our CLI-only customizations intact: do not reintroduce GUI or Windows-specific code.
- Keep only the Gemini, OpenAI and Claude providers. When upstream changes a provider we deleted (Azure, Bedrock, Custom Server, DeepSeek, Mistral, OpenRouter), resolve the conflict by keeping it deleted.

### Quick Steps

- Fetch upstream: `git fetch upstream`
- Review ahead/behind:
  - Upstream ahead: `git log --oneline main..upstream/main`
  - Fork ahead: `git log --oneline upstream/main..main`
- Merge: `git merge upstream/main` and resolve conflicts by
  - Preserving: top-level `pyproject.toml` (CLI description/version, scripts) and Linux/macOS-only constraints
  - Adopting: changes under `PySubtrans/` (core engine), tests, locales
- Validate after merge: `make check && make test`
- Push if green: `git push origin main`

### Prompt Template (for Codex)

- "Merge upstream/main into our main; preserve CLI-only fork constraints (no GUI/Windows), keep top-level pyproject CLI metadata/scripts, adopt upstream PySubtrans changes, run checks/tests with make, and push to origin/main if tests pass."

### When to Rebase

- Only if a linear history is required and force-push is acceptable. Otherwise, prefer merges.

## Repository Hygiene

- Never print or read `.env` contents.
- New files or scripts must not add imports in the middle of functions.
- Use `regex` (not `re`) for regular expressions.
