# Galam remote endpoint action audit — 20 October 2026

Attempted **42 registered remote models** across six endpoint groups. **13 models passed all three checks in at least one complete live run.** Local Endpoint was excluded.

## What was tested

1. Ask the selected model to create a small, standalone `index.html` with a specified title, heading, paragraph and button. Check actual disk content.
2. Ask it to read the file and replace `Version Alpha` with `Version Beta`. Compare the entire before/after content to verify unrelated code was preserved.
3. Ask it to invoke Galam `execute_bash` with `wc -c index.html`. Check the real exit status, byte count, and that the file remained unchanged.

The real Galam router, agent loop, argument validation, file tools and shell dispatcher were used. Each model ran in a separate test workspace/process. Only the selected endpoint/model was available; model/endpoint fallbacks and direct-command shortcuts were disabled. The optional LangGraph planner was disabled to test the standard agent loop. Tests used literal file tools and only the requested byte-count shell command; unrelated actions were denied. No browser rendering, JavaScript behavior, arbitrary shell compatibility or performance benchmark is claimed.

A failed creation normally prevents a meaningful follow-on edit. Later rechecks can seed a clearly labelled audit fixture to test editing independently; such seeding never counts as model creation. Authentication/HTTP rejection and hard deadlines stop further attempts for that run. Stage deadlines were 75 seconds initially and up to 110 seconds on recheck.

**Reading the table:** ✓ means that stage passed in the best single recorded run; ✗ means it failed; — means it was blocked/not attempted. “All three observed” requires all three stages to pass in one run. The last column reports the latest completed observation, which can be a later quota failure even after an earlier successful run. Passing stages from different runs are never combined into an all-three pass.

## Results for every model

| Endpoint | Model | Create | Edit | Bash | All three observed | Latest observation |
|---|---|:---:|:---:|:---:|:---:|---|
| Cloudflare | `@cf/deepseek-ai/deepseek-r1-distill-qwen-32b` | ✓ | ✗ | ✓ | — | Daily Cloudflare neuron allowance exhausted |
| Cloudflare | `@cf/qwen/qwen2.5-coder-32b-instruct` | ✓ | ✓ | ✓ | ✓ | Daily Cloudflare neuron allowance exhausted |
| Cloudflare | `@cf/qwen/qwen3.8-27b` | ✗ | — | — | — | Daily Cloudflare neuron allowance exhausted |
| Cloudflare | `@cf/zai-org/glm-4.7-flash` | ✗ | — | — | — | Daily Cloudflare neuron allowance exhausted |
| Cloudflare | `@cf/meta/llama-4-scout-17b-16e-instruct` | ✗ | — | — | — | Daily Cloudflare neuron allowance exhausted |
| Cloudflare | `@cf/mistral/mistral-7b-instruct-v0.2-lora` | ✗ | — | — | — | Daily Cloudflare neuron allowance exhausted |
| NVIDIA | `deepseek-ai/deepseek-v4.1-flash` | ✗ | — | — | — | Stage deadline exceeded; incomplete |
| NVIDIA | `z-ai/glm-5.3` | ✗ | — | — | — | Stage deadline exceeded; incomplete |
| NVIDIA | `z-ai/glm-5.3-flash` | ✓ | ✓ | ✓ | ✓ | All three checks passed |
| NVIDIA | `moonshotai/kimi-k3` | ✗ | — | — | — | Provider reports model retired (HTTP 410) |
| NVIDIA | `nvidia/nemotron-3.5-lightning-30b-a3b` | ✓ | ✓ | ✓ | ✓ | All three checks passed |
| NVIDIA | `nvidia/nemotron-3-ultra-550b-a55b` | ✓ | ✓ | ✓ | ✓ | All three checks passed |
| NVIDIA | `poolside/laguna-xs-2.1` | ✗ | ✓ | ✗ | — | Quota/rate/capacity limit; not proof of model inability |
| NVIDIA | `google/gemma-4-31b-it` | ✗ | — | — | — | Stage deadline exceeded; incomplete |
| OpenRouter | `nvidia/nemotron-3.5-lightning:free` | ✓ | ✗ | ✓ | — | Daily OpenRouter free-request quota exhausted |
| OpenRouter | `nvidia/nemotron-3-super-120b-a12b:free` | ✗ | — | — | — | Daily OpenRouter free-request quota exhausted |
| OpenRouter | `nvidia/nemotron-3-ultra-550b-a55b:free` | ✓ | ✓ | ✓ | ✓ | Daily OpenRouter free-request quota exhausted |
| OpenRouter | `cohere/north-mini-code:free` | ✓ | ✗ | ✓ | — | Daily OpenRouter free-request quota exhausted |
| OpenRouter | `liquid/lfm-2.5-2.6b:free` | ✓ | ✗ | ✗ | — | Daily OpenRouter free-request quota exhausted |
| OpenRouter | `dots-studio/dots-3-note-preview:free` | ✗ | — | — | — | Daily OpenRouter free-request quota exhausted |
| OpenRouter | `poolside/laguna-xs-2.1:free` | ✗ | — | — | — | Quota/rate/capacity limit; not proof of model inability |
| Groq | `openai/gpt-oss-120b` | ✓ | ✓ | ✓ | ✓ | All three checks passed |
| Groq | `openai/gpt-oss-20b` | ✓ | ✓ | ✓ | ✓ | All three checks passed |
| Groq | `qwen/qwen3.8-27b` | ✓ | ✓ | ✓ | ✓ | All three checks passed |
| ModelScope | `Qwen/Qwen3.8-27B` | ✗ | — | — | — | Account setup: provider requires Alibaba Cloud account binding |
| ModelScope | `deepseek-ai/DeepSeek-V4.1-Flash` | ✗ | — | — | — | Account setup: provider requires Alibaba Cloud account binding |
| Direct | `gemini-3.5-flash` | ✓ | ✓ | ✓ | ✓ | All three checks passed |
| Direct | `gemini-3.5-flash-lite` | ✗ | — | — | — | Provider rejected request parameters (HTTP 400) |
| Direct | `gemini-2.5-flash` | ✓ | ✓ | ✓ | ✓ | All three checks passed |
| Direct | `gemini-3.8-flash` | ✗ | — | — | — | Provider temporarily unavailable (HTTP 503) |
| Direct | `command-a-plus-07-2025` | ✗ | — | — | — | Model/provider route unavailable or inaccessible (HTTP 404) |
| Direct | `north-mini-code-1-0` | ✗ | — | — | — | Provider generated no valid response (HTTP 422) |
| Direct | `command-a-plus-05-2026` | ✗ | — | — | — | Provider generated no valid response (HTTP 422) |
| Direct | `codestral-latest` | ✓ | ✓ | ✓ | ✓ | All three checks passed |
| Direct | `mistral-14b-latest` | ✗ | — | — | — | Configured model ID rejected (HTTP 400) |
| Direct | `mistral-8b-latest` | ✗ | — | — | — | Configured model ID rejected (HTTP 400) |
| Direct | `mistral-3b-latest` | ✗ | — | — | — | Configured model ID rejected (HTTP 400) |
| Direct | `mistral-large-latest` | ✓ | ✓ | ✓ | ✓ | All three checks passed |
| Direct | `anthropic/claude-sonnet-4.5` | ✗ | — | — | — | Provider requires credits/payment (HTTP 402) |
| Direct | `kilo-auto/free` | ✓ | ✓ | ✓ | ✓ | All three checks passed |
| Direct | `minimax-m2.7` | ✓ | ✓ | ✗ | — | Quota/rate/capacity limit; not proof of model inability |
| Direct | `mistral-Nemo-Instruct-2407` | ✓ | ✗ | ✓ | — | Workflow incomplete; attempted an action outside the test contract (create, modify) |

## Changes installed in Galam

- Added one shared action-response contract after model-family instructions: executable calls, real observations, read-before-edit, precise arguments, concise evidence-based completion, and no simulated results. This supplies runtime instructions; it does not train or permanently alter hosted models.
- Fixed TaskContract output detection. A named file followed by “in the current directory as…” no longer invents a folder named `as`; descriptive “file actions” no longer creates an output named `actions`. These false requirements caused successful creation to continue into redundant or damaging edits.
- Cloudflare conversational models now use its documented OpenAI-compatible chat endpoint, with native tools and the existing scoped text fallback. Explicit legacy `/ai/run` routing remains supported. The former model-prefix condition incorrectly overrode a configured v1 route.
- Preserve numeric/boolean text tokens instead of silently dropping digits such as `0`, `1` and `8` from streamed responses.
- Recognize provider errors embedded in HTTP 200/SSE responses, including capacity errors, rather than treating them as empty model answers or asking the model to repair a nonexistent action.
- If a provider ends a stream with `tool_calls` but supplies no usable call/arguments, request one complete non-streaming response from the same model. No action is inferred from reasoning, and no incomplete call is executed.
- Normalize Cohere v2 message/content responses into the shared text/action parser. Fixture-tested; the tested live Cohere IDs still returned 404/422, so no live Cohere success is claimed.
- Retry the same model once when HTTP 429 includes a short explicit cooldown, capped at 55 seconds; longer or unspecified quotas remain visible failures.
- Added a reusable confined audit runner at `tools/audit_remote_actions.py`.

The existing UI/settings screen design, selected endpoint/model, credential files and saved settings were preserved. Restart Galam to load the updated Python modules. No GitHub push was performed for this task.

## Constraints and follow-up

- Cloudflare reported its daily free neuron allowance exhausted during the audit. OpenRouter reported its daily free-model request allowance exhausted. Later blocked rechecks do not establish that those models cannot use Galam actions. Wait for provider quota reset before retesting; no automatic paid upgrade was made.
- ModelScope requires Alibaba Cloud account binding for both configured models. Changing prompts cannot resolve this account restriction.
- NVIDIA Kimi K3 was reported retired. Several Mistral aliases and one Cohere model ID were rejected. No replacement IDs were invented or silently selected.
- Paid Kilo Claude returned a credits/payment error; Kilo Auto Free passed separately.
- Slow models that exceeded a deadline remain unverified. A partial creation/edit or an HTTP 200 alone is not an all-three pass.
- The all-three result measures actual actions and disk/check outcomes, not perfect narrative accuracy. Cloudflare Qwen 2.5 completed the three actions in an earlier v1 run but omitted digits in its spoken byte-count acknowledgment. Numeric-token preservation was then fixed and regression-tested; the final live recheck was blocked by the daily Cloudflare quota.
- These small live checks establish the observed workflow only; they do not guarantee complex projects or future provider availability.

## Reproduce

```bash
cd /sdcard/cli/galamaicli
python tools/audit_remote_actions.py --secrets "$HOME/.secrets" --workers 2 --deadline 110
# Optional: --only 'Groq|Direct/codestral-latest'
```

The secrets reader accepts literal shell assignments without executing the file. It loads values only into the audit process environment. Generated audit workspaces are ignored by Git. Reports/transcripts are redacted against loaded credential values. Full per-model evidence paths are listed in `REMOTE_ENDPOINT_ACTION_REPORT.json`.

## Verification

127 regression tests passed (`python -m unittest discover -s tests -q`). Credential/settings hashes matched the pre-task backup. Live requests used the credentials from the user-provided Termux `.secrets` file; no values are included here.

## Protocol references

- [Cloudflare OpenAI-compatible API](https://developers.cloudflare.com/workers-ai/configuration/open-ai-compatibility/) documents the chat route used by the updated adapter.
- [Cohere v2 streaming](https://docs.cohere.com/v2/reference/chat-stream) documents its `delta.message.content.text` response shape.
