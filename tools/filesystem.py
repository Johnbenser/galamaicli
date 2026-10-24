import os
import re
import glob
import subprocess
import ast
import json
import stat
import tempfile
import difflib
from typing import Dict, Any
from engine.edit_journal import apply_edit, digest


def code_diff(before, after):
    """Context hunks with independent old/new line numbers, including newline edits."""
    old, new = before.splitlines(keepends=True), after.splitlines(keepends=True)
    rows = ["Changes (old → new line):"]
    for group in difflib.SequenceMatcher(None, old, new, autojunk=False).get_grouped_opcodes(2):
        rows.append(f"@@ -{group[0][1] + 1},{group[-1][2] - group[0][1]} +{group[0][3] + 1},{group[-1][4] - group[0][3]} @@")
        for tag, a, b, c, d in group:
            if tag == "equal":
                rows.extend(f"  {i + 1:4} {c + i - a + 1:4} | {old[i].rstrip(chr(10)).rstrip(chr(13))}" for i in range(a, b))
            if tag in ("replace", "delete"):
                for i in range(a, b):
                    rows.append(f"\033[38;5;203m- {i + 1:4}      | {old[i].rstrip(chr(10)).rstrip(chr(13))}\033[0m")
                    if not old[i].endswith(("\n", "\r")):
                        rows.append("  \\ No newline at end of old file")
            if tag in ("replace", "insert"):
                for i in range(c, d):
                    rows.append(f"\033[38;5;120m+      {i + 1:4} | {new[i].rstrip(chr(10)).rstrip(chr(13))}\033[0m")
                    if not new[i].endswith(("\n", "\r")):
                        rows.append("  \\ No newline at end of new file")
    return "\n".join(rows) if before != after else "[No content change]"


def _validated_atomic_write(path: str, content: str) -> None:
    """Check supported source formats and replace the file only after a complete write."""
    if path.endswith(".py"):
        ast.parse(content, filename=path)
    elif path.endswith(".json"):
        json.loads(content)
    parent = os.path.dirname(path)
    os.makedirs(parent, exist_ok=True)
    old_mode = stat.S_IMODE(os.stat(path).st_mode) if os.path.exists(path) else None
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=parent,
                                         prefix=".galam-", delete=False) as handle:
            temporary = handle.name
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        if old_mode is not None:
            os.chmod(temporary, old_mode)
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)

def view_file(path: str, start_line: int = 1, end_line: int = 200) -> str:
    """Read a segment of a file with line numbers."""
    try:
        start_line = int(re.search(r'\d+', str(start_line)).group(0))
    except Exception:
        start_line = 1
    try:
        end_line = int(re.search(r'\d+', str(end_line)).group(0))
    except Exception:
        end_line = 200
    abs_path = os.path.abspath(os.path.expanduser(path))
    if not os.path.exists(abs_path):
        # Path tolerance for spaces/casing
        p_dir = os.path.dirname(abs_path)
        p_base = os.path.basename(abs_path)
        if os.path.exists(p_dir):
            for e in os.listdir(p_dir):
                if e.strip() == p_base or e.strip().lower() == p_base.lower():
                    abs_path = os.path.join(p_dir, e)
                    break

    if not os.path.exists(abs_path):
        return f"Error: File not found at '{path}' (resolved: '{abs_path}')"
    if os.path.isdir(abs_path):
        return f"Error: '{path}' is a directory, not a file."
    from engine.security import sensitive_path
    if sensitive_path(abs_path):
        return 'Error: Credential files are protected; inspect or configure them locally.'

    try:
        with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()

        total_lines = len(lines)
        start_idx = max(1, start_line) - 1
        end_idx = min(total_lines, end_line)

        if start_idx >= total_lines:
            return f"File '{path}' has {total_lines} lines. Requested start_line {start_line} is beyond file end."

        output_lines = [f"File: {abs_path} (Lines {start_idx + 1}-{end_idx} of {total_lines}):\n"]
        for i in range(start_idx, end_idx):
            output_lines.append(f"{i + 1:4d} | {lines[i]}")

        return "".join(output_lines)
    except Exception as e:
        return f"Error: Reading file '{path}' failed: {str(e)}"

def write_file(path: str, content: str, overwrite: bool = True, expected_hash=None) -> str:
    """Write text content to a file, creating parent directories if needed."""
    abs_path = os.path.abspath(os.path.expanduser(path))
    if os.path.exists(abs_path) and not overwrite:
        return f"Error: File already exists at '{path}'. Set overwrite=true to replace."

    try:
        # Validate before recording or applying any edit.
        if abs_path.endswith(".py"):
            ast.parse(content, filename=abs_path)
        elif abs_path.endswith(".json"):
            json.loads(content)
        before = ""
        if os.path.exists(abs_path):
            with open(abs_path, encoding="utf-8", newline="") as original:
                before = original.read()
        edit_id, changed = apply_edit(abs_path, content, _validated_atomic_write,
                                     expected_hash if expected_hash is not None else (digest(before.encode("utf-8")) if os.path.exists(abs_path) else None))
        size = len(content.encode("utf-8"))
        recovery = f"\n[Edit recovery: {edit_id}]" if changed else "\n[No content change]"
        return f"Successfully wrote {size} bytes to '{abs_path}'.\n" + code_diff(before, content) + recovery + _validation_note(abs_path)
    except Exception as e:
        return f"Error: Writing file '{path}' failed: {str(e)}"

def _validation_note(path):
    suffix = os.path.splitext(path)[1]
    if suffix == ".py":
        return "\nValidation: Python syntax passed. Project tests have not been run by this write."
    if suffix == ".json":
        return "\nValidation: JSON parsing passed. Project tests have not been run by this write."
    return "\nValidation: file saved; no built-in syntax check for this format. Verify with the project's checks."


def replace_file_content(path: str, target_content: str, replacement_content: str, expected_hash=None) -> str:
    """Replace an exact contiguous block of text in an existing file and report line diffs."""
    abs_path = os.path.abspath(os.path.expanduser(path))
    if not os.path.exists(abs_path):
        return f"Error: File not found at '{path}'."

    try:
        with open(abs_path, "r", encoding="utf-8", newline="") as f:
            data = f.read()
        original_hash = digest(data.encode("utf-8"))
        if not target_content:
            return "Error: target_content must be a nonempty unique block."

        # Exact match or normalized newline match
        actual_target = target_content
        if actual_target not in data:
            norm_target = target_content.replace("\r\n", "\n")
            converted = norm_target.replace("\n", "\r\n") if "\r\n" in data else norm_target
            if converted in data:
                actual_target = converted
            else:
                # Allow trailing whitespace only when the complete line block is unique.
                wanted = [line.rstrip() for line in target_content.splitlines()]
                lines = data.splitlines(keepends=True)
                matches = [i for i in range(len(lines) - len(wanted) + 1)
                           if [line.rstrip() for line in lines[i:i + len(wanted)]] == wanted]
                if len(matches) == 1:
                    actual_target = "".join(lines[matches[0]:matches[0] + len(wanted)])
                    if not target_content.endswith(("\n", "\r")):
                        actual_target = actual_target.rstrip("\r\n")

        if actual_target not in data:
            return f"Error: 'target_content' not found in '{path}'. Please ensure exact whitespace and matching lines."

        count = data.count(actual_target)
        if count > 1:
            return f"Error: 'target_content' appears {count} times in '{path}'. Please provide a more specific unique block."

        target_content = actual_target

        char_idx = data.find(target_content)
        start_line = data[:char_idx].count("\n") + 1

        old_lines = target_content.splitlines()
        new_lines = replacement_content.splitlines()

        if "\r\n" in data and "\r\n" not in replacement_content:
            replacement_content = replacement_content.replace("\n", "\r\n")
        new_data = data.replace(target_content, replacement_content, 1)
        if abs_path.endswith(".py"):
            ast.parse(new_data, filename=abs_path)
        elif abs_path.endswith(".json"):
            json.loads(new_data)
        edit_id, changed = apply_edit(abs_path, new_data, _validated_atomic_write,
                                     expected_hash if expected_hash is not None else original_hash)

        recovery = f"\n[Edit recovery: {edit_id}]" if changed else "\n[No content change]"
        return f"Successfully modified '{abs_path}':\n" + code_diff(data, new_data) + recovery + _validation_note(abs_path)
    except Exception as e:
        return f"Error: Replacing content in '{path}' failed: {str(e)}"

def search_files(query: str, directory: str = ".", max_results: int = 50) -> str:
    """Filename globs search names; other queries search project text, then names.

    Runtime transcripts and generated dependency trees are not project context.
    Call view_file explicitly when a diagnostic log is actually needed.
    """
    import fnmatch
    import time
    from pathlib import Path
    from engine.security import sensitive_path

    abs_dir = os.path.abspath(os.path.expanduser(directory))
    if not os.path.isdir(abs_dir):
        return f"Error: Directory not found at '{directory}'."
    max_results = max(1, min(int(max_results), 200))
    excluded = {'.git', '__pycache__', '.ssh', '.aws', 'node_modules', '.venv', 'venv',
                'logs', 'conversations', '.galam_conversations', '.galam', '.galam_state'}
    state_dir = os.environ.get('GALAM_STATE_DIR')
    state_path = Path(state_dir).expanduser().resolve() if state_dir else None
    is_glob = any(char in query for char in '*?[')
    try:
        expression = re.compile(query) if not is_glob else None
    except re.error as error:
        return f"Error: Invalid search expression: {error}"
    names, matches = [], []
    started = time.monotonic()
    partial = False
    # Read authored source before generated bundles/root documents can exhaust
    # the existing result budget. Each directory is searched once.
    source_roots = [os.path.join(abs_dir, name) for name in ('src', 'app', 'lib', 'components')
                    if os.path.isdir(os.path.join(abs_dir, name)) and not os.path.islink(os.path.join(abs_dir, name))] if not is_glob else []
    visited = set()
    def walk():
        for base in source_roots + [abs_dir]:
            for root, dirs, files in os.walk(base, followlinks=False):
                real = os.path.realpath(root)
                if real in visited:
                    dirs[:] = []
                    continue
                visited.add(real)
                yield root, dirs, files
    for root, dirs, files in walk():
        dirs[:] = sorted(name for name in dirs if name not in excluded
                         and not Path(root, name).is_symlink()
                         and Path(root, name).resolve() != state_path)
        for name in sorted(files):
            if time.monotonic() - started > 10:
                partial = True
                break
            path = Path(root, name)
            if not is_glob and (name.endswith(('.chat.history.md', '.input.history')) or name == '.history'):
                continue
            if path.is_symlink() or sensitive_path(path):
                continue
            relative = str(path.relative_to(abs_dir))
            name_matches = (fnmatch.fnmatch(name, query) or fnmatch.fnmatch(relative, query)
                            if is_glob else query.lower() in name.lower())
            if name_matches:
                names.append(str(path))
                if is_glob and len(names) >= max_results:
                    partial = True
                    break
            if is_glob:
                continue
            try:
                if path.stat().st_size > 1_000_000:
                    continue
                with path.open(encoding='utf-8') as source:
                    for number, line in enumerate(source, 1):
                        if '\x00' in line:
                            break
                        if expression.search(line):
                            matches.append(f"{path}:{number}:{line.rstrip()[:1000]}")
                            if len(matches) >= max_results:
                                partial = True
                                break
            except (OSError, UnicodeError):
                continue
            if len(matches) >= max_results:
                break
        if partial:
            break
    found = names[:max_results] if is_glob or not matches else matches
    if found:
        suffix = "\n[Search limited; narrow the directory or query for more.]" if partial else ""
        return f"Matches for '{query}' in {abs_dir}:\n" + "\n".join(found) + suffix
    return f"No matches found for '{query}' in '{abs_dir}'." + (" [Search limited.]" if partial else "")
