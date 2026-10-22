# Correct startup decisions and existing-service verification

Conversation reviewed: `1a32524e`, 2026-10-22. This repair changes shared backend workflows and runtime guidance; it does not train hosted model weights.

## What failed

The requested project was already running in an external process. Galam observed HTTP 200, the correct process working directory, current build output and matching served bundle bytes, but continued inspecting files and routes until cancellation. Its completion predicate required a successful background launch in the current session. An empty task list was interpreted as missing readiness even though it listed only session-owned tasks.

The two saved task checkpoints recorded 24 requests / 134,862 tokens and 17 requests / 94,709 tokens. These are saved checkpoint counters, not a reconstructed final provider bill. Both were cancelled.

Other contributing issues:

- Recovery repeatedly promoted the manifest's default `start` candidate even though `dev` preserved the observed build/start chain.
- Context assembly replaced the latest recovery's specific operational evidence with generic text.
- Native shell redirection (`< /proc/.../cmdline`) could be mistaken for an angle-bracket placeholder.
- Commands assumed `/tmp` was writable in Termux. A failed download to that path was a command/environment mistake; inspection did **not** establish that the Python curl fallback caused it. Curl fallback behavior was not changed.
- During isolated testing, legacy file observations with null path metadata exposed a new integration error; optional paths are now filtered, successful future file inspections record their resolved path, and a regression covers legacy observations.

## Shared repair

1. **Verify an existing service before treating the address as a blocker.** For a general start request, compare actual local HTTP entrypoint bytes with public project files and verify referenced local JavaScript/CSS assets. HTTP 200 alone is insufficient. Conventional public output folders and explicitly inspected custom entrypoints are supported; unrelated folders are not scanned as possible applications.
2. **Keep readiness distinct from execution and ownership.** A successful match can satisfy a simple startup goal. The receipt says this verification did not execute the proposed command or create a process. It does not adopt, signal or register the external process as owned. Exact command execution, explicit builds, restarts and additional instances cannot be replaced by this check.
3. **Finish only the proven scope.** Simple startup returns an observed receipt without an extra acknowledgment request. Separately requested checks continue normally. Failed or unmatched readiness still requires correction or an honest blocker. No production step/token caps were introduced.
4. **Keep command choice grounded.** Manifest defaults are labelled candidates, alternatives retain their complete scripts, and the actual attempted command is saved intact. Recovery uses this evidence and already verified readiness instead of repeatedly ordering another launch. Only the latest generated recovery of each kind is included in a request; its useful content survives.
5. **Supply actual environment facts.** Startup context includes the runtime temporary directory and address hints derived from current `PORT` and observed project scripts. These are hints, not proof of readiness or permanent port settings. User overrides and observed conflicts can justify an alternative; unknown addresses use actual launch output. No credential environment values are added.
6. **Retain command semantics and guards.** Real stdin redirection passes validation; placeholder paths remain rejected. Existing review, credential protection, source inspection, dynamic ports and owned-process shutdown remain in place. UI/settings layouts and dependencies are unchanged.

The external-service match proves a snapshot of the served public files. It does not prove PID ownership, browser rendering or every backend API operation. Authentication, redirects and entrypoints that cannot be matched safely fail closed; explicit additional checks remain the model's task. Source changes after verification require fresh verification.

## Verification

- **425 tests passed in one full run**, no skips, **74.072 s**. Optional Vite/React and Flask fixture paths were supplied.
- Fourteen new regressions cover real external HTTP services, mismatched content/assets, missing legacy path metadata, custom inspected output directories, explicit restart/command semantics, process non-ownership, one-request acknowledgment, additional checks, retained launch evidence, temporary paths and configured address hints.
- **14 focused tests passed after the final receipt wording clarification and exact-command intent guard (including quoted commands).** The clarification scopes “not executed” to this verification rather than falsely denying a build run earlier in the task.
- A read-only check of the user's existing service matched its index, stylesheet, React vendor scripts, built application bundle and remaining referenced local scripts. The user's project source and server process were not modified, restarted or stopped.

### Live requests through the user's selected model

Endpoint: **Direct Endpoint**. Model: **`minimax-m3.1-flash`**. Starting response setting: **1,024 tokens**, using the existing profile. Fixtures use Python/Node standard libraries, immutable source and an actual build/start chain. Model fallback is disabled. All listed usage is provider reported.

| Trial | Result | Requests | Input | Output | Total | Time |
|---|---|---:|---:|---:|---:|---:|
| Existing external service, initial complete trial | Pass; same external service remained alive; no owned replacement | 16 | 66,699 | 8,547 | 75,246 | 84.24 s |
| Fresh build/start | Pass; build marker, exact served content and one owned service | 6 | 19,103 | 2,330 | 21,433 | 28.55 s |
| Existing service after configured address hints | Pass; used observed address and complete documented command | 7 | 23,373 | 2,766 | 26,139 | 30.95 s |

The fresh trial ultimately used **`npm run dev`**, retaining **`npm run build && npm start`**. The existing-service repeat proposed the same documented chain with the observed readiness address; Galam verified the existing service and accurately returned an external-service receipt without executing that proposed command.

The harness rejected some otherwise ordinary process/version/curl commands as experiment confinement, which increased recovery work. The trials are not a controlled causal latency/token comparison. An earlier diagnostic was interrupted while investigating the null-metadata integration defect; it has no complete saved provider total and is not counted as a passing trial. The three recorded completed trials total **109,175 input + 13,643 output = 122,818 provider-reported tokens**; that is not the entire investigation's bill.

These cases establish correct observed outcomes and removal of the forced-owned-launch loop. They do not promise every model will choose the first command optimally or make no redundant inspections. The final address-conflict wording refinement was checked by regressions rather than another live sweep.

See [compact live evidence](STARTUP_CORRECTNESS_RESULTS.json), [regressions](../tests/test_startup_correctness.py), and the earlier [adaptive stress report](ADAPTIVE_STRESS_RESULTS.md).
