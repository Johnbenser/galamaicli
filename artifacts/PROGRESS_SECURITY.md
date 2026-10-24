# Progress, recovery and local security

Historical implementation notes for `f3e047a`. Its aggregate launch-fact compaction and output-novelty recovery were insufficient in a subsequent real session. See [Launch workflow repair](LAUNCH_WORKFLOW_REPAIR.md) for the replacements, regression evidence and latest verification.

## Failure addressed

The audited server-start task made eight successful HTTP requests in 27.1 seconds, using 17,753 input and 1,240 output tokens (18,993 total), but never attempted a server launch. Repeated listings and archive retrieval consumed its eight-iteration allowance. Raising that allowance alone would have extended the same loop.

## Runtime changes

`engine/task_progress.py` tracks local evidence, unchanged file reads, repeated listing/metadata results, work windows and cumulative spend. `galam.py` renews the work window automatically instead of terminating at `max_iterations`. Two consecutive rounds without new evidence trigger a brief recovery message in the ordinary next request; four such rounds pause with a persisted incomplete assistant receipt. A recovery that obtains new evidence can continue. Novel errors can count as evidence. This is a heuristic, not semantic proof that every action helps.

Defaults: 128 actual request-entry calls, 120,000 measured/estimated tokens and 1,800 active seconds per task. `engine/usage.py` checks the next request even when invoked by fallback/retry/continuation code. Generation input/output estimates reserve room before sending; exact provider bills cannot be guaranteed. In-flight operations retain their existing timeouts. `/budget` explicitly changes allowances without discarding accumulated spend. `/btw` keeps its existing independent ledger; these task allowances are not a global account quota or a provider RPM scheduler.

State belongs to a real user request in its conversation; `continue` inherits unfinished task state and new tasks start independently. The lifecycle finalizer records actual final status and current cumulative spend. Saved metadata is stripped from ordinary API messages. Compact evidence is pinned only when history needs compaction. Existing instructions, native call/result pairing and inspected-source requirements remain in force. Original results remain retrievable; historical archives omit duplicate call aliases and refer to stored source results rather than serializing full observation wrappers repeatedly.

File read reuse requires matching file identity, contents and line range, with a four-megabyte hashing bound. It does not cache arbitrary Bash, live network state, server health or running processes. Simple listing fingerprints use actual directory entries; package script facts require a successful observed package read. These narrow rules are portable across models and API formats.

## Identity correction

Incorrect current-user identification was in `engine/prompt_context.py` and `routers/manager.py`; `engine/context_manager.py` also personalized the compact assistant to the creator. These now credit John Benser without identifying the installer as John. `engine/tool_parser.py` no longer synthesizes a creator-name file from vague prose. Attribution is never an authentication mechanism.

## Security audit and safeguards

`/audit [directory]` and `security_audit(directory)` share `engine/security.py`. The local audit reports paths/categories, never matched values. It bounds text size, visited/scanned files, elapsed traversal time and finding count; skips symlinks, generated dependencies, logs and conversation directories; and reports partial scans honestly. Git inspection is read-only. These checks do not consult a vulnerability database or perform a penetration test.

- Common credential paths and symlink targets are refused by model file tools. Search excludes common credential filenames and uses `--` before a query to avoid query-option injection.
- Credential values recognized from the current environment and common token/private-key formats are redacted before tool-result storage and when writing new diagnostic logs. New logs use private permissions where supported. This does not scrub existing local history/logs or identify every possible secret format.
- Shell children receive an environment without recognized credential names by default. The CLI's own provider transport still has credentials. Recognized credential-access commands require explicit review before credential inheritance; models cannot set the internal permission flag. Arbitrary programs can still read files, so this is defense in depth.
- Recognized critical commands require review even in ALWAYS-PROCEED. Structured cross-workspace writes and Git metadata writes also require review. The policy is heuristic, not a full shell interpreter or sandbox.
- Task cleanup only targets live processes tracked as started by the current Galam instance. No inferred ownership from process names/ports and no global fuser/pkill cleanup fallback.
- Both provider urllib fallback and the HTTP helper keep certificate checks enabled for every endpoint. Connection fallback remains possible, but never retries with verification disabled. HTTP adapter retries are disabled in favor of explicit, metered request paths.
- A writable temporary directory is verified and exposed as TMPDIR; arbitrary user shell strings containing /tmp are not rewritten.

## Verification and limits

312 local tests passed, including a scripted productive task longer than 25 rounds with a two-round checkpoint window, repeated-read recovery/stop, changed-file invalidation, equivalent listings, package fact retention, cumulative spend across resume, request guards inside transport entry, read-only audit, secret protection/redaction, subprocess credential inheritance, critical review, unrelated process protection and certificate failure behavior. Existing tests cover Bash, editing, native tool protocols, cancellation, queue, /btw, context isolation, resume and UI.

No paid model requests were used to implement or verify this change, and the user's portfolio was not changed or launched. Successful completion and token savings with a real model still need workload measurement. A longer useful task may correctly consume more tokens. The recorded 18,993-token incident is a baseline, not a claim of measured post-fix savings.

Use a separate OS account/container for untrusted code. Filesystem permissions depend on the platform; Android shared storage may not enforce Unix modes. The UI/settings appearance is retained. The added slash commands and stricter critical-action review are intentional behavior additions.
