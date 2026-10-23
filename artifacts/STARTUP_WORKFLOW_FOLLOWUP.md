# Startup workflow follow-up

Date: 2026-10-23

## Failure found

A recent Android startup attempt spent an action on `npm install`, which failed while creating an `esbuild` symlink in shared storage. Recovery then proposed recursive permission changes and ran the package's `start` script even though its `dev` script declares `build && start`. The runner marked the task complete after a root-page HTTP 200. That did not establish that the correct build ran or that direct refreshes on the site's navigation routes worked.

The user's project files and running server were left untouched by this repair. The recursive permission command had already been attempted in the earlier conversation; current Android shared-storage mode bits do not establish that its permission changes took effect, so no original modes are claimed or reconstructed.

## Shared changes

- Startup discovery resolves a nonexistent leading-slash project name only when that exact directory exists as a unique sibling of the current workspace. It does not search for fuzzy matches or silently choose among multiple projects.
- For package projects, startup guidance recommends the declared `dev` entrypoint and calls out a build-before-server chain. Existing `node_modules` prevents a speculative install recommendation. A real shared-storage symlink `EACCES` gets a specific recovery that preserves the project scripts and rejects recursive `chmod` as a remedy.
- Shell validation rejects recursive permission changes while retaining path-specific permission commands. The Command Agent and shared action contract both instruct the model to diagnose the actual filesystem error and preserve the build/start chain.
- Startup guidance requires application and relevant direct-route checks; HTTP 200 from `/` alone is not described as functional success.

## Verification

- **429 tests passed** in a full local regression run with no skips (80.401 seconds). Four new regressions cover exact sibling path recovery, build-then-start selection, recursive permission rejection and Android npm symlink-error recovery.
- A read-only check of the active server fetched the home page and all ten routes declared by the project. Each returned HTML with its React mount element. The root page's local JavaScript and stylesheet assets matched the project files. The server process was not restarted, signaled or adopted.
- These HTTP checks establish direct-route responses and source-asset matching; they do not simulate Android browser rendering, client-side clicks, or LAN access from another device.
