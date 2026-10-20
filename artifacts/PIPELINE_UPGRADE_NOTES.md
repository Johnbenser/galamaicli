# Galam pipeline upgrade

Implemented on 2026-10-20. Existing UI files, configuration code, saved settings, and execution-mode choices are preserved. The upgrade uses the Python standard library and adds no package requirements.

## Applied behavior

- Actions now carry a structured success/failure status and real exit code. Existing callers can still unpack `(output, background)`.
- Tool argument validation is shared by the normal action loop and optional LangGraph path.
- Explanatory shell examples stay visible and do not become executable actions. Incomplete text calls and malformed native arguments are not executed.
- Native tool calls survive a server returning ordinary JSON to a streaming request, and streamed argument fragments are assembled before use.
- A test command can run again after an edit. Recorded actions distinguish successful file operations from test execution.
- If a model announces a read without producing a tool call, Galam can recover a single existing file explicitly named in a read request. Recovery still uses the normal review mode and tool runner. It does not infer edits or guessed paths. Repeated read announcements can be answered from the verified file output.
- Read-only constraints such as “do not modify any files” no longer turn an inspection into a demand for a code change.
- Full action output is stored locally; long previews retain the beginning and end. The model can retrieve exact output through `read_action_output(action_id, start_line, end_line, query)`. A query searches beyond the first page; returned matches remain bounded.
- The repeated four/eight-message truncation rule is replaced by a context budget that reserves response space and retains earlier user constraints, the latest request, and recent tool evidence. Older discussion is selected by available budget. No extra summarizer model is called.
- Runtime instructions are condensed while retaining Galam identity, its tools, navigation rules, inspection-before-editing, and verification requirements. The same context assembler is used by main chat, side queries, planning, and continuations.
- A small code-symbol outline is automatically included for relevant coding questions. Indexing is scoped, bounded, cached, and respects root `.gitignore`/`.galamignore` patterns. Python uses AST; several other code formats use a lightweight symbol fallback. Dependency/build/log/transcript directories are excluded. This is an outline, not a complete dependency graph.
- Local discovery uses effective context metadata when the server advertises it. Output reservations apply across the existing provider request formats. Counts are conservative estimates, not exact tokenizer measurements.
- Python and JSON edits are validated before application. Replacements reject stale inspected hashes, ambiguous/empty blocks, and invalid syntax; existing line endings are retained.
- Galam-owned edits have recovery records outside the project. `undo_file_change(edit_id)` restores an edit only when the current contents still match its recorded result. The model may use `last` within the current workspace when the user requests undo. Later user edits are preserved by refusing a conflicting undo.

## Optional project verification

The default file checks validate Python syntax and JSON parsing. They do not prove that project tests pass. Galam receives that distinction in the tool result and is instructed to verify the requested behavior.

To schedule a fixed project command after a relevant edit, a project may contain `.galam-checks.json` in the current working directory:

```json
{
  "checks": {
    ".py": ["python -m unittest discover -s tests"],
    ".ts": ["npm run typecheck"]
  }
}
```

Commands run literally from the current session directory through the existing Action review/execution flow. The upgrade does not create this file in projects automatically and does not run arbitrary discovered scripts automatically.

## Storage and recovery

Runtime data defaults to `~/.galam/runtime` (or `GALAM_STATE_DIR` when explicitly set). Output is retained for up to seven days and 500 results. Edit recovery records remain available there until removed. Recovery covers journaled file edits; general shell side effects and package installations are outside that guarantee.

Original source backup and verification evidence: `/root/galam-upgrade-backups/20261020-055736/`. The archive contains the pre-upgrade source files. `hashes.json` supports checking the unchanged UI/configuration files; `verification.json` records a synthetic context comparison, and `live-verification.json` records the live model checks.

## Verification

Run:

```bash
python -m unittest discover -s tests -v
```

The 39 tests cover actual command exit codes, legacy return compatibility, review policies, curl failures, output retrieval, navigation, parser formats, explanatory and incomplete calls, context retention at 4K/8K/32K/128K limits, provider payloads and responses, local context metadata, scoped code outlines, edit conflicts, syntax rejection, recovery, newline preservation, failed-check reruns, configured checks, optional graph dispatch, and read-response recovery.

On a synthetic conversation with long repeated command output, the conservative input estimate decreased from **9,557 to 4,425 tokens**, while the initial API/UI constraint was retained. This is a fixture comparison, not a universal token reduction or speed guarantee. Hosted provider transport formats were tested with simulated responses; those tests do not establish live availability of every provider.

Live verification used the configured local model at `http://127.0.0.1:8080/v1`. Chat returned `5` for `2 + 3`. A separate full agent-loop check read a temporary file through `view_file` and answered its exact value, `79463`; the fixture remained unchanged, exactly one read was executed, and the loop completed without hitting its step limit. The endpoint was initially offline, so the existing bundled server startup path was used. Saved configuration was protected from writes during these checks.

## Faster action acknowledgments

Successful tool results now produce immediate receipts such as “Created …”, “Updated …”, or “Command completed successfully (exit status 0).” For simple completed requests, Galam records the final acknowledgment locally and releases the action loop without asking the model to generate another confirmation.

This applies to explicitly requested successful shell commands, empty-file creation, literal file contents, and exact literal replacements with a matching target. File receipts check the actual contents/hash; configured project checks finish before final completion. Multi-step tasks, complex changes, errors, ambiguous requests, and outstanding checks retain the normal continuation workflow. The UI layout, settings, and review modes are unchanged.

The suite now has **49 passing tests**. A controller comparison using the same successful create-file fixture required **two model requests before the fix and one afterward**. The second request served only to acknowledge completion. Request-count checks cover the normal and optional graph routes and verify that failures and multi-step requests continue. Verification and the pre-change backup are under `/root/galam-upgrade-backups/ack-20261020-171930/`.


## Empty build recovery and numbered change output (2026-10-20)

The latest screenshot and saved tool logs showed a requested radio HTML page was created with `touch`, then reported complete. Build/content creation now counts as a code task; setup commands alone cannot complete it. Empty `write_file` content is rejected for implementation requests, while deliberate empty-file requests remain supported. Code tasks use file tools instead of shell touch/inline writes. Existing empty files may be populated after inspection without requiring a whole-file overwrite instruction. Repeated setup commands also retain the unfinished-task guard.

Functional tool syntax now parses literal Python arguments with AST/tokenization, separating positional command and reason and preserving parentheses/escaped strings. It never evaluates expressions or falls back to executing malformed function text as shell. The canonical JSON tool format remains preferred.

New files, whole-file writes and focused edits share context diffs with independent old/new line numbers, red deletions and green additions. Narrow terminal wrapping counts visible text, preserving indentation and complete color escapes. File change output uses the full retained result and is not cut off at 14 wrapped lines; model feedback still uses bounded output without display color escapes. Existing card layout, menus and settings remain intact.

Verification: 59 pipeline regression tests passed, changed Python modules compile, and saved settings hashes match the pre-change backup. Repaired `/sdcard/cli/radio-technology/radio-technology.html` from the original model response retained in logs: 832 bytes; HTML parser confirms html/head/title/body/h1 tags. Browser rendering and a new live-model build were not exercised in this verification. Restart Galam to load the updated modules. Backup and detailed verification are under the path recorded in `/tmp/galam-empty-backup`.
