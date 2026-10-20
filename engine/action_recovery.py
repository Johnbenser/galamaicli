"""Recover an explicitly requested read when a model only announces the action."""

import os
import re
import uuid
import json

from engine.action_results import request_intent


def announces_read(text):
    return bool(re.search(r"\b(?:I\s+(?:am|will)|I'm|I'll)\s+(?:inspect(?:ing)?|read(?:ing)?|view(?:ing)?|check(?:ing)?)\b", text, re.I))


def recover_requested_read(request, response, directory):
    action, mutation = request_intent(request)
    if not action or mutation or not announces_read(response):
        return []
    if not re.search(r"\b(?:read|inspect|view)\b|view_file", request, re.I):
        return []
    matches = re.findall(r'''`([^`]+)`|"([^"]+)"|'([^']+)'|((?:~?/|\.\.?/)?[\w./-]+\.[A-Za-z0-9_]+)''', request)
    paths = set()
    for match in matches:
        raw = next((part for part in match if part), "").rstrip(".,;:")
        raw = os.path.expanduser(raw)
        candidate = raw if os.path.isabs(raw) else os.path.join(directory, raw)
        if os.path.isfile(candidate):
            paths.add(os.path.abspath(candidate))
    # No guessed paths or multi-file selection. Let the model repair ambiguous calls.
    if len(paths) != 1:
        return []
    arguments = {"path": paths.pop()}
    lines = re.search(r"\blines?\s+(\d+)\s*(?:-|to|through)\s*(\d+)", request, re.I)
    if lines:
        arguments.update(start_line=int(lines.group(1)), end_line=int(lines.group(2)))
    return [{"id": f"read_{uuid.uuid4().hex}", "type": "function", "function": {
        "name": "view_file", "arguments": json.dumps(arguments)}}]
