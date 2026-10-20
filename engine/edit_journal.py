"""Recover Galam-owned edits without requiring Git or discarding later changes."""

import base64
import hashlib
import json
import os
import stat
import threading
import time
import uuid
from pathlib import Path

from engine.result_store import atomic_bytes, runtime_dir

_LOCK = threading.RLock()


def digest(data):
    return hashlib.sha256(data).hexdigest() if data is not None else None


def apply_edit(path, content, writer, expected_hash=None):
    path = os.path.realpath(path)
    with _LOCK:
        before = Path(path).read_bytes() if os.path.exists(path) else None
        if expected_hash is not None and digest(before) != expected_hash:
            raise ValueError("File changed after inspection; inspect it again before editing.")
        after = content.encode("utf-8")
        if before == after:
            return "", False
        mode = stat.S_IMODE(os.stat(path).st_mode) if before is not None else 0o644
        edit_id = uuid.uuid4().hex
        record = {"id": edit_id, "path": path, "time": time.time(), "before_hash": digest(before),
                  "after_hash": digest(after), "before": base64.b64encode(before).decode() if before is not None else None,
                  "mode": mode, "status": "pending"}
        record_path = runtime_dir("edits") / (edit_id + ".json")
        # Persist recovery information before changing the user's file.
        atomic_bytes(record_path, json.dumps(record).encode())
        try:
            writer(path, content)
        except Exception:
            record["status"] = "failed"
            atomic_bytes(record_path, json.dumps(record).encode())
            raise
        record["status"] = "applied"
        try:
            atomic_bytes(record_path, json.dumps(record).encode())
        except OSError:
            pass  # A pending record is also recoverable by matching the final hash.
        from engine.repo_context import invalidate
        invalidate(path)
        return edit_id, True


def undo_edit(edit_id, directory):
    """Undo one scoped edit only if its written contents are still present."""
    root = os.path.realpath(directory)
    with _LOCK:
        records = []
        for path in runtime_dir("edits").glob("*.json"):
            try:
                record = json.loads(path.read_text())
                in_scope = os.path.commonpath((root, record["path"])) == root
                if in_scope and record["status"] in ("applied", "pending"):
                    records.append((record, path))
            except (OSError, ValueError, KeyError):
                continue
        records.sort(key=lambda item: item[0]["time"], reverse=True)
        selected = next((item for item in records if edit_id == "last" or item[0]["id"] == edit_id), None)
        if selected is None:
            raise ValueError("No matching recoverable edit in the current workspace.")
        record, record_path = selected
        target = Path(record["path"])
        if os.path.realpath(target) != str(target):
            raise ValueError("File path changed; recovery requires manual review.")
        current = target.read_bytes() if target.exists() else None
        if digest(current) != record["after_hash"]:
            raise ValueError("File changed since this edit; refusing to discard later changes.")
        if record["before"] is None:
            target.unlink()
        else:
            atomic_bytes(target, base64.b64decode(record["before"]), record["mode"])
        record["status"] = "undone"
        atomic_bytes(record_path, json.dumps(record).encode())
        from engine.repo_context import invalidate
        invalidate(str(target))
        return f"Successfully undid edit {record['id']} in '{target}'."
