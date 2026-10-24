# Total-token footer and workflow-cost review

21 October 2026.

## Scope

This update changes local accounting and presentation. The full proposed rate-limit/project-scope plan has not been merged. Request prompts, model tools, endpoint URLs, generation limits and settings screens are unchanged.

## Counting fixes

The optional planning graph executes the model query in a worker context. The old thread-local ledger could count the request only in that worker, leaving the parent task with zero requests and a false zero-token footer. An offline reproduction returned 15 provider tokens but showed 0 in the parent task. After replacing the ledger storage with a ContextVar, the same query reports one request and 15 tokens in the parent. Independent side-chat threads still keep separate ledgers.

Explicit provider total_tokens now takes precedence over reconstructing a total from input/output. Total-only provider records are retained; unknown component splits are not invented. Separate input and output usage frames can establish a total after both arrive. Duplicate cumulative usage snapshots do not add the same reported total again under the normal cumulative protocol.

All main-task requests, continuations and retries in the bound task context contribute to that task's total. Side replies retain their independent per-question accounting. This is usage tracking, not a provider billing invoice. If an interrupted stream omits its final usage, exact counts may be unavailable.

## Display

```text
Worked for 2m 05s • 02:22:08 PM • Total Token 1,234
```

Only the elapsed duration, local completion clock with AM/PM and combined total are shown. The terminal wraps the logical line naturally. Input/output components remain available internally for diagnostics and future token-rate accounting, but are absent from the footer.

- A reported total appears as a number.
- A character-based estimate appears with a `~` prefix.
- Unknown total appears as `unavailable`, rather than a fabricated zero.
- A fully local task with no model requests can correctly show zero.
- Failed/cancelled tasks retain their explicit stopped/cancelled labels.
- The completion timestamp remains frozen when replaying history.

Existing historical zero measurements cannot be reconstructed from formatting alone. This fixes newly measured tasks; it does not invent missing old counts.

## Cost review of the proposed plan

| Planned component | Added model prompt text needed | Added inference calls needed | Delay consideration |
| --- | --- | --- | --- |
| Shared rate governor, quota parsing and retry classification | None | None | Wait only when capacity/cooldown requires it; keep waiting cancellable. |
| Direct-file root checks, stale hashes and edit recovery | None on successful operations | None | Local path/file checks; failures may add a short corrective observation. |
| Local verification and completion receipts | None beyond necessary observations | None for acknowledgement | Tests take their actual execution time; avoid unnecessary broad checks. |
| Context selection and result compaction | Intended to reduce optional context | No summarizer/router model call | Preserve exact source, constraints and native tool/result pairs. |
| Optional future skill | Its relevant instructions/references count when sent | No selection-model call required | Budget separately; do not load all skills every turn. |

Do not merge the plan by pasting it into Galam's system prompt. Implement scheduling and validation in Python, preserving the existing action pipeline. A first request with available capacity should not receive a blanket sleep. Provider-requested waits cannot be eliminated by reducing prompt size.

Before a future full merge, compare identical tasks before/after: actual translated input, tool schemas, request count, provider totals, first-token latency, task duration and correctness. For scheduling-only successful paths, enforce unchanged outgoing JSON and unchanged inference-call counts. Context optimization should not increase matched-task input without a documented correctness reason. Complex changes can legitimately need more source and output than simple edits.

## Verification

Regression coverage includes the real installed optional planning graph with mocked network responses, copied worker contexts, isolated side threads, multiple requests, provider totals that differ from their components, total-only/partial usage, duplicate snapshots, fixed AM/PM rendering, error/cancellation labels, resume replay and exclusion of footer metadata from model context. Tests use local fixtures; no extra provider requests are made to fetch usage.

Validation: **277 tests passed** in the full regression run; the six focused accounting/footer tests also passed after the final explicit-total precedence check. Changed Python modules compiled and diff checks passed.
