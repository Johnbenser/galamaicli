"""Keep exact action output outside prompts; expose bounded, retrievable previews."""

import os
import re
import tempfile
import time
from pathlib import Path


def runtime_dir(part):
    root = Path(os.environ.get("GALAM_STATE_DIR", os.path.expanduser("~/.galam/runtime")))
    directory = root / part
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    return directory


def atomic_bytes(path, data, mode=0o600):
    path = Path(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".galam-", delete=False) as handle:
            temporary = handle.name
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def compact_text(text, limit):
    """Keep the start and final errors rather than silently losing the tail."""
    text = str(text)
    if len(text) <= limit:
        return text
    marker = "\n... [middle omitted; full output is stored] ...\n"
    available = max(0, limit - len(marker))
    head = available // 3
    return text[:head] + marker + (text[-(available - head):] if available > head else "")


def store_result(result):
    if result.output_path:
        return result
    try:
        directory = runtime_dir("results")
        path = directory / (result.action_id + ".txt")
        result.metadata["output_lines"] = len(result.output.splitlines())
        atomic_bytes(path, result.output.encode("utf-8", errors="replace"))
        result.output_path = str(path)
        if len(result.output) > 12000:
            result.output = compact_text(result.output, 11600)
            result.output += f"\n[Full output: action {result.action_id}; use read_action_output]"
        # Bound retained output by age and count without touching current results.
        entries = sorted(directory.glob("*.txt"), key=lambda p: p.stat().st_mtime, reverse=True)
        now = time.time()
        for index, old in enumerate(entries):
            if index >= 500 or now - old.stat().st_mtime > 7 * 86400:
                old.unlink(missing_ok=True)
    except OSError:
        # An unavailable output store must not change an already executed action's status.
        result.output = compact_text(result.output, 12000)
    return result


def read_action_output(action_id, start_line=1, end_line=120, query=""):
    if not re.fullmatch(r"[a-f0-9]{32}", str(action_id)):
        raise ValueError("Use the exact action ID from a tool result.")
    path = runtime_dir("results") / (str(action_id) + ".txt")
    if not path.is_file():
        raise ValueError("Action output is unavailable or has expired.")
    start = max(1, int(start_line))
    end = min(max(start, int(end_line)), start + 299)
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    if query:
        selected = [(n, line) for n, line in enumerate(lines, 1)
                    if n >= start and query.lower() in line.lower()][:end - start + 1]
    else:
        selected = [(n, line) for n, line in enumerate(lines, 1) if start <= n <= end]
    return compact_text("\n".join(f"{n}: {line}" for n, line in selected), 12000) or "No matching output lines."
