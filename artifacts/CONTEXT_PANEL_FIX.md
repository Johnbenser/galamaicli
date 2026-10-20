# Context overflow and Ctrl+O repair — 21 October 2026

## Screenshot findings

Both screenshots show context-budget failures after a real terminal action. One also shows an incorrect `/logs/tasks/...` lookup. Expanded model output previously went to stdout, so a second Ctrl+O could not remove it.

## Changes

- Added budget-triggered, recoverable compaction of active task output, historical assistant text and large completed call arguments. Large tool histories archive complete call/result groups together. Original user requests, workspace constraints and custom model instructions remain. Recent failures and the newest tool exchange stay available. Real instructions that cannot fit still raise a context error.
- Exact overflow is stored privately in the existing result store; the model receives an explicit partial-context notice and a `read_action_output` reference. Saved conversations and real tool execution arguments are not rewritten. No extra summarization model request is used.
- Kept complete automatic inspection output before budget selection, removing an earlier silent 14,000-character slice.
- Replaced stdout thought tracing with a bounded live prompt_toolkit panel. Ctrl+O genuinely shows/hides it. Selected visibility remains across prompts in the same session. The panel is removed on final prompt rendering so it is not committed to scrollback. Redraws are limited to 30 per second.
- The expanded panel follows the newest output. Ctrl+PageUp/PageDown browse the full supplied trace. New tasks reset scrolling while retaining visibility. Minimized Brainstorming summaries remain. Only model-supplied reasoning and factual execution progress are shown.
- Newly launched Galam servers provide their absolute log path in both the action result and subsequent workspace context, including health-check timeout errors.

## Verification

`python -m unittest discover -s tests -q`: 169 tests passed.

New regression coverage includes 4,000-line inspections in a 4,096-token window, a 70-action task, oversized write arguments, exact archive retrieval, original-history immutability, native call/result pairing, custom rules, actual log paths, and keyboard-driven renderer checks across repeated prompts with a retained input draft. Existing Bash, action, local profile, token and conversation-isolation tests also pass. Python compilation passed.

No live provider requests were needed for these failures. Restart Galam to load the changed code. View preference persists within the running session; terminal scrollback printed by the old version is not editable by the new panel.

Backup: `/root/galam-upgrade-backups/context-panel-20261021-004717/`.
