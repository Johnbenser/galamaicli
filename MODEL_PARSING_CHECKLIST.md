# Galam.ai Multi-Model Onboarding & Terminal Interaction Checklist

This document is the definitive operational standard for parsing, connecting, and verifying AI model families (DeepSeek, Qwen, Mistral, Llama, Hermes, GLM, Nemotron, Gemini, Claude) so they interact with Termux terminal tools seamlessly and reliably.

---

## 1. The 10-Step Model Handshake & Parsing Pipeline

When any model emits a response, Galam.ai processes it through this ordered pipeline:

```
┌────────────────────────────────────────────────────────┐
│ 1. Raw Stream / Payload Ingestion                      │
│    (SSE Chunks, urllib fallback, OpenAI/Anthropic/Gemini)│
└──────────────────────────┬─────────────────────────────┘
                           │
┌──────────────────────────▼─────────────────────────────┐
│ 2. Dual-Track Extraction (Content vs. Reasoning)       │
│    - Extracts native `delta.reasoning_content`         │
│    - Strips `<think>...</think>` and `<thought>` blocks│
│    - Isolates untagged R1 internal monologues          │
└──────────────────────────┬─────────────────────────────┘
                           │
┌──────────────────────────▼─────────────────────────────┐
│ 3. Inbound Tool Call Normalizer                        │
│    - Format 0: Mistral `[TOOL_CALLS] [...]`            │
│    - Format 1: Hermes `<|tool_call_start|>`            │
│    - Format 2: DeepSeek `< | DSML | invoke>`           │
│    - Format 3: DeepSeek Unicode `<｜tool call begin｜>` │
│    - Format 4: XML `<tool_call>...</tool_call>`        │
│    - Format 5: Functional `execute_bash(command="...")`│
│    - Format 6: Markdown fenced JSON ```json {...} ```  │
│    - Format 7: Embedded/raw JSON `{"name": "...", ...}`│
│    - Format 8: Fenced bash ```bash ... ```             │
│    - Format 9: Inline `Command: `...``                 │
└──────────────────────────┬─────────────────────────────┘
                           │
┌──────────────────────────▼─────────────────────────────┐
│ 4. Tool Argument Normalization & Aliasing              │
│    - Map aliases: `bash`, `sh`, `terminal` -> `execute_bash`│
│    - Map aliases: `kill_server`, `task` -> `manage_tasks`│
│    - Sanitize file paths & convert line numbers to int │
└──────────────────────────┬─────────────────────────────┘
                           │
┌──────────────────────────▼─────────────────────────────┐
│ 5. Reasoning Conclusion Fallback                       │
│    - Inspect last 1200 chars of internal monologue     │
│    - Extract committed backticked or quoted commands   │
└──────────────────────────┬─────────────────────────────┘
                           │
┌──────────────────────────▼─────────────────────────────┐
│ 6. Action Intent Trap                                  │
│    - Catch conversational promises without tool blocks  │
│    - Auto-re-prompt model to emit tool call immediately│
└──────────────────────────┬─────────────────────────────┘
                           │
┌──────────────────────────▼─────────────────────────────┐
│ 7. Pre-Execution Safety Guards                         │
│    - Intercept `pkill python` to prevent self-kill     │
│    - Check port conflicts (free stale external PIDs)   │
└──────────────────────────┬─────────────────────────────┘
                           │
┌──────────────────────────▼─────────────────────────────┐
│ 8. Autonomous Tool Execution & Telemetry Log           │
│    - Record start time, duration_ms, and returncode    │
│    - Append to `logs/galam/tool_executions_<date>.jsonl`│
└──────────────────────────┬─────────────────────────────┘
                           │
┌──────────────────────────▼─────────────────────────────┐
│ 9. Observation Feedback Handshake                      │
│    - Feed stdout/stderr back as user observation       │
│    - Preserve continuity across multi-turn sessions    │
└──────────────────────────┬─────────────────────────────┘
                           │
┌──────────────────────────▼─────────────────────────────┐
│ 10. Final Synthesis & Transparency Report              │
│     - Model acknowledges root dilemma, action, result  │
│     - Clean markdown rendered to stagnant terminal dock│
└────────────────────────────────────────────────────────┘
```

---

## 2. Model Family Interaction Checklist

Use this checklist when testing or onboarding any new model endpoint:

### Group A: DeepSeek Family (V3, R1, R1-Distill-Qwen, R1-Distill-Llama)
- [ ] **Reasoning Extraction**: Enforces native CoT or extracts untagged monologue before greeting.
- [ ] **DSML / Unicode Delimiters**: Normalizes `<｜tool call begin｜>` and `< | DSML | invoke>`.
- [ ] **Embedded Raw JSON**: Catches unclosed or unfenced JSON `{"name": "execute_bash", ...}` embedded in text.
- [ ] **Quoted Command Fallback**: Extracts `'ls -d ...'` from final committed reasoning.

### Group B: Mistral & Codestral Family
- [ ] **`[TOOL_CALLS]` Syntax**: Parses native bracketed tool arrays without syntax errors.
- [ ] **Shell Syntax Normalization**: Converts `execute_bash(command="...")` functional syntax.
- [ ] **File Edits**: Verifies `replace_file_content` arguments match expected schema.

### Group C: Qwen & Hermes Family (Qwen 2.5 Coder, Hermes 3)
- [ ] **Special Token Stripping**: Cleans `<|im_start|>`, `<|im_end|>`, and `<|tool_call_start|>`.
- [ ] **Markdown JSON & Bash Blocks**: Extracts commands from fenced markdown code blocks.
- [ ] **Path Tolerance**: Resolves `/sdcard/` paths without escaping issues.

### Group D: Gemini, Claude & Proprietary Models (Direct / Kilo / OpenRouter)
- [ ] **Native Function Calls**: Handles standard `functionCall` / `tool_calls` stream deltas.
- [ ] **XML Subtag Compatibility**: Handles `<tool_name>...</tool_name><parameters>...</parameters>`.
- [ ] **Clean Stream Output**: Filters out system tokens before displaying to user.

---

## 3. Terminal Safety & Stability Rules
1. **Never Self-Terminate**: Commands like `pkill python` or `kill <my_pid>` must be safely routed to stop background server processes only, never Galam.ai itself.
2. **Pre-Flight Port Verification**: Check `socket.bind()` before launching servers; kill stale listeners automatically.
3. **Stagnant UI**: The dock input field `> █` must remain fixed and interactive regardless of background execution state.
4. **Zero Token Waste**: Logs in `logs/models/` and `logs/galam/` must **never** be injected into conversational prompts.
