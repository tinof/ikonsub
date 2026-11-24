# ikonsub Roadmap Log

Closed roadmap steps go here as `## <date>: <ID> <title> (PR #<n>)`, with the step's text and its gate
evidence. Open work is in [`ROADMAP.md`](../ROADMAP.md).

## 2026-11-27: Steering ends priority 3 and moves D1, D4 and F5 (steering)

- **Decided 2026-11-27 (owner):** priority 3, Configuration consistency, ends: D2 and D4 shipped one
  boolean parser and one default model per provider, and no step was open. D4 moves here with it.
- D1 and F5 stayed only because the 2026-11-17 Focus named them; the new Focus (D6, E3, F9.1) does
  not, so they move here too.
- The agent decisions of 2026-11-17 (E5.1, D4.1) and 2026-11-18 (D1.2) were kept.

### D1: OpenAI connection drops end the run (moved from the roadmap)

- **Status:** Core complete; OpenAI and Gemini retry dropped connections and streams, parallel
  Gemini batches included (D1.1-D1.3, in the log). No step is open; the item stays because the Focus
  line names D1.

### D4: Default model names are spread over the entry points (moved from the roadmap)

- **Status:** Core complete; every entry point reads one default per provider from
  `PySubtrans/MKV/Config.py`, and OpenAI defaults to `gpt-6-luna` (D4.1, D4.2, in the log). No step is
  open; the item stays because priority 3 names it, and the owner decides whether that priority ends.

### F5: Resume a failed translation (moved from the roadmap)

- **Status:** Core complete; a failed run resumes from its finished batches (F5.1, in the log). No
  step is open; the item stays because the Focus line names F5.1.

## 2026-11-26: D1.3 Parallel Gemini batches stream too (PR #45)

- [D1.3][desk][batch] `GeminiClient` (`PySubtrans/Providers/Clients/GeminiClient.py`) streams whenever streaming is enabled, with or without a callback, as
  `AnthropicClient` does. Gate: mocked-client tests that a parallel batch uses
  `generate_content_stream` and that a parallel stream ending without `finish_reason` is retried;
  `make check && make test`.
- Before: parallel batches call `RequestTranslation` without a callback
  (`PySubtrans/SubtitleTranslator.py:384`), so `GeminiClient` sent a blocking `generate_content`.
  D1.2's missing-`finish_reason` retry never ran there, and `request_timeout` capped the whole reply.
- Resolution: `_get_gemini_response` streams whenever `enable_streaming` is set, with or without a
  callback, as `AnthropicClient` does; `stream_responses` off still sends one blocking request.
  `TranslationRequest` is unchanged: with no callback it only accumulates text.
- Evidence: `tests/PySubtransTests/test_GeminiClientRetry.py::GeminiParallelStreamTests` (the two
  parallel tests fail on the pre-fix code); `make check && make test` 550 passed; CI on PR #45.
  CodeRabbit cold review LGTM. Not run: a live parallel Gemini run.

## 2026-11-24: D6.2 What the working guard costs (PR #39)

- [D6.2][owner][live][reassess] One Opus 5.5 low run of Blue Lights S04E05 before and after D6.1:
  batches retried, cost, wall-clock, cues over 25 cps and `subtitle_metrics`. Gate: the numbers in
  the log. Needs: D6.1 (code).
- Resolution: measured on `main@f3c1b18` (arm A0) against the D6.1 branch at `163d8e5` (arms A and
  A2, the same settings twice), sequential, 60/10/50, title and description passed. The table is
  under F8.1 below.
- Evidence: without the fix no batch was retried (29 requests, $0.88, 336 s). With it, 3 and 2 of 29
  batches were retried and every retry passed (32 and 31 requests, $1.06 and $0.97, 402 and 371 s):
  10 to 20% more cost and time. Cues over 25 cps in the final file: 3 before, 5 and 2 after; over
  17 cps 13.2% before, 12.3% and 12.0% after. The blind judge scored the three arms level (accuracy
  4.70, 4.75, 4.70). Each retry was caused by one or two single lines (`#68`, `#315`, `#530`,
  `#534`, `#544`, `#574`), never by a run of lines, so this episode showed no drift for the guard to
  catch, and a whole batch was regenerated for each line. That is the case F9.1 is for.

## 2026-11-24: F8.1 Claude parallel A/B (PR #39)

- [F8.1][owner][live][reassess] A/B by environment variables on Blue Lights S04E05, Opus 5.5:
  sequential twice (the noise floor), parallel with 4 workers and a 1,500-token history at 60/10/50,
  the same at 120/30/80, and the better one at effort medium. Gate: wall-clock, cost, 429s,
  `subtitle_metrics`, a blind 80-line judge and the account's output-token limit in the log, and
  the owner's choice of the Claude default. Needs: D6.1 (code).
- Resolution (owner, 2026-11-24): arm C becomes the Claude default: parallel, 4 workers, batches
  120/30/80, a 1,500-token source history. Workers stay at the measured 4; the account's
  output-token limit was not looked up, so a higher count is unmeasured. Effort is unchanged: the
  owner wants F9's condensing pass compared with effort medium before any default moves. Not taken:
  arm B (the 2026-11-19 batch size, 12% dearer at the same speed) and staying sequential.


Opus 5.5, Blue Lights S04E05 (958 source cues), one run per arm, inside the add-on container; code
is the D6.1 branch at `163d8e5` except A0. Parallel arms: `PARALLEL_BATCHES=1 MAX_PARALLEL_WORKERS=4
MAX_CONTEXT_HISTORY_TOKENS=1500`. Cost at $4/$20 per MTok, cache reads $0.20, cache writes $5.

| Arm | Seconds | Requests | Guard retries | Output tokens | Cost | Cues | > 17 cps | > 20 cps | > 25 cps | Judge accuracy | Judge fluency |
|---|---|---|---|---|---|---|---|---|---|---|---|
| A0 `main`, sequential 60/10/50, low | 336 | 29 | 0 | 29,109 | $0.88 | 810 | 13.2% | 4.7% | 3 | 4.70 | 4.76 |
| A sequential 60/10/50, low | 402 | 32 | 3 | 36,186 | $1.06 | 810 | 12.3% | 5.1% | 5 | 4.75 | 4.81 |
| A2 the same again | 371 | 31 | 2 | 33,213 | $0.97 | 809 | 12.0% | 4.0% | 2 | 4.70 | 4.84 |
| B parallel 60/10/50, low | 115 | 33 | 4 | 36,092 | $1.25 | 809 | 11.6% | 4.3% | 4 | 4.75 | 4.69 |
| C parallel 120/30/80, low | 110 | 21 | 3 | 36,092 | $1.12 | 807 | 10.8% | 3.7% | 3 | 4.71 | 4.71 |
| D parallel 120/30/80, medium | 250 | 18 | 0 | 89,514 | $2.11 | 797 | 0.5% | 0.0% | 0 | 4.58 | 4.74 |

- Speed: four workers finish in 110 to 115 s against 371 to 402 s, about 3.4 times faster. No 429
  and no pacing wait beyond the 0.1 s request spacing. The account's output-token limit is not
  recorded yet (it is on the Console's limits page); four low-effort streams produce about 20,000
  output tokens a minute.
- Cost: parallel costs 10 to 25% more than sequential, all of it uncached input. A request carries
  3,800 (B) or 4,600 (C) uncached input tokens against 2,300 sequential: the 1,500-token source
  history replaces one-line summaries. The cache was warm from the run before, so the cost of a cold
  first wave was not measured.
- Quality at low effort: B and C are level with A and A2 on reading speed and on the judge, within
  the spread of the two equal runs (0.3 points over 17 cps, 0.05 judge accuracy).
- Effort medium (D): 4 cues over 17 cps instead of 87, and no guard retry, for 2.5 times the output
  tokens, twice the cost and twice the time of C. It gets there by condensing harder: the judge gave
  it accuracy 4.58 with 7 lines at 3 or below (3 to 4 in the other arms), for example "a murder
  trial" became "the case" and "for a day or two" was dropped. Requests reached 7,928 output tokens,
  half of `DEFAULT_MAX_TOKENS`, so larger batches at medium would be cut off.
- Judge: one Opus 5.5 low agent, 80 seeded lines with one cue per arm, letters shuffled per item,
  accuracy and fluency 1 to 5. One judge run on one episode.
- Material: `/home/ubuntu/Github/ikonsub-reference/runs-2026-11-24-f81/` (outputs, logs, usage,
  `run_f81.sh`), and `run_arm.py`, `summarize_runs.py` and `build_judge.py` beside it.

## 2026-11-24: F8.2 gate runs (PR #42 open)

- Sonnet 5.5 low, Blue Lights S04E05, the D6.1, F8.2 and F8.3 branches merged, cold cache, Claude
  defaults (parallel, 4 workers, 120/30/80): 96 s, 24 requests, one cache write of 3,612 tokens (the
  warm-up works), $0.61. Cues over 17 cps 15.6%, over 20 cps 7.6%, over 25 cps 9; the sequential
  baseline of 2026-11-20 (no working guard): 15.1%, 7.4%, 11, $0.46.
- The guard retried 6 of 18 batches on Sonnet, each for one or two lines (`#4`, `#59`, `#68`,
  `#511`, `#544`, `#574`, `#829`); 4 retries passed and 2 failed on the same line. Whole-batch
  retries are a larger share of the cost on Sonnet than on Opus (2 to 4 of 29).
- Not run: Gemini before and after. Both runs stopped at the first request with `403 Spend cap
  breached for project ... aiplatform.googleapis.com`. The `<history>` sentence and the guard's
  retry rate on 150-line Gemini batches are unmeasured.
- Material: `/home/ubuntu/Github/ikonsub-reference/runs-2026-11-24-f82/`. Runs so far: $8.00.

## 2026-11-24: E3.1 Name the reference episodes (owner decision)

- [E3.1][owner] Name two or three reference episodes, their sources and baseline translations, and
  where they live on the host; they become `## Material` in `.claude/workflow.md`. Gate: the paths in
  the contract and `test -e` passing on each.
- Resolution (owner, 2026-11-24): Blue Lights S04E05 (958 cues) and Cupertino S01E04 (927 cues), the
  two episodes F6.3 and F7.1 were measured on. Sources, baselines and the title and description of
  each are in `/home/ubuntu/Github/ikonsub-reference/`, outside the repository. The baselines are
  the F7.1 "after" runs (Sonnet 5.5 low, 2026-11-20), taken before D6.1, so without a working 25 cps
  guard. `judge-2026-11-20/` keeps that session's judge items and scripts.
- Evidence: `test -e` passes on the five `## Material` paths.

## 2026-11-20: F7.1 Plain Finnish instructions (PR #37)

- [F7.1][owner][live] Rewrite `instructions_fi.txt` in plain Finnish: one priority order, the context
  tags explained, idiom and register guidance, the same format, budget, line and dash rules. Gate:
  `make check && make test`; `subtitle_metrics` before and after on a real episode no worse, and every
  reply line keeps its `Translation>` marker.
- Resolution: `instructions/instructions_fi.txt` rewritten: one priority order, the context tags
  explained, idiom and register guidance, an explicit `#N` / `Translation>` reply format; the budget,
  two-line, 42-character, dash and `[MERGE]` rules (now rule 5) unchanged in substance.
- Evidence (Sonnet 5.5 low, title and description passed): Cupertino S01E04 before $0.535, > 17 cps
  26.6%, > 20 cps 17.6%; after $0.467, 24.2%, 16.6%; 841 of 841 reply lines with `Translation>`. An
  earlier draft without the format rule dropped the markers (read only by `fallback_patterns[5]`)
  and read faster on Cupertino (31.3%). A blind Opus judge of 80 lines on that draft: accuracy 4.65
  against 4.53 for the old instructions, 25 errors against 32. `make check && make test` 547 passed.
  Gemini cold review LGTM. Not run: the final wording on Blue Lights S04E05.

### F7: The Finnish instructions are written for older models (moved from the roadmap)

- **Status:** Open; from the 2026-11-19 Sonnet 5.5 review.
- `instructions/instructions_fi.txt` claimed the model had "a wide history of earlier translations"
  (it had one-line summaries), named two rules "the most important", shouted in capitals, and asked
  for a per-line think-aloud procedure. Current Claude models follow plain instructions literally.
- [F7.1][owner][live] Rewrite `instructions_fi.txt` in plain Finnish: one priority order, the context
  tags explained, idiom and register guidance, the same format, budget, line and dash rules. Gate:
  `make check && make test`; `subtitle_metrics` before and after on a real episode no worse, and every
  reply line keeps its `Translation>` marker.
## 2026-11-20: F6.3 Name the title and language in the prompt (PR #36)

- [F6.3][owner][live][reassess] The prompt line substitutes title and language instead of the literal
  `for [movie] ... [language]`; Claude keeps `context_lines` and `lookahead_lines` at 0 and its
  60/10/50 batches (owner, 2026-11-19, after three measured runs). Gate: `make check && make test`;
  `subtitle_metrics` before and after on Blue Lights S04E05 no worse. Needs: F6.1, F6.2.
- Resolution: `instructions_fi.txt` and `instructions_el.txt` use `[ for movie]` and
  `[target_language]`, so the line reads "...subtitles for Blue Lights S04E05 into ... Finnish." with
  a title and drops the clause without one; the cached system prompt is the same for every episode.
  The Claude context defaults were not adopted (owner, 2026-11-19).
- Measurement behind the decision (Blue Lights S04E05, 958 cues, Sonnet 5.5 low, one run each): main
  defaults $0.51, cues > 17 cps 14.9%; context 20/5 at 60/10/50 $0.52, 16.4%; context with 120/30/80
  $0.41, 17.4% (fewer output tokens, probably less thinking). Raw replies over budget 29%, 36%, 35%.
  Neither context arm fixed the probe's sense errors, which change from run to run at low effort, and
  the batcher splits at long pauses, so few sentences cross a batch edge.
- Evidence: `TestPromptLine` (title and no title, system prompt unchanged, no raw placeholders in the
  Greek line). Readiness run: before 804 cues, > 17 cps 14.9%, > 20 cps 6.6%; after 803 cues, 16.6%,
  6.8%, within the 1.5 to 2.1 points two near-identical runs varied by; the owner accepted it as met
  (2026-11-20). `make check && make test` 547 passed. Gemini cold review LGTM; CI on PR #36.
- Same session (owner, 2026-11-20): a blind judge of 80 lines across Blue Lights S04E05 and Cupertino
  S01E04 found no gain from Sonnet medium over low (+11-20% cost), so Claude stays at low.

### F6: Claude batches translate without the lines around them (moved from the roadmap)

- **Status:** Partial; prompts can carry previous and upcoming lines (F6.1) and transubs takes a title
  and a description (F6.2), both in the log. Open: the prompt-line fix (F6.3).
- A Claude batch sees one-line summaries of earlier batches only: not its own earlier Finnish, not
  the next lines, not the title. Sentences cut at a batch edge lose their start, a reply is
  translated without its question ("Nah, I'm telling ya" -> "En vaadi, vaan kerron"; that pair was
  in a batch's first lines, so context could not have caught it), and register drifts. The prompt
  line says `for [movie] into ... [language]` literally. The owner chose a context block in each
  prompt over a cached conversation (2026-11-19: about +3% per episode against +24-35%).
- [F6.3][owner][live][reassess] The prompt line substitutes title and language instead of the literal
  `for [movie] ... [language]`; Claude keeps `context_lines` and `lookahead_lines` at 0 and its
  60/10/50 batches (owner, 2026-11-19, after three measured runs). Gate: `make check && make test`;
  `subtitle_metrics` before and after on Blue Lights S04E05 no worse. Needs: F6.1, F6.2.
## 2026-11-19: F6.2 transubs takes a title and a description (PR #35)

- [F6.2][desk] `transubs --title` and `--description` put the title and a description into the
  project settings of a single-file run. Gate: `make check && make test`; a run without the flags
  sends the same settings as before.
- Resolution: `scripts/transubs.py` takes `--title=` and `--description=` for a single file and sets
  `movie_name` and `description` in the settings it hands the project; without them nothing
  changes, and with no file they are a usage error. SRT output ignores the title (only the SSA
  handler writes one). The flags are added in two helpers so `main()` stays at 50 statements.
- Evidence: `tests/PySubtransTests/test_CliPipeline.py::TestTransubsTitleAndDescription` (flags land,
  settings unchanged without them, usage error, a value starting with "-", `--help` lists both).
  `make check && make test` 544 passed. Gemini cold review LGTM.

## 2026-11-19: F6.1 Previous and upcoming lines in sequential prompts (PR #34)

- [F6.1][desk] Sequential batches can show the last N source lines with their translations and the
  next M source lines, behind `context_lines` and `lookahead_lines` (default 0). Gate: `make check &&
  make test`; tests show default prompts carry only the old tags and the parallel path unchanged.
- Resolution: `PySubtrans/Helpers/ContextHelpers.py` builds `<previous_lines>` (source with its
  delivered translation, merged lines joined) and `<upcoming_lines>` (the next source lines of the
  scene) from `context_lines` and `lookahead_lines`; `SubtitleTranslator` passes them on the
  sequential path and gives each half of a split batch the other half's lines. Parallel context and
  default prompts are unchanged; both keys are in the resume fingerprint.
- Evidence: `tests/PySubtransTests/test_ContextLines.py` (11 tests: old tags only at 0/0, pairs in
  order and capped, scene edges, merges, parser finds nothing in a block, split halves, retry keeps
  the context, settings and fingerprint); four mutations each fail it. `make check && make test` 539
  passed. Gemini cold review LGTM. Not run: a live translation with the settings on (F6.3).

## 2026-11-19: D5.1 Drop all-caps sound descriptions (PR #33)

- [D5.1][desk] `filter_subtitles` drops all-caps sound lines from mixed-case files and keeps shouts,
  acronyms and all-caps sources. Gate: `SubtitleFilter` tests; `make check && make test`.
- Resolution: `PySubtrans/MKV/SubtitleFilter.py:remove_caps_sound_lines` runs in `filter_subtitles`
  after `subtitle_filter` and before `remove_markup_only_cues`. It drops lines with no lowercase
  letter and no end punctuation (two or more words, or one word of five letters or more) when a file
  has at least three such lines and at least half its visible lines are mixed case; markup is ignored.
- Evidence: `tests/PySubtransTests/test_SubtitleFilter.py::TestRemoveCapsSoundLines` (sound lines,
  shouts and acronyms, all-caps files with and without markup, too few caps lines). On the Blue
  Lights S04E05 English SDH track, 65 distinct sound lines go (959 cues to 890) and no dialogue line.
  `make check && make test` 528 passed. Gemini cold review: one P1 (markup letters counted as
  lowercase), fixed with a red-then-green test. Not run: a live translation with the filter.

### D5: All-caps sound descriptions reach the model (moved from the roadmap)

- **Status:** Open; found in a Sonnet 5.5 translation review on 2026-11-19.
- BBC hearing-impaired tracks write sounds in capitals ("GUNSHOTS", "SIREN WAILS"). `subtitle_filter`
  removes bracketed sounds only, so the continuation merge glued them into dialogue ("No, no, no!
  GUNSHOTS Shots fired.") and the model translated them as speech, in capitals with spelling errors.
- [D5.1][desk] `filter_subtitles` drops all-caps sound lines from mixed-case files and keeps shouts,
  acronyms and all-caps sources. Gate: `SubtitleFilter` tests; `make check && make test`.

## 2026-11-18: F5.1 Resume a failed translation from its finished batches (PR #32)

- [F5.1][desk] Reuse the translated lines of a `.partial` file (or a persistent project) so a re-run
  translates only the missing batches. Gate: a test where a run that failed after 2 of 4 batches
  resumes and sends only batches 3 and 4 to the provider.
- Resolution: `PySubtrans/TranslationResume.py` saves the finished batches of a failed run to
  `<name>.<lang>.srt.resume.json` beside the `.partial`. The next run batches the source as usual and
  grafts them back only when every batch's originals and a settings fingerprint match, then skips
  them through `SubtitleTranslator.resume`. A successful run removes both files; fix-finnish-subs
  still runs once on the complete output. Same five call sites in `exsubs.py` and `transubs.py`.
- Decided 2026-11-18 (owner): a sidecar, not parsing the `.partial` (it renumbers cues and loses
  `[MERGED]` lines and summaries); deleting the `.partial` cancels the resume; a successful run
  removes both files; a changed model, provider, language or instructions starts over with a warning.
- Evidence: `tests/PySubtransTests/test_TranslationResume.py` (gate for both scripts, resumed prompts
  and output byte-equal to an uninterrupted run, a source transubs already prepped in place,
  multi-scene, large context, parallel, stale settings and source, corrupt state); five mutations of
  the module each fail it. `make check && make test` 523 passed; CI on PR #32. Gemini cold review
  LGTM. Not run: a live failed-then-resumed run on a real episode.

## 2026-11-18: D1.2 Retry a dropped Gemini stream (PR #31)

- [D1.2][desk] Check `GeminiClient._send_messages` (`GeminiClient.py:334`) for the same gap and retry
  a dropped stream there too. Gate: a test of a mid-stream drop on the Gemini stream that retries and
  succeeds.
- Before: a raw `httpx` error during stream iteration was already retried by `_send_messages`, and
  `ResetStreamingProgress()` cleared the failed attempt. A connection that closed cleanly ended the
  stream with no `finish_reason`, and `_process_gemini_response` returned the partial text as success.
- Resolution: `_get_gemini_response` raises a retryable `TranslationResponseError` when a stream ends
  without a finish reason and without a prompt block; the usage is still recorded.
- Decided 2026-11-18 (agent, recommended; owner may override): the check covers the stream path
  only; not taken: the same check for non-streaming responses, which cannot end part way.
- Evidence: `tests/PySubtransTests/test_GeminiClientRetry.py::GeminiStreamRetryTests` (each test fails
  with its fix reverted); `make check && make test` 504 passed; CI on PR #31. Gemini cold review LGTM.

## 2026-11-17: E5.7 Split run_diagnostics (PR #26)

- [E5.7][desk][batch] Split `run_diagnostics` (81 statements, `PySubtrans/MKV/Diagnostics.py:8`) by phase.
  Gate: `make check && make test`, behaviour unchanged.
- Resolution: six private helpers (tools, disk test, filesystem, memory, I/O stats,
  recommendations), the largest 24 statements; `run_diagnostics(console)` keeps its signature and the
  `PLR0915` marker is gone.
- Evidence: no test covered it, so `tests/PySubtransTests/test_Diagnostics.py` was added first and
  pins the console output and commands in four cases; it passes on the pre-split and split code.
  `make check && make test` 497 passed; CI on PR #26. Built and reviewed by `/ship:roadmap-batch`
  (self mode, Claude cold review LGTM).

## 2026-11-17: E5.8 Split BatchProcessor.run (PR #27)

- [E5.8][desk][batch] Split `BatchProcessor.run` (72 statements, `scripts/batch_translate.py:123`) by phase.
  Gate: `make check && make test`, behaviour unchanged.
- Resolution: `run` resolves the directories, logs the settings and loops; private methods load,
  resolve the destination, translate and save. Step order, log messages and error handling unchanged.
- Evidence: no test exercised `run`, so `tests/PySubtransTests/test_BatchTranslate.py` was added
  (translate, fail, skip, preview, no files, missing source, save failure); it passes 5/5 on the
  pre-split code too. `make check && make test` 498 passed; CI on PR #27. Built and reviewed by
  `/ship:roadmap-batch` (self mode, Claude cold review LGTM).

## 2026-11-17: E5.1 Lint functions over 50 statements (PR #24)

- [E5.1][desk][batch] Add ruff `PLR0915` at `lint.pylint.max-statements = 50` and mark the 8 current
  violations with `# noqa: PLR0915 -- E5.<n>`, naming each one's split step. Gate: `make check` green,
  and a probe function with 51 statements makes it fail.
- **Decided 2026-11-17 (batch, recommended; owner may override):** ruff at HEAD reported 10
  violations: the 8 named ones plus `test_SubtitleBatches` and `test_SubtitleEditor_UpdateLine` in
  `tests/PySubtransTests/test_ChineseDinner.py`, which had no split step. They carry
  `# noqa: PLR0915 -- E5.15`, and E5.15 splits them. Not taken: a `tests/**` per-file-ignore, which
  would loosen the contract's `*.py` function limit.
- Resolution: `PLR0915` is in the pinned ruff `select` list, with `[tool.ruff.lint.pylint]`
  `max-statements = 50`; the 10 functions over the limit carry markers naming E5.5 to E5.12 and E5.15.
- Evidence: a temporary 51-statement function made `make check` fail with `PLR0915 Too many statements
  (51 > 50)` in `PySubtrans/`, `scripts/`, `tools/` and `tests/`; `make check && make test` 493 passed;
  CI on PR #24. Built and reviewed by `/ship:roadmap-batch` (self mode, Claude cold review LGTM).

## 2026-11-17: D2.2 One env_bool for scripts and providers (PR #21)

- [D2.2][desk][batch] One shared `env_bool` helper used by the scripts and the providers, per the decision.
  Gate: tests of each accepted spelling through both a script and a provider. Needs: D2.1.
- Resolution: `env_bool` in `PySubtrans/Options.py` (it already existed and accepted only
  `true/yes/1`) now takes `1/true/yes/on` in any case, stripped; unset or empty returns the default.
  The scripts (`_using_vertex`, `SISUSUB_AI_REVIEW`, `LARGE_CONTEXT_MODE`, transubs
  `PARALLEL_BATCHES`), all ten `== "True"` checks in the OpenAI, Gemini and Claude providers, and the
  `Options` flags read it; each variable keeps its default. `EXSUBS_FIXER_VERBOSE` stays a presence
  flag.
- **Decided 2026-11-18 (owner):** an empty variable (for example `GEMINI_USE_VERTEX=`) means "use
  the default", as `env_bool` does since PR #21. Not taken: empty means false, as the scripts did before.
- Evidence: `tests/PySubtransTests/test_EnvBool.py` (every spelling through `env_bool`, exsubs,
  transubs and `Provider_Gemini`; 9 tests fail on the old code); `make check && make test` 491 passed;
  CI on PR #21. Built and reviewed by `/ship:roadmap-batch` (self mode, Claude cold review LGTM).

### D2: Boolean env vars parse differently in scripts and providers (moved from the roadmap)

- **Status:** Open; the owner decided the spellings (D2.1, in the log).
- The scripts accept `1/true/yes/on` (`scripts/exsubs.py:536`, `scripts/transubs.py:492`), while
  `Provider_Gemini` accepts only the exact string `True` (`PySubtrans/Providers/Provider_Gemini.py:96`).
- **Decided 2026-11-17 (owner):** `1/true/yes/on`, case-insensitive, count as true in every script and
  provider. A user who exported `GEMINI_USE_VERTEX=true` gets Vertex everywhere, as they asked.
- [D2.2][desk][batch] One shared `env_bool` helper used by the scripts and the providers, per the decision.
  Gate: tests of each accepted spelling through both a script and a provider. Needs: D2.1.

## 2026-11-17: D4.2 Default OpenAI to gpt-6-luna (PR #22)

- [D4.2][desk][batch] Set the shared OpenAI default to `gpt-6-luna`. Gate: the D4.1 test expects
  `gpt-6-luna` for every CLI, and a test that the model routes to `OpenAIReasoningClient`. Needs: D4.1.
- Resolution: `OPENAI_DEFAULT_MODEL = "gpt-6-luna"` in `PySubtrans/MKV/Config.py`; exsubs, transubs,
  gpt-subtrans and the OpenAI provider use it when no model is given and `OPENAI_MODEL` is unset.
- Evidence: `test_cli_openai_default_is_gpt_6_luna` (`test_CliDefaultModels.py`) and
  `test_default_model_uses_reasoning_client` (`test_ProviderOpenAI.py`); `make check && make test`
  484 passed; CI on PR #22. No live call to `gpt-6-luna` ran. Built and reviewed by
  `/ship:roadmap-batch` (self mode, Claude cold review LGTM).

## 2026-11-17: D1.1 Retry dropped OpenAI connections (PR #18)

- [D1.1][desk][batch] Retry `APIConnectionError` and mid-stream transport errors in the OpenAI chat and
  Responses clients with the existing backoff, resetting stream progress as `AnthropicClient` does.
  Gate: tests where a connection error before the response and a drop mid-stream are retried and the
  retry's text is used; `make check && make test`.
- Before: `OpenAIClient._try_send_messages` raised `TranslationError` on `openai.APIConnectionError`
  without a retry. openai 3.42.1 wraps a transport failure while reading a stream as
  `APIConnectionError` (`openai/_streaming.py:55-61`), so a mid-stream drop took the same path.
- Resolution: `_try_send_messages` retries `openai.APIConnectionError` and `httpx2.TransportError`
  with the shared backoff; the Responses stream re-raises a raw transport error to that loop, and
  `ResetStreamingProgress()` already runs before each attempt. After the last attempt the batch
  fails with `TranslationError`, and the run goes on.
- Evidence: `tests/PySubtransTests/test_OpenAIClientRetry.py` (4 of 5 tests fail on the old code);
  `make check && make test` 478 passed, none skipped; CI on PR #18. Built and reviewed by
  `/ship:roadmap-batch` (self mode, Claude cold review LGTM).

## 2026-11-17: D4.1 One module for default models (PR #19)

- [D4.1][desk][batch] Move each provider's default model into one module that every script reads,
  values unchanged. Gate: a test that each CLI's default equals the shared constant.
- **Decided 2026-11-17 (owner):** the OpenAI default becomes `gpt-6-luna`. D4.1 keeps today's
  effective value `gpt-5-mini` and drops the unused `gpt-4o-mini` in `PySubtrans/MKV/Config.py:20`;
  D4.2 then switches the constant.
- **Decided 2026-11-17 (batch, recommended; owner may override):** the provider fallbacks
  `Provider_OpenAI.py:42` and `Provider_Claude.py:31` read the shared constant too, values unchanged.
  Not taken: scripts only, which would leave `gpt-5-mini` in the OpenAI provider after D4.2.
- Resolution: `PySubtrans/MKV/Config.py` defines `OPENAI_DEFAULT_MODEL`, `CLAUDE_DEFAULT_MODEL` and
  `GEMINI_DEFAULT_MODEL`, and `MODE_TO_DEFAULT_MODEL` is built from them. exsubs, transubs,
  gpt_subtrans, gemini_subtrans, claude_subtrans, `batch_translate.py` `DEFAULT_OPTIONS` and the
  OpenAI and Claude providers read them. Env var overrides are unchanged.
- Evidence: `tests/PySubtransTests/test_CliDefaultModels.py` (fails when a literal drifts);
  `make check && make test` 477 passed; CI on PR #19. Built and reviewed by `/ship:roadmap-batch`
  (self mode, Claude cold review LGTM).

## 2026-11-17: D2.1 Decide how boolean env vars parse (steering)

- [D2.1][owner] Decide which spellings count as true everywhere, and whether the provider default may
  change for existing setups. Gate: the decision recorded in `docs/ROADMAP_LOG.md`.
- **Decided 2026-11-17 (owner):** `1/true/yes/on`, case-insensitive, count as true in every script and
  provider (D2.2 builds it). Not taken: a strict `GEMINI_USE_VERTEX` in the providers, or exact `True`
  only. A user who exported `GEMINI_USE_VERTEX=true`, as `scripts/exsubs.py:709` and `readme.md:132`
  advise, now gets Vertex in `batch-translate` and `gemini-subtrans` too.

## 2026-11-17: E2.1 Choose the first ikonsub version (steering)

- [E2.1][owner] Choose the first ikonsub version. Gate: the choice recorded in the log.
- **Decided 2026-11-17 (owner):** `0.1.0`. Not taken: `1.6.0` (continuing LLM-Subtrans' numbering) or
  `1.0.0`. Today the wheel says `0.0.1.devN` (no tags) and `PySubtrans/version.py` says `v1.5.6`.

## 2026-11-17: E5.13 Trim AGENTS.md (dropped in steering)

- [E5.13][desk] Trim AGENTS.md (10615 bytes, budget 8000): move detail to its topic doc or the log.
  Gate: `wc -c AGENTS.md` at most 8000, with every rule still in `AGENTS.md` or linked from it.
- **Decided 2026-11-17 (owner):** dropped. `AGENTS.md` is a symlink to `CLAUDE.md` (11261 bytes), and
  the contract's `claude-md` size limit is 24000 bytes, which `/ship` enforces.
