# Session recovery, queue and resume — 21 October 2026

## Screenshot and root causes

The screenshot reports ResourceExhausted / total request limit reached, then leaves Brainstorming active. Model failures could fall through to endpoint fallback, which recursively revisited earlier endpoints. Cancellation only set a boolean while blocking network reads or approval waits could keep the serial worker occupied. New prompts already entered an invisible queue, making them appear ignored.

## Installed fixes

- Definitive request quota exhaustion returns a failure immediately. A query tracks visited endpoints to bound fallback. Existing short Retry-After retries remain bounded.
- Task lifecycle and worker finalizers settle active/thinking/streaming state on failure, cancellation and success.
- Per-task cancellation events release model connection, JSON and SSE waits. Abandoned I/O has no ability to append model output, mutate context, execute actions or update settings for a later task. Late responses are disposed. The provider may continue an already submitted remote request until it finishes/times out; cancellation does not establish a remote billing refund.
- Foreground shell cancellation terminates only the process group created for that action. Already running background servers remain separate tasks. File operations completed before cancellation are not rolled back.
- Both physical double-Esc key sequences and separated Esc taps within the confirmation window cancel the current task and release approval waits. Single Esc during a task preserves draft input. /cancel remains immediate.
- FIFO pending prompts are visible, and /queue and /queue clear inspect/remove pending prompts. Pending prompts do not enter model context until started. Queue entries are in memory for the current session; exiting discards pending entries. Approved-plan dispatch remains compatible.
- Shortcut suggestions show at 30–60 seconds, 240–270 seconds, 450–480 seconds, and so on. Active cancellation and queue status are still shown when relevant.
- galamai resume launches a local recent-chat picker. Up/down and PageUp/PageDown navigate; Enter loads only the selected chat; Shift+D permanently removes its history JSON; Esc exits. The picker includes date, ID, prompt count and recent text. Resume replays up to 20 recent conversational messages while retaining all saved selected-chat context. The legacy -c option remains available.
- Existing Termux history can be discovered under proot without merging separate chats. Read-only verification found 106 existing conversations; none were deleted or rewritten.
- Launchers were installed in /usr/local/bin/galamai and /data/data/com.termux/files/usr/bin/galamai. Both forward all arguments to this checkout. The project also includes a portable source launcher.

## Verification

196 tests passed: `python -m unittest discover -s tests -q`.

New gates cover hard quota failure without transport/model/endpoint retry, visited-endpoint reentry, final-state cleanup, blocked I/O cancellation with late response disposal, stalled SSE cancellation, owned foreground process cancellation, next queued prompt execution after cancellation, unchanged old/new response separation, FIFO/clear behavior, approved-plan queue compatibility, exact hint scheduling, real double-Esc key input, local resume replay without an AI request, selected-chat ownership, safe permanent deletion, and picker keyboard selection/deletion.

Python compilation passed. `galamai resume --help` works. No live provider calls were needed; the external request quota itself is not changed by this patch. API keys and existing settings were not edited.

Backup: /root/galam-upgrade-backups/session-queue-20261021-015635/ .

Restart the CLI to load the updated task lifecycle, queue and key bindings.
