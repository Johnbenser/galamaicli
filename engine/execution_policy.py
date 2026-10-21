"""Execution mode decisions shared by the CLI and settings UI."""

import shlex

MODES = ("REVIEW", "AUTO", "ALWAYS-PROCEED")


def normalize_mode(value):
    mode = str(value or "REVIEW").strip().upper().replace("_", "-").replace(" ", "-")
    return mode if mode in MODES else "REVIEW"


def next_mode(value):
    current = normalize_mode(value)
    return MODES[(MODES.index(current) + 1) % len(MODES)]


def is_safe_read(tool_name, args):
    if tool_name in ("view_file", "search_files", "read_action_output", "security_audit"):
        return True
    if tool_name != "execute_bash":
        return False
    command = str(args.get("command", "")).strip()
    if not command or any(ch in command for ch in ("$", "`", "\n", "\r")):
        return False
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|<>()")
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        return False
    if not tokens or any(token in (";", "&", "|", "||", ">", ">>", "<", "(", ")") for token in tokens):
        return False
    segments = []
    current = []
    for token in tokens:
        if token == "&&":
            if not current:
                return False
            segments.append(current)
            current = []
        else:
            current.append(token)
    if not current:
        return False
    segments.append(current)
    safe_commands = ("ls", "pwd", "whoami", "cat", "head", "tail", "grep", "rg", "which", "cd", "find")
    for segment in segments:
        if segment[0] not in safe_commands:
            return False
        if segment[0] == "find" and any(t in ("-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint", "-fprintf", "-fls") for t in segment[1:]):
            return False
        if segment[0] == "rg" and any(t == "--pre" or t.startswith("--pre=") for t in segment[1:]):
            return False
    return True


def requires_review(mode, tool_name, args):
    from engine.security import review_reason
    if review_reason(tool_name, args):
        return True
    mode = normalize_mode(mode)
    return mode == "REVIEW" or (mode == "AUTO" and not is_safe_read(tool_name, args))
