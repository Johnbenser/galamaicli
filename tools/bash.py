import os
import subprocess
import time
from ui.styles import C_PURPLE, C_WHITE, C_YELLOW, C_RED, C_GREEN, C_GRAY, C_RESET
from ui.dock_layout import request_user_confirmation

MAX_OUTPUT_CHARS = 12000

TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "execute_bash",
            "description": "Execute terminal commands in Termux/Linux. Supports foreground commands, background daemons (&), and filesystem inspection.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The exact shell command to run.",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Short explanation of why this command is being run.",
                    }
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "view_file",
            "description": "Read file contents with line numbers.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to file"},
                    "start_line": {"type": "integer", "description": "1-indexed starting line"},
                    "end_line": {"type": "integer", "description": "1-indexed ending line"}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Create or overwrite a file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Target file path"},
                    "content": {"type": "string", "description": "File text content"},
                    "overwrite": {"type": "boolean", "description": "Overwrite if file exists"}
                },
                "required": ["path", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "replace_file_content",
            "description": "Replace a unique contiguous block of text in an existing file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to target file"},
                    "target_content": {"type": "string", "description": "Exact text block to replace"},
                    "replacement_content": {"type": "string", "description": "New replacement text"}
                },
                "required": ["path", "target_content", "replacement_content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_files",
            "description": "Search codebase with grep or filename pattern.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search pattern or string"},
                    "directory": {"type": "string", "description": "Directory to search from"}
                },
                "required": ["query"]
            }
        }
    }
]

def run_local_command(command: str, reason: str = "", mode: str = "always_proceed") -> str:
    print(f"\n{C_PURPLE}╭─[GALAM ACTION]{C_RESET}")
    if reason:
        print(f"{C_PURPLE}│ Reason:{C_RESET} {reason}")
    print(f"{C_PURPLE}│ Command:{C_RESET} {C_WHITE}{command}{C_RESET}")
    print(f"{C_PURPLE}╰──────────────────────────────────────────{C_RESET}")

    if mode == "review":
        ans = request_user_confirmation(f"{C_YELLOW}Execute on your device? [y/N]: {C_RESET}", default="n", prompt_label="Execute [y/N]> ")
        if ans not in ("y", "yes"):
            print(f"{C_RED}✗ Execution declined by user.{C_RESET}\n")
            return "Notice: User skipped this command run for now. Suggest another approach or ask again later."
    else:
        print(f"{C_GREEN}⚡ Auto-executing (Always Proceed Mode)...{C_RESET}")

    is_background = command.strip().endswith("&")

    try:
        if is_background:
            proc = subprocess.Popen(
                command,
                shell=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                preexec_fn=os.setpgrp if hasattr(os, "setpgrp") else None
            )
            time.sleep(0.5)
            return f"[Background daemon spawned with PID {proc.pid}.]"
        else:
            proc = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=120
            )
            out = proc.stdout + proc.stderr
            trimmed = out.strip()
            if len(trimmed) > MAX_OUTPUT_CHARS:
                trimmed = trimmed[:MAX_OUTPUT_CHARS] + f"\n... [Output truncated to {MAX_OUTPUT_CHARS} characters] ..."
            return trimmed if trimmed else "[Command executed with exit code 0 and no output]"
    except subprocess.TimeoutExpired:
        return "[Error: Command timed out after 120 seconds]"
    except Exception as e:
        return f"[Execution Error: {str(e)}]"

def run_direct_bash(cmd_line: str):
    cmd = cmd_line[1:].strip()
    if not cmd:
        return
    print(f"{C_GRAY}$ {cmd}{C_RESET}")
    try:
        subprocess.run(cmd, shell=True)
    except Exception as e:
        print(f"{C_RED}Command error: {e}{C_RESET}")
    print()
