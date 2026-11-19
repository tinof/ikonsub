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

### Next up (checked 2026-11-18 at main@999e6f9)
<!-- roadmap-next: rev=7 scope=all config=4ea1c07a9b1a probes=credentials:ok,gemini-api-key:fail,media-tools:ok ready=b00b1cd5cea7 -->

1. **E5.14 Write `tools/check_pure_move.py`.** Gate: a fixture pure move passes and a one-line edit
   fails. Why: unblocks E5.2-E5.4 and the E1 chain. Branch: `chore/e5-14-check-pure-move`.

**Reassess:** owner-decision (F5.1, PR #32): the owner set the resume behaviour (sidecar state,
full run on changed settings, cleanup on success); no Focus step is open now.

### Waiting for the owner

Steps that need a decision, material or a test only the owner can provide. Other steps of the
same item stay startable. `/ship:roadmap-next` reads this list; remove an entry when it is settled.

- **D3.2, F1.4, F1.5, F2.2, F3.1, F4.2: paid live runs.** Run them, or approve a scope and a budget
  for an agent run; `/ship:roadmap-batch` never starts them.
- **E2.3: tag the first release (`0.1.0`).** It pushes a tag and adds `## Release` to `.claude/workflow.md`.
- **E3.1: name the reference episodes and where they live.** `AGENTS.md:177` asks for a comparison
  against "the kept baseline artifacts", but no path names them.

## Priorities

Order (2026-11-05, owner's choice): failures that end or silently degrade a run first, then the
measurement every quality change needs, then configuration that drifts between entry points, then
maintainability, then translation-quality features, then throughput and releases.

1. **Run reliability** (D1, D3, F5). A transient provider error or an API change must not end a
   run, and a failed run must not start over from zero. Outcome: A dropped connection or a provider
   API change no longer ends a translation, and a failed run picks up where it stopped. Gate: each item's tests, plus one live run per
   provider path that D1 and D3 touch.
2. **Quality baseline** (E3). A repeatable before/after comparison on named reference episodes.
   Outcome: Any prompt or batching change is judged in one command against the same reference
   episodes. Gate: `tools/` compares a new translation with the baseline in one command.
3. **Configuration consistency** (D4). One parser for boolean env vars and one source for default
   models. Outcome: Every command reads the same defaults, and an exported variable means the same
   thing everywhere. Gate: tests that every entry point reads the same value.
4. **Maintainability** (E1, E5, E4). One copy of the shared pipeline code, files and functions within
   the size limits, no orphan TODOs. Outcome: The pipeline code exists once, files and functions fit
   the size limits, and no unexplained TODO remains. Gate: `make check` with PLR0915 on and no markers left.
5. **Translation quality** (F1, F2, F3). Structured output, a global glossary and smarter batching,
   each behind a setting until measured. Outcome: Names stay spelled the same through an episode and
   replies are read without regex guesswork, each change proven on the baseline before it becomes
   the default. Gate: `subtitle_metrics` on the E3 baseline no worse, and the
   targeted metric better.
6. **Throughput and release** (F4, E2). Parallel Gemini batches on an API key, and versioned
   releases with a changelog. Outcome: An API-key user gets the fastest safe batch mode by default,
   and installs a numbered release with a changelog. Gate: a measured default for F4, and a tagged release.

**Focus (set 2026-11-17):** D1, F5.1, D4; retries and resume end the failures that stop a run, and
the owner's `gpt-6-luna` default lands in one constant. D3.1 waits until a Gemini API key can run
D3.2; priority 1's gate also needs live runs, so desk steps alone do not close it.

## Defects

### D1: OpenAI connection drops end the run

- **Status:** Core complete; OpenAI and Gemini retry dropped connections and streams (D1.1, D1.2, in
  the log). No step is open; the item stays because priority 1's gate and the Focus line name it.

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

- **Status:** Core complete; every entry point reads one default per provider from
  `PySubtrans/MKV/Config.py`, and OpenAI defaults to `gpt-6-luna` (D4.1, D4.2, in the log). No step is
  open; the item stays because priority 3 names it, and the owner decides whether that priority ends.

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
- [F4.1][desk][batch] Correct the docstring and the readme on parallel mode with an API key. Gate:
  `make check`.
- [F4.2][owner][live][api-key][reassess] Measure throughput and 429s for parallel and sequential runs on a Gemini API
  key, and set the default from the result. Gate: the measurement in the log. Needs: F4.1.

### F5: Resume a failed translation

- **Status:** Core complete; a failed run resumes from its finished batches (F5.1, in the log). No
  step is open; the item stays because the Focus line names F5.1.

### F6: Claude batches translate without the lines around them

- **Status:** Open; found in a Sonnet 5.5 probe on 2026-11-19 (Blue Lights S04E05).
- A Claude batch sees one-line summaries of earlier batches only: not its own earlier Finnish, not
  the next lines, not the title. Sentences cut at a batch edge lose their start, a reply is
  translated without its question ("Nah, I'm telling ya" -> "En vaadi, vaan kerron"), and register
  drifts. The prompt line says `for [movie] into ... [language]` literally. The owner chose a
  context block in each prompt over a cached conversation (2026-11-19: about +3% per episode
  against +24-35%).
- [F6.1][desk] Sequential batches can show the last N source lines with their translations and the
  next M source lines, behind `context_lines` and `lookahead_lines` (default 0). Gate: `make check &&
  make test`; tests show default prompts carry only the old tags and the parallel path unchanged.
- [F6.2][desk] `transubs --title` and `--description` put the title and a description into the
  project settings of a single-file run. Gate: `make check && make test`; a run without the flags
  sends the same settings as before.
- [F6.3][owner][live][reassess] Claude runs default to `context_lines` 20, `lookahead_lines` 5 and
  larger batches, and the prompt line substitutes title and language. Gate: `make check && make
  test`; `subtitle_metrics` before and after on Blue Lights S04E05 no worse, the probe's named lines
  read correct, cost per episode in the log. Needs: F6.1, F6.2.

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
- **Decided 2026-11-17 (owner):** the settings-file version gets its own constant, pinned at
  `v1.5.6` in `PySubtrans/Options.py` (`default_settings["version"]`, the check at `:241-243`), so a
  saved `v1.5.6` file never looks newer than a `0.x` release. Not taken: settings follow the package
  version.
- [E2.2][desk][batch] Read `PySubtrans.__version__` from `importlib.metadata`, keep the settings
  version pinned at `v1.5.6` in `Options.py`, and start `CHANGELOG.md`. Gate: a test that
  `__version__` equals `importlib.metadata.version("ikonsub")`, and a test that a saved `v1.5.6`
  settings file is not migrated and stays `v1.5.6`. Needs: E2.1.
- [E2.3][owner] Tag `0.1.0` and add a `## Release` section to `.claude/workflow.md`. Gate:
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

- **Status:** Open; ruff `PLR0915` fails new functions over 50 statements and two are split (E5.1,
  E5.7, E5.8, in the log); 3 files over 800 lines and 8 marked functions remain.
- [E5.2][desk] Split `scripts/exsubs.py` (1476 lines, limit 800) by pure moves; the shared pipeline
  helpers go into one module `scripts/transubs.py` can use. Gate: `make check && make test`, and
  `git diff --color-moved=dimmed-zebra` shows only moved blocks. Needs: E5.14.
- [E5.3][desk] Split `scripts/transubs.py` (1129 lines, limit 800) by pure moves. Gate: `make check &&
  make test`, and a diff that shows only moved blocks. Needs: E5.14.
- [E5.4][desk] Split `PySubtrans/SubtitleTranslator.py` (983 lines, limit 800) by pure moves. Gate:
  `make check && make test`, and a diff that shows only moved blocks. Needs: E5.14.
- [E5.5][desk] Split `translate_srt_file` (172 statements, `scripts/transubs.py:546`) by phase. Gate:
  `make check && make test`, behaviour unchanged. Needs: E5.3.
- [E5.6][desk] Split `translate_subtitles` (132 statements, `scripts/exsubs.py:920`) by phase. Gate:
  `make check && make test`, behaviour unchanged. Needs: E5.2.
- [E5.9][desk] Split `main` (68 statements, `scripts/exsubs.py:1277`) by phase. Gate: `make check &&
  make test`, behaviour unchanged. Needs: E5.2.
- [E5.10][desk] Split `SubtitleTranslator._translate_parallel` (55 statements,
  `PySubtrans/SubtitleTranslator.py:229`) by phase. Gate: `make check && make test`, behaviour
  unchanged. Needs: E5.4.
- [E5.11][desk] Split `process_video_file` (54 statements, `scripts/exsubs.py:752`) by phase. Gate:
  `make check && make test`, behaviour unchanged. Needs: E5.2.
- [E5.12][desk] Split `OpenAIClient._try_send_messages` (56 statements,
  `PySubtrans/Providers/Clients/OpenAIClient.py:104`) by phase. Gate: `make check && make test`,
  behaviour unchanged. Needs: D1.1.
- [E5.14][desk] Write `tools/check_pure_move.py`, the `move-gate` of `.claude/workflow.md`: for the
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
