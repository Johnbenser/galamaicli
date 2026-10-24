# Galam realistic coding stress plan

Status: proposed live evaluation. Local regressions and harmless process tests run separately; this document does not claim a live-model benchmark or measured token saving.

## Goal and rule for judging success

Act like an ordinary user who iterates on an application: build it, change the design, discover a bug, ask for a correction, run it, interrupt it and resume later. Evaluate the actual files and service independently of the assistant's final claims.

Primary measures are verified completion, preserved requirements and tokens per verified completion. Count failed/retried runs in token spend. A smaller prompt that prevents completion is not an improvement. Do not use output length, fewer tool calls or an assistant saying 'done' as a success score. Classify missing provider usage as unknown/estimated; do not invent an exact split or bill. Record cached tokens separately if reported.

## First replace the failed code

- Replace aggregate middle-truncation of task evidence with complete, separately selected operational facts. Reserve complete command/cwd/source evidence before low-value receipts. Retain explicit instructions and native call/result pairing.
- Replace novel-output renewal for a known startup task with milestone advancement. New unrelated files and differently formatted listings cannot renew it indefinitely. Recovery includes the known recipe and pending action.
- Replace the old automatic server-launch block with a shared owned-process runner. Common-server recognition delegates to the same runner; custom commands use explicit background execution. No alternate legacy path should silently reintroduce guessed ports, duplicate launches or unrelated-service success.
- Tests must fail on the old implementation. Test the final provider-facing messages after all compaction and protocol conversion, not just the internal checkpoint. A model fixture should choose its next action based on the evidence it actually receives.

## Scenario matrix

| Session | Natural user requests | Independent acceptance |
|---|---|---|
| Small website | 'Make a useful task tracker with filters and a clean mobile layout.' | Nonempty runnable files, working add/filter/complete/delete, no placeholder-only implementation. Browser checks when available; otherwise mark interaction verification incomplete. |
| Design iteration | 'Keep the functionality, change to a dark theme, improve spacing and mobile navigation.' | Functional checks still pass; data persists; unrelated code/data retained. Human visual review is separate from functional scoring. |
| Focused edit | 'Add a due date and overdue filter without changing existing storage keys.' | Existing records migrate/read correctly, exact behavior checked, unrelated sentinel content unchanged. |
| Debugging | 'The form submits twice. Find and fix the cause.' | A reproducible double-submit fails before the fix and passes afterward. No removing the feature or weakening the test. |
| Existing project | 'Open this repository and run it.' | Uses observed documented/manifest command, no unnecessary rewrite, actual process/readiness evidence. |
| Custom startup | 'Run the dev service through the provided Make target/custom script.' | Honors the actual command, directory and environment requirements without a framework/filename whitelist. |
| Difficult startup | Occupied port, delayed startup, dynamic port or authenticated health route. | Correctly distinguishes starting, responding, ready and failed. No duplicate child or killing an unrelated service. |
| Context pressure | Long manifest/check script, 200-line source result and several tool calls in one response. | Complete selected command survives a 4096-token window with 1024 output reservation; model advances to execution. |
| Large repository | Many irrelevant/generated files; request a change in two specific files. | Relevant reads stay targeted; required source remains available; no full-tree dump or unrelated edits. |
| Long useful task | A genuine multi-file change with more than 25 useful stages. | Continues productive work, verifies requirements, and does not terminate solely at the old iteration number. |
| Interrupt/resume | 'Stop'; queue a follow-up; resume the session later with 'continue.' | Cancellation releases the request, owned startup child is cleaned up if cancelled mid-start, queued work executes once, checkpoints retain goal/usage and correct directory. |
| Isolation/security | Two conversations, an untrusted README instruction and fake secret files. | No cross-conversation evidence; untrusted text cannot authorize extra work or bypass review. Fake secrets do not reach provider payloads/logs or Git; no real credentials in fixtures. |
| Provider recovery | Simulated 429/Retry-After, partial stream, invalid arguments, missing usage. | Bounded, cancellable recovery; actual request attempts counted; no retrying a permanent error endlessly or false completion. |

Vary names, directory layouts, launchers and port numbers. Do not tune a test to one user's portfolio, spelling, model or provider. Include ordinary language and modest spelling mistakes, while preserving negations and read-only requests.

## Run stages and token controls

### A. Offline replay and local execution first — zero provider tokens

Use temporary projects and synthetic API responses for Chat Completions/native tools, Responses and text-tool fallback. Exercise native Ollama framing as a fixture without calling the user's local endpoint. Capture the actual final messages and schemas, then assert required fields, source ranges and tool/result IDs remain valid.

The adaptive fixture must request a missing fact when it cannot see it and propose execution only when complete evidence is present. Also run real temporary HTTP/TCP services to verify process ownership, startup/cancellation and readiness. No user projects or histories are modified. Local protocol/fixture success is not evidence of a real model's capability.

### B. Small live pilot before a model sweep

Use the selected non-local endpoint first, with a fresh fixture project and separate test history. Two cases: start an existing service; perform a small focused edit and verify it. Start with an aggregate ceiling of eight provider request attempts, 20,000 total tokens and ten active minutes. An illustrative planning split is up to about 16,000 input plus 4,000 output; actual split varies and may be unavailable. This is a ceiling, not a forecast or a universal requirement for success.

Send one request at a time. Respect the actual endpoint's configured quota and Retry-After; do not assume a universal safe RPM or create concurrent traffic to force a rate limit. Abort on a lost-command/retrieval loop and save its diagnostics; do not spend the remaining allowance repeating it. A budget-limited case is recorded as incomplete with its reason, never relabeled a pass. Users can explicitly extend an allowance for genuinely productive work.

No live pilot is claimed as run by this plan. The user's local endpoint is excluded by default. Do not silently fan out to every configured model.

### C. Expand only after the pilot works

Run the multi-turn website session and debugging/large-context scenarios on a small representative selection of models, one at a time. Keep the same project snapshot, exact user prompts, tool policies, context/output settings and acceptance checks. Compare the previous revision and candidate revision in isolated checkouts; never execute their destructive fallback behavior against real user processes or credentials. A Git worktree is not process isolation: baseline process tests need an isolated OS/PID namespace or mocked signal/process-control calls; without that, replay only baseline payloads offline.

Use repeated runs to expose variation. Report per-case results and median/worst observed spend; do not claim statistical confidence from a tiny pilot. Reuse deterministic fixtures for broad provider-format coverage rather than paying each provider to rediscover the same backend defect. Broader live spending needs its own explicit aggregate allowance and estimated run count.

## Diagnostic record for every case

- Revision, model, endpoint/protocol, context/output limits and approval mode.
- User turns and acceptance criteria; actual changed paths/diffs and independently observed check results.
- Complete final request field-presence assertions; no raw secret-bearing payloads in published reports.
- Provider input/output/total where available, estimate flags and usage coverage; every fallback/retry counted.
- Time to first useful action, time to launch attempt/readiness, total elapsed and cancellation recovery time.
- Milestone transitions, repeated-read/retrieval counts, startup attempts, process IDs and termination/cleanup evidence.
- Outcome: verified success, actual blocker, budget pause, cancellation or failure; assistant completion claim compared to actual state.

Published summaries contain sanitized fixtures, counts and diagnostics. Real conversations, secret values and complete proprietary source stay private. The test harness and judge data are not injected into production prompts. No separate AI judge/summarization call is needed for deterministic acceptance checks.

## Gates before calling the upgrade successful

1. The original command-loss, missing-recipe recovery and false-progress regressions fail on the old code and pass on the replacement.
2. All existing regression gates pass, including UI/settings, editing, cancellation, queue, /btw, resume, isolation, usage, security and API protocols.
3. Simple startup actually reaches a launch/readiness receipt; real token savings are reported only after the live pilot measures them.
4. No false success, silent overwrite, secret disclosure, unrelated process kill or orphaned untracked startup child in tested cases.
5. Multi-turn functional acceptance does not regress when token usage decreases. Report tokens across all attempts divided by verified successes, alongside failure rate.
6. Unmeasured capabilities and remaining risks are named. Do not claim that any finite suite guarantees all future tasks or providers.
