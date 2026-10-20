"""Optional project checks scheduled through Galam's existing review/execution flow."""

import json
import os
from pathlib import Path


def checks_for_file(path, directory):
    root = Path(os.path.realpath(directory))
    path = Path(os.path.realpath(path))
    try:
        if os.path.commonpath((str(path), str(root))) != str(root):
            return []
        config = root / ".galam-checks.json"
        if not config.is_file() or config.stat().st_size > 16384:
            return []
        data = json.loads(config.read_text())
        commands = data.get("checks", {}).get(path.suffix, [])
        if isinstance(commands, str):
            commands = [commands]
        if not isinstance(commands, list):
            return []
        # Literal commands run from the existing session directory; no unquoted interpolation.
        return list(dict.fromkeys(c for c in commands if isinstance(c, str) and c.strip()))[:4]
    except (OSError, ValueError, AttributeError):
        return []
