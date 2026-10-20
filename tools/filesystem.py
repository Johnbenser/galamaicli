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
    """Search for files by pattern or grep inside files."""
    abs_dir = os.path.abspath(os.path.expanduser(directory))
    if not os.path.exists(abs_dir):
        return f"Error: Directory not found at '{directory}'."

    # Try fast ripgrep or grep
    try:
        cmd = ["grep", "-rnI", "--exclude-dir=.git", "--exclude-dir=__pycache__", query, abs_dir]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=10)
        lines = res.stdout.strip().splitlines()
        if lines:
            trimmed = lines[:max_results]
            out = "\n".join(trimmed)
            if len(lines) > max_results:
                out += f"\n... and {len(lines) - max_results} more matches."
            return f"Matches for '{query}' in {abs_dir}:\n{out}"
    except Exception:
        pass

    # Fallback: filename search
    matches = []
    for root, dirs, files in os.walk(abs_dir):
        if ".git" in dirs:
            dirs.remove(".git")
        if "__pycache__" in dirs:
            dirs.remove("__pycache__")
        for f in files:
            if query.lower() in f.lower():
                matches.append(os.path.join(root, f))
                if len(matches) >= max_results:
                    break

    if matches:
        return f"Found matching files in {abs_dir}:\n" + "\n".join(matches)
    return f"No matches found for '{query}' in '{abs_dir}'."
