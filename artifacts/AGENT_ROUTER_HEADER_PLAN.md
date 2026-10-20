# Agent Router header diagnosis and integration plan

## Live test — 21 October 2026

Endpoint: `https://agentrouter.org/v1/chat/completions`.
Model: `deepseek-v4-flash`. Same existing credential and same body for every test: one user message asking for OK, non-streaming, max_tokens 16. No other model providers were contacted. No project code, settings or credential files changed.

| Header variant | Status | Error |
| --- | --- | --- |
| Current Galam headers / requests client defaults | 401 | unauthorized_client_error |
| Explicit GalamAI User-Agent and JSON Accept | 401 | unauthorized_client_error |
| Same plus originator=galamaicli, X-Title=Galam.AI and actual Galam repository referer | 401 | unauthorized_client_error |

Each response arrived in about 0.23–0.24 seconds. None contained a chat completion or usage data. Billing cannot be inferred from missing usage. Authentication values are absent from the report. Full sanitized results: `AGENT_ROUTER_HEADER_DIAGNOSTIC.json`.

All three bodies reported `unauthorized client detected` and directed the caller to https://discord.gg/HgekCyHJqB. This is evidence of client rejection, not evidence that the API key is incorrect or correct. Model capabilities remain unverified.

## Research

Firsthand reproduction and maintainer follow-up: https://github.com/lidge-jun/opencodex/issues/2717 . The integration report documents client-identity-dependent admission, and separately reports content-policy failures after admission. Its maintainer confirms a fix in that client. These are firsthand integration observations, not Agent Router's official custom-client specification.

Official documentation attempts at https://agentrouter.org/docs/opencode.html and https://agentrouter.org/docs/codex.html returned a WAF challenge to the research browser. Searches did not uncover an indexed official header contract. The research does not establish that an arbitrary Galam identity is supported. Client-name examples copied from unrelated apps are not an approved Galam integration contract.

## Proposed implementation, specific to Agent Router

1. Obtain Agent Router's approved custom-client identity/header requirements or enable access for Galam. Suggested support message: "I am integrating my own coding assistant, Galam.AI, using my existing API token. POST /v1/chat/completions returns 401 unauthorized_client_error with User-Agent GalamAI and originator galamaicli. Do you allow custom clients? Please authorize this identity or provide the supported custom-client header contract." Do not send the token in a support message.
2. Add an Agent Router-specific header builder using the approved identity and ordinary JSON/SSE Accept headers. Select it only for the named endpoint and verified Agent Router host. Apply it through the existing payload builder so normal, streaming, continuation and fallback requests use consistent settings. Keep Authorization from AGENT_ROUTER_API_KEY; never hardcode it or mutate the shared requests Session headers.
3. Diagnose provider errors accurately: unauthorized_client_error means client admission; identify invalid-token failures separately. Do not clear keys or repeatedly retry client-admission failures. Preserve actual provider status/message. Handle 429 using Retry-After and bounded existing retries, and distinguish unavailable model/service errors from authentication errors. Do not rewrite user prompts to evade content rejections.
4. After client admission succeeds, verify one small JSON chat, SSE chat and a native-tool round trip. Then test all five configured models with isolated temporary HTML create/edit/Bash tasks using Galam's real pipeline. Confirm supported routes and payloads per model family before adding alternative Messages or Responses transports. Keep credentials and user project files out of test fixtures.
5. Mock regression tests should verify endpoint/host isolation, header parity across retries and continuations, secret redaction, and actionable client-rejection errors. Keep the current UI, settings workflow and other endpoint behavior intact.

Current state: research and three isolated live header tests complete. A successful production configuration is not established; no speculative production header changes were installed.
