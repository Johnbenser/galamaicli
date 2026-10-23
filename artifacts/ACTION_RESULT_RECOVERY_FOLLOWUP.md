# Simple action result recovery

Date: 2026-10-23

## Failure found

A request to list folders ran one successful `ls -la` command. The normal completion check did not recognize the natural-language request as satisfied, so Galam sent a second model request only to summarize the listing. That request failed, and the user saw a failed task despite the complete successful command output.

## Shared change

Galam now produces a local receipt for a single successful, plain `ls` command when the user's request is only to list files or folders. Folder-only requests get directory names parsed from the command's real long-listing output; other simple listings include the actual output. An explicit requested path must match a path in the command. Pipelines, truncated listings, failed actions and requests that combine listing with a mutation or another operation continue through the normal model workflow.

This avoids a redundant summary request for a read-only action, so a later provider failure cannot discard the already verified answer. It does not weaken completion checks for builds, edits, server tasks or multi-step requests.

## Verification

- **432 tests passed** in the full local regression suite, no skips (174.179 seconds).
- Added regressions proving the folder result finishes after one model call, compound listing-plus-mutation requests do not use the shortcut, and the command listed the exact requested path.
- The original action output remains the source for the returned names; no user files or services are modified.
