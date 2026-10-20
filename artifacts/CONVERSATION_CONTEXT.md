# Conversation-owned context

Each conversation loads one history file by its conversation ID. Context summaries,
selected messages, native tool histories, and action-mode selection use that
conversation's messages. They do not collect every saved conversation.

History rows carry a private `_galam_conversation_id` field. The agent binds rows
to the selected conversation at task start and when saving. Tagged foreign rows
are excluded before context selection and tool-protocol conversion. Ownership
metadata is not added to the provider's chat-message payload.

Old JSON-list histories remain supported: loading assigns their own file's ID in
memory without rewriting the file. Updated histories are saved atomically. The
loader accepts only a single conversation file name, not a path to another file.

## Plans

Plan approval now selects a plan referenced in the current conversation. It no
longer searches the shared artifacts directory for the newest plan. New plan
references are saved with their conversation immediately; legacy assistant
messages containing `Saved to ...plan_....md` remain usable on resume. If the
latest referenced plan is missing, Galam asks for a new plan rather than choosing
another chat's or an older plan.

## Existing behavior

- Start a fresh conversation with `glm`; resume one with `glm -c CONVERSATION_ID`.
- Model/API settings and credentials retain their existing global behavior.
- Bash execution, approval policy, file tools, UI, and Ctrl+O controls are unchanged.
- Conversations can still work on the same files on disk. Working-directory
  snapshots and inspected source reflect the actual workspace.
- Independent planning/side requests receive only their supplied message list.

## Verification

140 tests passed, including isolated save/resume, legacy history, foreign
constraint/system/tool-result filtering, native call/result pairing, plan
approval isolation, and a real harmless Bash execution through the agent loop.
Tests used temporary histories and mocked model responses; no provider calls
were needed. Existing user conversation files were not migrated or modified.
