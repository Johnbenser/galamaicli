# Local model manager implementation

## Integration

- New logic modules: `engine/local_profiles.py`, `local_tuning.py`, `local_process.py`, `local_commands.py`, and `local_ui.py`.
- `local_models`: list of `{name, path, ctx, threads}` profiles in the existing local settings, never in source control.
- `local_tuning`: `{mode, temperature, max_tokens, context_window}`. Auto estimates prefer filename parameter counts; otherwise use file size. These are starting points, not benchmark results. The 1.5B/3B/7B examples use contexts 2048/8192/4096 and output allowances 512/768/512.
- `local_manager`: defaults to true. Setting it false and restarting restores the previous menu and request behavior. Existing pre-manager localhost recovery remains available when disabled.
- New item immediately before Back to Chat. Existing main items, endpoint/model actions, 10-line window, seven visible slots, clear sequence, and existing cursor returns remain intact. New work screens use the same scroll behavior. No text-entry widget was added.
- Diagnostics and scanning use daemon workers; stale callbacks cannot overwrite a newer view. Connection tests do not save settings, start servers, or invoke Galam's model router.
- Only local requests use the new temperature/token/context settings. The context budget and payload share the same output reservation. Advertised server context is the upper bound; diagnostics also cache observed metadata for aliases advertised as a bare GGUF filename.

## Server ownership

Selection only saves the profile and shows the quoted command. `/local command` prints its complete, copyable form outside the fixed settings window. Profiles support environment overrides `GALAM_LLAMA_SERVER_BIN` and the existing local endpoint URL setting.

Explicit `/local start` and automatic recovery can launch a selected profile on a free loopback HTTP port. An existing external listener is never replaced. An explicit `/local restart` requires a Galam-created record whose PID, Linux `/proc` process start time, and command fingerprint still match. Identity is rechecked before SIGTERM. Missing replacement files/binaries leave the existing process running. No pkill or broad process-name matching is used. Platforms where process ownership cannot be verified refuse restart.

Owned startup waits up to 120 seconds. Connection tests use 5-second connect and 180-second read timeouts for each request, showing each health/model/chat result. Throughput is approximate completion tokens divided by total request latency, including prompt processing; it is not a decode-only benchmark. If usage is absent, output token count is estimated. Loading, down/unreachable, wrong service/port busy, model mismatch, auth failures, missing visible output, and small-context errors have separate diagnoses.

## Verification

86 regression tests cover the existing pipelines plus profile validation, duplicate handling, scan sizing, safe command quoting, mocked connection states, local/manual tuning, remote-provider isolation, menu selection/navigation/scrolling, stale workers, rollback flag, PID reuse, and safe owned startup/restart behavior. No test writes the real settings or kills a real server.

A read-only live connection check on the configured endpoint reported server down/unreachable. Successful live chat with the external model is therefore not claimed. The check left the user's Termux server session untouched.

## Rollback

Set `"local_manager": false` in the loaded local settings file, then restart Galam. Keep settings synchronized if editing multiple configuration copies manually. Full pre-change backup and a working copy were created before implementation; their paths and verification reports are recorded under `/root/galam-upgrade-backups/local-manager-20261020-191658/`. Local credentials and saved settings were left unchanged during installation.
