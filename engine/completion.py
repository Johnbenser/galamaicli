"""Acknowledge real outcomes locally; skip inference only for proven simple tasks."""

import os
import re
import shlex
import hashlib
from pathlib import Path


def action_acknowledgment(tool, args, result):
    if not result.success:
        if tool == "execute_bash" and result.exit_code is not None:
            return f"Command failed (exit status {result.exit_code})."
        return next((line for line in result.output.splitlines() if line.strip()), "Action incomplete.")
    if tool in ("write_file", "replace_file_content"):
        path = result.metadata.get("path") or args.get("path", "file")
        operation = result.metadata.get("operation", "updated")
        if result.metadata.get("changed") is False:
            text = f"`{path}` already has the requested contents."
        else:
            text = f"{'Created' if operation == 'created' else 'Updated'} `{path}`."
        if result.verified:
            suffix = os.path.splitext(str(path))[1].lower()
            if suffix in (".py", ".json"):
                text += " Python syntax passed." if suffix == ".py" else " JSON validation passed."
        return text
    if tool == "execute_bash":
        if result.is_background:
            return next((line for line in result.output.splitlines() if line.strip()), "Background action completed.")
        return "Command completed successfully (exit status 0)."
    if tool == "undo_file_change":
        return result.output.splitlines()[0]
    return next((line for line in result.output.splitlines() if line.strip()), f"{tool} completed successfully.")


def _command_words(command):
    try:
        return shlex.split(command.strip())
    except ValueError:
        return []


def _request_body(request):
    text = request.strip()
    text = re.sub(r"^(?:(?:please|kindly)\s+|(?:can|could|would)\s+you\s+)+", "", text, flags=re.I)
    # A request to report completion does not add another operation.
    return re.sub(r"\s+(?:and|then)\s+(?:let me know(?: when (?:it(?:'s| is)|you(?:'re| are)) (?:done|finished))?|confirm (?:when (?:done|finished)|it(?:'s| is) done))\s*[.!]?\s*$", "", text, flags=re.I)


def _atomic(request, allow_test=False):
    # Conjunctions inside quoted content are data, not extra requested steps.
    outside = re.sub(r'''`[^`]*`|"[^"]*"|'[^']*' ''', "", request, flags=re.X)
    outside = re.sub(r"[\w./~-]+\.[A-Za-z0-9_]+", "", outside)
    blocked = r"and|then|also|after|before|explain|analyze|review|debug"
    if not allow_test:
        blocked += r"|test|tests|verify|deploy|install"
    return not re.search(r"\b(?:" + blocked + r")\b|[\n;]", outside, re.I)


def _target_mentioned(request, path, directory):
    try:
        words = shlex.split(request.replace("`", '"'))
    except ValueError:
        return False
    wanted = os.path.realpath(path)
    for word in words:
        raw = word.rstrip(".,;:!?")
        if raw.lower() in {"file", "folder", "directory", "script", "document", "text", "new"}:
            continue
        candidate = os.path.expanduser(raw)
        candidate = candidate if os.path.isabs(candidate) else os.path.join(directory, candidate)
        if os.path.realpath(candidate) == wanted:
            return True
    return False


def _quoted_content(request):
    match = re.search(r'''\b(?:containing|with\s+(?:(?:the\s+)?(?:text|content)\s+)?|to\s+(?:say|contain)\s+)\s*([`"'])(.*?)\1\s*[.!]?\s*$''', request, re.I | re.S)
    return match.group(2) if match else None


def _plain_creation(request, path):
    # Creation without supplied content is complete only for an empty file.
    name = re.escape(os.path.basename(path))
    return bool(re.fullmatch(
        r"(?:create|make|touch|write)\s+(?:(?:a|an)\s+)?(?:(?:empty|new)\s+)*(?:file\s+(?:(?:named|called)\s+)?)?"
        r"(?:[`\"']?)(?:[^\s`\"']*/)?" + name + r"(?:[`\"']?)(?:\s+file)?(?:\s+(?:for me|please))?[.!]?", request, re.I))


def completed_simple_request(request, events, pending_checks, directory):
    """Return a final receipt only when no task interpretation remains necessary.

    Complex changes, failures, multi-step requests and pending checks keep the
    existing model-driven continuation. No model name or UI preference is used.
    """
    if pending_checks or not events or not all(event["result"].success for event in events):
        return None
    request = _request_body(request)
    checks = [event for event in events if event.get("verification")]
    edits = [event for event in events if event["tool"] in ("write_file", "replace_file_content")]
    if len(edits) == 1:
        if not _atomic(request):
            return None
        event = edits[0]
        result, args = event["result"], event["args"]
        path = result.metadata.get("path") or args.get("path", "")
        if not _target_mentioned(request, path, directory) or not Path(path).is_file():
            return None
        try:
            content_hash = result.metadata.get("content_hash")
            if content_hash and hashlib.sha256(Path(path).read_bytes()).hexdigest() != content_hash:
                return None
        except OSError:
            return None
        # Other mutations cannot be silently treated as part of one file edit.
        if any(item is not event and not item.get("verification") and item["tool"] not in
               ("view_file", "search_files", "read_action_output") for item in events):
            return None
        if event["tool"] == "write_file":
            if not re.match(r"(?:create|make|touch|write)\b", request, re.I):
                return None
            expected = _quoted_content(request)
            try:
                actual = Path(path).read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                return None
            if expected is None:
                if not _plain_creation(request, path) or actual:
                    return None
            elif actual not in (expected, expected + "\n") or args.get("content") not in (expected, expected + "\n"):
                return None
        else:
            match = re.search(r'''\b(?:replace|change)\s+([`"'])(.*?)\1\s+(?:with|to)\s+([`"'])(.*?)\3''', request, re.I | re.S)
            if not match or (args.get("target_content"), args.get("replacement_content")) != (match.group(2), match.group(4)):
                return None
        summary = action_acknowledgment(event["tool"], args, result)
        if checks:
            summary += f" {len(checks)} configured check(s) passed."
        return summary
    if edits or checks:
        return None
    commands = [event for event in events if event["tool"] == "execute_bash"]
    if len(commands) != 1 or any(event["tool"] not in ("execute_bash", "view_file", "search_files") for event in events):
        return None
    event = commands[0]
    if not _atomic(request, allow_test=True):
        return None
    match = re.fullmatch(r"(?:run|execute)\s+(.+?)\s*[.!]?", request, re.I | re.S)
    if not match:
        return None
    expected = match.group(1).strip()
    if len(expected) > 1 and expected[0] == expected[-1] and expected[0] in "`\"'":
        expected = expected[1:-1]
    if not _command_words(expected) or _command_words(expected) != _command_words(event["args"].get("command", "")):
        return None
    return action_acknowledgment("execute_bash", event["args"], event["result"])
