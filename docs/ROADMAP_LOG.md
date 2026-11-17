# ikonsub Roadmap Log

Closed roadmap steps go here as `## <date>: <ID> <title> (PR #<n>)`, with the step's text and its gate
evidence. Open work is in [`ROADMAP.md`](../ROADMAP.md).

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
