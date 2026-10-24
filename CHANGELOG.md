# Changelog

User-facing improvements and bug fixes for Galam.AI. Entries describe implemented behavior; provider-side availability and model quality can vary.

## Unreleased — connections, workflows and presentation

### Terminal references and live clarification

- Retrieve current path, package-script and Python/Node entrypoint facts before terminal execution. Reject concrete contradictions with declared alternatives; refresh facts after metadata changes. Keep complex commands under existing review and runtime verification. Coalesce repeated command-failure guidance without copying the whole user request again.
- Add the `ask_user` Galam Action with an inline question, numbered choices and an `answer>` prompt. Safe local discovery can withdraw an unanswered question after one exact verified file/folder match. Ambiguous or unnamed targets wait for the user; answers preserve case and never approve execution.
- Route answers around the busy-message queue and slash completion. Preserve late replies as drafts, clear questions on cancellation and continue queued work. Simple verified navigation/listing requests avoid model calls and optional planner imports.
- Preserve read-only existing-service verification before rejecting an unnecessary missing launcher. Reuse still requires served project bytes and assets to match local files; restarts, builds, exact commands and extra requested checks retain their requirements.
- Added 22 behavioral tests and an offline Propose–Probe–Compose component evaluator. **457 tests passed**, no skips (215.780 seconds). The real supplied navigation/listing sample used only `cd`/`ls` and zero model requests. Details and limits: [terminal workflow evidence](artifacts/TERMINAL_ACTION_WORKFLOW.md).

### Configure and navigate local model servers

- Restructured only the Local Models flow into Server Connection, Model Profiles, Tuning, and connection testing, with back navigation returning to the relevant local screen.
- Added an inline local server editor for the URL (including host and port), `GET` model-list path, and `POST` chat path. The defaults remain `/v1/models` and `/v1/chat/completions`; paths are validated and saved through the existing settings store, with an in-place save confirmation and route preview.
- Applied the saved routes consistently to model discovery, test requests, and real local chat requests. Connection testing checks these two routes directly, so a compatible server does not also need to expose `/health`.
- Added regression coverage for defaults, custom routes, saved ports, navigation, and normal chat URL selection. The full suite passed **435 tests**, no skips (123.978 seconds).

### Keep successful simple actions when a summary response fails

- Investigated a folders-list request that executed `ls -la` correctly but ended as “task incomplete” because a second, redundant model acknowledgment request failed. Verified single-command folder/directory listings now return names directly from the successful output without another model call.
- The local receipt accepts only a plain `ls` pipeline (optionally preceded by `cd`), checks an explicit requested path against the command, and reports only what the output contains. Compound tasks, transformed/truncated commands and failed actions still use normal continuation and cannot be marked complete by the listing shortcut.
- Added three regressions. **All 432 tests passed**, no skips (174.179 seconds). See [action result recovery follow-up](artifacts/ACTION_RESULT_RECOVERY_FOLLOWUP.md).

### Android project startup and refresh reliability

- Resolve a missing leading-slash project name only when it matches one exact sibling of the current workspace; ambiguous or unmatched names remain blocked before launch.
- Prefer the package manifest's declared development workflow, including a build step it composes. Avoid preemptive dependency installation when local dependencies already exist; diagnose Android shared-storage symlink `EACCES` separately and reject recursive `chmod` commands as a generic repair.
- Require startup guidance to check the served application and relevant refresh/navigation routes instead of treating the root page's HTTP 200 as proof that the site works. A read-only live check returned HTML for the project's home page and all ten declared clean routes; its served entrypoint assets matched the project files. The server was not restarted or modified.
- Added four regressions. The earlier full run passed 429 tests; the latest full run is recorded above. See [startup workflow follow-up](artifacts/STARTUP_WORKFLOW_FOLLOWUP.md).

### Startup correctness and existing services

- Investigated `1a32524e`: a healthy external server could not satisfy startup because completion required a new owned launch. Match actual served project HTML and referenced local assets, report external readiness honestly, and preserve ownership boundaries. Exact commands, builds/restarts and additional checks retain their execution requirements.
- Label default manifest recipes as candidates, preserve alternatives and actual attempted build/start chains, retain useful latest recovery evidence, and stop recommending a new launch after readiness is verified.
- Add observed address/current PORT and runtime temporary-directory hints; preserve explicit overrides and conflict recovery without permanent port assignments. Accept real stdin redirection while still rejecting placeholder paths.
- Handle missing legacy inspection paths and record resolved paths for future successful file observations. Added fourteen regressions; all 425 tests passed without skips. Selected-model live trials verified existing-service reuse and a correct fresh build/start. See [evidence and limitations](artifacts/STARTUP_CORRECTNESS_REPAIR.md).

### Adaptive builder stress testing and lower-waste recovery

- Added a reproducible live six-stage builder evaluation: diagnose/repair, HTML creation, exact edits, broken build/start recovery, read-only follow-up and cross-model handoff. Independent files/tests/HTTP/process ownership determine success; provider quota and experiment stops remain explicit.
- Fixed grammatical references being mistaken for required output filenames, which kept already completed pages in forced recovery. Preserved quoted filenames, literals, output types and positive vs negated clauses.
- Carry restriction clauses instead of reissuing completed tasks. Fresh named-file tasks omit unrelated action transcripts while preserving relevant facts, same-file context, original history, continuation, user rules and conditional recall. Scoped edit restrictions no longer become permanent global rules.
- Fixed coordinated prohibitions being classified as mutation requests. Explicit non-action requests that prohibit changes enforce read-only tools even in Always Proceed mode. Generated retry feedback stays internal.
- Diagnose malformed action JSON without executing guesses; repeated syntax errors can switch the current task to the existing XML parameter parser. Preserved source, review and execution checks; removed redundant original-request copies from recovery.
- Reuse actual tracked service state/URL for relevant follow-ups. Recover an unresponsive readiness hint from a verified local address announced by the owned process, retaining health paths and rejecting malformed/external hints. Repeated startup failures receive coalesced evidence-based guidance without a retry cap.
- All 411 local regressions passed in one run with no skips. See [adaptive stress results](artifacts/ADAPTIVE_STRESS_RESULTS.md) for live outcomes and measurement limits. Production caps, UI, settings design and dependencies are unchanged.

### Service lifetime and explicit shutdown

- Fixed successful background servers disappearing from task tracking when their launcher shell exited. A dedicated owned supervisor preserves the original command and follows its service group through nested launchers, checks, actual exit and explicit shutdown.
- Preserved dynamic-port discovery, command reuse, readiness verification, cancellation and stopping only owned processes; added no model calls or project-specific startup rules.
- Restricted natural-language shutdown to explicit, scoped requests. Negations/questions no longer stop servers, and ambiguous requests cannot stop every task.
- Added real lifetime and shutdown-intent regressions. See [service lifetime evidence](artifacts/SERVICE_LIFETIME_REPAIR.md).

### Build completion and requested destinations

- Fixed website/app implementations being marked complete after directory setup when the request also mentioned a folder.
- Scoped explicitly requested output destinations to the current task so Always Proceed avoids redundant ordinary file approvals. Preserved critical review; declined actions can lead to alternatives without repeated identical approval prompts.
- Fixed filename globs returning model transcripts and excluded runtime/dependency/credential data from ordinary project searches.
- Recovered provider-buffered, empty length-limited responses with request-local output capacity; regenerated incomplete action arguments instead of splicing broken JSON. Kept provider capacities, prepared-request validation, local manual tuning and explicit spending allowances.
- Saved explicit incomplete outcomes for endpoint exhaustion and corrected shell read commands misidentified as source writes. See [repair evidence](artifacts/BUILD_COMPLETION_REPAIR.md).

### Startup routing, context recovery and uncapped continuation

- Fixed startup wording such as “run my web on localhost” bypassing the Command Agent. Added read-only project metadata discovery and late handoff for authorized background commands; the model chooses the actual startup recipe.
- Kept compound “start the app, then run checks” requests in startup routing without allowing the ready-service receipt to skip remaining work. Verified services remain visible while follow-up work runs.
- Removed default total step/request/token/time caps and count-based repetition/protocol termination. Checkpoint cadence, explicit user spending caps, cancellation, reviews and provider/operation constraints remain. Added `/budget ... off` and migration of older generated caps/global context overrides.
- Fixed ordinary multi-tool context overflow by coalescing generated guidance, separating generated worker instructions from user rules and fitting recoverable result previews. Exact command/path arguments remain intact. Complete prepared requests include serialization/schema overhead in their capacity check.
- Added local, conditional conversation recall with source references, inherited subjects for follow-ups and a distinction between proposals and observed findings/commands. No extra model or embedding calls. Exact stored tool evidence no longer expires by age/count.
- Fixed quoted HTML being rejected as a placeholder, curl timeout values becoming URLs, and parser exceptions reporting success. Honor provider Retry-After values above the old 55-second threshold. Output can continue past the old three-continuation cap, with duplicate-response detection.
- Saved readable query-failure outcomes and added deterministic local handling of the current conversation-ID question. Preserved settings appearance, dock layout, queue, `/btw`, review/edit safeguards and combined token accounting.
- Added real Vite/React asset, Python, Flask, Node, custom Make and build-then-serve regressions. A selected-model live pilot successfully served both a build/Python project and a Vite project; results and limitations are recorded separately from scripted tests.

### Same-model Command Agent

- Startup requests enter a focused Command Agent using the selected endpoint/model. The parent waits; execution stays in the existing reviewed action loop. Unrelated chat turns are omitted while existing user constraints and current-task evidence remain.
- Added a stage row below the input for reading, build/start, diagnosis, repair and verification. After readiness, the tracked service remains listed as running; unknown health is labeled unverified.
- Preserved complete build/start chains and added bounded local build waiting without AI polling. The worker can inspect actual errors, make permitted focused repairs and run project checks. Repeated identical failures cannot renew progress.
- Shared parent cancellation, request/token allowances, history checkpoints and the combined token footer. A ready service returns an observed receipt without another model acknowledgment. No silent worker endpoint/model fallback or global model-setting changes.
- Corrected the settled task outcome after cancellation. Added real temporary-process tests for startup, syntax repair, build waiting and cancellation, plus routing, model pinning, history, accounting, rollback and dock-state regressions.

### Launch workflow repair

- Replaced aggregate task-fact middle-truncation with complete selected launch evidence, source/cwd references and migration of older saved facts. Small recent targeted tool answers are preserved through the next decision.
- Replaced novel-output renewal for known startup recipes with milestone-based recovery. Recovery includes the retained command; unrelated file reads and formatted listings cannot keep extending startup indefinitely.
- Replaced the old server-launch block with one owned-process runner for recognized commands and explicit background execution. Custom commands support local HTTP(S)/TCP readiness, log URL discovery, pending-startup tracking and cancellable local polling without extra AI calls.
- A pending-readiness retry reuses the same owned process. Unrelated healthy services do not establish success; authentication responses and unverified readiness are reported separately.
- Preserved background/readiness arguments through native, optional graph and text tool paths. Added conservative adjacent-verb spelling correction while retaining question/negation guards.
- Removed a remaining `manage_tasks` fallback that could terminate an external PID after an owned-task lookup failed.
- Added regressions that fail on the old implementation, adaptive final-context launch verification and real temporary service tests. Added a realistic coding stress plan; live-model completion and token savings remain unmeasured.

### Added

- Productive tasks continue through renewable work windows; the old iteration setting no longer ends useful work at eight or 25 steps. Conversation checkpoints preserve evidence and cumulative request/token/time allowances across `continue`.
- Repetition recovery, unchanged exact-file read reuse, equivalent-listing/metadata evidence detection and compact task facts retained during context compaction. Archive retrieval references originals instead of repeatedly embedding retrieved wrappers.
- `/budget` to inspect or explicitly extend cumulative task allowances; `/audit [directory]` and a relevant-request security tool for bounded, read-only checks without exposing matched credentials.
- Security regression coverage for credential paths/symlinks, redaction before output storage, credential inheritance, critical-action review, owned-process cleanup and TLS verification.

### Security and workflow fixes

- Removed hardcoded current-user identity from full, compact and fallback prompts. John Benser remains credited as creator; creator attribution grants no permissions. Removed invented creator-name file generation.
- Protected common credential paths from model file tools; excluded common secret files from project searches. Recognized credential/destructive/privileged shell actions and structured writes outside the workspace require review in all execution modes.
- Ordinary shell commands no longer inherit recognized credential environment variables. Reviewed credential commands can still use them. New tool results and logs redact known credential values and recognizable token/private-key formats.
- Removed insecure TLS retries from both provider fallback and the HTTP helper. Removed hidden adapter retries so explicit request entry points account for transport attempts.
- Automatic process cleanup no longer scans/kills unrelated system processes. Executor receives a verified writable temporary directory through `TMPDIR`.

### Earlier additions

- Embedded four-step model connection wizard with review before save, presented in the original compact inline settings style.
- Native Ollama `/api/chat` requests, NDJSON/JSON replies, structured action round trips and provider token accounting.

- Settings → Model Connections: add/edit a model, assign an endpoint group, choose Chat Completions or Responses, enter a masked API key and optionally activate immediately.
- Home-owned `~/.galam/endpoints.json` for connection metadata; keys are saved only to `~/.secrets` with private permissions, literal shell quoting and preserved existing exports/comments.
- Immediate credential application in Galam, startup credential reload and a deduplicated guarded Bash/Zsh source line for future terminals.
- Responses API input/tool-output conversion, streamed function arguments, final snapshots, provider usage and connection-scoped reasoning continuity.
- Custom-connection regression tests, including local HTTP/Bash round trips, transactional rollback, stale-export reload and phone-sized editor input/save/cancel behavior.

- Conversation-owned context and resumable history with working-directory restoration.
- Full-screen centered resume picker, conversation previews, five persistent accent colors, keyboard navigation and permanent selected-history deletion.
- FIFO prompt queue with pending text pinned above the input and no unsolicited printed queue IDs.
- Independent `/btw` side-chat worker, private history and separate cancellation while the main task continues.
- Local GGUF profile discovery, connection testing, AUTO/MANUAL tuning and owned-server lifecycle commands.
- Recoverable context compaction, compact prompt construction and local request-token accounting.
- Agent Router endpoint registration, environment-key lookup and endpoint-specific diagnostics.
- Structured terminal response renderer for tables, sections, lists, numbered code blocks and status text.
- Permanent per-response duration, fixed completion timestamp, input/output/total token footers and saved metadata for history replay.
- Comprehensive Android/Termux installation guide, system/data-flow diagrams, module map and command reference.
- `/changelog` renders the repository's update record locally without a model request.

### Improved

- Server detection checks actual executable positions; reading Vite configs or printing startup instructions stays in the foreground.
- Resolve launch ports through nested package scripts and environment defaults, preserving explicit port overrides. Each launch gets its own log.
- Normal provider stops no longer cause speculative repeated reasoning continuations; explicit token-limit cutoffs still continue.
- Server-start tasks require verified startup evidence; incomplete replies cannot turn successful inspections into a false completion. Resumed short continuations retain the original task.

- Completion footers show duration, a frozen local AM/PM clock and only Total Token; approximate totals use `~` and unavailable measurements remain explicit.
- Fixed false zero-token totals when the optional planning graph runs inference in worker contexts; main and side tasks retain separate ledgers.
- Preserve authoritative provider totals, total-only usage and split input/output frames without requiring extra usage requests or adding prompt text.

- Restored the original inline settings menu and terminal background while retaining the new setup flow, URL preservation and selection fixes. Settings closes by clearing its own render region.
- Resumed conversation replay uses live chat formatting, user dividers/arrows and saved duration/token footers instead of plain role labels; context and actions remain untouched.
- JSON framing handles mislabeled native streams, unexpected non-streaming NDJSON and pretty JSON without the `Extra data` parsing failure. Function fragments and reported usage are retained.
- Fallback confirmation hides the thinking spinner and ends printed question lines before showing the input prompt.

- Endpoint assignment follows the highlighted row, fixing stale checked values that could save Direct selections under Groq. Numbers remain available, including multi-digit choices.
- Default preserves the exact entered request URL. Payload inference is local; explicit format choices also leave the destination unchanged. Typed URLs survive endpoint-group changes.
- Matching provider hosts can reuse the endpoint credential variable without typing it; unrelated hosts receive separate references.
- Removed automatic route rewriting, including the unwanted conversion of Ollama native `/api/chat` URLs to `/v1/chat/completions`. Provider URL suggestions are shown only for empty or untouched fields.
- Credential lookup initializes the secret loader before checking environment aliases, including standalone diagnostics.
- Explicit daily/account quota errors stop redundant same-account model fallback; ordinary temporary model limits remain distinct.
- Removed the built-in NVIDIA Kimi K3 entry after a live HTTP 410 response; HTTP 410 errors now identify an unavailable/retired model.

- Native and fallback text tool protocols, argument assembly and bounded action repair.
- File/directory creation contracts verify actual outputs and content before completion.
- Verified simple filesystem receipts can finish without a redundant model confirmation call.
- Context selection preserves current instructions and relevant tool evidence while bounding older observations; no extra summarizer model is required.
- Tool-output retention, focused edits, syntax validation, project-check scheduling and edit-recovery evidence.
- Ctrl+O keeps its expanded/minimized preference across prompts; compact labels mirror supplied reasoning and actual execution locally.
- Provider usage accounting aggregates requests and continuations, prefers measured counts, and explicitly labels estimates or unavailable values.

### Fixed

- Empty code files being treated as completed implementations.
- Slow/redundant post-action acknowledgments for verified simple tasks.
- Context from unrelated conversations entering the active chat.
- Thought panel not reliably hiding on the next Ctrl+O press.
- Loading animation depending on network activity rather than active-task state.
- Stale thinking/streaming state and advancing completion clocks after a reply ends.
- Cancellation leaving connection/stream waits or approval waits stuck and preventing subsequent queued prompts.
- Definitive quota errors falling into repeated endpoint fallback.
- Side questions blocking behind a busy main task.
- Queue display noise and prompt placement around the input dock.
- Only the latest response retaining a visible completion footer.
- Raw Markdown tables, clipped long code/paths and inline formatting corrupting literal code such as `__init__.py`.
- Empty failed-tool output being presented with a success-style label.

### Verification

- Latest recorded full local suite: **261 tests passed**.
- Changed response-presentation modules passed Python compilation.
- Coverage includes actions/edits, context ownership and overflow, streaming/usage, cancellation/queue recovery, side-chat isolation, Ctrl+O, resume and terminal rendering.
- Fixture tests do not establish every live provider/model combination. Agent Router can reject custom clients under its access policy; the adapter does not bypass that policy.

## Initial public source

- Multi-provider CLI, local endpoint integration, settings UI and verified action pipeline published as the project's initial source snapshot.
