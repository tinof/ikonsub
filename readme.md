# ikonsub

A specialized CLI subtitle translator for Linux and macOS. 

The name is a nod to Ioannis Ikonomou, the European Commission translator who speaks 32 languages.

ikonsub is derived from [LLM-Subtrans](https://github.com/machinewrapped/llm-subtrans) by machinewrapped and is now developed separately. It drops the Windows/GUI parts and is optimized for terminal power users, automation, and superior linguistic quality.

## Key Features & Upgrades

- **Smart Line Merging (`[MERGE]` tag):** The upstream engine strictly enforces a 1:1 translation ratio per line. This fork allows the AI to merge fragmented source lines into a single fluent line—critical for agglutinative languages (like Finnish, Turkish, or Japanese) where word order completely changes.
- **All-in-One MKV Pipeline (`exsubs`):** Bypasses the need for external tools. Automatically extracts subtitle tracks directly from `.mkv` files, filters them, translates them, and saves the output.
- **Parallel Translation:** `--parallel` mode translates multiple subtitle batches simultaneously via `ThreadPoolExecutor` (auto-enabled with 8 workers for modern Gemini models), making large jobs up to 8x faster.
- **Large Context Mode:** Built to utilize the massive context windows of modern models (like Gemini 2.5/3.x). It feeds the AI thousands of tokens of story history to drastically improve character consistency and tone over long movies.
- **Resilient API Handling:** Transient provider errors (429/5xx) are retried with jittered exponential backoff; on Vertex AI (dynamic shared quota) no artificial client-side RPM limit is applied.
- **Readability-Aware Translation:** Each line in the prompt carries its display time and a character budget (`#123 [2.4s, max 36 chars]`, derived from a 15 CPS target), so the model condenses lines that would be unreadable instead of translating word-for-word. The Finnish instructions also have the model infer dialogue dashes for two-speaker cues.
- **CLI-Native Modern Stack:** No PySide6, no Windows hooks, no PyInstaller scripts. Built entirely around [`uv`](https://docs.astral.sh/uv/) for incredibly fast dependency and environment management.
- **Direct Subtitle Workflow (`transubs`):** Efficient, highly-tuned SRT/ASS/VTT translation script for when you don't need MKV extraction.
  
## Installation

Install using [uv](https://docs.astral.sh/uv/) (recommended):

```sh
# Install uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install ikonsub with common providers
uv tool install "ikonsub[openai,gemini,claude] @ git+https://github.com/tinof/ikonsub.git"

# To update
uv tool upgrade ikonsub
```

## Workflows

### 1. `exsubs` — Extract & Translate from MKV

The primary workflow for video files. Extracts subtitles from MKV and translates them using optimized defaults.

```sh
# Translate MKV subtitles to Finnish using Gemini (Vertex AI default)
exsubs video.mkv --gemini -l Finnish

# Process all MKV files in the current directory
exsubs --gemini -l Finnish

# Interactive track selection
exsubs video.mkv --gpt -l Spanish -i
```

**Defaults**: Uses Gemini via Vertex AI in location `global` (default model: `gemini-4-hydrogen`, override with `GEMINI_MODEL`). Optimized for 1M context window. Automatically enables parallel translation for modern Gemini models. Temperature defaults to 1.0 on Gemini 3.x (lowering it causes repetition loops) and 0.3 elsewhere; override with `LLM_TEMPERATURE`. Gemini 4 ignores temperature. On Gemini 4 the thinking level is `low` unless `GEMINI_THINKING_LEVEL` is set; `GEMINI_THINKING_BUDGET` does not apply there (Gemini 4 rejects budgets).

**Finnish post-processing**: after translation, `exsubs` runs
[sisusub](https://github.com/tinof/sisusub)'s `fix-finnish-subs` once with
`--width-limit 42 --max-cps 17 --cps-target 15` (deterministic layout/timing
fixes plus an AI review pass; set `SISUSUB_AI_MODEL` to run the review on a
stronger model than the translator). A readability report is written next to
the output; set `EXSUBS_FIXER_VERBOSE=1` for per-proposal logging. Measure any
result objectively with:

```sh
uv run python tools/subtitle_metrics.py translated.fi.srt --source english.srt
```

#### `exsubs` Options

| Flag                  | Description                                                                    |
| --------------------- | ------------------------------------------------------------------------------ |
| `--gemini`            | Use Gemini model (default)                                                     |
| `--gpt`               | Use OpenAI GPT model                                                           |
| `--claude`            | Use Anthropic Claude model                                                     |
| `-l`, `--language`    | Target language (e.g., `Finnish`, `Spanish`)                                   |
| `-i`, `--interactive` | Manually select subtitle track to extract                                      |
| `--no-filter`         | Preserve music cues, fonts, effects, etc. (disabled by default)                |
| `--no-progress`       | Hide progress bars                                                             |
| `--no-metrics`        | Don't print translation statistics at the end                                  |
| `--parallel`          | Enable parallel batch translation (auto-enabled for Gemini Flash/Pro)          |
| `--no-parallel`       | Force sequential translation                                                   |
| `--parallel-workers`  | Number of parallel workers (default: 4, or 8 for modern Gemini)                |
| `--max-batch-size`    | Max lines per batch (default: 600 for Gemini, 200 for parallel)                |
| `--min-batch-size`    | Min lines per batch                                                            |
| `--copy-local`        | Copy MKV to local temp before processing (useful for network mounts)           |
| `--setup-vertex`      | Interactive wizard to configure Vertex AI                                      |
| `--diagnose`          | Run system diagnostics                                                         |

---

### 2. `transubs` — Subtitle File Translation

Directly translate existing subtitle files (.srt, .ass, .vtt) with the same tuned engine as `exsubs`.

```sh
# Translate an SRT file
transubs input.srt --gemini -l French

# Translate without removing music cues, fonts, effects, etc.
transubs --no-filter input.srt -l German
```

#### `transubs` Options

| Flag               | Description                                           |
| ------------------ | ----------------------------------------------------- |
| `--gemini`         | Use Gemini model (default)                            |
| `--gpt`            | Use OpenAI GPT model                                  |
| `--claude`         | Use Anthropic Claude model                            |
| `-l`, `--language` | Target language                                       |
| `--proofread`      | Fix flow/grammar without translating                  |
| `--no-filter`      | Preserve music cues, fonts, effects, etc.             |
| `--no-progress`    | Hide translation progress line                        |
| `--no-metrics`     | Don't print translation statistics at the end         |
| `--setup-vertex`   | Interactive wizard to configure Vertex AI             |

---

### 3. `gemini-subtrans`, `gpt-subtrans`, `claude-subtrans` — Provider Tools

The base tools for fine-grained control not covered by the wrapper scripts. Supported providers are Gemini, OpenAI and Claude. Run `gemini-subtrans --help` for all options.

```sh
gemini-subtrans --target_language Japanese subtitle.srt
```

---

## Configuration

Settings are managed via environment variables or `.env` file. Common overrides:

```sh
export GEMINI_USE_VERTEX=true             # Use Vertex AI (default for exsubs)
export GEMINI_MODEL=gemini-4-hydrogen     # Specific model
export VERTEX_LOCATION=global             # Vertex AI location (gemini-4-hydrogen and gemini-4-carbon serve only global, us and eu)
export GEMINI_THINKING_LEVEL=low          # Thinking level (default low on Gemini 4; xhigh only on gemini-4-hydrogen)
export SCENE_THRESHOLD=300                # Start a new scene when the gap between lines exceeds 300s (large context)
export MAX_BATCH_SIZE=600                 # Max lines per batch
export MAX_RETRIES=5                      # Retries for transient API errors (default: 5)
export PARALLEL_BATCHES=false             # transubs: Gemini batches run concurrently by default (150 lines each)
export MAX_PARALLEL_WORKERS=8             # Concurrent batch requests in parallel mode
export GEMINI_REQUEST_TIMEOUT=180         # Seconds a request may wait (streamed: between chunks) before it is retried
export GEMINI_FALLBACKS=gemini-4-argon@us,gemini-3.8-flash@us  # model@location tried in order after repeated failures
export GEMINI_ATTEMPTS_PER_TARGET=2       # Failed attempts before moving to the next fallback
export LLM_TEMPERATURE=0.3                # Override the model-aware temperature default
export SISUSUB_AI_MODEL=gemini-3.1-pro-preview  # Stronger model for the fix-finnish-subs review pass
export EXSUBS_FIXER_VERBOSE=1             # Verbose fix-finnish-subs output (dropped-proposal logging)
```

**Vertex AI Setup**: Run `exsubs --setup-vertex` for a guided configuration wizard.

---

## Development

This project uses `uv` for all development tasks.

```sh
git clone https://github.com/tinof/ikonsub.git
cd ikonsub
uv sync --all-extras --dev

# Run tests
uv run pytest

# Format/Lint
uv run ruff check --fix && uv run ruff format
```

---

## License

MIT License.
