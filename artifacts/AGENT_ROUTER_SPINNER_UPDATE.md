# Agent Router integration and continuous animation — 21 October 2026

## Installed changes

Agent Router alone now receives truthful Galam client identity headers through the shared payload builder. Non-streaming requests accept JSON; streaming requests accept SSE. Identity headers are not added to other endpoints or to lookalike hosts. Standard Authorization continues to use the existing credential resolver.

If Agent Router supplies an approved custom-client identity, backend setting `agent_router_headers` can override User-Agent, originator, X-Title, HTTP-Referer, version and X-Client-Version. Names merge case-insensitively. Authorization/Host/transport overrides and control characters are rejected. No alternate application's identity is shipped. No settings-menu changes were made.

Client admission errors now identify the need for approved custom-client access, explicitly leave API key validity unverified, and stop model fallback. The same diagnosis applies to side queries and errors within JSON/SSE responses. Agent Router's urllib fallback retains certificate verification and does not resubmit a client rejection with verification disabled. Other endpoints retain their existing behavior.

The thinking animation now refreshes every 80 ms while a task is active, including connection waits, stalls between streamed tokens and tool phases. Network callbacks and token arrivals are no longer required to advance it. Idle/cancelled tasks stop animating; a closing display cannot kill the animation thread. Existing panel controls and layout remain.

## Verification

- `python -m unittest discover -s tests -q`: 179 tests passed, including 10 new endpoint/host-isolation, header-validation, rejection, transport and animation tests.
- Compilation passed.
- One live request through Galam's real Agent Router pipeline: client rejected, exactly one request, no fallback, zero settings saves. Sanitized result: `AGENT_ROUTER_INTEGRATION_CHECK.json`.

Agent Router is not yet usable with the tested Galam identity. Provider approval or a provider-issued custom-client header contract is still required. Model chat, streaming and action capabilities remain unverified until admission succeeds. Existing keys are neither cleared nor changed.

Restart Galam to load these changes.

Backup: `/root/galam-upgrade-backups/router-spinner-20261021-014025/`.
