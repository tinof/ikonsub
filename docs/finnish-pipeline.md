# Finnish Readability Pipeline

Moved from `AGENTS.md`, which links here. These are binding repo rules.

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
  risk when lowered); OpenAI defaults to 0.3. Gemini 4 ignores temperature;
  its knob is thinking, which the scripts set to `low` on Gemini 4 through
  `gemini_thinking_level()` (`GEMINI_THINKING_LEVEL` wins). `GeminiClient`
  resolves thinking per fallback target: Gemini 4 rejects `thinking_budget`
  and `minimal` with HTTP 400. The default `gemini-4-hydrogen` is short-term
  GA (45-day retirement notice). `GEMINI_3_MODEL_PATTERN`
  in both scripts — change both or neither. Claude takes no temperature (API
  version 2026-10-21 rejects it); its quality/cost knob is `CLAUDE_EFFORT`
  (default `medium`, pinned for the run because changing it breaks the prompt
  cache). The Claude system prompt carries the only cache breakpoint: keep
  per-batch content out of it.
- **Measure, don't eyeball:** `tools/subtitle_metrics.py` reports raw-character
  line lengths, CPS, display times, dialogue-dash counts, and cue-count delta
  vs the source. Run it before/after on a real episode for any change touching
  translation quality, and compare against the kept baseline artifacts.
