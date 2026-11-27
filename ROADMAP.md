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
| `batch` | Routed to an unattended `/ship:roadmap-batch` run; set by `/ship:roadmap-init` or `/ship:roadmap-assess` with the owner's yes |

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

### Next up (checked 2026-11-26 at main@ab59387)
<!-- roadmap-next: rev=7 scope=all config=4ea1c07a9b1a probes=credentials:ok,gemini-api-key:fail,media-tools:ok ready=b907c0e1de31 -->

1. **D3.1 Drop the Gemini `v1alpha` and civic-integrity overrides.** Gate: unit tests on the SDK
   default API version and no civic-integrity setting; `make check && make test`. Why: priority 1,
   but the Focus line holds it until D3.2 can run on an API key. Branch: `fix/d3-1-gemini-api-version`.
2. **E5.14 Write `tools/check_pure_move.py`.** Gate: a fixture pure move passes and a one-line edit
   fails. Why: unblocks E5.2-E5.4 and the E1 chain. Branch: `chore/e5-14-check-pure-move`.

**Reassess:** owner-decision (steering 2026-11-27): Focus is D6, E3, F9.1, and priority 3 ended.

### Waiting for the owner

Steps that need a decision, material or a test only the owner can provide. Other steps of the
same item stay startable. `/ship:roadmap-next` reads this list; remove an entry when it is settled.

- **D3.2, F1.4, F1.5, F2.2, F3.1, F4.2, F7.2, E3.4, F8.5, F9.2: paid live runs.** Run them, or
  approve a scope and a budget for an agent run; `/ship:roadmap-batch` never starts them.
- **Vertex spend cap:** Gemini runs fail with `403 Spend cap breached` since 2026-11-24. The F8.2
  Gemini gate run and every other Gemini step wait for the owner to raise or reset the cap.
- **E2.3: tag the first release (`0.1.0`).** It pushes a tag and adds `## Release` to `.claude/workflow.md`.

## Priorities

Order (2026-11-05, owner's choice): failures that end or silently degrade a run first, then the
measurement every quality change needs, then maintainability, then translation-quality features,
then throughput and releases. Configuration consistency (D4) ended on 2026-11-27 (owner): one
default per provider and one boolean parser shipped (in the log).

1. **Run reliability** (D3, D6). A transient provider error or an API change must not end a
   run, and a failed run must not start over from zero. Outcome: A dropped connection or a provider
   API change no longer ends a translation, and a failed run picks up where it stopped. Gate: each item's tests, plus one live run per
   provider path that D1 and D3 touch.
2. **Quality baseline** (E3). A repeatable before/after comparison on named reference episodes.
   Outcome: Any prompt or batching change is judged in one command against the same reference
   episodes. Gate: `tools/` compares a new translation with the baseline in one command.
3. **Maintainability** (E1, E5, E4). One copy of the shared pipeline code, files and functions within
   the size limits, no orphan TODOs. Outcome: The pipeline code exists once, files and functions fit
   the size limits, and no unexplained TODO remains. Gate: `make check` with PLR0915 on and no markers left.
4. **Translation quality** (F1, F2, F3, F9). Structured output, a global glossary and smarter batching,
   each behind a setting until measured. Outcome: Names stay spelled the same through an episode and
   replies are read without regex guesswork, each change proven on the baseline before it becomes
   the default. Gate: `subtitle_metrics` on the E3 baseline no worse, and the
   targeted metric better.
5. **Throughput and release** (F4, F8, E2). Parallel Gemini batches on an API key, and versioned
   releases with a changelog. Outcome: An API-key user gets the fastest safe batch mode by default,
   and installs a numbered release with a changelog. Gate: a measured default for F4, and a tagged release.

**Focus (set 2026-11-27):** D6, E3, F9.1; the reading-speed guard must fire before any measurement
counts, the baseline tools judge every quality change in one command, and F9.1 retranslates only the
lines the guard rejects. D6, E3.3 and F9 come with roadmap PR #39, which lands first. D3.1 still
waits until a Gemini API key can run D3.2.

## Defects

### D3: Gemini API-key path uses v1alpha and a deprecated harm category

- **Status:** Open.
- The API-key client pins `api_version = "v1alpha"` (`PySubtrans/Providers/Clients/GeminiClient.py:587`,
  `PySubtrans/Providers/Provider_Gemini.py:284`); the SDK default is `v1beta`.
  `HARM_CATEGORY_CIVIC_INTEGRITY` (`GeminiClient.py:167`) is deprecated in google-genai.
- [D3.1][desk] Drop the `v1alpha` overrides and the civic-integrity setting in
  `PySubtrans/Providers/Clients/GeminiClient.py` and `PySubtrans/Providers/Provider_Gemini.py`. Gate: unit tests that the
  API-key client and the model listing use the SDK default API version and send no civic-integrity
  setting; `make check && make test`.
- [D3.2][owner][live][api-key] One live API-key run with `thinking_level` set that translates a batch
  on `v1beta`; revert D3.1 if it fails. Gate: the run's result in the log. Needs: D3.1.

### D6: The 25 cps reading-speed guard never fires

- **Status:** Open; found 2026-11-24 in a Gemini review and reproduced: a 77-character line on a
  1-second cue passes validation. Measured (D6.2, in the log): the working guard retries 2 to 4 of
  29 batches for single lines, 10 to 20% more cost, no drift in that episode.
- `TranslationParser.ProcessTranslation` validates (`PySubtrans/TranslationParser.py:136`) before
  `MatchTranslations` sets start and end (`:241-244`). Every line has duration 0 then, so
  `PySubtrans/SubtitleValidator.py:92` skips the check. Guard 3 of the line-correspondence invariant
  (`AGENTS.md`) has never run, and every earlier measurement was taken without it.
- [D6.1][desk] Validate `batch.translated` after `MatchTranslations` and the post-processor in
  `SubtitleTranslator.ProcessBatchTranslation`. Gate: a test through `ProcessBatchTranslation` where a
  line over 25 cps raises `ReadingSpeedError` and a `[MERGE]` cue within its extended time does not;
  `make check && make test`.

## Features

### F1: Structured output instead of regex parsing

- **Status:** Open; from `development-ideas.md` idea 1.
- `TranslationParser` extracts line numbers and text with regex patterns that the Finnish pipeline
  depends on (`AGENTS.md`, "Timing annotations and the parser are a matched pair").
- [F1.1][desk] Write `docs/structured-output.md`: the response schema per provider (Gemini
  `response_schema`, OpenAI Responses `json_schema`, Claude), how `[MERGE N+M]`, timing annotations
  and `names` map onto it, the fallback to `TranslationParser`, and the measurement plan. Gate: the
  doc merged, `make check`.
- [F1.6][owner][reassess] Decide go or no-go on structured output from `docs/structured-output.md`.
  Gate: the decision in the log. Needs: F1.1.
- [F1.2][desk] Implement it for Gemini behind a `structured_output` setting (default off, registered in
  `Options.default_settings`). Gate: unit tests with a mocked Gemini client, including `[MERGE N+M]`
  and the fallback to `TranslationParser`. Needs: F1.6.
- [F1.3][desk] Extend it to OpenAI and Claude, still default off. Gate: the same unit tests per
  provider. Needs: F1.2.
- [F1.4][owner][live] Measure Gemini structured output on a reference episode. Gate: `subtitle_metrics`
  with a cue-count delta of 0 and CPS no worse than the regex path, in the log. Needs: F1.2.
- [F1.5][owner][live][reassess] Measure OpenAI and Claude the same way, and decide the default per
  provider. Gate: the measurements and the decision in the log. Needs: F1.3, F1.4.

### F2: Global glossary pre-pass

- **Status:** Open; from `development-ideas.md` idea 3.
- The context window carries names from the previous batch only; a name from batch 1 can be spelled
  differently in batch 4. Parallel batches (F8) see none of each other's Finnish, so the glossary
  matters more there; on Claude it can sit with the description in a second cached system block.
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
- [F4.1][desk][batch] Correct the `_parallel_batches` docstring in `scripts/transubs.py` and
  `readme.md` on parallel mode with an API key. Gate:
  `make check`.
- [F4.2][owner][live][api-key][reassess] Measure throughput and 429s for parallel and sequential runs on a Gemini API
  key, and set the default from the result. Gate: the measurement in the log. Needs: F4.1.

### F7: The Finnish instructions are written for older models

- **Status:** Partial; plain Finnish instructions shipped (F7.1, in the log), not yet run on Gemini.
- Rule 4 (`instructions/instructions_fi.txt:26`) keeps names "samalla tavalla kuin
  `<previous_lines>`-lohkossa", but `context_lines` defaults to 0 and parallel batches never send
  that block, so in a default run the rule points at nothing.
- [F7.2][owner][live] Rule 4 points at the earlier context the prompt does carry (`<history>`, the names
  list, `<previous_lines>` when present). Gate: rule 4 names `<previous_lines>` only as optional and
  the `**KONTEKSTI**` list is unchanged, so the system prompt stays the same for every batch;
  `make check && make test`; `subtitle_metrics` before and after on one live Gemini run no worse.

### F8: Claude batches run one at a time

- **Status:** Open; measured and decided (F8.1, in the log): parallel with 4 workers and 120/30/80
  batches becomes the Claude default, 3.4 times faster at level quality. Building it is F8.2.
- A Claude run streams about 92 output tokens a second through one connection: 105 requests and
  112,240 output tokens took 20 minutes. Batch size does not change that; concurrency does.
- `PARALLEL_BATCHES=1` works for Claude today with three flaws: up to 10,000 uncached history tokens
  per batch (`PySubtrans/Helpers/ContextHelpers.py:340`), a cache write by every first-wave worker,
  and `instructions/instructions_fi.txt:11` calling `<history>` summaries while parallel mode fills
  it with source lines (Gemini's parallel runs carry that label too).
- [F8.2][desk] Claude parallel defaults in both scripts (4 workers, 120/30/80, a 1,500-token
  history), a `parallel_warmup`
  setting that sends the first batch ahead so later ones read the cache, and a `<history>` description
  in `instructions_fi.txt` that fits both modes. Gate: `test_CliPipeline` (Claude settings, env
  overrides, Gemini unchanged), a test that batch 2 is sent only after batch 1's response has
  started, `make check && make
  test`; one live Claude run with a single cache write, and `subtitle_metrics` before and after on
  one Gemini run. Needs: F8.1.
- [F8.3][desk] `CLAUDE_SPEED=fast` sends `speed="fast"` on `claude-opus-5-5` (twice the price, up to
  2.5 times the output speed) and the usage ledger prices such runs; default off (owner,
  2026-11-24). Gate: tests with a mocked client (sent only for Opus 5.5 with the variable set, the
  ledger price); `make check && make test`.
- [F8.4][desk] Parallel context from the last N and the next M source lines across scene edges,
  behind two settings whose default keeps today's history. Gate: `DummyProvider` tests for both
  settings and unchanged default prompts; `make check && make test`.
- [F8.5][owner][live][reassess] Measure F8.4 on Claude and on Gemini (input tokens,
  `subtitle_metrics`, judge) and set each default. Gate: the measurement in the log. Needs: F8.4.

### F9: One failed line regenerates its whole batch

- **Status:** Open; matters once D6.1 makes the reading-speed guard work.
- `RequestRetranslation` takes `line_numbers` and ignores it (`PySubtrans/SubtitleTranslator.py:752`).
  29 to 36% of raw replies exceed the 15 cps budget (F6.3, in the log) and are left to
  `fix-finnish-subs`.
- [F9.1][desk] Behind a `repair_lines` setting (default off): when at most 8 lines or a quarter of a
  batch fail validation, retranslate only those lines, each with two neighbours as read-only context;
  otherwise keep the whole-batch retry, since many fast lines in a row is the drift signature. Gate:
  `DummyProvider` tests for both paths and for a repaired `[MERGE]` cue; `make check && make test`.
  Needs: D6.1.
- [F9.2][owner][live][reassess] Measure F9.1 against D6.2 (requests, cost, cues over 17 and 25 cps,
  judge), and decide the default and whether a condensing pass for lines between 15 and 25 cps
  follows. Gate: the measurement and the decision in the log. Needs: F9.1, D6.2.

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

- **Status:** Open; the first version is `0.1.0` (E2.1, in the log).
- **Decided 2026-11-17 (owner):** the first ikonsub release is `0.1.0`.
- **Decided 2026-11-26 (owner):** the resume fingerprint (`PySubtrans/TranslationResume.py:213`)
  takes the package version, so a `.resume.json` from older code reruns in full after an upgrade. Not
  taken: the pinned settings version.
- **Decided 2026-11-17 (owner):** the settings-file version gets its own constant, pinned at
  `v1.5.6` in `PySubtrans/Options.py` (`default_settings["version"]`, the check at `:241-243`), so a
  saved `v1.5.6` file never looks newer than a `0.x` release. Not taken: settings follow the package
  version.
- [E2.2][desk][batch] Read `PySubtrans.__version__` (`PySubtrans/version.py`) from
  `importlib.metadata`, keep the settings version pinned at `v1.5.6` in `PySubtrans/Options.py`, keep
  the package version in the resume fingerprint (`PySubtrans/TranslationResume.py`), and start
  `CHANGELOG.md`. Gate: a test that
  `__version__` equals `importlib.metadata.version("ikonsub")`, and a test that a saved `v1.5.6`
  settings file is not migrated and stays `v1.5.6`. Needs: E2.1.
- [E2.3][owner] Tag `0.1.0` and add a `## Release` section to `.claude/workflow.md`. Gate:
  `uv tool list` shows the tagged version. Needs: E2.2.

### E3: Translation-quality baseline

- **Status:** Partial; the reference set is Blue Lights S04E05 and Cupertino S01E04 (E3.1, in the
  log; paths under `## Material` in `.claude/workflow.md`). The comparison commands are open, and
  the baselines predate D6.1, so they were made without a working 25 cps guard.
- [E3.2][desk] Add a `tools/` command that runs `subtitle_metrics` on a new translation and its
  baseline and prints the deltas. Gate: a unit test on fixture files, and one run on a reference
  episode. Needs: E3.1.
- [E3.3][desk] `tools/judge_translation.py`: a blind paired judge over a seeded sample of lines from
  two translations of one source, with a fixed rubric (meaning, register, idiom, fits the budget) and
  a selectable judge model, so a Claude run can be scored by another family. Gate: a unit test with
  a mocked client, and one run on a reference episode. Needs: E3.1.
- [E3.4][owner][live] Rerun both reference baselines on `main` after D6.1 (Sonnet 5.5 low, the
  F7.1 instructions, title and description from `context.json`), so a comparison measures the change
  and not the guard's retries. Gate: the new `baseline.fi.srt` files at the `## Material` paths and
  their `subtitle_metrics` in the log. Needs: D6.1.

### E4: Orphan TODOs

- **Status:** Open; four TODO comments without a roadmap step.
- `PySubtrans/SubtitleProcessor.py:183-184` (minimum durations, overlapping times),
  `PySubtrans/SubtitleEditor.py:126` (untranslated line after an edit),
  `PySubtrans/SubtitleTranslator.py:965` (merge through `SubtitleEditor`).
- [E4.1][desk] Fix each TODO with a test, or delete it and add a roadmap step. Gate: `rg -i todo
  PySubtrans scripts` finds none without a step ID.

### E5: Code size

- **Status:** Open; ruff `PLR0915` fails new functions over 50 statements and two are split (E5.1,
  E5.7, E5.8, in the log); 3 files over 800 lines and 8 marked functions remain.
- [E5.2][desk][batch] Split `scripts/exsubs.py` (1476 lines, limit 800) by pure moves; the shared pipeline
  helpers go into one module `scripts/transubs.py` can use. Gate: `make check && make test`, and
  `git diff --color-moved=dimmed-zebra` shows only moved blocks. Needs: E5.14.
- [E5.3][desk][batch] Split `scripts/transubs.py` (1129 lines, limit 800) by pure moves. Gate: `make check &&
  make test`, and a diff that shows only moved blocks. Needs: E5.14.
- [E5.4][desk][batch] Split `PySubtrans/SubtitleTranslator.py` (983 lines, limit 800) by pure moves. Gate:
  `make check && make test`, and a diff that shows only moved blocks. Needs: E5.14.
- [E5.5][desk] Split `translate_srt_file` (172 statements, `scripts/transubs.py:546`) by phase. Gate:
  `make check && make test`, behaviour unchanged. Needs: E5.3.
- [E5.6][desk] Split `translate_subtitles` (132 statements, `scripts/exsubs.py:920`) by phase. Gate:
  `make check && make test`, behaviour unchanged. Needs: E5.2.
- [E5.9][desk][batch] Split `main` (68 statements, `scripts/exsubs.py:1277`) by phase. Gate: `make check &&
  make test`, behaviour unchanged. Needs: E5.2.
- [E5.10][desk][batch] Split `SubtitleTranslator._translate_parallel` (55 statements,
  `PySubtrans/SubtitleTranslator.py:229`) by phase. Gate: `make check && make test`, behaviour
  unchanged. Needs: E5.4.
- [E5.11][desk][batch] Split `process_video_file` (54 statements, `scripts/exsubs.py:752`) by phase. Gate:
  `make check && make test`, behaviour unchanged. Needs: E5.2.
- [E5.12][desk][batch] Split `OpenAIClient._try_send_messages` (56 statements,
  `PySubtrans/Providers/Clients/OpenAIClient.py:104`) by phase. Gate: `make check && make test`,
  behaviour unchanged. Needs: D1.1.
- [E5.14][desk][batch] Write `tools/check_pure_move.py`, the `move-gate` of `.claude/workflow.md`: for the
  changed Python files between `--base` and HEAD, the top-level functions, classes and assignments
  must match by AST before and after (only imports and `__all__` may change); it accepts and logs
  `--step <ID>`. Gate: tests where a fixture pure move passes and a move with one edited line fails;
  `make check && make test`.
- [E5.15][desk][batch] Split the test methods `test_SubtitleBatches` (172 statements,
  `tests/PySubtransTests/test_ChineseDinner.py:102`) and `test_SubtitleEditor_UpdateLine` (85, `:486`)
  into helpers or smaller methods and drop their `PLR0915` markers. Gate: `make check && make test`,
  with the same assertions still run.

## Explicit non-goals

- No GUI and no Windows support: ikonsub is a CLI for Linux and macOS.
- Only the Gemini, OpenAI and Claude providers.
- Nothing is merged from or sent to upstream LLM-Subtrans.
