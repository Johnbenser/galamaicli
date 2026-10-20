# Token workflow upgrade — 21 October 2026

## Applied

- Compact request instructions replace repeated runtime prose. Model-family custom instructions and user workspace instructions remain intact. Ordinary questions do not automatically scan/list the workspace; pure greetings still use Galam's existing local greeting handler.
- Context contains the current conversation only. Completed earlier turns are selected by recency/relevant file references, rather than filling a large context window. Evicted user constraints remain once, without duplicating retained requests. Repeated “continue” keeps its originating task.
- Successful file writes/edits send a compact operation/result instead of echoing the entire displayed diff. Full history and terminal diffs remain local. Exact inspected source is preserved for edits; an oversized inspection produces an explicit context-limit failure rather than silently removing source lines.
- Historical native tool arguments are represented correctly when switching to text-call endpoints; compacting a result never removes the only copy of its generated code/patch.
- Explicit commands can initially use the smaller Bash/output-retrieval schema set. Failed actions or protocol repair restore the full tool set. Ambiguous builds/edits retain all tools. Existing named filesystem contracts keep their own selection rules.
- Precisely specified commands with factual output/status reporting can finish from their real result without a second model acknowledgment. Pending checks, background tasks, failures, extra requested work and ambiguous commands keep the normal workflow. Existing exact-edit receipts remain supported.
- Per-request accounting records provider input/output/total, reasoning and cached-input counts when supplied, with estimates and incomplete coverage clearly identified. Retries are separate requests; cached/reasoning components are not added to the same totals twice. No extra inference or model-based summarizer is used.

Bash execution, approval rules, working-directory behavior, source-change guards, file validation, numbered colored diffs, Ctrl+O, output limits, and settings screens retain their existing implementations. No global output cap was reduced.

## Measured offline input sizes

The same seven representative request checkpoints were measured before and after. These are Galam's conservative internal estimates, not provider billing counts. The text-path fixture also verifies that previously native history remains available after switching protocols.

| Fixture | Before input | After input, same 7 checkpoints | Reduction |
|---|---:|---:|---:|
| Native tools (Groq shape) | 20,811 | 12,265 | 41.1% |
| Text calls (Gemini shape) | 13,933 | 9,208 | 33.9% |

If the final Bash acknowledgment request is unnecessary, the six remaining checkpoints total 10,614 and 7,521 input tokens respectively: 49.0% and 46.0% below the seven-checkpoint baselines.

The ordinary-question fixture fell from 1,097 to 185 estimated input tokens for the Groq shape, and 1,077 to 162 for the Gemini shape. This is not a claim that every chat/task will achieve the same reduction.

Reproduce without contacting an endpoint:

```bash
python tools/measure_context.py
```

Detailed measurements: `artifacts/TOKEN_OPTIMIZATION_MEASUREMENTS.json`.

## Input/output/total projection

For reference, the previous live Gemini 3.5 HTML-create/edit/Bash chain reported 16,043 input + 430 output = 16,473 tokens across seven requests.

Applying the offline text-context ratios to that baseline gives these planning estimates:

| Comparable small workflow | Projected input | Projected output | Projected total |
|---|---:|---:|---:|
| Same 7 requests with compact context | about 10,600 | 350–550 | about 10,950–11,150 |
| 6 requests when the Bash receipt applies | about 8,660 | 300–500 | about 8,960–9,160 |

These are extrapolations, not measured post-upgrade Gemini bills. File size, tokenizer, reasoning, model choices and retries can change both counts and request count. The earlier five-request forecast is not guaranteed; verification was preserved rather than removed to force that target.

## Live verification

Both selected remote models passed real HTML creation, exact focused editing and `execute_bash` byte-count checks in confined workspaces:

| Endpoint/model | Requests | Result |
|---|---:|---|
| Direct / `codestral-latest` | 6 | All three passed |
| Groq / `openai/gpt-oss-20b` | 7 | All three passed |

Their Bash stages each used one model request, followed by a local result-based receipt. Models/endpoint fallbacks and direct-command shortcuts were disabled in the harness, so another model did not get credit. No Local Endpoint was called. Evidence: `artifacts/remote-action-audit-token-optimized-20261021/report.json`.

162 regression tests passed, including real harmless Bash execution, failure/review behavior, native call/result pairing, text fallback, exact source retention, conversation isolation, pending-check receipts and provider usage accounting. Changed Python modules compile.

## Agent Router

Added the `Agent Router` endpoint and these exact user-supplied IDs:

- `deepseek-v4-flash`
- `gpt-6-astra`
- `gpt-6.1-sol`
- `claude-opus-4-8`
- `claude-opus-5`

Chat/action requests use `https://agentrouter.org/v1/chat/completions`, authenticated with the value of `AGENT_ROUTER_API_KEY`. The supplied models/Responses/embeddings/Messages URLs are stored as route metadata; the chat engine does not send chat requests to those different APIs. Provider context limits were not advertised, so existing conservative fallback budgeting applies.

All five live attempts returned HTTP 401 with **“unauthorized client detected”**. Their coding/action compatibility remains unverified until the provider authorizes the client/account configuration. No client impersonation or authentication bypass was attempted.

Galam now reads literal credential assignments from the known home/Termux `.secrets` files without executing them. Existing exported values take priority; unrelated shell configuration is ignored. Credential values are not placed in source or this report. Saved settings and credential files were not modified.

Evidence: `artifacts/remote-action-audit-agent-router-20261020/report.json` (the run began before the date rollover).

## Usage records and rollback

Successful model interaction logs now include `metadata.usage`, with per-request source labels, provider counts, missing-usage input estimates and a coverage flag. `metadata.estimated_generated_tokens` includes supplied answer/reasoning/tool-call text as an estimate. The older `tokens_approx` field is a legacy visible-text estimate, not an invoice. Full provider usage is not always returned by a streamed endpoint; no unsupported `stream_options` fields are forced on providers.

Task counters are available in `AGENT_STATE['usage_task']`. They contain counts and model names, not request bodies or credentials, and are not sent back as conversation context.

Backend switch `token_optimization: false` in existing JSON settings disables the new prompt/history/tool/receipt optimizations. Individual features can be disabled with a dictionary containing `prompts`, `history`, `results`, `tools` or `receipts` set to false. No UI migration is needed. Original sources and before/after measurements are backed up under the path recorded in the verification manifest.

Restart Galam to load the updated modules. No GitHub push was performed for this task.
