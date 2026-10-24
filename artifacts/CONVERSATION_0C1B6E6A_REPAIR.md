# Listing, project selection and diagnosis recovery

## Observed failures

The saved conversation exposed several connected workflow failures:

- A request included subfolders, but the first action returned only a storage
  symlink's metadata. The local receipt incorrectly marked the listing complete.
- A named sibling project was subsequently interpreted beneath Galam's own
  directory. Startup metadata described the wrong project and prompted redundant
  searches before the user supplied the absolute path again.
- Navigation troubleshooting used 13 model requests and an estimated 135,396
  tokens while searching and reading source without first checking served pages.
  Those usage figures are recorded estimates, not verified provider billing.
- A final check request returned an unclosed `tool_call` block. No action ran,
  but the reply was recorded as completed.

## General repairs

Natural directory listings now resolve a directory symlink's actual root before
execution. A requested recursive listing uses a read-only traversal at the
requested depth; a top-level `ls` or symlink metadata cannot produce a recursive
completion receipt. Explicit shell commands retain their requested behavior.
Listing observations also distinguish directory link counts from item counts.

Startup discovery resolves an explicitly named nearby project by exact
filesystem evidence before reading metadata or changing the working directory.
Missing and ambiguous names remain unresolved, with clarification guidance.
No past-conversation lookup or project-name special case was added.

Check/diagnosis requests receive action handling, and an unclosed action block
requires complete regeneration before execution. An unfinished, unrequested
action cannot be recorded as completed or authorize a write. Diagnostic requests
without change/start authorization remain read-only.

Local page/navigation failures receive fresh HTTP evidence from a known current
task address, explicit local URL, or unambiguous configured project port. The
probe records page status, local asset-byte matches and navigation source hints.
It does not start/stop services or edit project files. HTTP success is explicitly
distinguished from verified browser clicks and JavaScript behavior.

Content searches visit authored source before root documents and generated
outputs can consume the existing result budget. Assistant chat/input transcript
contents are omitted from incidental content searches. Saved history and source
remain intact. No model step, token or context allowance was changed.

## Read-only live evidence

- The real named project resolved correctly, including its declared build/start
  chain, without starting a process or changing project files.
- The running address returned HTTP 200 for the root and ten tested HTML pages.
  Four referenced JavaScript/CSS assets matched their local bytes.
- These checks did not establish browser click behavior, and no frontend repair
  is claimed from HTTP evidence alone. No live cross-provider model benchmark
  was performed.

Regression cases use unrelated disposable project names and ephemeral HTTP
servers. Coverage includes recursive symlink-root listings, explicit metadata
commands, exact/ambiguous sibling discovery, incomplete action recovery,
read-only diagnostic scope, source-first search and real failing route/asset
responses.

## Verification

- **470 tests passed, no skips, 146.879 seconds** in the final full run, with
  user settings restored by the regression runner.
- **13 focused regressions passed in 3.943 seconds**, including an actual
  disposable HTTP server with a broken page and mismatched asset, plus a source
  file larger than 256 KB whose navigation references remain discoverable.
- Read-only live project discovery preserved the real declared build/start
  chain. Live HTTP results are summarized above. No user project file, running
  server, port assignment, model endpoint or settings screen was changed.
- An earlier run hit timing-sensitive fixture failures; isolated reruns passed.
  The asynchronous answer test now waits for completion rather than cancelling
  a correct task after an arbitrary short join. Production cancellation and
  task allowances were not changed.
