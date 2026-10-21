# Launch workflow repair and recurring-failure checks

## What was replaced

The earlier progress/security update allowed productive work beyond fixed iteration counts, but a subsequent real startup task still performed dozens of inspections without proposing a launch. The new evidence tests exposed three concrete failures in that version: loss of a complete startup expression, recovery without the retained command, and unrelated novel output indefinitely renewing a known startup task. All three regressions failed before the replacements.

`engine/task_evidence.py` now selects a complete launch fact with command, invocation, cwd and source reference. `engine/task_progress.py` no longer middle-truncates concatenated package scripts and recent receipts. It migrates usable older script facts, extracts numbered manifest observations, and makes recovery goal-directed once a recipe is known. A large required command remains whole; if essential evidence cannot fit the model window, the existing context-budget error is preferable to silently corrupting it. A selected candidate is evidence, not permission or an automatically executed command.

`engine/context_compaction.py` preserves small recent targeted answers (up to 1,200 characters) through the next decision, even when another tool returns a large source file beside them. Old native call/result pairing, exact-source requirements for edits and original-result retrieval references remain intact.

The old automatic server-launch block in `engine/task_runner.py` was removed and replaced by delegation to `engine/process_runner.py`. Both common-command detection and explicit `background=true` use the same execution path. The existing Bash tool gained optional `background` and `readiness_url` fields; they are retained by text parsing and native/optional graph argument contracts. Ordinary foreground commands retain their existing behavior.

## Process lifecycle

A command runs in the resolved project directory with the existing credential/environment/review policy. Its own process group and private log are recorded immediately after spawn. Readiness uses an explicit local HTTP(S)/TCP URL, an inferred configured port where available, or a local URL announced in logs. There is no guessed default port in this runner. Python child output is unbuffered so startup messages can be observed.

Startup polling is local and cancellable; it does not make model requests. Default startup timeout is 30 seconds, configurable with a 120-second bound. Unknown readiness is reported explicitly and the live process remains tracked. A repeated command/cwd reuses that process rather than launching a second copy. Pre-existing unowned listeners cannot prove the requested project started. HTTP authentication responses are distinguished from successful health checks. Cancelled startup terminates its owned process group and reaps the parent.

The `manage_tasks` execution wrapper also contained an external-PID termination fallback after owned lookup failed. That remaining path is removed. Existing review, credential protection/filtering, TLS verification and conversation isolation remain in effect. These measures are not an OS sandbox.

## Verification

325 local tests passed. New coverage includes:

- Three regressions demonstrated failing on the previous code, then passing after replacement.
- Long multi-script manifests and recent receipts without a clipped startup expression; numbered manifest extraction.
- An adaptive model fixture that examines the actual compacted provider-facing messages at a 4,096-token context/1,024-output reservation. It requests missing evidence, and only proposes a launch when it sees the complete command. The emitted command starts a real temporary HTTP service and passes readiness in at most three model-fixture turns.
- Custom launcher filenames, startup log URL discovery, explicitly provided readiness and a pending-startup gate. Rechecking readiness uses the same PID.
- Unrelated healthy services on a requested address, authentication-required responses, cancellation during startup and external-PID stop refusal.
- Text-tool background/readiness argument retention and conservative misspelled adjacent startup verbs with negation/question checks.

The pending-startup test uses an explicit release gate instead of relying on overlapping sleep durations on a loaded phone. Tests run in isolated temporary projects, with independent HTTP checks and tracked-process cleanup. The user's portfolio is not modified or launched. No paid/live model requests were used.

Fixture success does not establish live model quality, universal language/framework compatibility or a measured token-saving percentage. The [coding stress plan](VIBE_CODING_STRESS_PLAN.md) specifies the bounded live pilot and broader multi-turn evaluation, including independent acceptance, quota-aware pacing and tokens across all attempts per verified success. New failure transcripts should become sanitized payload/behavior regressions before another workaround is added.
