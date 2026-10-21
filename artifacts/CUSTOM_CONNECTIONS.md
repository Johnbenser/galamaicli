# Settings and custom model connections

Updated 21 October 2026.

## Terminal workflow

Settings uses a full-screen colored workspace with an active-engine panel, framed menu, status and keyboard hints. Execution mode, fallback, streaming, provider/model selection and the local GGUF manager retain their existing actions. Browsing another endpoint does not activate it until a model is selected; activation saves the endpoint/model aliases together.

Model Connections opens inside the same Application, with its own key bindings, then returns to settings. The wizard has four steps:

1. **Model:** sorted numbered endpoint picker and model ID; existing custom connections can be loaded from Saved models.
2. **Request:** complete request URL and payload format.
3. **Credentials:** optional variable reference, masked key, context limit and activation preference.
4. **Review:** destination group, model, exact URL, resolved payload format, variable reference and activation. The key value is never shown. Saving happens only from this step through the UI.

Tab/Shift+Tab changes focus. Ctrl+S advances or saves from Review. Escape goes back, returning to settings from the first step; Cancel discards the form. Narrow layouts stack buttons so they remain reachable. Review text scrolls. Confirmations stay inside the UI.

The selector uses `select_on_focus=True`: arrows change the checked value along with the highlighted row. Its controls remain stable across render passes. This fixes the stale-value path that could save a Direct choice under Groq. Number entry remains available, including numbers above nine.

## Exact URL contract

**Default sends the entered request URL unchanged.** Neither default detection nor an explicit payload format appends, strips or replaces API paths. URLs are validated, with surrounding whitespace trimmed; credentials, query strings, fragments, malformed URLs and known non-chat routes are refused.

Examples:

| Entered request URL | Default payload |
| --- | --- |
| `https://ollama.com/api/chat` | Native Ollama Chat |
| `https://service.example/v1/chat/completions` | Chat Completions |
| `https://service.example/v1/responses` | Responses |
| A custom request path | Saved model format or the endpoint catalog preference |

An empty or untouched suggested URL can follow the selected provider. A typed URL is preserved when changing the catalog group. Credential inputs clear only when an automatically suggested destination changes host. A group name controls where the model appears; the request URL controls where it connects.

Use the provider's full documented route. Responses is normally plural; a typed singular `/response` is preserved, so a provider may reject it. A bare base URL is also preserved and must be completed by the user if the server requires a route. This replaces the earlier automatic route-normalization behavior, including the unwanted Ollama-to-OpenAI conversion.

## Storage and credentials

- Metadata: selected home `~/.galam/endpoints.json`, mode 600; URL, protocol, model, group and variable reference only.
- Keys: selected home `~/.secrets`, mode 600; unrelated exports/comments are preserved and values are shell-quoted as literals.
- Updating a credential deduplicates only that assignment. Blank key input keeps the referenced credential.
- Home selection follows the current home with `.secrets`, then an existing Termux home; `GALAM_HOME` overrides it.
- Inputs have no persistent history; the password buffer and undo state clear on save/cancel.
- A lock, atomic writes and rollback protect profile/secret/RC updates. Symlinked destinations are refused.
- The running Galam process receives the credential immediately; referenced saved values reload on startup without executing the secret file.
- A guarded source line is added to Bash/Zsh RC files when a stored credential is available. An already-open parent terminal needs `source ~/.secrets` if it needs that variable itself.
- Matching provider hosts can reuse the group's credential variable. Other hosts receive a generated variable unless explicitly assigned.
- Main/side tasks must finish or be cancelled before connections are edited. Built-in model IDs cannot be overwritten through this editor.

## Shared action pipeline

Adapters translate only at the transport boundary, after Galam adds its common messages and tool schemas. Chat Completions, Responses and native Ollama replies normalize into the same action contract. Bash permissions, file validation, edit previews, cancellation, context selection and conversation isolation retain their existing execution paths. Translation and URL detection add no inference requests.

Responses uses input items, flat functions, original call IDs, function outputs, max-output tokens and stateless reasoning continuity. JSON/SSE parsing avoids duplicated final snapshots. Reasoning continuity is scoped to the originating endpoint/model/URL.

Native Ollama uses `/api/chat` messages, options such as `num_predict`, object-valued function arguments and named tool results. JSON and NDJSON streams return content, provider-supplied thinking text, structured actions and prompt/evaluation token counts. Separate streamed calls receive distinct action indexes. Its reported input/output usage is counted once per request.

Custom models survive local discovery refresh; per-model URLs bypass built-in hardcoded routes. Context limits remain capped by the custom model's declared limit.

References: [Ollama chat API](https://docs.ollama.com/api/chat), [Ollama tool calling](https://docs.ollama.com/capabilities/tool-calling), [Responses function calling](https://developers.openai.com/api/docs/guides/function-calling).

## Verification

The full regression suite covers existing workflows as well as private credential writes, rollback, provider references, exact URLs, model moves, response adapters and keyboard navigation. Local HTTP fixtures exercise Chat Completions, Responses and native Ollama through the router, execute harmless real Bash commands, and return results through each protocol. Ollama is tested with JSON and NDJSON replies and exact reported usage.

Keyboard tests exercise 44- and 28-column terminals, embedded settings navigation, arrows → Tab → confirm endpoint selection, review-before-save, masked secrets and cancellation. These fixtures validate client behavior; they do not establish live compatibility or model availability for every provider.

Final regression result: **265 tests passed**, including the new settings/native-Ollama coverage. Changed modules compiled successfully.
