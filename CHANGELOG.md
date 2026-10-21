# Changelog

User-facing improvements and bug fixes for Galam.AI. Entries describe implemented behavior; provider-side availability and model quality can vary.

## Unreleased — connections, workflows and presentation

### Added

- Embedded four-step model connection wizard with review before save, presented in the original compact inline settings style.
- Native Ollama `/api/chat` requests, NDJSON/JSON replies, structured action round trips and provider token accounting.

- Settings → Model Connections: add/edit a model, assign an endpoint group, choose Chat Completions or Responses, enter a masked API key and optionally activate immediately.
- Home-owned `~/.galam/endpoints.json` for connection metadata; keys are saved only to `~/.secrets` with private permissions, literal shell quoting and preserved existing exports/comments.
- Immediate credential application in Galam, startup credential reload and a deduplicated guarded Bash/Zsh source line for future terminals.
- Responses API input/tool-output conversion, streamed function arguments, final snapshots, provider usage and connection-scoped reasoning continuity.
- Custom-connection regression tests, including local HTTP/Bash round trips, transactional rollback, stale-export reload and phone-sized editor input/save/cancel behavior.

- Conversation-owned context and resumable history with working-directory restoration.
- Full-screen centered resume picker, conversation previews, five persistent accent colors, keyboard navigation and permanent selected-history deletion.
- FIFO prompt queue with pending text pinned above the input and no unsolicited printed queue IDs.
- Independent `/btw` side-chat worker, private history and separate cancellation while the main task continues.
- Local GGUF profile discovery, connection testing, AUTO/MANUAL tuning and owned-server lifecycle commands.
- Recoverable context compaction, compact prompt construction and local request-token accounting.
- Agent Router endpoint registration, environment-key lookup and endpoint-specific diagnostics.
- Structured terminal response renderer for tables, sections, lists, numbered code blocks and status text.
- Permanent per-response duration, fixed completion timestamp, input/output/total token footers and saved metadata for history replay.
- Comprehensive Android/Termux installation guide, system/data-flow diagrams, module map and command reference.
- `/changelog` renders the repository's update record locally without a model request.

### Improved

- Completion footers show duration, a frozen local AM/PM clock and only Total Token; approximate totals use `~` and unavailable measurements remain explicit.
- Fixed false zero-token totals when the optional planning graph runs inference in worker contexts; main and side tasks retain separate ledgers.
- Preserve authoritative provider totals, total-only usage and split input/output frames without requiring extra usage requests or adding prompt text.

- Restored the original inline settings menu and terminal background while retaining the new setup flow, URL preservation and selection fixes. Settings closes by clearing its own render region.
- Resumed conversation replay uses live chat formatting, user dividers/arrows and saved duration/token footers instead of plain role labels; context and actions remain untouched.
- JSON framing handles mislabeled native streams, unexpected non-streaming NDJSON and pretty JSON without the `Extra data` parsing failure. Function fragments and reported usage are retained.
- Fallback confirmation hides the thinking spinner and ends printed question lines before showing the input prompt.

- Endpoint assignment follows the highlighted row, fixing stale checked values that could save Direct selections under Groq. Numbers remain available, including multi-digit choices.
- Default preserves the exact entered request URL. Payload inference is local; explicit format choices also leave the destination unchanged. Typed URLs survive endpoint-group changes.
- Matching provider hosts can reuse the endpoint credential variable without typing it; unrelated hosts receive separate references.
- Removed automatic route rewriting, including the unwanted conversion of Ollama native `/api/chat` URLs to `/v1/chat/completions`. Provider URL suggestions are shown only for empty or untouched fields.
- Credential lookup initializes the secret loader before checking environment aliases, including standalone diagnostics.
- Explicit daily/account quota errors stop redundant same-account model fallback; ordinary temporary model limits remain distinct.
- Removed the built-in NVIDIA Kimi K3 entry after a live HTTP 410 response; HTTP 410 errors now identify an unavailable/retired model.

- Native and fallback text tool protocols, argument assembly and bounded action repair.
- File/directory creation contracts verify actual outputs and content before completion.
- Verified simple filesystem receipts can finish without a redundant model confirmation call.
- Context selection preserves current instructions and relevant tool evidence while bounding older observations; no extra summarizer model is required.
- Tool-output retention, focused edits, syntax validation, project-check scheduling and edit-recovery evidence.
- Ctrl+O keeps its expanded/minimized preference across prompts; compact labels mirror supplied reasoning and actual execution locally.
- Provider usage accounting aggregates requests and continuations, prefers measured counts, and explicitly labels estimates or unavailable values.

### Fixed

- Empty code files being treated as completed implementations.
- Slow/redundant post-action acknowledgments for verified simple tasks.
- Context from unrelated conversations entering the active chat.
- Thought panel not reliably hiding on the next Ctrl+O press.
- Loading animation depending on network activity rather than active-task state.
- Stale thinking/streaming state and advancing completion clocks after a reply ends.
- Cancellation leaving connection/stream waits or approval waits stuck and preventing subsequent queued prompts.
- Definitive quota errors falling into repeated endpoint fallback.
- Side questions blocking behind a busy main task.
- Queue display noise and prompt placement around the input dock.
- Only the latest response retaining a visible completion footer.
- Raw Markdown tables, clipped long code/paths and inline formatting corrupting literal code such as `__init__.py`.
- Empty failed-tool output being presented with a success-style label.

### Verification

- Latest recorded full local suite: **261 tests passed**.
- Changed response-presentation modules passed Python compilation.
- Coverage includes actions/edits, context ownership and overflow, streaming/usage, cancellation/queue recovery, side-chat isolation, Ctrl+O, resume and terminal rendering.
- Fixture tests do not establish every live provider/model combination. Agent Router can reject custom clients under its access policy; the adapter does not bypass that policy.

## Initial public source

- Multi-provider CLI, local endpoint integration, settings UI and verified action pipeline published as the project's initial source snapshot.
