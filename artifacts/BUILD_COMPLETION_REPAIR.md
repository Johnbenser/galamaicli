# Build completion and action review repair

## Failure evidence

Conversation `d7efba84` requested an implemented website in a new folder outside the current Galam checkout. Only directories and inspections completed. The final announcement said construction was starting, but no source file was written. The saved task incorrectly reported completion (6 requests; 19,270 input and 3,471 output tokens).

The implementation-intent classifier recognized “website” as requiring contents, but a separate navigation check suppressed that requirement when “folder” appeared. A filename glob was also passed to content grep, which returned Galam's model transcript instead of project HTML files. Outside-workspace file tools requested safety review even when the human had explicitly selected that output destination.

## Changes

- Content requirements take precedence over navigation words. Folder creation and reads cannot satisfy an implementation request. Repair feedback identifies missing implementation evidence and asks for a working first file followed by focused edits.
- Current-human-request destination clauses establish a temporary, resolved output scope. Always Proceed can use ordinary file tools there without another approval. REVIEW/AUTO semantics, unrelated destinations, Git metadata, credential restrictions and destructive shell review remain. Scope does not come from retrieved data or assistant announcements, and ends with the task.
- Declining one action lets the model choose an alternative; an identical declined proposal cannot reopen the same approval during that request. Cancellation still stops execution.
- Filename globs search names. Project searches omit runtime transcripts, dependency trees, credentials and symlinks; result previews are bounded. Explicit file reads remain available for needed diagnostics.
- Shell write detection no longer mistakes a read command's stderr redirection or a later command after `echo` for source creation.
- Truncated action JSON is regenerated as a complete smaller action instead of being concatenated as prose. Complete calls retain validation and review.
- If a native action remains buffered with no visible progress after capacity recovery, the current task switches to the existing text tool contract and requests a compact complete action. Later human requests start with normal endpoint capabilities.
- A length-limited empty stream enters output recovery instead of silently trying another model. For actionable tasks, response capacity may grow within the connection's remaining context and advertised output capacity. A generated recovery marker retains the learned allowance within the same human task; later human requests start normally. Saved settings are unchanged. `adaptive_action_output: false` disables growth; manual local tuning, prepared-request capacity validation and explicit task spending allowances remain effective.
- Endpoint exhaustion saves a clear incomplete receipt and last observed result, rather than leaving a future-tense build announcement as the final message.

## Verification

**Recorded verification:** 377 tests passed with no skips (50.324 seconds), followed by 14 focused checks passing after the final failure-receipt and recovery-scope assertions. The tracked-file credential scan passed for 152 files.

Regression coverage includes website-plus-folder requests, actual temporary file creation after setup, requested outside-workspace destinations, scope reset, traversal/symlink escape, critical reviews, declined alternatives, exhaustion receipts, filename globs, transcript exclusion, incomplete native/text actions, buffered empty length responses, output growth isolation and provider ceilings. Existing uncapped prose continuation remains covered separately.

Live checks use temporary projects and the selected MiniMax/Direct connection, with explicit harness spending limits. They do not alter user projects. Early runs exposed provider-buffered actions exhausting a 1,024-token response before returning arguments; those failures are part of the diagnostic evidence, not successful build claims. The final live check used 8 requests in 85.29 seconds, reported 23,734 input + 12,382 output = 36,116 provider tokens, and wrote a 15,695-byte HTML file without approval prompts. It stopped at the harness's explicit 40,000-token spending allowance before full website verification. This establishes real nonempty creation and review recovery, not a fully verified website or a token-savings benchmark. The harness allowance is not a production default.

This repair cannot guarantee provider availability or model quality, and does not make finite provider context unlimited. Security checks remain defense in depth rather than an OS sandbox. No settings or terminal layout redesign is included.
