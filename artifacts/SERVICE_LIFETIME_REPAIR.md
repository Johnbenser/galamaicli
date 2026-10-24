# Service lifetime and shutdown intent repair

## Observed failure

Conversation `45296ad5` successfully built and served a project, checked its routes and opened the browser. The recorded launch used a shell command that backgrounded the server, waited briefly, and performed checks. Galam registered the shell PID; the actual server had a different PID. At inspection, the original server still responded with HTTP 200, but Galam had lost its task row after the launcher shell exited.

`TaskManager` correctly removes tasks whose tracked process exits. The missing piece was launch ownership: a shell's lifetime does not necessarily equal its background service's lifetime. During readiness, the same mismatch could also make the runner report an early exit and terminate surviving children.

## General repair

All owned long-running launches now run under a small standard-library supervisor in a dedicated session/process group. It executes the original shell command unchanged and remains alive until that shell and its service group have exited. Shell background jobs, nested launch scripts, build/start chains and follow-up checks therefore retain one stable owner.

- Parent-shell exit alone cannot remove the service's running row or cause a second launch.
- Reusing the same live command retains the existing owner.
- Real service exit removes the row; a failing launcher with no live service reports failure.
- Individual task-stop requests wait for the tracked owner to exit before removing it, escalates an ignored graceful stop, and retains ownership if the OS has not finished shutdown. Stop and cancellation address only Galam's owned process group. Unrelated services are not stopped.
- Readiness continues to use actual configured, supplied or announced addresses. No port, project path, executable name, or framework establishes ownership. No permanent port or per-project exception was added.
- Tracking adds no model requests or new prompt instructions; existing active-task context remains available. The supervisor uses direct-child evidence for the healthy case and process-group scanning as fallback; no repeated system-wide scan is needed when an adopted service child is visible.

The old natural-language shutdown shortcut also matched mentions and negations such as “do not stop my server.” It now accepts explicit stop requests, resolves a specific tracked task/PID/port/runtime, and requires “all” before stopping multiple tasks. Questions, conditions and ordinary mentions follow the normal conversation pipeline.

## Verification

Recorded verification: the full run covered 388 tests in 169.252 seconds, with 387 passing and no skips. All 11 new lifetime, shutdown-intent and probe-transport checks passed, as did the existing framework/build/cancellation process checks. One existing queue-worker test missed its one-second join deadline under full-suite load; its isolated rerun passed in 0.140 seconds. This is reported as a rerun, not a single fully passing suite invocation. Test fixtures use the normal startup allowance rather than asserting cold-start scheduling speed on Android; build-stage release and lifetime assertions remain required.


Plain HTTP readiness probes also skip unnecessary TLS certificate loading; HTTPS retains its verified TLS context. Both paths have dedicated regression checks.

Temporary real-process tests cover shell exit after successful startup, nested launcher exit before readiness, completed assistant responses preserving the running row, dynamic-port readiness, actual service exit, exact-command reuse, failed launches, explicit stopping, and survival of an unrelated server. Shutdown-intent tests cover negation, questions, conditional instructions, ambiguity and explicit targets. Existing Python, Node, Vite/React, Flask, Make, build-chain and cancellation tests exercise the same runner.

No user project source was modified, restarted or stopped. The original service was checked read-only. No live model test was needed for this local lifecycle defect.

## Scope

The supervisor manages its launched process group on Linux/Android. Programs deliberately creating independent sessions or delegating to an external service manager need that manager's own lifecycle interface; Galam does not discover ownership through process names or occupied ports. OS termination of an entire application/session remains an OS event. This change preserves the existing settings and terminal layouts and applies to new launches after Galam restarts.
