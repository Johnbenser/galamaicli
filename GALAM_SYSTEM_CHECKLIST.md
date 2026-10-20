# Galam.ai System & Autonomous Action Checklist

This operational checklist defines the core principles, behavior invariants, and execution standards for **Galam.ai CLI**, ensuring a rock-solid developer experience, zero token waste, persistent terminal docks, and seamless multi-model tool handling.

---

## 1. Stagnant Terminal Dock & Text Field Invariants

The Galam.ai user interface must remain predictable, rock-solid, and non-flickering across all interaction states:

- [ ] **Prompt Text Field (`> █`) Stagnation**:
  - The input prompt dock must remain anchored at the bottom of the active screen.
  - The input prompt must NEVER disappear or flicker when models stream reasoning, execute bash tools, or run background processes.
- [ ] **Animation & Spinner Stability**:
  - Spinners and status indicators must update in-place without erasing previous conversational outputs.
  - Long-running commands must report elapsed time cleanly without flooding the terminal with newlines.
- [ ] **Process Lifecycle & Health Check Protection**:
  - Background processes (e.g. dev servers, HTTP daemons) are persistent and MUST NOT be killed automatically when an AI finishes its turn.
  - **Mandatory HTTP 200 OK Pre-Confirmation**: Before Galam.ai displays the server confirmation card (`print_server_card`) or returns an observation to the model, it polls `http://127.0.0.1:<port>` to verify that the server returns HTTP 200 OK. If the server crashes or fails on startup, the error is immediately captured and handled rather than reporting a false-positive active server.
  - The task dock remains visible and solid until the user explicitly requests cancellation or task management via `/tasks` or `kill`.
  - Self-termination guards: intercept any inadvertent `pkill python` or `kill -9` targeting Galam's own PID or Termux shell parent.
- [ ] **Ctrl+O Thoughts Toggle (`CTRL + O`)**:
  - Pressing `Ctrl + O` toggles between Expanded and Minimized thought states.
  - When Expanded, past reasoning traces are displayed in a clean framed box, and live thought tokens stream in real time.
  - When Minimized, thought output is completely hidden, the prompt dock shows compact `Thinking..` without topic leakage, and the toolbar reflects `ctrl+o thoughts (minimized)`.
- [ ] **Interactive Fallback Policy & Failure Transparency**:
  - If `fallback_policy` is set to `"ask"`, Galam.ai prompts the user via `[Y/n]` with the designated fallback endpoint/model rather than silently redirecting.
  - Transparent error diagnostics report why a request failed or delayed (e.g. rate limit, busy server, network timeout).

---

## 2. Galam.ai Autonomous Action Engine Standards

When any supported model indicates intent or is tasked with file/terminal actions:

- [ ] **Multi-Format Inbound Normalization**:
  - Parse tool calls from all 11 supported formats:
    - Native JSON & streaming chunks
    - Mistral bracketed `[TOOL_CALLS]`
    - DeepSeek Unicode delimiters (`<｜tool call begin｜>`) & DSML (`< | DSML | invoke>`)
    - XML tags (`<tool_call>`, `<invoke>`, `<action>`)
    - Functional calls (`execute_bash(command="...")`)
    - Markdown fenced blocks (````bash ... ```` and ````json ... ````)
    - Embedded unescaped JSON objects
    - In-line colon prefixes (`command: ...`, `execute_bash: ...`)
- [ ] **Untagged Monologue & CoT Isolation**:
  - Dual-track extraction routes `<think>`, `<thought>`, and untagged R1 stream monologues into reasoning blocks.
  - User-facing responses never leak raw internal monologue or exploratory musings.
  - If a model exhausts its token budget mid-monologue before emitting closing tags or greetings, the stream is cleanly quarantined as reasoning, and committed tool intents are synthesized.
- [ ] **Reasoning Conclusion Fallback**:
  - When a model discusses and commits to executing commands (e.g., `touch /sdcard/cli/file.txt`, `mkdir`, `python3 server.py`) in its thought process without formal tool wrappers, the parser extracts the committed command and triggers an autonomous Galam.ai Action.
- [ ] **Action Intent Trap & Fallback Synthesis**:
  - If the assistant states conversational intent (e.g. *"I will create the file"*, *"I'll help you create..."*, *"Let me check the port"*, *"I should use the execute_bash tool"*) without emitting a tool block, Galam intercepts the turn and forces immediate tool generation.
  - When thoughts discuss directory creation or writing files (`mkdir -p`, `touch`, `echo`), Galam synthesizes the autonomous bash command immediately rather than waiting or asking the user.
- [ ] **Stream Auto-Continuation for Free Endpoints**:
  - Free API endpoints enforce strict per-turn token limits (1024-2048 tokens). Large parameter reasoning models frequently exhaust token budgets inside internal monologues (`<think>`).
  - Galam detects truncated streams (e.g. `finish_reason == "length"`, unclosed `<think>` or `<tool_call>` tags, mid-sentence trailing connectors, or heavy reasoning with empty body).
  - Rather than stalling or aborting, Galam triggers an autonomous continuation pass (up to 3 passes). Each pass issues a fresh request to replenish the token budget, streams new tokens into the UI dock, and stitches the full response and tool calls together.
- [ ] **Post-Tool Summary Turn Protection & Anti-Looping**:
  - When a tool execution succeeds with exit code 0, the next turn is strictly designated as a **Summary Turn**.
  - During summary turns, reasoning command extraction is disabled. Galam will NOT extract speculative preparatory commands (`mkdir -p`) from internal reasoning traces.
  - If the model's summary output is truncated or reiterates internal monologues, Galam synthesizes the verified action completion card directly and cleanly concludes the loop.
- [ ] **Session Abort Recovery & Unfinished Task Continuation**:
  - If an AI session unexpectedly terminates or stalls while the user's primary actionable request remains unfinished (e.g., directory made but text file not yet created), Galam intercepts the premature exit.
  - Galam displays `[Resuming unfinished action...]` and prompts immediate autonomous tool execution to finish what was left to do.
- [ ] **Zero-Fluff, Action-First Policy**:
  - Eliminate all flowery conversational filler, repetitive self-introductions, apologies, and textbook commentary on actionable requests.
  - Output results directly: execute the bash/file command, show the outcome, and report verified completion.
- [ ] **Zero-Token Telemetry Policy**:
  - Execution metrics (start time, `duration_ms`, exit codes) and telemetry logs are written exclusively to `logs/` and NEVER injected into LLM conversation history.
  - Only raw tool stdout/stderr is returned in the observation turn.

---

## 3. Autonomous Action Execution Checklist

For every terminal action executed by Galam.ai:

| Step | Requirement | Validation Rule |
| :--- | :--- | :--- |
| **1. Parse** | Detect tool name & args | Normalize aliases (`bash` -> `execute_bash`) |
| **2. Sanitize** | Validate arguments | Escape paths with spaces; verify write targets |
| **3. Safety Guard** | Intercept self-destruction | Block `pkill python` / kill parent shell |
| **4. Execute** | Run command or file op | Non-blocking background mode for servers; synchronous for short tools |
| **5. Log** | Write telemetry | Append to `logs/galam/tool_executions_<date>.jsonl` |
| **6. Feed Back** | Return observation | Send stdout/stderr to LLM for synthesis |
| **7. Report** | Display result | Render styled box to terminal dock |

---

## 4. Maintenance & Verification

- Before onboarding a new model family, execute the test suite in `test/test_parser.py` and review entries in `logs/models/<model_name>.jsonl`.
- Verify that terminal renders maintain the stagnant `> █` dock on Termux/Android terminal dimensions.
