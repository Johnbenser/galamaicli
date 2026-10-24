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


def explicit_report_command(request):
    """Only an explicit command followed by a factual reporting request."""
    request = _request_body(request)
    prefix = re.match(r'^(?:run|execute)\s+(?:(?:this|the|exact|terminal|shell|bash|command|using|execute_bash|galam|action)\b[\s:,-]*)*', request, re.I)
    if not prefix:
        return None
    tail = request[prefix.end():].strip()
    if tail[:1] in ('`','"',"'"):
        end = tail.find(tail[0],1)
        if end<0:
            return None
        command, suffix = tail[1:end], tail[end+1:]
    else:
        parts = re.split(r'\s*\.\s+(?=(?:report|show|return|tell|do not)\b)|\s+(?:and|then)\s+(?=(?:report|show|return|tell)\b)',tail,maxsplit=1,flags=re.I)
        command, suffix = parts[0].rstrip(' .'), parts[1] if len(parts)>1 else ''
    suffix = re.sub(r'\bdo not change any files\b','',suffix,flags=re.I)
    words = re.findall(r'[\w]+',suffix.lower())
    allowed = {'and','then','please','report','show','return','tell','me','the','its','actual','command','terminal',
               'result','results','output','byte','count','exit','status','code','success','failure','whether','it','succeeded','failed'}
    if not command or any(word not in allowed for word in words) or re.search(r'[;&|`$<>]',suffix):
        return None
    from engine.tool_parser import _looks_like_shell_command
    if not _looks_like_shell_command(command) or not _atomic(command,allow_test=True):
        return None
    return command


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


def _simple_listing_receipt(request, event, directory):
    """Answer a one-step read-only folder/file listing from its real command output."""
    request_words = re.sub(r'(?<![\w:])(?:/|~/)[^\s`"\'<>;,]+', ' PATH ', request)
    if not re.search(r'\b(?:list|show|display)\b', request_words, re.I) or not re.search(
            r'\b(?:folders?|directories|files?|contents?)\b', request_words, re.I):
        return None
    if re.search(r'\b(?:create|make|write|edit|modify|change|delete|remove|build|install|start|stop|run|test|verify|deploy)\b',
                 request_words, re.I):
        return None
    command = str(event['args'].get('command', ''))
    # Accept only a plain `ls` (optionally after `cd`); pipes, commands with
    # side effects, and truncated listings need normal model interpretation.
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=';&|<>')
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        return None
    if any(token in (';', '|', '||', '>', '<', '>>') for token in tokens):
        return None
    segments, current = [], []
    for token in tokens + ['&&']:
        if token == '&&':
            if current:
                segments.append(current)
            current = []
        else:
            current.append(token)
    if not segments or sum(segment[0] == 'ls' for segment in segments) != 1 or any(
            segment[0] not in ('cd', 'ls') for segment in segments):
        return None
    if any(segment[0] == 'cd' and len(segment) != 2 for segment in segments):
        return None
    from engine.task_progress import TaskProgress
    if TaskProgress.listing_evidence(command, directory) is None:
        return None

    requested_paths = re.findall(r'(?<![\w:])(?:/|~/)[^\s`"\'<>;,]+', request)
    cwd = Path(directory).expanduser().resolve()
    listed_path = None
    for segment in segments:
        if segment[0] == 'cd':
            cwd = Path(cwd, segment[1]).expanduser().resolve()
        else:
            targets = [value for value in segment[1:] if not value.startswith('-')]
            if len(targets) > 1:
                return None
            listed_path = Path(cwd, targets[0]).expanduser().resolve() if targets else cwd
    if requested_paths:
        requested = Path(requested_paths[-1].rstrip('.,:!?')).expanduser().resolve()
        if requested != listed_path:
            return None

    result = re.sub(r'\x1b\[[0-9;]*m', '', event['result'].output).strip()
    if not result:
        return None
    if (re.search(r'\b(?:folders?|directories)\b', request_words, re.I) and
            not re.search(r'\b(?:files?|contents?)\b', request_words, re.I)):
        folders = []
        for line in result.splitlines():
            fields = line.split(None, 8)
            if len(fields) == 9 and fields[0].startswith('d') and fields[8] not in ('.', '..'):
                folders.append(fields[8])
        if folders:
            label = str(listed_path)
            return f"Folders in `{label}`:\n" + '\n'.join(f'- {name}' for name in folders)
    from engine.result_store import compact_text
    return 'Directory listing completed successfully:\n' + compact_text(result, 2400)


def completed_simple_request(request, events, pending_checks, directory):
    """Return a final receipt only when no task interpretation remains necessary.

    Complex changes, failures, multi-step requests and pending checks keep the
    existing model-driven continuation. No model name or UI preference is used.
    """
    events = [event for event in events if not (event['tool'] == 'ask_user' and event['result'].success
              and event['result'].metadata.get('resolution') in ('answered', 'discovered'))]
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
    listing = _simple_listing_receipt(request, event, directory)
    if listing:
        return listing
    from engine.token_policy import enabled
    from config import CONFIG
    expected = explicit_report_command(request) if enabled(CONFIG, 'receipts') else None
    if (expected and _command_words(expected) == _command_words(event['args'].get('command',''))
            and not event['result'].is_background):
        from engine.result_store import compact_text
        output = compact_text(event['result'].output.strip(),1200)
        summary = action_acknowledgment('execute_bash',event['args'],event['result'])
        return summary + ('\n'+output if output else '')
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
