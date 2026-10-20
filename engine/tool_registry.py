"""One lightweight argument contract shared by the existing execution paths."""

TOOLS = {
    "make_directory": {"path": str},
    "execute_bash": {"command": str, "reason": str},
    "view_file": {"path": str, "start_line": int, "end_line": int},
    "write_file": {"path": str, "content": str, "overwrite": bool},
    "replace_file_content": {"path": str, "target_content": str, "replacement_content": str},
    "search_files": {"query": str, "directory": str},
    "manage_tasks": {"action": str, "target": str},
    "read_action_output": {"action_id": str, "start_line": int, "end_line": int, "query": str},
    "undo_file_change": {"edit_id": str},
}
REQUIRED = {
    "make_directory": ("path",),
    "execute_bash": ("command",), "view_file": ("path",),
    "write_file": ("path", "content"),
    "replace_file_content": ("path", "target_content", "replacement_content"),
    "search_files": ("query",), "manage_tasks": (),
    "read_action_output": ("action_id",), "undo_file_change": ("edit_id",),
}


def validate_arguments(name, arguments):
    if name not in TOOLS:
        raise ValueError(f"Unknown tool: {name}")
    if not isinstance(arguments, dict):
        raise ValueError(f"{name} requires an argument object")
    result = {}
    for key in REQUIRED[name]:
        if key not in arguments:
            raise ValueError(f"{name} requires {key}")
    for key, value in arguments.items():
        if key.startswith("_"):
            continue  # Internal hashes are added only by the action loop after validation.
        expected = TOOLS[name].get(key)
        if expected is None:
            raise ValueError(f"Unknown argument {key} for {name}")
        if expected is int:
            if isinstance(value, bool):
                raise ValueError(f"{key} must be an integer")
            try:
                value = int(value)
            except (ValueError, TypeError):
                raise ValueError(f"{key} must be an integer") from None
            if value < 1:
                raise ValueError(f"{key} must be positive")
        elif expected is bool:
            if isinstance(value, str) and value.lower() in ("true", "false"):
                value = value.lower() == "true"
            if not isinstance(value, bool):
                raise ValueError(f"{key} must be a boolean")
        elif not isinstance(value, str):
            raise ValueError(f"{key} must be text")
        if key in ("path", "command", "action_id", "edit_id") and not value.strip():
            raise ValueError(f"{key} must not be empty")
        result[key] = value
    return result
