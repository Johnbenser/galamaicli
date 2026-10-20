# Galam.ai Telemetry & Behavioral Diagnostic Logs Reference Guide

This document establishes the telemetry architecture and standard operating procedures for analyzing logs in **Galam.ai CLI**.

---

## 1. Directory Structure

Logs are partitioned strictly into two separate streams located under `logs/`:

```
/sdcard/cli/Galam.ai CLI/logs/
├── models/
│   ├── deepseek_v4.1-flash.jsonl
│   ├── deepseek_deepseek-r1.jsonl
│   ├── qwen_qwen3.8-27b.jsonl
│   └── ... (one isolated file per AI model)
└── galam/
    ├── galam_system_2026-10-08.jsonl
    ├── galam_system_2026-10-09.jsonl
    └── ... (daily system diagnostic events)
```

> [!IMPORTANT]
> **Zero Token Waste Policy**:
> Neither model logs nor Galam system logs are ever fed back into the LLM system prompt or conversational context during active sessions. They are stored strictly on disk in JSONL format for developer inspection, bug-fixing, and offline behavioral optimization.

---

## 2. Per-Model Behavioral Logs (`logs/models/<model_name>.jsonl`)

Each line is a JSON record tracking individual model performance:

| Field | Description |
| :--- | :--- |
| `timestamp` | Human-readable date and time (`YYYY-MM-DD HH:MM:SS`) |
| `model` | Exact model identifier (e.g. `deepseek-v4.1-flash`, `qwen/qwen3.8-27b`) |
| `endpoint` | Active endpoint (`NVIDIA`, `Groq`, `Cloudflare`, `OpenRouter`, etc.) |
| `user_prompt` | User request that triggered the generation |
| `response_content`| Raw response emitted by the model |
| `reasoning_summary`| CoT reasoning summary (first 400 characters) |
| `tool_calls` | List of normalized tool calls emitted by the model |
| `error` | Connection errors, timeouts, or format parse exceptions |
| `redundancies` | Detected anti-patterns (e.g., duplicate `<tool_call>` tags, passive disclaimers) |
| `improvements` | Recommended behavioral adjustments for model prompt/config |

### When to inspect model logs:
- When a model outputs verbose conversational text instead of executing a tool.
- When an AI model hallucinates non-existent tools or unclosed syntax.
- When evaluating which model under an endpoint has the highest success rate.

---

## 3. Galam.ai System Telemetry Logs (`logs/galam/galam_system_<date>.jsonl`)

Captures core engine runtime diagnostics, process control, and session health:

| Field | Description |
| :--- | :--- |
| `level` | `INFO`, `WARNING`, `ERROR`, `CRITICAL` |
| `event_type` | Diagnostic category (e.g. `USER_ABORT`, `ENDPOINTS_EXHAUSTED`, `MODEL_CONNECTION_ERROR`, `TOOL_EXECUTION_ERROR`) |
| `summary` | Concise one-line explanation of the event |
| `details` | Context dictionary (PIDs, ports, step count, traceback info) |

### Monitored System Events:
1. **`MODEL_CONNECTION_ERROR`**: Endpoint HTTP errors, socket timeouts, or rate limits.
2. **`ENDPOINTS_EXHAUSTED`**: When all fallback models under an endpoint fail.
3. **`USER_ABORT`**: When generation is interrupted via `Ctrl+C` or prompt cancel.
4. **`WORKER_EXECUTION_ERROR`**: Unhandled queue exceptions in `agent_worker_loop`.
5. **`TOOL_EXECUTION_ERROR`**: Command failures or file operation exceptions.
6. **`UNKNOWN_TOOL_INVOCATION`**: Model calls an unregistered tool alias.

---

## 4. Developer Inspection Checklist

When diagnosing a reported dilemma or crash:
1. **Check Galam System Logs First**:
   ```bash
   tail -n 30 "/sdcard/cli/Galam.ai CLI/logs/galam/galam_system_$(date +%Y-%m-%d).jsonl"
   ```
   Look for `ENDPOINTS_EXHAUSTED`, `QUERY_EXCEPTION`, or unexpected exit reasons.
2. **Check the Specific Model's Log**:
   ```bash
   tail -n 20 "/sdcard/cli/Galam.ai CLI/logs/models/<sanitized_model_name>.jsonl"
   ```
   Inspect `redundancies`, unclosed tool blocks, or connection timeout errors.
3. **Verify Zero Token Intrusion**:
   Ensure `build_enforced_messages()` in `routers/manager.py` does not inject these files into LLM queries.
