# Workflow contract

What `/ship`, `/ship:roadmap-next` and `/ship:roadmap-init` need to know about this repository. The schema,
with every key and section, is `~/.claude/skills/ship/reference/contract.md`. Keep every `##` heading
exactly as written: the skills look sections up by name. A missing section, or one whose body is `none`,
turns that part off. A diff that touches this file is always Tier 2.

## Settings

```
contract:       1.2
repo:           tinof/ikonsub
base:           main
merge:          squash
branch:         <type>/<id>-<slug>
roadmap:        ROADMAP.md
roadmap-log:    docs/ROADMAP_LOG.md
roadmap-memory: on
push-roadmap:   no
docs-prefix:    "docs(roadmap): "
gate:           make check && make test
gate-focused:   uv run pytest -q -k '<pattern>'
gate-fallback:  none
gate-skip:      'readme.md' 'docs/**' 'development-ideas.md' 'PySubtrans/README.md'
ci:             pr-checks
logic:          'PySubtrans/**' 'scripts/**' 'tools/**'
tests:          'tests/**'
self-check:     none
review-surfaces: exec
self-review:    allowed
```

- With `merge: squash` the PR body becomes the commit body: follow the PR body format in `~/.claude/CLAUDE.md` (no headings or checklists, no hard wrap).

## Gates

none

## Tier 2 paths

```
instructions/**
PySubtrans/TranslationParser.py
PySubtrans/SubtitleValidator.py
PySubtrans/SubtitleProcessor.py
PySubtrans/Options.py
PySubtrans/TranslationPrompt.py
PySubtrans/SubtitleTranslator.py
scripts/exsubs.py
scripts/transubs.py
PySubtrans/Helpers/Resources.py
pyproject.toml
uv.lock
.github/**
AGENTS.md
CLAUDE.md
.claude/**
.agents/**
```

The instruction templates, the parser, the validator, the continuation pre-merge, `Options`, the prompt
builder (timing annotations), `SubtitleTranslator` (the `postprocess_translation` key) and the two CLI
scripts (pipeline settings, the `fix-finnish-subs` call) together carry the Finnish readability contract
in `AGENTS.md`, which has broken silently before.
`Resources.py` resolves the config directories that installed setups depend on. `pyproject.toml` and
`uv.lock` decide what `uv tool install` users get. CI workflows and agent config change how every
later change is checked.

## Routing

default

## Testing rules

- Tests extend `LoggedTestCase` (`PySubtrans/Helpers/TestCases.py`) and use the `assertLogged*` methods.
- No test calls a live provider API. Use `DummyProvider`/`DummyTranslationClient` or mock the SDK client,
  as `tests/PySubtransTests/test_AnthropicClientRetry.py` does.
- CLI tests clear the environment and patch `load_dotenv`, as `tests/PySubtransTests/test_CliPipeline.py`
  does, so a developer's `.env` cannot change the result. Never read `.env`.
- `fix-finnish-subs` runs exactly once per translation; a test must not make it run twice or with a
  timeout or retry.
- A change to translation output (prompts, `instructions/`, parser, validator, batching) needs
  `tools/subtitle_metrics.py` before and after on a real episode. Without the episode on this host, the
  reviewer marks it NEEDS-HUMAN.

## Size limits

- `python`: files '*.py'; file 800; function 50 by `ruff PLR0915`; item E5.
- `claude-md`: files 'CLAUDE.md'; file 24000 bytes; item E5.
- `agents-md`: files 'AGENTS.md'; file 8000 bytes; item E5.
- `roadmap`: files 'ROADMAP.md'; file 48000 bytes; item E5.

## Probes

- `credentials: { test -s .env || printenv GEMINI_API_KEY >/dev/null || printenv OPENAI_API_KEY >/dev/null || printenv ANTHROPIC_API_KEY >/dev/null || { printenv GOOGLE_CLOUD_PROJECT >/dev/null && test -f "$HOME/.config/gcloud/application_default_credentials.json"; }; } && echo "provider credentials found"` some provider can run live: a `.env`, an API key in the environment, or Vertex with gcloud application-default credentials (`.env` is tested for existence, never read).
- `gemini-api-key: printenv GEMINI_API_KEY >/dev/null && echo "GEMINI_API_KEY set"` the Gemini Developer API (API-key) path can run live, not only Vertex.
- `media-tools: command -v ffmpeg ffprobe mkvextract fix-finnish-subs >/dev/null && echo "media tools ok"` the MKV extraction and Finnish fixer tools are on PATH.

## Tags

- `live`: needs probe `credentials`, `media-tools`
- `api-key`: needs probe `gemini-api-key`

## Readiness rules

- A change to translation output (`instructions/**`, `PySubtrans/TranslationParser.py`, `PySubtrans/SubtitleValidator.py`, prompt building or batching) needs a live run with `tools/subtitle_metrics.py` before and after (probe `credentials`).

## Material

none

## Branching

- Types: `feat/`, `fix/`, `docs/`, `chore/`, `refactor/`.
- Sharing rule: two steps of the same item may share a branch when they touch the same file and trigger the same gates (for example two E5 function splits in one script).
- Exceptions: a step that changes translation output (prompts, parser, validator, batching) always has its own branch.

## Release

none

## Unattended

- `move-steps`: E5.2-E5.4
- `move-gate`: `set -o pipefail; git show origin/main:tools/check_pure_move.py | uv run python - --base origin/main`
- `move-review`: gate

## Codex notes

- Codex's read-only sandbox cannot run `uv`, `make` or `pytest` (uv cannot write its cache), and has no network; it reviews by reading. Its "checks could not run" note is expected, not a finding.
- Never ask Codex to read `.env`.

## Reminders

- A `live` step spends provider credits (Gemini, OpenAI, Claude): confirm the run and its scope with the owner before starting it.

## Notes

`gate` is the same check CI runs (`.github/workflows/ci.yml`): ruff, ruff format, basedpyright standard
and pytest. Every needed tool is installed on the Oracle host, so `gate-fallback` is `none`.
`gate-skip` lists only prose docs; `AGENTS.md`/`CLAUDE.md` are excluded on purpose because they carry
rules agents follow. This host runs Gemini through Vertex with gcloud credentials, so `credentials`
accepts that as well as API keys. The repo had no tags or release doc on 2026-11-05, so `## Release` is `none`; E2
in the roadmap sets versioning up. `## Material` stays `none` until E3.1 names the baseline episodes.

`self-review: allowed` (owner, 2026-11-05) lets `/ship:roadmap-batch` build and review with Claude
only, with no Codex cross-family review. Steps that touch the Tier 2 paths above (for example E4.1 in
`SubtitleProcessor.py`, F2.1 in `Options.py`) are better shipped one at a time with `/ship`, which
still routes Codex when asked. The roadmap's `live` tag means a paid provider run, not a deploy: every
`live` step also carries `owner`, so batch skips it and roadmap-next leaves it to the owner. The move
gate runs the base's copy of `tools/check_pure_move.py` (E5.14), so a step cannot change its own
proof; until E5.14 merges, the move steps fall back to a normal review.
