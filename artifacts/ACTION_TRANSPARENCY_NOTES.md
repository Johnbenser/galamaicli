# Shared action protocol and transparent progress

## Evidence and fixes

The latest screenshot (2026-10-20 19:52) and saved model logs showed repeated creation announcements with no tool call. The local server advertised native tool support, but Galam supplied no native schemas. Identical text reminders then produced the same prose.

- `engine/tool_protocol.py` derives model schemas from the same argument registry used for execution. Compatible chat endpoints use native calls for action requests; other formats retain text calls. An explicit unsupported-tool response triggers one retry using the text protocol, cached per endpoint/model. Auth/context errors are not treated as unsupported tools.
- Native assistant calls and their matching results are recorded and converted to correct assistant/tool roles. Calls that validation, inspection, or approval prevented are explicitly marked not executed. Context selection retains or evicts these groups together; it never presents orphan native results. Native tool markup is removed from assistant prose to avoid duplicating the same proposal in history.
- Schema costs are reserved in the context budget. Unambiguous simple filesystem creation requests receive only the relevant file/directory tools and a smaller prompt that retains the configured model instructions and user constraints; unrelated repository outlines are omitted. Complex requests retain normal context selection.
- One bounded recovery mechanism replaces repetitive no-progress reminders. It changes to a structured action request, stops identical no-progress exchanges, and permits a clarification question rather than inventing missing inputs. No-op edits or existing directories do not advance the workspace revision.
- `make_directory(path)` is a typed, reviewed tool that verifies the result is a directory and preserves file collisions. It uses the same registry, native/text parsing, dispatch, optional graph validation, and execution policy as other tools.
- `engine/task_contract.py` checks explicit named filesystem outputs, required types, and quoted literal file content. It does not generate mutations from model prose or guess a complete plan for arbitrary coding tasks. Missing named outputs prevent a model's success statement from closing the task. A requested directory cannot be written as a file; explicit literal content cannot be changed to another value during remaining work. Fully specified creation-only compounds can return factual receipts immediately when every output exists and configured checks have finished, before speculative extra calls in the same model response. Extra testing/coding/explanatory obligations disable this shortcut. Complex or ambiguous language remains model-planned.

## Model output and progress

`engine/transparency.py` separates model-provided output from actual execution phases. The incremental tag splitter supports think/thought/reasoning tags across streamed token boundaries and preserves literal tags inside source/tool arguments. Native reasoning fields, readable provider detail blocks, JSON responses, and side requests reach the trace. Reasoning is display-only, never a tool proposal.

The current task retains all its model requests. Ctrl+O displays the complete supplied trace plus execution phases and toggles further live output. The minimized Brainstorming line uses a deterministic brief excerpt from supplied text or a factual phase, with no extra summarization model call. A non-reasoning model is reported as supplying no reasoning; private model internals are not invented. Existing terminal scrollback is retained. Closing a display cannot interrupt tool/model processing.

## Verification and limits

116 regression tests cover the existing pipeline/local-manager behavior plus native schemas, unsupported-endpoint fallback, native call/result grouping, context eviction, repeated prose, clarification, exact filesystem types and content, compound completion, reasoning token splits, JSON/provider fields, Ctrl+O history, minimized summaries, and side-request isolation.

Early confined live runs exposed two additional faults: repeated edits when native history was missing, then an uncreated folder falsely reported complete. The fixes above address those shared mechanisms. A later confined live run verified both requested outputs but exposed a delay caused by speculative extra calls in the same model response. Immediate evidence-based completion now has two dedicated regression tests. The last live recheck could not start because localhost:8080 refused connections; the final immediate-completion change is therefore unit-verified, not claimed as a final live-model pass. Timing and output checks are recorded separately in the private backup directory. Local server prompt processing was slow during verification. No model server was stopped or restarted, and live writes were confined to temporary directories.

The source was backed up and changed on a working copy first. Saved settings, credentials, existing menu layouts, and unrelated local changes were preserved. Backup and detailed verification files: `/root/galam-upgrade-backups/action-transparency-20261020-195626/`.
