# ikonsub Roadmap Log

Closed roadmap steps go here as `## <date>: <ID> <title> (PR #<n>)`, with the step's text and its gate
evidence. Open work is in [`ROADMAP.md`](../ROADMAP.md).

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
