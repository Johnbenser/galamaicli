# NVIDIA connection check — 21 October 2026

## Screenshot diagnosis

The screenshot showed a custom Direct Endpoint 404, followed by OpenRouter rate limits. A model named `nvidia/...` under OpenRouter is still served through OpenRouter; that is distinct from Galam's NVIDIA endpoint.

The saved custom route was `https://ollama.com/api/chat/chat/completions`. The native Ollama route had been treated as an OpenAI-compatible base. The normalizer now resolves native/double-appended Ollama routes to `https://ollama.com/v1/chat/completions`. Existing profiles receive the corrected effective route when loaded; the model ID and secret remain untouched. Model availability on that service was not inferred from a corrected URL.

## Authorized live NVIDIA results

The stored credential successfully authenticated to `https://integrate.api.nvidia.com/v1/models`: **HTTP 200**, 81 model IDs in the returned catalog. Tiny synthetic chat requests then tested the configured model IDs directly against NVIDIA. These results are point-in-time observations, not uptime or latency guarantees.

| Model | Latest result | Observed seconds |
|---|---|---:|
| `deepseek-ai/deepseek-v4.1-flash` | HTTP 200, final `OK` reply | 1.07 |
| `poolside/laguna-xs-2.1` | HTTP 200, final `OK.` reply | 0.79 |
| `nvidia/nemotron-3-ultra-550b-a55b` | HTTP 200, final `OK` reply | 1.89 |
| `nvidia/nemotron-3.5-lightning-30b-a3b` | HTTP 200, final `OK` reply | 1.74 |
| `z-ai/glm-5.3-flash` | HTTP 200, final `OK` reply | 7.18 |
| `z-ai/glm-5.3` | HTTP 200, final `OK` reply on follow-up | 23.28 |
| `google/gemma-4-31b-it` | Read timeout at both 30 and 60 seconds | unavailable |
| `moonshotai/kimi-k3` | Not in returned catalog; generation returned HTTP 410 Gone | 0.23 |

Two thinking models initially used a 64-token probe budget without emitting a final answer. They returned final answers with a 256-token probe budget. That is distinguished from failure to connect. GLM also recovered on the follow-up. Gemma remains unverified as usable at the time of this check; its entry was not mislabeled as successful. Kimi K3's retired built-in NVIDIA entry was removed to avoid repeated guaranteed failures. Custom user-defined routes are preserved.

## Full Galam streaming path

- NVIDIA DeepSeek returned exactly `CONNECTION_OK` through Galam's actual router/stream parser in **0.92 seconds**. Provider usage: **199 input + 20 output = 219 tokens**.
- NVIDIA Poolside also returned a response through the streaming path in **3.03 seconds**. It formatted the marker as JSON; this establishes response delivery, not strict marker-format compliance. That request lacked complete provider usage, so its counts remain labeled estimated.
- Tests did not change the user's selected endpoint/model or saved settings and did not send private conversation text. Only synthetic connection prompts were sent. Keys were loaded privately and never written into this report.

The machine-readable report is [NVIDIA_CONNECTION_CHECK.json](NVIDIA_CONNECTION_CHECK.json). Timed-out requests have unknown usage; they are not treated as zero cost.

## Related client fixes and verification

- Numbered endpoint selection and visible Default API format reduce accidental provider/route mistakes.
- Credential lookup bootstraps the app's secret loader before checking environment aliases, including standalone checks.
- Confirmed daily/account limits stop repeated fallback across models on the same exhausted account. Temporary per-model rate limits remain separate.
- Full regression suite: **261 tests passed**, including **33 custom-connection checks** and real keyboard selection in the editor.

Provider format references: [NVIDIA hosted LLM API](https://docs.api.nvidia.com/nim/reference/llm-apis), [Ollama base URLs](https://docs.ollama.com/api/introduction), [Ollama OpenAI compatibility](https://docs.ollama.com/api/openai-compatibility).

Restart Galam to load the updated code. Select NVIDIA through `/settings` → Active Endpoint to use NVIDIA directly; new custom models use the Model Connections selector.
