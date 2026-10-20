# Custom model connections — 21 October 2026

## Settings flow

`/settings` → Model Connections opens a separate full-screen editor after the inline settings application exits and clears its own ten-line region. Closing the editor reopens the existing settings screen and keeps the conversation intact. Existing items and their action meanings are retained; the existing seven-item scroll window handles the added item.

The editor supports a sorted, numbered endpoint selector, exact model ID, base/full URL, Default with its resolved format/route, optional Chat Completions or Responses overrides, credential variable, masked key, context window and optional immediate activation. Previously saved custom models can be loaded and edited. Complete route pastes select the format; `/response` is normalized to `/responses`. Feedback is rendered inside the application; saving prints no confirmation into its live layout. Escape cancels promptly and discards the key field.

Main/side tasks must finish or be cancelled before editing connections. This avoids reconfiguring a live model exchange. Existing endpoint/settings screens remain available as before.

## Storage and shell behavior

- Public source contains the implementation, not the user's connection definitions or keys.
- Definitions: selected home `~/.galam/endpoints.json`, mode 600, metadata and variable references only.
- Keys: selected home `~/.secrets`, mode 600. The writer preserves unrelated exports/comments, quotes values as literal shell strings and deduplicates the assigned variable.
- Home selection follows an existing `.secrets` in the current home, or the existing Termux home when the alternate home lacks it. `GALAM_HOME` explicitly overrides selection.
- The editor has no persistent input history; key rendering is masked and its buffer/undo state clears after save/cancel.
- A file lock serializes profile/credential updates. Atomic file writes and rollback protect against partial save failures. Symlinked secret/store/RC destinations are refused rather than silently replaced.
- Bash/Zsh receive one guarded source line when a stored credential is available. Existing source commands are recognized; comments or an echoed source command do not count.
- The running Galam process receives the saved value immediately. Catalog reload reads the referenced literal value from `.secrets`, so an older export in the launching shell does not override that saved custom credential.
- A child process cannot rewrite an already-open parent shell's environment. New shells read the guarded RC line; use `source ~/.secrets` in an existing parent shell when that shell itself needs the update.
- Galam never executes arbitrary code from `.secrets` while loading keys.

Built-in model IDs cannot be overwritten through the editor. New models may join existing groups, with their connection stored per model. Models using one credential variable share its value. The editor does not add a key to `storage/keys.json`, settings, model history, or Git.

## Request/response integration

The existing action loop remains the canonical contract. New models can use native tool schemas or the existing text fallback. Chat Completions keeps its normal message format. Responses is translated at the transport boundary, after tool options are added, so retries, continuations and side questions use the correct wire shape. Usage estimates count the actual translated request.

Responses uses input items, flat function definitions, `function_call_output` with the original call ID, `max_output_tokens`, and stateless continuity. JSON and SSE output normalize back to Galam responses. Argument deltas and final snapshots do not duplicate calls or arguments. Failed/incomplete responses do not masquerade as completed answers. Encrypted reasoning items are retained only for the originating endpoint/model/URL and are excluded when switching connections; only provider-supplied summaries are displayed.

Custom models survive local-server catalog refresh. Custom routes assigned to a built-in provider bypass that provider's hardcoded request URL, while existing built-in models retain their routes. Custom context limits cap larger global context preferences.

Official format references: [function calling](https://developers.openai.com/api/docs/guides/function-calling) and [Responses streaming events](https://developers.openai.com/api/reference/resources/responses/streaming-events). Compatibility with a third-party service still depends on its implementation of those formats.

## Verification

- Custom tests cover preserved files, private permissions, literal keys, duplicate exports/source lines, blank-key reuse, profile edits, invalid URL/environment names, malformed stores, symlinks, failed-write rollback, stale parent exports, protocol conversion, response errors, final snapshots and connection-scoped continuity.
- Both API families were exercised against a local HTTP fixture. Its returned tool request ran a harmless real Bash command, then the result returned with the same call ID.
- A streamed Responses fixture verifies the main router returns a complete action and provider-reported usage.
- Keyboard-driven editor checks use a 44-column, 24-row output, masked input, Ctrl+S and Escape. Confirmation prints nothing outside the UI; cancellation writes no files.
- The existing local-manager menu test was updated for the additional section while checking original actions, cursor behavior and manager disabling.
- **Full final suite: 261 tests passed.** Custom-connection coverage: 33 tests. Changed modules passed Python compilation. The regression suite uses local fixtures. Authorized live NVIDIA checks are recorded separately in NVIDIA_CONNECTION_CHECK.md. Real user secret and RC files were not modified for these tests; temporary homes contained all test credentials.

## Numbered selection and format defaults

The editor no longer exposes an endpoint text field. Choose endpoint displays sorted numbers; typing a number and Enter selects it, including choices above nine. Arrow selection remains available in the list. Changing provider updates the known base URL and clears credential fields from the previous provider.

Default shows the selected model's saved format when known; otherwise it uses the most common format in that endpoint catalog, with Chat Completions winning ties. This computation is local. The saved profile retains the concrete wire format and its default/override preference. Version prefixes are preserved in URLs. Complete routes and repeated suffixes are normalized before appending exactly one route. Ollama native `/api/chat` and known `/api` bases resolve to its compatible `/v1` interface, including saved malformed double-appended routes.

Blank credential references reuse a group's variable only when the target host matches the group's host. A different host receives a generated variable. No credential value appears in the model catalog or form confirmation.
