# ikonsub - Development Ideas & Improvements

Based on the analysis of the ikonsub architecture (including its existing sliding context window and scene detection mechanisms), here are the key areas for improving the translation pipeline:

## 1. Structured Outputs (JSON Schema) vs. Regex Parsing
**Current State:** The system relies on string matching and regex (`TranslationParser.py`) to extract line numbers and translated text from the LLM's markdown-style response.
**Vulnerability:** Highly fragile. If the LLM hallucinates formatting, skips numbers, or adds conversational filler, lines are dropped or misaligned.
**Improvement:** Enforce strict JSON Schema (`response_format`) in the API calls (supported by both Gemini and OpenAI). This mathematically guarantees the API returns an exact array of JSON objects `[{"line": 1, "text": "..."}]`, eliminating parsing errors entirely.

## 2. Adaptive Semantic Chunking over Hard Thresholds
**Current State:** Scene detection splits batches based on hardcoded silence thresholds (e.g., 30s or 300s). For dense media (like documentaries), this threshold is never met, forcing the system to arbitrarily slice a single massive scene into arbitrary batches.
**Improvement:** Implement adaptive chunking. If a scene exceeds the maximum batch size, the batcher should find the *longest available silence* within that window to make the cut, rather than strictly requiring a 30-second gap. Alternatively, use lightweight semantic embeddings to split batches when the topic changes.

## 3. Global Glossary via Pre-pass Entity Extraction
**Current State:** The sliding context window maintains consistency with the *immediately preceding* batch, but cannot remember characters or terms established in Batch 1 that reappear in Batch 4.
**Improvement:** Run a rapid, low-cost pre-pass (e.g., using `gemini-3.1-flash-lite`) over the entire SRT to extract proper nouns, character names, and unique terminology. Inject this "Global Glossary" into every batch prompt to ensure 100% consistency across the entire file.
