# Server inspection, launch detection and completion

21 October 2026.

## Failure and cause

A project-inspection command containing `cat vite.config.mjs` was incorrectly classified as a server launch. Detection searched runner names anywhere in the whole command string, so the `vite` portion of a config filename matched. Galam backgrounded the inspection, checked an unrelated default port, and reported a false startup failure. Per-port append-only logs could then mix an older launch's output into a newer failure.

The action loop also allowed a successful inspection/output retrieval to be followed by a generic empty-answer placeholder and a completed task status, despite no verified server startup.

## Fix

- Detect recognized runners at executable positions in shell command segments, rather than matching names in arguments, config files, echoed instructions, comments or heredoc bodies.
- Keep foreground inspections in the existing cancellable shell runner, with actual exit status and output.
- Infer ports from explicit runner arguments, environment settings and nested package scripts. Follow dev/start/preview references with cycle protection. Recognize defaults such as `PORT=${PORT:-5501}`; explicit CLI ports take priority.
- Preserve Python HTTP-server port arguments and its standard 8000 default, along with Vite's 5173 default.
- Allocate a separate log per managed server launch so a later error cannot quote a previous command's appended output. Close the parent's log descriptor after launch handling.
- Preserve completion reasons for non-streaming replies. A normal provider stop does not trigger repeated continuation merely because the answer contains only reasoning; explicit token-limit cutoffs still continue.
- Mark reasoning-only/no-answer/no-tool responses as incomplete. Apply bounded protocol repair when an action remains required; stop honestly if repair fails.
- A server-start request requires successful background-launch evidence from the managed server path, which performs HTTP verification. A successful directory listing or archive read does not satisfy that requirement.
- Resolve short continuation prompts against the current conversation's real user task, excluding tool observations and foreign conversation rows. Resumed `continue` requests retain the outstanding server goal.

The command is not rewritten by classification, shell code is not executed while inspecting scripts, and no additional model query is used to classify runners or infer ports. Existing UI and execution modes are retained. Runner recognition is conservative; it is not a full shell interpreter or a filesystem sandbox.

## Verification

Regression cases include:

- chained project/config/log inspections;
- runner names in echo/grep/printf arguments, quoted separators, heredocs and Python string literals;
- actual npm, npx, Python and Node runner commands;
- nested package scripts, environment defaults, explicit port overrides and script cycles;
- real foreground inspection preserving its output and an existing project log;
- a real temporary HTTP server started, HTTP-checked and stopped by its test;
- successful inspection followed by an incomplete response cannot mark a server task completed;
- resumed continuation preserves the server goal and stops with an incomplete footer when no server is launched;
- the router marks a long reasoning-only reply incomplete using its original single mocked request, without three speculative continuations.

Tests use temporary projects and local/mocked transports. No private conversation transcript is included here, and the user's existing project files and saved conversation were not rewritten. Live model quotas were not consumed by these checks.

Final validation: **286 regression tests passed**, including nine targeted server-workflow tests. Changed modules compiled; diff checks passed.
