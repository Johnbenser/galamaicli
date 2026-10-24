# Adaptive builder stress evaluation

## Goal

Exercise Galam as a user would: understand an existing project, diagnose actual errors, create and edit code, verify changes, start a service, preserve its lifetime, and continue with another model. Improve runtime guidance and orchestration from observed failures; hosted model weights are not trained.

Production step, context and task-spending defaults are unchanged. This evaluation has finite scenarios and separately identified experiment allowances. A provider rejection or experiment timeout is an availability/incomplete result, not proof of a model's coding inability.

## Matrix

| Stage | User challenge | Independent evidence |
|---|---|---|
| Diagnose/repair | Analyze a tiny project, run its tests, correct an injected calculation defect | Real test exit status, boundary cases, protected-file hashes, focused source diff |
| Build | Create a small complete standalone page using the requested content | Nonempty HTML, title/heading/navigation/content; no external dependencies |
| Exact edit | Change one paragraph without disturbing other content | Whole-file comparison against the exact requested replacement |
| Startup recovery | Run the documented build/start chain; resolve an injected server error and leave it running | Build artifacts, real HTTP content, actual runtime URL, one owned service |
| Follow-up | Explain the previous failure and current address without modifying/stopping the service | Accurate response, unchanged source and owner, HTTP still responds |
| Model handoff | Switch the selected model in the same conversation and perform a focused follow-up edit | Preserved user constraints, correct edit, real checks, service remains reachable |

Every model gets an equivalent isolated fixture. No user project, secrets, selected model setting or existing server is changed. Commands are confined to the fixture's documented operations. Native and text action protocols are exercised locally, including malformed calls, truncated outputs, stale reads, failing commands, conversation isolation, cancellation and quota handling.

## Metrics

- Per-stage provider input/output/total tokens, explicitly labelled estimates when provider usage is absent.
- Requests, first actual action latency, elapsed time, tool calls, corrective attempts and unchanged duplicate proposals.
- New verified outcomes per model request; repeated checks after real edits remain valid work.
- Requested vs observed completion, independent artifact/test/HTTP checks, protected-content integrity and ownership retention.
- No price estimate without verified pricing. No universal ranking from a single run. No combining successes across different runs into one passing result.

## Build order

1. Implement a reproducible fixture and assertion-based local harness.
2. Run the same live scenarios through selected configured online models with fallback disabled. Exclude Local Endpoint. Use existing credential loading without evaluating shell secrets.
3. Inspect failures and redundant requests; distinguish provider/account failures from Galam defects and test confinement.
4. Make the smallest shared pipeline fixes that remove demonstrated waste while retaining actions, source checks, user constraints and recovery.
5. Rerun affected scenarios and local regressions; publish results with failures, costs, scope and verification limits.

Default pilot: three previously configured online models across different endpoint/model families, run sequentially to avoid bursts. Wider sweeps follow the user's selected scope. Experiment allowances and confinement are harness controls only; they are never written to Galam's user settings or production defaults. Stop spending on a credential/quota rejection instead of repeatedly retrying it.
