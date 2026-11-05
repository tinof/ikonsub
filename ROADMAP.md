# ikonsub Roadmap

This is the single source of truth for open `ikonsub` work. Closed steps, measurements and long
evidence are in [`docs/ROADMAP_LOG.md`](docs/ROADMAP_LOG.md).

> **North star:** Finnish (and other) subtitles from MKV and subtitle files that stay in sync with the
> audio and read comfortably, judged by `tools/subtitle_metrics.py` on real episodes and by runs that
> finish without manual retries.

Status meanings: **Open** has not shipped; **Partial** has useful pieces in place but does not meet
the gate; **Core complete** meets the gate but keeps named follow-up work; **Deferred** is
intentionally not scheduled. Items with no step left move to the log.

**Step format.** Every open step is one line that `/ship:roadmap-next` reads:
`- [<ID>][tags] <step>. Gate: <evidence>.` with an optional `Needs: <ID>[, <ID>]` for a prerequisite
(`Needs: <ID> (code)` also accepts a step that has landed with its gate pending). IDs are
`<item>.<n>` (for example `D1.2`), assigned once and never reused or renumbered. A new step takes 1 +
the highest number ever used for that item in `ROADMAP.md`, `docs/ROADMAP_LOG.md` and `Roadmap-Step:`
commit trailers. A step without a gate, or with `Gate: TODO(owner)`, waits for the owner.

| Tag | Meaning |
|---|---|
| `desk` | Code or doc work; needs nothing |
| `owner` | Needs the owner: a decision, material or a test only they can run |
| `reassess` | A stop point: its outcome decides what follows, so the next run re-plans |
| `live` | A paid provider run: needs credentials (probe `credentials`) and the media tools (probe `media-tools`). Always paired with `owner`: the owner runs it or approves its scope |
| `api-key` | Needs a Gemini API key in the environment (probe `gemini-api-key`): the Developer API path, which Vertex runs do not exercise |

**How to update.** Format: `~/.claude/skills/ship/reference/roadmap-format.md`. This file holds open
work only; closed steps, Done notes and measurements go to the log.
- When a step ships, `/ship` removes its line, rewrites the item's Status to its current state (at
  most three lines), and adds the step's full text with the gate evidence to the log under
  `## <date>: <ID> <title> (PR #<n>)`.
- A step whose gate needs evidence that does not exist yet stays, with `Landed: PR #<n>; gate
  pending.` appended.
- An item with no steps left moves to the log.

**Next up** is a checkpoint: `/ship` promotes it when a step ships, or writes a **Reassess** line when
what it learned changes the plan, and `/ship:roadmap-next` confirms it instead of re-ranking unless
something changed. A **Checked** bullet (date, commit, `file:line`) on each examined item lets a later
run re-check only what changed since that commit.

### Next up

Filled in by the first `/ship:roadmap-next` run.

### Waiting for the owner

Steps that need a decision, material or a test only the owner can provide. Other steps of the
same item stay startable. `/ship:roadmap-next` reads this list; remove an entry when it is settled.

- **D2.1: decide how boolean env vars parse.** Accepting `true` in the providers switches
  `batch-translate` and `gemini-subtrans` to Vertex for anyone who followed `exsubs`' advice to export
  `GEMINI_USE_VERTEX=true` (`scripts/exsubs.py:702`).
- **D4.2: choose the OpenAI default model.** Moving `--mode chatgpt` from `gpt-4o-mini` to a GPT-5
  model switches users to the reasoning client and changes cost and latency.
- **E2.1: choose the first ikonsub version.** No tags exist, so the wheel says `0.0.1.devN` while
  `PySubtrans/version.py` says `v1.5.6`.
- **D3.2, F1.4, F1.5, F2.2, F3.1, F4.2: paid live runs.** Run them, or approve a scope and a budget
  for an agent run; `/ship:roadmap-batch` never starts them.
- **E2.3: tag the first release.** It pushes a tag and adds `## Release` to `.claude/workflow.md`.
- **E3.1: name the reference episodes and where they live.** `AGENTS.md:177` asks for a comparison
  against "the kept baseline artifacts", but no path names them.

## Priorities

Order (2026-11-05, owner's choice): failures that end or silently degrade a run first, then the
measurement every quality change needs, then configuration that drifts between entry points, then
maintainability, then translation-quality features, then throughput and releases.

1. **Run reliability** (D1, D3, F5). A transient provider error or an API change must not end a
   run, and a failed run must not start over from zero. Gate: each item's tests, plus one live run per
   provider path that D1 and D3 touch.
2. **Quality baseline** (E3). A repeatable before/after comparison on named reference episodes.
   Gate: `tools/` compares a new translation with the baseline in one command.
3. **Configuration consistency** (D2, D4). One parser for boolean env vars and one source for default
   models. Gate: tests that every entry point reads the same value.
4. **Maintainability** (E1, E5, E4). One copy of the shared pipeline code, files and functions within
   the size limits, no orphan TODOs. Gate: `make check` with PLR0915 on and no markers left.
5. **Translation quality** (F1, F2, F3). Structured output, a global glossary and smarter batching,
   each behind a setting until measured. Gate: `subtitle_metrics` on the E3 baseline no worse, and the
   targeted metric better.
6. **Throughput and release** (F4, E2). Parallel Gemini batches on an API key, and versioned
   releases with a changelog. Gate: a measured default for F4, and a tagged release.

## Defects

### D1: OpenAI connection drops end the run

- **Status:** Open; Claude retries dropped connections since PR #5, OpenAI does not.
- `OpenAIClient._try_send_messages` raises `TranslationError` on `openai.APIConnectionError` without a
  retry (`PySubtrans/Providers/Clients/OpenAIClient.py:182`). A transport error mid-stream reaches its
  `except Exception` and becomes `TranslationImpossibleError`, which stops the run
  (`OpenAIClient.py:202`).
- [D1.1][desk] Retry `APIConnectionError` and mid-stream transport errors in the OpenAI chat and
  Responses clients with the existing backoff, resetting stream progress as `AnthropicClient` does.
  Gate: tests where a connection error before the response and a drop mid-stream are retried and the
  retry's text is used; `make check && make test`.
- [D1.2][desk] Check `GeminiClient._send_messages` (`GeminiClient.py:314`) for the same gap and retry
  a dropped stream there too. Gate: a test of a mid-stream drop on the Gemini stream that retries and
  succeeds.

### D2: Boolean env vars parse differently in scripts and providers

- **Status:** Open; needs an owner decision first.
- The scripts accept `1/true/yes/on` (`scripts/exsubs.py:536`, `scripts/transubs.py:492`), while
  `Provider_Gemini` accepts only the exact string `True` (`PySubtrans/Providers/Provider_Gemini.py:96`).
- [D2.1][owner] Decide which spellings count as true everywhere, and whether the provider default may
  change for existing setups. Gate: the decision recorded in `docs/ROADMAP_LOG.md`.
- [D2.2][desk] One shared `env_bool` helper used by the scripts and the providers, per the decision.
  Gate: tests of each accepted spelling through both a script and a provider. Needs: D2.1.

### D3: Gemini API-key path uses v1alpha and a deprecated harm category

- **Status:** Open.
- The API-key client pins `api_version = "v1alpha"` (`PySubtrans/Providers/Clients/GeminiClient.py:587`,
  `PySubtrans/Providers/Provider_Gemini.py:284`); the SDK default is `v1beta`.
  `HARM_CATEGORY_CIVIC_INTEGRITY` (`GeminiClient.py:167`) is deprecated in google-genai.
- [D3.1][desk] Drop the `v1alpha` overrides and the civic-integrity setting. Gate: unit tests that the
  API-key client and the model listing use the SDK default API version and send no civic-integrity
  setting; `make check && make test`.
- [D3.2][owner][live][api-key] One live API-key run with `thinking_level` set that translates a batch
  on `v1beta`; revert D3.1 if it fails. Gate: the run's result in the log. Needs: D3.1.

### D4: Default model names are spread over the entry points

- **Status:** Open.
- Defaults live in `scripts/exsubs.py:39,52`, `scripts/transubs.py:30,47`, `scripts/gpt_subtrans.py:22`,
  `scripts/gemini_subtrans.py:22` and `scripts/claude_subtrans.py:47`, and have drifted before.
- [D4.1][desk] Move each provider's default model into one module that every script reads, values
  unchanged. Gate: a test that each CLI's default equals the shared constant.
- [D4.2][owner] Choose the OpenAI default model and set it in the shared constant. Gate: the decision
  in the log and the test updated. Needs: D4.1.

## Features

### F1: Structured output instead of regex parsing

- **Status:** Open; from `development-ideas.md` idea 1.
- `TranslationParser` extracts line numbers and text with regex patterns that the Finnish pipeline
  depends on (`AGENTS.md`, "Timing annotations and the parser are a matched pair").
- [F1.1][desk][reassess] Write `docs/structured-output.md`: the response schema per provider (Gemini
  `response_schema`, OpenAI Responses `json_schema`, Claude), how `[MERGE N+M]`, timing annotations
  and `names` map onto it, the fallback to `TranslationParser`, and the measurement plan. Gate: the
  doc merged and the owner's go or no-go in the log.
- [F1.2][desk] Implement it for Gemini behind a `structured_output` setting (default off, registered in
  `Options.default_settings`). Gate: unit tests with a mocked Gemini client, including `[MERGE N+M]`
  and the fallback to `TranslationParser`. Needs: F1.1.
- [F1.3][desk] Extend it to OpenAI and Claude, still default off. Gate: the same unit tests per
  provider. Needs: F1.2.
- [F1.4][owner][live] Measure Gemini structured output on a reference episode. Gate: `subtitle_metrics`
  with a cue-count delta of 0 and CPS no worse than the regex path, in the log. Needs: F1.2.
- [F1.5][owner][live][reassess] Measure OpenAI and Claude the same way, and decide the default per
  provider. Gate: the measurements and the decision in the log. Needs: F1.3, F1.4.

### F2: Global glossary pre-pass

- **Status:** Open; from `development-ideas.md` idea 3.
- The context window carries names from the previous batch only; a name from batch 1 can be spelled
  differently in batch 4.
- [F2.1][desk] A pre-pass with a cheap model extracts names and terms from the whole file into the
  existing `names` setting, behind a `glossary_prepass` setting (default off). Gate: `DummyProvider`
  tests showing the glossary reaches every batch prompt.
- [F2.2][owner][live][reassess] Measure name-spelling variants with and without the pre-pass on a reference
  episode, and set the default from the result. Gate: the measurement in the log. Needs: F2.1.

### F3: Semantic batch boundaries

- **Status:** Partial; long scenes split at the longest gap, near the middle on ties (PR #3).
- `development-ideas.md` idea 2 also proposes splitting where the topic changes, using embeddings.
- [F3.1][owner][live][reassess] Measure batch sizes and split points on a dense documentary after PR #3, and
  decide whether a semantic split is worth building. Gate: the measurement and the decision in the
  log.

### F4: Parallel Gemini batches on an API key

- **Status:** Open; workers share RPM pacing since PR #5, but parallel stays Vertex-only.
- The `_parallel_batches` docstring still says workers do not share the RPM pacing
  (`scripts/transubs.py:483`).
- [F4.1][desk] Correct the docstring and the readme on parallel mode with an API key. Gate:
  `make check`.
- [F4.2][owner][live][api-key][reassess] Measure throughput and 429s for parallel and sequential runs on a Gemini API
  key, and set the default from the result. Gate: the measurement in the log. Needs: F4.1.

### F5: Resume a failed translation

- **Status:** Open.
- A failed run moves its output to `<name>.fi.srt.partial` (PR #7), but the next run translates every
  line again; both scripts create `SubtitleProject(persistent=False)` (`scripts/exsubs.py:1112`,
  `scripts/transubs.py:773`).
- [F5.1][desk] Reuse the translated lines of a `.partial` file (or a persistent project) so a re-run
  translates only the missing batches. Gate: a test where a run that failed after 2 of 4 batches
  resumes and sends only batches 3 and 4 to the provider.

## Engineering backlog

### E1: One copy of the exsubs/transubs pipeline code

- **Status:** Open; about 630 duplicated lines, now covered by `tests/PySubtransTests/test_CliPipeline.py`.
- `GEMINI_3_MODEL_PATTERN`, the temperature policy and the `merge_continuation_*` and
  `max_translation_cps` values are defined in both scripts (`AGENTS.md`: "change both or neither").
- [E1.1][desk] Replace `scripts/transubs.py`'s copies of the shared helpers with the module E5.2
  creates. Gate: `test_CliPipeline` green and the settings each script passes to `init_options`
  unchanged. Needs: E5.2.
- [E1.2][desk] Define `GEMINI_3_MODEL_PATTERN`, the temperature policy and the Finnish pipeline values
  once, in code only. Gate: the same settings test, and one definition of each (`rg -c`). Needs: E1.1.
- [E1.3][desk] Update the `AGENTS.md` wording ("change both or neither") to name the single
  definition. Gate: `make check`, and the wording matches the code. Needs: E1.2.

### E2: Versioning and a changelog

- **Status:** Open; needs the owner's version choice.
- [E2.1][owner] Choose the first ikonsub version. Gate: the choice recorded in the log.
- [E2.2][desk] Read `PySubtrans.__version__` from `importlib.metadata` and start `CHANGELOG.md`. Gate:
  a test that `__version__` equals `importlib.metadata.version("ikonsub")`. Needs: E2.1.
- [E2.3][owner] Tag the chosen version and add a `## Release` section to `.claude/workflow.md`. Gate:
  `uv tool list` shows the tagged version. Needs: E2.2.

### E3: Translation-quality baseline

- **Status:** Open; `tools/subtitle_metrics.py` exists, the reference set does not.
- [E3.1][owner] Name two or three reference episodes, their sources and baseline translations, and
  where they live on the host; they become `## Material` in `.claude/workflow.md`. Gate: the paths in
  the contract and `test -e` passing on each.
- [E3.2][desk] Add a `tools/` command that runs `subtitle_metrics` on a new translation and its
  baseline and prints the deltas. Gate: a unit test on fixture files, and one run on a reference
  episode. Needs: E3.1.

### E4: Orphan TODOs

- **Status:** Open; four TODO comments without a roadmap step.
- `PySubtrans/SubtitleProcessor.py:183-184` (minimum durations, overlapping times),
  `PySubtrans/SubtitleEditor.py:126` (untranslated line after an edit),
  `PySubtrans/SubtitleTranslator.py:965` (merge through `SubtitleEditor`).
- [E4.1][desk] Fix each TODO with a test, or delete it and add a roadmap step. Gate: `rg -i todo
  PySubtrans scripts` finds none without a step ID.

### E5: Code size

- **Status:** Open; 3 files over 800 lines, 8 functions over 50 statements, `AGENTS.md` over its
  8,000-byte budget (scan of 2026-11-05).
- PLR0915 is not in ruff's `select` (`pyproject.toml`), so nothing stops new long functions.
- [E5.1][desk] Add ruff `PLR0915` at `lint.pylint.max-statements = 50` and mark the 8 current
  violations with `# noqa: PLR0915 -- E5.<n>`, naming each one's split step. Gate: `make check` green,
  and a probe function with 51 statements makes it fail.
- [E5.2][desk] Split `scripts/exsubs.py` (1476 lines, limit 800) by pure moves; the shared pipeline
  helpers go into one module `scripts/transubs.py` can use. Gate: `make check && make test`, and
  `git diff --color-moved=dimmed-zebra` shows only moved blocks. Needs: E5.14.
- [E5.3][desk] Split `scripts/transubs.py` (1129 lines, limit 800) by pure moves. Gate: `make check &&
  make test`, and a diff that shows only moved blocks. Needs: E5.14.
- [E5.4][desk] Split `PySubtrans/SubtitleTranslator.py` (983 lines, limit 800) by pure moves. Gate:
  `make check && make test`, and a diff that shows only moved blocks. Needs: E5.14.
- [E5.5][desk] Split `translate_srt_file` (165 statements, `scripts/transubs.py:550`) by phase. Gate:
  `make check && make test`, behaviour unchanged. Needs: E5.3.
- [E5.6][desk] Split `translate_subtitles` (125 statements, `scripts/exsubs.py:918`) by phase. Gate:
  `make check && make test`, behaviour unchanged. Needs: E5.2.
- [E5.7][desk] Split `run_diagnostics` (81 statements, `PySubtrans/MKV/Diagnostics.py:8`) by phase.
  Gate: `make check && make test`, behaviour unchanged.
- [E5.8][desk] Split `BatchProcessor.run` (72 statements, `scripts/batch_translate.py:122`) by phase.
  Gate: `make check && make test`, behaviour unchanged.
- [E5.9][desk] Split `main` (68 statements, `scripts/exsubs.py:1264`) by phase. Gate: `make check &&
  make test`, behaviour unchanged. Needs: E5.2.
- [E5.10][desk] Split `SubtitleTranslator._translate_parallel` (55 statements,
  `PySubtrans/SubtitleTranslator.py:229`) by phase. Gate: `make check && make test`, behaviour
  unchanged. Needs: E5.4.
- [E5.11][desk] Split `process_video_file` (54 statements, `scripts/exsubs.py:750`) by phase. Gate:
  `make check && make test`, behaviour unchanged. Needs: E5.2.
- [E5.12][desk] Split `OpenAIClient._try_send_messages` (52 statements,
  `PySubtrans/Providers/Clients/OpenAIClient.py:106`) by phase. Gate: `make check && make test`,
  behaviour unchanged. Needs: D1.1.
- [E5.13][desk] Trim AGENTS.md (10615 bytes, budget 8000): move detail to its topic doc or the log.
  Gate: `wc -c AGENTS.md` at most 8000, with every rule still in `AGENTS.md` or linked from it.
- [E5.14][desk] Write `tools/check_pure_move.py`, the `move-gate` of `.claude/workflow.md`: for the
  changed Python files between `--base` and HEAD, the top-level functions, classes and assignments
  must match by AST before and after (only imports and `__all__` may change); it accepts and logs
  `--step <ID>`. Gate: tests where a fixture pure move passes and a move with one edited line fails;
  `make check && make test`.

## Explicit non-goals

- No GUI and no Windows support: ikonsub is a CLI for Linux and macOS.
- Only the Gemini, OpenAI and Claude providers.
- Nothing is merged from or sent to upstream LLM-Subtrans.
