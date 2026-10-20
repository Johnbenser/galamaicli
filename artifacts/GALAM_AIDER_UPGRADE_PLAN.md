# Galam upgrade plan informed by Aider

Reviewed 2026-10-20. This document proposes an implementation; runtime changes are not part of this review.

The objective is to improve Galam's autonomous terminal actions, coding reliability, and context efficiency across local and hosted models. Galam's existing execution modes, persistent working directory, background tasks, provider choices, and Action display define the integration requirements.

Aider source inspected: commit `5dc9490bb35f9729ef2c95d00a19ccd30c26339c`, checked out at `/tmp/galam-aider-review`. Relevant files: `aider/run_cmd.py`, `commands.py`, `history.py`, `repomap.py`, `models.py`, `coders/base_coder.py`, `coders/chat_chunks.py`, `coders/editblock_coder.py`, and `coders/shell.py`.

## Findings established from Galam's current code

| Finding | Evidence | Consequence for the upgrade |
|---|---|---|
| Explanatory shell blocks can be interpreted as actions. | An isolated parser probe gave `extract_tools_from_text()` an explanation containing a fenced `pwd` command with an explicit instruction not to run it. The parser produced one tool call. | Separate executable proposals from examples and explanation; keep provenance through execution. |
| The renderer removes explanatory shell examples. | The same probe showed `clean_response_text()` removed the `pwd` example. | Rendering must use parsed spans and action classification, rather than deleting every shell-looking fence. |
| A failed curl fallback can lose its error status. | A mocked failed external curl followed by a mocked HTTP 404 fallback returned `[Executed via Python Curl (urllib)]: HTTP 404: not found`, without an exit failure marker. No real HTTP request was made. | Carry HTTP status and process exit status as data. Text matching cannot determine success. |
| Earlier user constraints can disappear. | A ten-message fixture containing an initial API compatibility constraint and a later standalone query became four dialogue messages; the constraint was absent. | Preserve the active task and constraints independently of recent dialogue. |
| Identical actions are suppressed for an entire request. | `galam.py` stores `tool_name + arguments` in `executed_commands` before execution and rejects later matching calls. | A test command must be allowed to run again after an edit; approval rejection and failure must not count as successful execution. |
| Server control can affect more than the requested service. | The natural-language stop shortcut invokes `stop_all()` followed by Python, Vite, and Node stops. Port recovery can call `free_port()`, which scans external processes and invokes `fuser -k`. | Resolve exact tasks and ownership before stopping or replacing services. |
| Cancellation does not fully control foreground commands. | `/cancel` changes `AGENT_STATE['abort']`; foreground `subprocess.run()` can remain blocked until its 120-second timeout. | Track and cancel the owned process group through the runner. |
| Foreground output is buffered, then limited to its first 12,000 characters. | `execute_shell_action()` captures output in memory, then returns only the prefix. Tool telemetry saves only an 800-character preview. | Stream and persist output first, then select useful context. Errors at the end must remain retrievable. |
| Multiple execution and schema paths have diverged. | `tools/bash.py` contains another runner and `TOOLS_SCHEMA`; the main path uses `engine/task_runner.py`. Optional LangChain schemas cover a different set of tools. | Define one tool registry and runner contract, with wrappers for existing callers. |
| Native tools are parsed but the active request builder does not attach the shared tools schema. | `engine/payload_builder.py` builds the main requests; the older NVIDIA/OpenRouter functions attach schemas separately. | Native tool support requires request, response, history, and result adapters together. |
| Some displayed commands lack handlers. | `/add-dir`, `/agents`, `/edit`, and `/changelog` are accepted by the known-command set but have no matching branches in `handle_slash_command()`. | Generate help, completion, and dispatch from one command registry. |
| `/clear` clears the screen rather than conversation context. | Its handler runs terminal clear and prints the banner. | Keep that behavior explicit; introduce `/context clear` for context reset. |
| Current local integration has model-specific defaults. | `engine/local_server.py` names a specific GGUF; local model discovery assigns every discovered model an 8,192-token context window. | Separate server launch configuration from model identity, and resolve effective limits from capabilities or explicit configuration. |

These findings are code-path observations and isolated probes. This review did not exercise process termination, server restart, arbitrary undo, or live hosted-model requests.

## What Aider contributes and how it fits Galam

| Area | Observed Aider behavior | Galam adaptation | Integration consequence |
|---|---|---|---|
| Suggested shell commands | Parses designated shell blocks, asks before running suggestions, and offers to share output with chat. | Convert explicit model proposals to Galam actions; use Galam's configured review policy. Agent actions automatically contribute compact results. | Copying every Aider confirmation would interrupt Galam's existing autonomous workflow. |
| User shell commands | `/run` reports the token size before optionally adding output; `/test` feeds back failed output. | User `!` and `/run` use the common runner with user origin recorded. Retain an outcome reference for follow-up questions; support configurable output sharing. | A manual command can stay free of model calls while remaining available when the user says “fix that error.” |
| Terminal execution | Streams output and can hand an interactive terminal to `pexpect`. | First implement foreground streaming and cancellation; add interactive PTY handoff separately. | Direct PTY takeover would conflict with prompt_toolkit's active input and review prompt handling. |
| Working directory | Runs commands from the project root supplied to the runner. | Capture Galam's current session directory per action, and record navigation transitions. | Adopting a fixed root would break “go there, then run…” behavior. |
| Background services | The inspected execution path runs shell commands and captures output; it is not a replacement for Galam's task ownership and readiness lifecycle. | Retain Galam's task management and improve its state/result contracts. | Shell commands can still spawn background processes; Galam must manage owned services explicitly. |
| Context | Separates history, repository map, read-only files, active files, and current messages. | Assemble those categories plus action results and a task ledger within an explicit budget. | Directory snapshots alone do not provide code relationships, and recent-message trimming loses facts. |
| Repository map | Ranks code symbols and caches extraction. | Add an optional budgeted symbol index for code tasks and selected roots. | Large directory scans and mandatory native parser packages would burden Termux. |
| History | Summarizes old messages and retains recent context. | Persist verified task facts first; use optional model summaries for older discussion. | Summarization adds calls and may lose facts; its failure must not erase memory. |
| Editing | Uses model-configurable whole-file and search/replace formats. | Allow explicit edit envelopes to normalize into Galam's existing edit tools. | A new edit parser must not create a second file-writing backend. |
| Validation | Can lint changed files and run configured tests, with bounded repair attempts. | Configure checks per project and run them through the same Action runner and policy. | Arbitrary discovered scripts must not silently become automatic execution commands. |
| Recovery | Tracks edits with Git and offers undo. | Journal Galam-owned edits, with optional Git integration and explicit commits. | Many Galam workspaces are not Git repositories; existing user edits need separate preservation. |
| Models | Has model settings for formatting and supported request parameters. | Resolve capabilities per endpoint and model, including user overrides and unknown-model fallback. | A model-family substring is insufficient to choose API parameters or tool protocol. |

Source references: [shell runner](https://github.com/Aider-AI/aider/blob/5dc9490bb35f9729ef2c95d00a19ccd30c26339c/aider/run_cmd.py), [command handling](https://github.com/Aider-AI/aider/blob/5dc9490bb35f9729ef2c95d00a19ccd30c26339c/aider/commands.py), [shell suggestion execution](https://github.com/Aider-AI/aider/blob/5dc9490bb35f9729ef2c95d00a19ccd30c26339c/aider/coders/base_coder.py#L2434), [repository map](https://aider.chat/docs/repomap.html), [history summaries](https://github.com/Aider-AI/aider/blob/5dc9490bb35f9729ef2c95d00a19ccd30c26339c/aider/history.py), [edit formats](https://aider.chat/docs/more/edit-formats.html), [validation](https://aider.chat/docs/usage/lint-test.html), [Git integration](https://aider.chat/docs/git.html).

## Shared architecture and contracts

The execution flow should be:

`user request -> scoped session/task state -> context assembly -> provider adapter -> explicit action proposal -> schema/preflight/review -> runner -> typed result + stored output -> context update -> next action or evidence-based answer`

Direct user commands enter at the action stage. Provider retries enter only at the inference stage. An inference retry must never replay an already executed action.

Use small Python dataclasses and interfaces first, retaining wrappers for the current function signatures. LangChain/LangGraph remains optional; both paths must use the same validation, action results, context assembly, and provider adapter. Existing Android dependency pins require no change to introduce these contracts.

1. **ActionProposal:** action ID, request ID, origin (`user` or `model`), tool, validated arguments, source format, exact working directory, and any expected file hash. Complete native calls and explicit text envelopes are eligible proposals. Reasoning and explanatory code blocks remain text; legacy ambiguous proposals trigger one short formatting repair.
2. **ActionResult:** action ID, status (`completed`, `failed`, `running`, `cancelled`, `timed_out`, `rejected`), real exit code or signal when applicable, duration, directory before/after, output references, truncation metadata, and optional process/task ID. A running service has separate readiness evidence. A file write is successful only as a write; it is not evidence that the feature works.
3. **SessionState:** current directory, selected roots, active task, user constraints, recent result IDs, selected model/profile, and permission decisions. Capture an immutable request view and use event/revision numbers to merge updates. Avoid process-wide `os.chdir()` and environment mutation as session state.
4. **ModelProfile:** endpoint/model identity, input/output/shared-context limits, token-count method, streaming support, native-tool support, message-role constraints, supported parameters, reasoning behavior, and preferred explicit text/edit format. Overrides precede verified server metadata, then known profile defaults, then a conservative generic profile with visible uncertainty.
5. **ContextBundle:** required task facts, latest request, selected code/reference spans, recent action results, compact older memory, and a token breakdown. Provider formatting must preserve the semantics and pairing of tools/results.

## Stage 0: Establish compatibility and measure the baseline

Create an inventory of live entry points and configuration precedence before extraction. Cover normal chat, `/plan`, `/btw`, direct `!`, natural-language shortcuts, streaming/non-streaming, settings/model switching, review modes, session resume, and optional LangGraph.

Create deterministic fixtures and fake provider transports. Record completed task outcomes, request counts, input/output tokens where known, context estimates where not known, action failures, action parse repairs, and elapsed time. Capture a reversible source backup outside the indexed project before implementation. The project currently has no Git repository, so do not assume a checkout can restore it.

Exit criterion: the existing flows have reproducible fixtures and each proposed module has an identified caller and compatibility wrapper.

## Stage 1: Correct and unify Galam Action execution

Primary integration files: `galam.py`, `engine/task_runner.py`, `engine/tool_parser.py`, `engine/execution_policy.py`, `tools/bash.py`, `engine/logger.py`. Proposed additions: `engine/action_types.py`, `engine/action_service.py`, and `engine/tool_registry.py`.

- Put user commands, model tools, deterministic navigation/start shortcuts, and task-control shortcuts through the same action service. Preserve REVIEW/AUTO/ALWAYS-PROCEED semantics; explicit user origin prevents unnecessary inference or duplicate approval.
- Replace string-prefix success detection with typed status. Preserve the numeric HTTP status from the curl fallback and the process exit status separately.
- Validate tool arguments without requiring LangGraph. Generate native schemas and optional LangChain wrappers from the tool registry.
- Parse executable spans once. Preserve examples in rendered answers. Keep explicit legacy XML/JSON formats supported while ambiguous reasoning/prose is repaired into an explicit proposal before execution.
- Deduplicate by action ID. Detect loops using repeated unsuccessful proposals with unchanged relevant state. Allow tests, file reads, and other justified commands to run again after edits, process transitions, or an explicit user rerun. Never cache a mutating action as a reusable success.
- Replace broad stop shortcuts with target resolution. Owned tasks are distinct from discovered external services. An occupied port produces a conflict result; replacing an unrelated process requires a specific target and the existing execution policy.
- Make task completion depend on the requested outcome. Distinguish “file changed,” “check passed,” “server running,” and “server ready.” Preserve unfinished steps even after an intermediate command succeeds.

Compatibility wrappers continue returning `(text, is_background)` while callers migrate to `ActionResult`. The Action card, logger, task ledger, and model observation all derive their status from the same result.

Exit criterion: the parser/example and curl probes behave correctly; review modes remain intact; repeated verification after an edit works; service control targets only the selected service.

## Stage 2: Stream, retain, and retrieve shell output

Proposed additions: `engine/command_runner.py` and `engine/result_store.py`.

- Use `Popen` with an owned process group for foreground commands. Stream output to the Action display and an output store; bound memory even for large logs. Retain output channel/order information where supported.
- Return timeout and cancellation as explicit results. Cancel the tracked group, preserve partial output, and release the UI promptly. Record descendants/ownership so cancellation cannot target Galam or an unrelated service.
- Keep each compound or multiline shell command intact. Aider's line-by-line suggested-command execution should not be copied into Galam: splitting would change heredocs, shell variables, and compound-command semantics.
- Persist directory changes through recorded session transitions. Initially retain current leading-`cd` behavior; later expand compound navigation only with an explicit parser/runner contract. Document that arbitrary shell exports do not persist across subprocesses. Explicit session environment overrides can be added independently.
- Store complete output before selecting a model preview. Large successes receive a compact outcome; failures receive useful error lines plus bounded head/tail. Mark omissions and provide `read_action_output(action_id, start_line, end_line, query)` for exact retrieval. Apply output retention limits on disk.
- For manual commands, record a compact outcome and result ID without an LLM call. Context sharing can be `auto`, `none`, or `full`; model actions default to automatic bounded feedback.
- Make server readiness configurable: process alive, TCP ready, or a specified HTTP endpoint/status. Register a launched task before health polling. A slow service remains tracked as starting, and an HTTP root 404 does not universally mean startup failure.
- Add interactive PTY handoff only after input ownership, return-to-chat, cancellation, and prompt_toolkit restoration are tested. Keep noninteractive execution available everywhere.

Exit criterion: an error at the end of a large log remains retrievable; cancellation stops an owned foreground process; a starting server remains tracked; no command splitting occurs; the prompt recovers after execution.

## Stage 3: General model profiles and provider parity

Integration files: `config.py`, `storage/endpoints.py`, `routers/manager.py`, `engine/payload_builder.py`, `engine/local_models.py`, `engine/local_server.py`, and provider-specific routers. Proposed addition: `engine/model_profiles.py`.

- Resolve profiles by endpoint and served model ID, with configured aliases. Keep family rules as optional formatting defaults.
- Attach native tool schemas only for supported/configured endpoints, and serialize tool replies with matching IDs and required roles. For text-only endpoints use a short explicit action envelope; both normalize to the same proposal/result contract.
- Normalize responses once across streaming, normal JSON, stream fallback, and continuation. Do not execute a partial tool call. Preserve native calls even when the server answers a streaming request with JSON.
- Reassemble context and recalculate limits after a model switch, continuation, or fallback. Preserve task state and approved action history independently of provider messages.
- Resolve request parameters from capabilities; avoid sending unsupported temperature, reasoning, or chat-template fields solely because of a family name. Bound transport, formatting repair, and continuation retries independently.
- Configure local server binary, model path, host/port, and launch arguments separately from the served model ID. Auto-start is an explicit server lifecycle setting. A discovery result must not silently select an arbitrary model when several are available; preserve selection or report the mismatch.
- Read effective server context metadata when supported, including `meta.n_ctx` when present. Never treat trained context size as the currently allocated window. Generic discovery without context metadata must use an override or documented conservative limit, rather than hardcoding every model to 8K.
- Cache discovery with refresh/invalidation rather than making it a dependency of every request. Retain a manual model ID path when discovery is unavailable. Keep existing provider fallback preferences and distinguish authentication, overload, and unreachable-server errors.

Exit criterion: native-tool, text-tool, strict-role, reasoning, unknown-model, and local/hosted fixtures all execute the same harmless tasks. No provider route bypasses action validation or context budgeting.

## Stage 4: Reduce tokens while preserving task continuity

Integration: replace the current last-four/last-eight heuristic in `build_enforced_messages()`. Proposed additions: `engine/context_manager.py`, `engine/token_budget.py`, and `engine/task_memory.py`.

**Budget the actual request.** For a shared-window endpoint:

`input budget = effective window - reserved output/reasoning allowance - protocol/safety allowance`

Endpoints with separate input/output limits use their own constraint equations. Count tool schemas, provider wrappers, files, system text, history, and observations after serialization. Use an available tokenizer or provider count method, caching results; otherwise label the estimate and reserve more space. Input estimation failure is never a zero-token request. Update estimates with returned usage when available.

**Prioritize facts.** Pin the exact current request, applicable user constraints, directory, active plan steps, pending errors, approvals, and recent evidence needed for the next action. Fit relevant file spans and results around those facts. Compact old discussion rather than dropping constraints based on pronouns. If required content alone exceeds the limit, report that fact and offer a narrower scope; do not silently omit the user's request.

**Keep evidence retrievable.** Store full results and transcript separately from the assembled prompt. Store file references with content hashes and line ranges; reread on external changes. Mark previously passed checks stale when relevant files change. Compact repetitive repair instructions and duplicate tool-output echoes.

**Make memory inexpensive.** Start with a deterministic task ledger built from user constraints and typed action events. Summarize older discussion only when needed; an optional summarizer may use the selected model or an explicitly configured alternate. Retain original history and merge summaries by event version to avoid races. Never route private local context to a hosted summarizer implicitly.

**Keep stable context stable.** Place stable instructions and reference content before volatile task/output content when provider formatting allows. Gate provider cache headers on capability. Prompt caching can reduce billed work or prefill time; it does not increase a context window or replace trimming.

**Expose the budget.** `/tokens` or `/context` reports measured/estimated tokens by category, reserved output, omitted content, and active files. Avoid displaying an exact count when only an estimate is available. `/context clear` resets task memory explicitly; `/clear` retains its current screen-clearing behavior.

Exit criterion: a long-session fixture preserves its original constraints and pending work, large outputs remain recoverable, model switching triggers rebudgeting, and serialized requests fit every tested limit. Tests cover 4K, 8K, 32K, and 128K profiles; no policy is tied to one model's size.

## Stage 5: Add code awareness without expensive scanning

Proposed additions: `engine/repo_index.py` and `engine/file_context.py`; connect them to the context manager and command registry.

- Support `/add`, `/drop`, `/read-only`, `/map`, and an implemented `/add-dir`. File selection controls detailed context; it does not remove the ability to inspect another relevant file through an explicit action.
- Index selected project roots with ignore rules. Exclude dependency/build directories, Git internals, Galam logs, transcripts, caches, and credential files from automatic inclusion. Respect `.gitignore` and `.galamignore`, with explicit file selection handled visibly.
- Use Python AST where appropriate and lightweight installed/fallback parsers elsewhere. Tree-sitter is optional and loaded only when available; do not add mandatory native dependencies to Termux.
- Rank paths and symbols using the user request, selected files, recent edits/errors, and available dependency references. A small useful map comes before a full graph-ranking implementation.
- Cache per-file extraction by identity/content state. Invalidate on Galam edits and detect external changes; directory modification time alone is insufficient for nested edits.
- Allocate map tokens from the current request's budget. A simple navigation command does not need a repository map; a cross-file code change does. Show a suggested file when its full contents do not fit.

Exit criterion: a cross-file fixture locates the right definitions with less context and no loss of required code. Indexing a project does not traverse the whole SD card, dependency trees, or unrelated roots.

## Stage 6: Reliable edits, focused checks, and recovery

Integration: `tools/filesystem.py`, Action service, project configuration. Proposed additions: `engine/edit_journal.py` and `engine/project_checks.py`.

- Preserve current atomic single-file writes and Python/JSON validation. Improve review to show an actual proposed patch before applying it.
- Support profile-selected explicit native JSON or search/replace envelopes through the same editing backend. A simple text diff can reduce JSON escaping burden for some models; choose it from measured format reliability. Whole-file creation remains available for new files.
- Stage and validate each proposed edit against the inspected hash. Reject ambiguous matches and external changes with a compact actionable mismatch. Do not silently apply a fuzzy patch to another file.
- Record original and final hashes and contents for Galam-owned edits. Validate all files in a proposed group before replacing them, and journal each application for crash recovery. Multiple filesystem replacements are not one globally atomic transaction.
- Provide `/diff` and `/undo` for journaled changes. Undo checks current hashes and refuses to discard later user edits silently. Git projects can expose explicit `/commit`; automatic commits and committing existing dirty user work are optional policies.
- Run configured checks through ActionResult and the same review policy. Select narrow checks for changed files, offer project test/build commands, cap automatic repair attempts, and summarize actual outcomes. Missing optional tools mean “check not run,” not “passed.”
- Track requested modifications and verification separately so a successful write cannot complete a multi-step coding task by itself.
- Generic shell side effects, package installations, and external services cannot promise automatic undo. Shell-driven file changes should invalidate code/context caches and be reported separately from journaled edits.

Exit criterion: edit, failing check, corrective edit, and rerun succeeds in a fixture; ambiguous patches leave files unchanged; undo preserves subsequent user edits; non-Git projects work; read-only/rename/permission errors preserve recoverable originals.

## Stage 7: Expose optional workflows and improve maintainability

Introduce separate chat-purpose and execution-policy settings. `/chat-mode ask|action|code|plan` controls the task purpose; `/mode review|auto|always-proceed` continues to control execution review. Enforce ask-mode mutation restrictions at the Action service, including proposed shell actions, rather than relying on a prompt. Keep read-only shell auto-execution conservative; shell-command classification is not a complete sandbox.

Generate help, completion, handlers, and model-visible tool descriptions from registries. Give every displayed command a real handler or an explicit unsupported message. Preserve existing conversation JSON through a versioned loader and migration that keeps the original transcript intact.

Planner/editor or secondary utility models are optional configurations. A single chosen model can do the whole task. Two-model editing adds latency, cost, and context sharing; enable it only when a measured workflow benefits and both routes are explicitly configured.

Defer voice, browser copy/paste, IDE comment watchers, broad provider abstraction replacement, and mandatory dependency changes until core actions and context pass the rollout gates. Those features do not resolve the confirmed failures.

## Validation and rollout gates

| Test group | Required scenarios |
|---|---|
| Action interpretation | Native calls, explicit XML/JSON, text diff, quoted shell examples, reasoning with commands, malformed/truncated calls, repeated streamed chunks. No example executes. |
| Shell behavior | Exit 0 with text containing “error,” nonzero with no text, Unicode output, long output with final error, spaces in paths, intact multiline commands, timeout, cancellation, rerun after edit. |
| Service lifecycle | Slow startup, HTTP root 404 with valid health endpoint, occupied unrelated port, task ownership, restart recovery, selected-service stop. Use temporary fixture services only. |
| Review and UI | REVIEW/AUTO/ALWAYS-PROCEED parity, explicit user command origin, denied/rejected action, review prompt cancellation, no PTY/input contention. |
| Context | Original constraints survive many turns, referential request, resumed session, changed files, retrievable omitted output, required context too large, correct tool/result pairing. |
| Provider compatibility | Streaming/JSON, native/text tools, strict roles, unknown models, unsupported parameters, provider fallback, smaller-context switch, optional LangGraph absent/present. |
| Editing and checks | Unique/ambiguous replacements, external concurrent edit, syntax failure, multiple-file partial-write recovery, failing check then rerun, undo with later user changes, no Git. |
| Environment | Current Python/Termux pins, paths under `/sdcard`, unavailable ripgrep/parser/Git, filesystem permission failure, no mandatory new compiler toolchain. |

Build new modules behind narrow per-feature toggles and preserve old callers through adapters during migration. Never run both execution backends for the same action. State/schema changes must be backward readable; rolling back a flag must not invalidate the saved transcript.

Use deterministic provider fixtures for the first gate. Then use representative configured models with the same tasks and settings: text-only local, native-tool-capable, reasoning, and a hosted/unknown-profile route when available. This is capability coverage, not a requirement that all endpoints be installed before development can proceed. Live runs require no unrelated server restart or destructive system-wide command.

Compare verified completion rate, input/output tokens, requests per task, parse repairs, unnecessary reads, latency, repeated actions, and context-limit failures. Report measured token counts separately from estimates and cache savings separately from context reduction. Publish improvements only for measured cases; token reduction alone does not pass if the task becomes less correct.

Recommended implementation order: Stage 0 -> Stage 1 -> Stage 2 -> Stage 3 -> Stage 4 -> Stage 5 -> Stage 6 -> Stage 7. Edit recovery can begin earlier once typed actions and versioned state exist, but automated checks depend on repeatable command execution and trustworthy outcomes.

The first useful delivery is typed, correctly scoped Galam Actions with preserved output and reliable reruns. The next is shared model/context budgeting. Repository maps and additional coding workflows build on those foundations.
