import os
import re
import sys
import time
import json
import ssl
import shlex
import signal
import subprocess
import threading
import urllib.request
import urllib.error
import urllib.parse
import socket
import glob
import difflib
from typing import List, Dict, Tuple, Any, Optional

from tools import filesystem
from config import AGENT_STATE
from engine.logger import log_galam_system_event, log_tool_execution
from engine.action_results import ActionResult, as_result
from engine.result_store import store_result, read_action_output
from engine.tool_registry import validate_arguments

SERVER_RUNNERS = [
    r"(?:python\d?|python3?)\s+(?:[\w\./\-]+/)?(?:server\.py|app\.py|main\.py)",
    r"python\d?\s+-m\s+http\.server(?:\s+\d+)?",
    r"(?:npm|yarn|pnpm|bun)\s+(?:run\s+)?(?:dev|start|serve|preview)",
    r"(?:npx\s+)?(?:vite|next|astro|remix)(?:\s+dev)?",
    r"node\s+(?:[\w\./\-]+/)?(?:server|index|app|main)\.(?:js|mjs|cjs)",
    r"(?:uvicorn|gunicorn|fastapi)\s+[\w\.:]+",
    r"flask\s+run",
    r"(?:npx\s+)?(?:live-server|http-server)(?:\s+\d+)?"
]

EXCLUDED_COMMANDS = (
    "grep", "ps", "kill", "pkill", "cat", "echo",
    "head", "tail", "which", "find", "pgrep", "sleep"
)

class TaskManager:
    def __init__(self):
        self.tasks: List[Dict] = []
        self._lock = threading.Lock()

    def cleanup_dead_tasks(self):
        with self._lock:
            alive = []
            for t in self.tasks:
                proc = t.get("process")
                pid = t.get("pid")
                if proc is not None and proc.poll() is None:
                    try:
                        os.kill(pid, 0)
                        alive.append(t)
                    except OSError:
                        pass
            self.tasks = alive

    def register_task(self, command: str, process: subprocess.Popen, display_cmd: str, port: str = "5500") -> Dict:
        self.cleanup_dead_tasks()
        task_id = len(self.tasks) + 1
        entry = {
            "id": task_id,
            "cmd": display_cmd,
            "full_cmd": command,
            "start_time": time.strftime("%H:%M:%S"),
            "pid": process.pid,
            "port": port,
            "process": process
        }
        with self._lock:
            self.tasks.append(entry)
        return entry

    def stop_task(self, target) -> bool:
        target_str = str(target).lower().strip()
        with self._lock:
            for t in list(self.tasks):
                match = (
                    str(t.get("id")) == target_str or
                    str(t.get("pid")) == target_str or
                    target_str in str(t.get("cmd", "")).lower() or
                    target_str in str(t.get("full_cmd", "")).lower() or
                    target_str in str(t.get("port", "")).lower() or
                    (target_str in ("py", "python") and any(k in str(t.get("full_cmd", "")).lower() for k in ("python", "python3", "py "))) or
                    (target_str in ("react", "vite", "next", "node") and any(k in str(t.get("full_cmd", "")).lower() for k in ("node", "vite", "next", "npm", "yarn", "bun", "pnpm")))
                )
                if match:
                    proc = t.get("process")
                    pid = t.get("pid")
                    try:
                        os.killpg(os.getpgid(pid), signal.SIGTERM)
                    except Exception:
                        try:
                            if proc:
                                proc.terminate()
                            os.kill(pid, signal.SIGKILL)
                        except Exception:
                            pass
                    t["process"] = None
                    self.tasks.remove(t)
                    return True

        # External fallback: if target is a known framework stack
        external_targets = {
            "python": "python",
            "py": "python",
            "react": "vite",
            "vite": "vite",
            "next": "next",
            "node": "node"
        }
        if target_str in external_targets:
            proc_pattern = external_targets[target_str]
            # NEVER kill python globally as it terminates Galam CLI itself!
            if proc_pattern == "python":
                # Only terminate python server scripts
                freed = False
                for p in glob.glob("/proc/[0-9]*"):
                    pid_str = os.path.basename(p)
                    try:
                        p_int = int(pid_str)
                        if p_int in (os.getpid(), os.getppid()):
                            continue
                        with open(f"{p}/cmdline", "rb") as f:
                            raw_cmd = f.read().decode("utf-8", errors="replace").replace("\x00", " ")
                        if not raw_cmd.strip() or "galam.py" in raw_cmd:
                            continue
                        if any(k in raw_cmd for k in ("http.server", "server.py", "app.py")):
                            os.kill(p_int, signal.SIGTERM)
                            freed = True
                    except Exception:
                        pass
                return freed
            else:
                res = subprocess.run(f"pkill -f {proc_pattern}", shell=True)
                return res.returncode == 0

        # External fallback: if target is a port number
        if target_str.isdigit() and len(target_str) >= 4:
            freed = self.free_port(int(target_str))
            res = subprocess.run(f"fuser -k -n tcp {target_str} 2>/dev/null", shell=True)
            return freed or res.returncode == 0

        # External fallback: if target is a PID number
        if target_str.isdigit():
            try:
                os.kill(int(target_str), signal.SIGTERM)
                return True
            except Exception:
                pass

        return False

    def is_port_in_use(self, port: int) -> bool:
        """Checks if a TCP port is currently bound or in use."""
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(('0.0.0.0', port))
            s.close()
            return False
        except OSError:
            s.close()
            return True

    def find_conflicting_pids(self, port: str = "5500", cmd_hint: str = "") -> List[int]:
        """Scans /proc to identify orphaned server processes holding a port or matching a server command."""
        pids = []
        my_pid = os.getpid()
        clean_hint = os.path.basename(cmd_hint.strip().split()[-1]) if cmd_hint else ""
        for p in glob.glob("/proc/[0-9]*"):
            pid_str = os.path.basename(p)
            try:
                p_int = int(pid_str)
                if p_int == my_pid:
                    continue
                with open(f"{p}/cmdline", "rb") as f:
                    raw_cmd = f.read().decode("utf-8", errors="replace").replace("\x00", " ")
                if not raw_cmd.strip() or "galam.py" in raw_cmd:
                    continue
                # Match by script name, port parameter, or server runner
                matches_port = (
                    f"--port {port}" in raw_cmd or
                    f"-p {port}" in raw_cmd or
                    f" {port} " in raw_cmd or
                    raw_cmd.rstrip().endswith(f" {port}")
                )
                matches_hint = bool(clean_hint and len(clean_hint) > 3 and clean_hint in raw_cmd)
                matches_known_server = any(k in raw_cmd for k in ("http.server", "server.py", "app.py", "vite", "next dev"))
                
                if matches_port or matches_hint or (str(port) in ("5500", "8080", "3000") and matches_known_server):
                    pids.append(p_int)
            except Exception:
                pass
        return pids

    def free_port(self, port: int, cmd_hint: str = "") -> bool:
        """Terminates any background process (internal or external) occupying the specified port."""
        freed = False
        # 1. Stop internal tasks on this port
        with self._lock:
            for t in list(self.tasks):
                if str(t.get("port")) == str(port) or (cmd_hint and cmd_hint in str(t.get("full_cmd"))):
                    pid = t.get("pid")
                    try:
                        os.killpg(os.getpgid(pid), signal.SIGKILL)
                    except Exception:
                        try:
                            os.kill(pid, signal.SIGKILL)
                        except Exception:
                            pass
                    self.tasks.remove(t)
                    freed = True

        # 2. Stop external orphaned processes via /proc scan
        conflicting = self.find_conflicting_pids(str(port), cmd_hint)
        for c_pid in conflicting:
            try:
                os.kill(c_pid, signal.SIGTERM)
                time.sleep(0.1)
                os.kill(c_pid, signal.SIGKILL)
                freed = True
            except OSError:
                pass

        # 3. Termux fuser fallback
        subprocess.run(f"fuser -k -n tcp {port} 2>/dev/null", shell=True)
        time.sleep(0.15)
        return freed

    def stop_all(self):
        with self._lock:
            for t in list(self.tasks):
                pid = t.get("pid")
                try:
                    os.killpg(os.getpgid(pid), signal.SIGKILL)
                except Exception:
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except Exception:
                        pass
            self.tasks.clear()

    def sync_external_kill(self, raw_cmd: str):
        """Removes background tasks when a shell kill command targets their PID."""
        pids = re.findall(r'\b\d{3,6}\b', raw_cmd)
        for p in pids:
            self.stop_task(p)

    def get_active_tasks(self) -> List[Dict]:
        self.cleanup_dead_tasks()
        with self._lock:
            active_list = list(self.tasks)
            # Solid persistence: Also discover any background servers running on the system
            # (e.g. if started before CLI restart or unmonitored child process)
            existing_pids = {t.get("pid") for t in active_list}
            my_pid = os.getpid()
            for p in glob.glob("/proc/[0-9]*"):
                pid_str = os.path.basename(p)
                try:
                    pid_int = int(pid_str)
                    if pid_int in existing_pids or pid_int == my_pid:
                        continue
                    with open(f"{p}/cmdline", "rb") as f:
                        raw_cmd = f.read().decode("utf-8", errors="replace").replace("\x00", " ").strip()
                    if not raw_cmd or "galam.py" in raw_cmd:
                        continue
                    if any(k in raw_cmd for k in ("http.server", "server.py", "app.py", "vite", "next dev", "uvicorn", "flask run")):
                        disp_cmd, port = self.extract_server_target(raw_cmd)
                        discovered = {
                            "id": len(self.tasks) + 1,
                            "cmd": disp_cmd,
                            "full_cmd": raw_cmd,
                            "start_time": "background",
                            "pid": pid_int,
                            "port": port,
                            "process": None
                        }
                        self.tasks.append(discovered)
                        existing_pids.add(pid_int)
                        active_list.append(discovered)
                except Exception:
                    pass
            return active_list

    def is_server_command(self, cmd: str) -> bool:
        clean = cmd.strip()
        lines = [l.strip() for l in clean.splitlines() if l.strip()]
        for line in lines:
            first_word = line.split()[0].lower() if line.split() else ""
            if first_word in EXCLUDED_COMMANDS:
                continue
            for pat in SERVER_RUNNERS:
                if re.search(pat, line, re.IGNORECASE):
                    return True
            if line.endswith("&") and first_word not in EXCLUDED_COMMANDS:
                return True
        return False

    def extract_server_target(self, cmd: str) -> Tuple[str, str]:
        """Extracts the display name (e.g. python3 -m http.server 8080) and port."""
        port = "5500"
        port_match = re.search(r'(?:--port|-p|\bport\s*[:=]?)\s*(\d{3,5})', cmd, re.IGNORECASE)
        if port_match:
            port = port_match.group(1)
        else:
            num_match = re.search(r'\b([1-9]\d{3,4})\b', cmd)
            if num_match:
                port = num_match.group(1)
            elif "8080" in cmd:
                port = "8080"
            elif "3000" in cmd:
                port = "3000"
            elif "5500" in cmd:
                port = "5500"

        # Search for the actual server runner sub-command match
        display_cmd = ""
        for pat in SERVER_RUNNERS:
            m = re.search(pat, cmd, re.IGNORECASE)
            if m:
                matched_str = m.group(0).strip()
                p_after = re.search(r'\b\d{3,5}\b', cmd[m.end():m.end()+15])
                if p_after and port == "5500":
                    port = p_after.group(0)
                display_cmd = matched_str
                break

        if not display_cmd:
            lines = [l.strip() for l in cmd.strip().splitlines() if l.strip()]
            display_cmd = lines[-1] if lines else "server"

        return display_cmd, port

    def get_task_pill_tokens(self) -> list:
        tasks = self.get_active_tasks()
        if not tasks:
            return []
        latest = tasks[-1]
        time_str = latest.get("start_time", "background")
        cmd_str = latest.get("cmd", "server")

        return [
            ("fg:#facc15 bold", "• "),
            ("fg:#94a3b8", f"[{time_str}] "),
            ("fg:#818cf8 bold", f"{cmd_str} "),
            ("fg:#4ade80", "running\n")
        ]

GLOBAL_TASK_MANAGER = TaskManager()

def print_server_card(display_cmd: str, port: str, pid: int):
    """Renders the server running acknowledgment card matching AGY."""
    entry_point = display_cmd.strip()
    if " " in entry_point and not ("-m " in entry_point):
        parts = entry_point.split()
        entry_point = parts[-1] if not parts[-1].isdigit() else parts[0]
    card = f"""
\033[1;37mYour server from {entry_point} is now up and running in the background:\033[0m

  \033[38;5;120m• URL:\033[0m         \033[4;38;5;75mhttp://127.0.0.1:{port}\033[0m
  \033[38;5;120m• Entry point:\033[0m \033[1;37m{entry_point}\033[0m
  \033[38;5;120m• Status:\033[0m      \033[1;32mActive (PID {pid})\033[0m \033[38;5;244m· Running in background\033[0m
  \033[38;5;244m• Control:\033[0m     Use \033[1;36m/tasks\033[0m to inspect or terminate
"""
    sys.__stdout__.write(card + "\n")
    sys.__stdout__.flush()

def format_action_step(tool_name: str, args: Dict[str, Any]) -> str:
    """Formats an Antigravity-style action status line."""
    if tool_name == "execute_bash":
        cmd = args.get("command", "").strip()
        first_line = cmd.splitlines()[0] if cmd else "shell"
        if first_line.startswith("ls "):
            return f"\033[38;5;120m• Ran\033[0m \033[38;5;250m({first_line})\033[0m"
        elif first_line.startswith("cat ") or first_line.startswith("head ") or first_line.startswith("grep "):
            target = first_line.split()[-1]
            return f"\033[38;5;120m• Explored 1 file\033[0m \033[38;5;250m({target})\033[0m"
        elif GLOBAL_TASK_MANAGER.is_server_command(cmd):
            disp, _ = GLOBAL_TASK_MANAGER.extract_server_target(cmd)
            return f"\033[38;5;120m• Running\033[0m \033[38;5;250m({disp})\033[0m"
        return f"\033[38;5;120m• Ran\033[0m \033[38;5;250m({first_line})\033[0m"
    elif tool_name == "view_file":
        p = args.get("path", "")
        return f"\033[38;5;120m• Viewed\033[0m \033[38;5;250m({p})\033[0m"
    elif tool_name == "write_file":
        p = args.get("path", "")
        return f"\033[38;5;120m• Created file\033[0m \033[38;5;250m({p})\033[0m"
    elif tool_name == "replace_file_content":
        p = args.get("path", "")
        return f"\033[38;5;120m• Edited file\033[0m \033[38;5;250m({p})\033[0m"
    elif tool_name == "search_files":
        q = args.get("query", "")
        return f"\033[38;5;120m• Searched codebase\033[0m \033[38;5;250m('{q}')\033[0m"
    elif tool_name == "manage_tasks":
        act = args.get("action", "manage")
        tgt = args.get("target", "tasks")
        return f"\033[38;5;120m• Task Control\033[0m \033[38;5;250m({act} {tgt})\033[0m"
    return f"\033[38;5;120m• Executed\033[0m \033[38;5;250m{tool_name}\033[0m"

def _execute_tool_call_legacy(tool_name: str, args: Dict[str, Any], invoking_model: str = "") -> Tuple[str, bool]:
    """Dispatches any registered tool call safely with telemetry logging."""
    start_t = time.time()
    out = ""
    is_bg = False
    success = True
    exit_code = 0

    try:
        if tool_name == "execute_bash":
            cmd = args.get("command", "").strip()
            out, is_bg = execute_shell_action(cmd)
            # Detect error signals in output
            if any(err_kw in out.lower() for err_kw in ("[error:", "[execution error:", "[failed to start", "[exit status ", "command not found", "permission denied")):
                success = False
                exit_code = 1
        elif tool_name == "manage_tasks":
            action = str(args.get("action", "list")).strip().lower()
            target = str(args.get("target", "all")).strip()
            if action == "list":
                tasks = GLOBAL_TASK_MANAGER.get_active_tasks()
                if not tasks:
                    out = "No active background tasks running in Galam.ai."
                else:
                    lines = ["Active Background Tasks in Galam.ai:"]
                    for t in tasks:
                        lines.append(f"  - [Task ID {t['id']}] PID {t['pid']} · Command: `{t['cmd']}` (Port: {t.get('port', '5500')})")
                    out = "\n".join(lines)
            elif action in ("stop", "kill", "terminate"):
                if target.lower() in ("all", "*"):
                    count = len(GLOBAL_TASK_MANAGER.get_active_tasks())
                    GLOBAL_TASK_MANAGER.stop_all()
                    out = f"Successfully stopped all background tasks ({count} task(s) terminated)."
                else:
                    ok = GLOBAL_TASK_MANAGER.stop_task(target)
                    if ok:
                        out = f"Successfully stopped background task {target}."
                    else:
                        try:
                            os.kill(int(target), signal.SIGTERM)
                            out = f"Successfully sent SIGTERM to external process PID {target}."
                        except Exception:
                            out = f"Task/PID {target} not found in Galam.ai active tasks or already stopped."
                            success = False
                            exit_code = 1
            else:
                out = f"Unknown manage_tasks action: '{action}'"
                success = False
                exit_code = 1
        elif tool_name == "view_file":
            path = str(args.get("path", "")).strip()
            path = re.sub(r'<[^>]*>', '', path).strip().strip("'\"")
            s_raw = str(args.get("start_line", 1))
            e_raw = str(args.get("end_line", 200))
            s_m = re.search(r'\d+', re.sub(r'<[^>]*>', '', s_raw))
            e_m = re.search(r'\d+', re.sub(r'<[^>]*>', '', e_raw))
            s = int(s_m.group(0)) if s_m else 1
            e = int(e_m.group(0)) if e_m else 200
            out = filesystem.view_file(resolve_session_path(path), s, e)
            if out.startswith("Error:"):
                success = False
                exit_code = 1
        elif tool_name == "write_file":
            path = args.get("path", "")
            content = args.get("content", "")
            ov = bool(args.get("overwrite", True))
            out = filesystem.write_file(resolve_session_path(path), content, ov)
            if out.startswith("Error:"):
                success = False
                exit_code = 1
        elif tool_name == "replace_file_content":
            path = args.get("path", "")
            target = args.get("target_content", "")
            repl = args.get("replacement_content", "")
            out = filesystem.replace_file_content(resolve_session_path(path), target, repl)
            if out.startswith("Error:"):
                success = False
                exit_code = 1
        elif tool_name == "search_files":
            query = args.get("query", "")
            directory = args.get("directory", ".")
            try:
                out = filesystem.search_files(query, resolve_session_path(directory))
            except Exception as e:
                out = f"Error executing search_files: {e}"
                success = False
                exit_code = 1
        else:
            log_galam_system_event(
                event_type="UNKNOWN_TOOL_INVOCATION",
                summary=f"Model invoked unrecognized tool: {tool_name}",
                details={"tool_name": tool_name, "args": args}
            )
            out = f"Error: Unknown tool '{tool_name}'"
            success = False
            exit_code = 1
    except Exception as ex:
        out = f"Execution exception: {ex}"
        success = False
        exit_code = 1

    return ActionResult(out, success, exit_code, is_bg, "failed" if not success else "running" if is_bg else "completed")


def execute_tool_call(tool_name: str, args: Dict[str, Any], invoking_model: str = ""):
    """One result contract; existing callers can still unpack (output, background)."""
    started = time.monotonic()
    expected_hash = args.get("_expected_hash") if isinstance(args, dict) else None
    try:
        args = validate_arguments(tool_name, args)
        if tool_name == "execute_bash":
            result = as_result(execute_shell_action(args["command"]))
        elif tool_name == "read_action_output":
            result = ActionResult(read_action_output(**args))
        elif tool_name == "undo_file_change":
            from engine.edit_journal import undo_edit
            result = ActionResult(undo_edit(args["edit_id"], session_directory()), verified=True)
        elif tool_name in ("write_file", "replace_file_content"):
            path = resolve_session_path(args["path"])
            existed = os.path.isfile(path)
            options = dict(args, path=path, expected_hash=expected_hash)
            out = getattr(filesystem, tool_name)(**options)
            result = as_result(out)
            if result.success:
                match = re.search(r"\[Edit recovery: ([a-f0-9]{32})\]", out)
                result.verified = path.endswith((".py", ".json"))
                result.metadata = {"path": path, "edit_id": match.group(1) if match else "", "changed": bool(match),
                                   "operation": "updated" if existed else "created"}
                try:
                    from engine.edit_journal import digest
                    with open(path, "rb") as written:
                        result.metadata["content_hash"] = digest(written.read())
                except OSError:
                    pass
                if match:
                    from engine.project_checks import checks_for_file
                    result.checks = checks_for_file(path, session_directory())
        else:
            result = as_result(_execute_tool_call_legacy(tool_name, args, invoking_model))
    except Exception as error:
        result = ActionResult(f"Error: {error}", False, 1, status="failed")
    result = store_result(result)
    log_tool_execution(tool_name=tool_name, args=args, stdout_preview=result.output,
                       duration_ms=(time.monotonic() - started) * 1000,
                       success=result.success, exit_code=result.exit_code,
                       model_name=invoking_model)
    return result

def get_subprocess_env() -> dict:
    """Returns an enriched environment dictionary ensuring $API_KEYS and all endpoint keys are available."""
    env = os.environ.copy()
    if "API_KEYS" in env and "API_KEY" not in env:
        env["API_KEY"] = env["API_KEYS"]
    elif "API_KEY" in env and "API_KEYS" not in env:
        env["API_KEYS"] = env["API_KEY"]
    return env

def python_curl(url: str, method: str = "GET", headers: dict = None, data: Any = None, timeout: int = 45) -> Tuple[str, int]:
    """
    Pure Python standard-library curl implementation via urllib.request.
    Eliminates dependency on the external curl binary in Termux, bypassing
    missing binaries, certifi mismatches, and connection issues.
    """
    headers = headers or {}
    req_data = None
    if data is not None:
        if isinstance(data, dict):
            req_data = json.dumps(data).encode("utf-8")
            if "Content-Type" not in headers:
                headers["Content-Type"] = "application/json"
        elif isinstance(data, str):
            req_data = data.encode("utf-8")
        elif isinstance(data, bytes):
            req_data = data

    req = urllib.request.Request(url, data=req_data, headers=headers, method=method.upper())

    # Try verified SSL first
    try:
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return body, resp.status
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace") if hasattr(e, "read") else str(e)
        return f"[HTTP {e.code}]: {err_body}", e.code
    except Exception:
        # Fallback to unverified SSL (essential in Termux when root certificates are not present)
        try:
            ctx_unverified = ssl._create_unverified_context()
            req2 = urllib.request.Request(url, data=req_data, headers=headers, method=method.upper())
            with urllib.request.urlopen(req2, timeout=timeout, context=ctx_unverified) as resp:
                body = resp.read().decode("utf-8", errors="replace")
                return body, resp.status
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace") if hasattr(e, "read") else str(e)
            return f"[HTTP {e.code}]: {err_body}", e.code
        except Exception as ex2:
            return f"[Python Curl Error: {ex2}]", -1

def parse_and_run_curl_command(cmd_str: str) -> Optional[Tuple[str, bool]]:
    """
    Parses a curl command string, expands shell variables ($API_KEYS, etc.),
    and executes via Python standard library urllib.request.
    """
    try:
        env = get_subprocess_env()
        expanded_cmd = os.path.expandvars(cmd_str)
        for k, v in env.items():
            if f"${k}" in cmd_str:
                expanded_cmd = expanded_cmd.replace(f"${k}", v)
            if f"${{{k}}}" in cmd_str:
                expanded_cmd = expanded_cmd.replace(f"${{{k}}}", v)

        tokens = shlex.split(expanded_cmd)
        if not tokens or tokens[0] != "curl":
            return None

        method = "GET"
        headers = {}
        data = None
        url = None
        i = 1

        while i < len(tokens):
            tok = tokens[i]
            if tok in ("-X", "--request") and i + 1 < len(tokens):
                method = tokens[i+1].upper()
                i += 2
            elif tok in ("-H", "--header") and i + 1 < len(tokens):
                h_val = tokens[i+1]
                if ":" in h_val:
                    hk, _, hv = h_val.partition(":")
                    headers[hk.strip()] = hv.strip()
                i += 2
            elif tok in ("-d", "--data", "--data-raw", "--data-binary") and i + 1 < len(tokens):
                data = tokens[i+1]
                if method == "GET":
                    method = "POST"
                i += 2
            elif tok == "--json" and i + 1 < len(tokens):
                data = tokens[i+1]
                headers["Content-Type"] = "application/json"
                if method == "GET":
                    method = "POST"
                i += 2
            elif tok in ("-s", "-sS", "-k", "--insecure", "-L", "--location", "-v"):
                i += 1
            elif not tok.startswith("-") and url is None:
                url = tok
                i += 1
            else:
                i += 1

        if not url:
            return None

        body, status = python_curl(url=url, method=method, headers=headers, data=data)
        is_err = status != 200 and not (200 <= status < 300)
        return body, is_err
    except Exception as e:
        return f"[Python Curl Parse Error: {e}]", False

def session_directory() -> str:
    directory = AGENT_STATE.get("working_directory") or os.getcwd()
    if not os.path.isdir(directory):
        directory = os.getcwd()
        AGENT_STATE["working_directory"] = directory
    return os.path.abspath(directory)


def resolve_session_path(path: str) -> str:
    path = os.path.expanduser(str(path or "."))
    return path if os.path.isabs(path) else os.path.normpath(os.path.join(session_directory(), path))


def resolve_navigation_path(path: str) -> str:
    """Treat a missing /child as the named child of the last visited directory."""
    expanded = os.path.expanduser(path)
    target = resolve_session_path(expanded)
    if os.path.isdir(target):
        return target
    if os.path.isabs(expanded):
        candidate = os.path.normpath(os.path.join(session_directory(), expanded.lstrip("/")))
        if os.path.isdir(candidate):
            return candidate
    return target


def workspace_snapshot(directory: str = None, limit: int = 60) -> str:
    """Give the model current, bounded filesystem facts instead of guessed filenames."""
    directory = directory or session_directory()
    try:
        entries = sorted(os.scandir(directory), key=lambda entry: entry.name.lower())
    except OSError as exc:
        return f"Cannot inspect {directory}: {exc}"
    listed = [f"{'dir' if entry.is_dir(follow_symlinks=False) else 'file'} {entry.name}"
              for entry in entries[:limit]]
    remaining = len(entries) - len(listed)
    suffix = f"\n... {remaining} more entries; inspect this directory for the rest." if remaining else ""
    parent = os.path.dirname(os.path.abspath(directory))
    nearby = []
    try:
        nearby = sorted(entry.name for entry in os.scandir(parent)
                        if entry.is_dir(follow_symlinks=False) and not entry.name.startswith("."))[:40]
    except OSError:
        pass
    sibling_context = "\nNearby directories: " + ", ".join(nearby) if nearby else ""
    return f"Current directory: {directory}\nEntries ({len(entries)} total):\n" + "\n".join(listed) + suffix + sibling_context


def find_project_directory(description: str, start: str = None) -> Optional[str]:
    """Resolve a named project using actual directories near the current workspace."""
    start = os.path.abspath(start or session_directory())
    words = re.findall(r"[a-z0-9]+", description.lower())
    stop = {"my", "the", "a", "an", "site", "website", "project", "app", "folder",
            "directory", "i", "made", "built", "created", "for", "me", "please", "there"}
    terms = {"ticket" if word.startswith("ticket") else word.rstrip("s")
             for word in words if word not in stop and len(word) > 2}
    if not terms:
        return None
    roots = [start, os.path.dirname(start)]
    candidates = {}
    for root in roots:
        try:
            entries = os.scandir(root)
        except OSError:
            continue
        with entries:
            for entry in entries:
                if not entry.is_dir(follow_symlinks=False) or entry.name.startswith("."):
                    continue
                path = os.path.abspath(entry.path)
                name_words = {"ticket" if word.startswith("ticket") else word.rstrip("s")
                              for word in re.findall(r"[a-z0-9]+", entry.name.lower())}
                overlap = len(terms & name_words)
                if not overlap:
                    continue
                score = overlap * 5 - len(name_words - terms)
                index_path = os.path.join(path, "index.html")
                if os.path.isfile(index_path) and os.path.getsize(index_path) > 0:
                    score += 1
                if any(os.path.isfile(os.path.join(path, filename))
                       for filename in ("server.py", "app.py", "main.py", "package.json")):
                    score += 3
                if os.path.isfile(os.path.join(path, "README.md")):
                    score += 1
                candidates[os.path.realpath(path)] = (score, path)
    ranked = sorted(candidates.values(), key=lambda item: item[0], reverse=True)
    if not ranked or ranked[0][0] < 4:
        return None
    if len(ranked) > 1 and ranked[0][0] == ranked[1][0]:
        return None
    return ranked[0][1]


def preflight_shell_command(command: str) -> Tuple[bool, str]:
    """Catch guessed navigation and Python entrypoints before asking for approval."""
    prepared, navigation = _prepare_navigation(command.strip())
    if prepared is None:
        return False, navigation
    directory = navigation or session_directory()
    for match in re.finditer(r'(?:^|&&|;)\s*(?:python|python3|python3\.\d+)\s+(?!-)([^\s;&|]+\.py)(?=\s|$)', prepared):
        token = match.group(1).strip("'\"")
        path = token if os.path.isabs(token) else os.path.join(directory, token)
        if not os.path.isfile(path):
            return False, f"Python entry file does not exist: {path}"
    for match in re.finditer(r'(?:^|&&|;)\s*(?:npm|pnpm|yarn|bun)\s+(?:run\s+)?(dev|start|serve|preview)(?=\s|$)', prepared):
        package_path = os.path.join(directory, "package.json")
        if not os.path.isfile(package_path):
            return False, f"package.json does not exist in {directory}"
        try:
            with open(package_path, encoding="utf-8") as package_file:
                scripts = json.load(package_file).get("scripts", {})
        except (OSError, ValueError, TypeError) as exc:
            return False, f"Cannot read scripts from {package_path}: {exc}"
        if match.group(1) not in scripts:
            return False, f"npm script {match.group(1)!r} does not exist in {package_path}. Available: {', '.join(scripts) or 'none'}"
    return True, ""


_LEADING_CD = re.compile(r'^\s*cd\s+(?:--\s+)?(?P<path>\'[^\']*\'|"[^"]*"|[^\s;&|]+)(?=\s*(?:&&|;|$))')


def _prepare_navigation(command: str):
    match = _LEADING_CD.match(command)
    if not match:
        return command, None
    raw = match.group("path")
    try:
        token = shlex.split(raw)[0]
    except (ValueError, IndexError):
        return command, None
    target = resolve_navigation_path(token)
    if not os.path.isdir(target):
        parent = os.path.dirname(target)
        names = os.listdir(parent) if os.path.isdir(parent) else []
        nearby = difflib.get_close_matches(os.path.basename(target), names, n=3, cutoff=0.55)
        hint = (" Nearby directories: " + ", ".join(os.path.join(parent, name) for name in nearby)) if nearby else ""
        return None, f"[Error: Directory does not exist: {target}.{hint}]"
    replacement = "cd -- " + shlex.quote(target)
    return replacement + command[match.end():], target


def execute_shell_action(cmd: str):
    return store_result(as_result(_execute_shell_action_legacy(cmd)))


def _execute_shell_action_legacy(cmd: str) -> Tuple[str, bool]:
    clean_cmd = cmd.strip()
    if not clean_cmd:
        return "", False

    clean_cmd, navigation_target = _prepare_navigation(clean_cmd)
    if clean_cmd is None:
        return navigation_target, False
    run_cwd = session_directory()

    if clean_cmd.startswith("kill ") or clean_cmd.startswith("pkill "):
        # CRITICAL SAFETY: Prevent suicidal kill commands that terminate Galam.ai itself
        my_pid = str(os.getpid())
        ppid = str(os.getppid())
        tokens = clean_cmd.split()
        if any(t in (my_pid, ppid) for t in tokens):
            return "[Safety guard: Ignored kill targeting Galam CLI's own process]", False
        if clean_cmd in ("pkill python", "pkill python3", "pkill -f python", "pkill -9 python"):
            # Redirect to safe server termination
            GLOBAL_TASK_MANAGER.stop_task("python")
            return "[Safely terminated background Python server processes without affecting Galam CLI]", True
        GLOBAL_TASK_MANAGER.sync_external_kill(clean_cmd)

    if clean_cmd.startswith("termux-setup-storage"):
        if os.path.exists("/sdcard"):
            return "[Storage access already active: /sdcard is mounted and accessible]", False
        try:
            subprocess.run(clean_cmd, shell=True, env=get_subprocess_env(), stdin=subprocess.DEVNULL, timeout=4)
            return "[termux-setup-storage initiated. Please approve Android permission prompt if shown.]", False
        except Exception:
            return "[Storage permission command completed]", False

    sub_env = get_subprocess_env()

    if GLOBAL_TASK_MANAGER.is_server_command(clean_cmd):
        disp_cmd, port = GLOBAL_TASK_MANAGER.extract_server_target(clean_cmd)
        bg_cmd = re.sub(r'\s*&\s*$', '', clean_cmd).strip()

        # Detect working directory hint (e.g. if command mentions a folder like /sdcard/cli/jbnsrr-portfolio)
        server_cwd = navigation_target or run_cwd
        path_in_cmd = re.search(r'(/[\w\.\-/]+)', clean_cmd)
        if path_in_cmd:
            cand_p = path_in_cmd.group(1).rstrip("/")
            if os.path.isdir(cand_p):
                server_cwd = cand_p
            elif clean_cmd.startswith("cd ") or clean_cmd.startswith("cd -- "):
                parent = os.path.dirname(cand_p)
                suggestions = []
                if os.path.isdir(parent):
                    names = [name for name in os.listdir(parent)
                             if os.path.isdir(os.path.join(parent, name))]
                    suggestions = difflib.get_close_matches(os.path.basename(cand_p), names, n=3, cutoff=0.5)
                hint = (" Nearby directories: " + ", ".join(os.path.join(parent, name) for name in suggestions)
                        if suggestions else "")
                return f"[Error: Working directory does not exist: {cand_p}.{hint}]", False

        # npm scripts may specify their own port; use it for the health check.
        if server_cwd and re.search(r'\b(?:npm|pnpm|yarn|bun)\s+(?:run\s+)?(?:dev|start|serve|preview)\b', clean_cmd):
            try:
                with open(os.path.join(server_cwd, "package.json"), encoding="utf-8") as package_file:
                    scripts = json.load(package_file).get("scripts", {})
                script_name = re.search(r'\b(?:npm|pnpm|yarn|bun)\s+(?:run\s+)?(dev|start|serve|preview)\b', clean_cmd).group(1)
                script = str(scripts.get(script_name, ""))
                declared_port = re.search(r'(?:--port(?:=|\s+)|\bPORT=)(\d{3,5})', script)
                if declared_port:
                    port = declared_port.group(1)
                elif "vite" in script.lower() and port == "5500":
                    port = "5173"
            except (OSError, ValueError, TypeError):
                pass

        # Python entrypoints often declare a default port inside argparse or app.run.
        if port == "5500" and server_cwd:
            script_match = re.search(r'\bpython(?:3(?:\.\d+)?)?\s+([^\s;&|]+\.py)(?=\s|$)', clean_cmd)
            if script_match:
                script = script_match.group(1).strip("'\"")
                script_path = script if os.path.isabs(script) else os.path.join(server_cwd, script)
                try:
                    with open(script_path, encoding="utf-8", errors="replace") as source_file:
                        source = source_file.read(50000)
                    declared = re.search(r'add_argument\(\s*["\']--port["\'][^\n]*?default\s*=\s*(\d{3,5})', source)
                    if not declared:
                        declared = re.search(r'\b(?:app\.run|serve|HTTPServer)\b[^\n]*?\bport\s*=\s*(\d{3,5})', source)
                    if declared:
                        port = declared.group(1)
                except OSError:
                    pass

        # Step A: Pre-flight port check after the working directory and port are known.
        port_num = int(port) if port.isdigit() else 5500
        if GLOBAL_TASK_MANAGER.is_port_in_use(port_num):
            try:
                _, existing_status = python_curl(f"http://127.0.0.1:{port}", timeout=2)
                if 200 <= existing_status < 400:
                    if navigation_target:
                        AGENT_STATE["working_directory"] = navigation_target
                    return f"[Server already running at http://127.0.0.1:{port} (HTTP {existing_status} verified)]", True
            except Exception:
                pass
            return f"[Error: Port {port} is occupied by another process; existing service did not pass HTTP health check]", False

        # Redirect background server stdout and stderr to persistent task log to avoid Linux 64KB pipe buffer deadlock and SIGPIPE
        server_log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "logs", "tasks")
        os.makedirs(server_log_dir, exist_ok=True)
        server_log_file = os.path.join(server_log_dir, f"server_{port}.log")
        log_out = open(server_log_file, "a", encoding="utf-8", errors="replace")

        try:
            proc = subprocess.Popen(
                bg_cmd,
                shell=True,
                cwd=server_cwd,
                env=sub_env,
                stdout=log_out,
                stderr=subprocess.STDOUT,
                text=True,
                preexec_fn=os.setsid if hasattr(os, "setsid") else None
            )

            # Step B: Immediate health check to guarantee the process did not die on launch
            time.sleep(0.5)
            ret_code = proc.poll()
            if ret_code is not None:
                # Process terminated immediately
                stderr_out = ""
                try:
                    with open(server_log_file, "r", encoding="utf-8", errors="replace") as lf:
                        lines = lf.readlines()
                        stderr_out = "".join(lines[-10:])
                except Exception:
                    pass

                # If it failed due to address already in use, free the port and retry once
                if "address already in use" in stderr_out.lower() or GLOBAL_TASK_MANAGER.is_port_in_use(port_num):
                    GLOBAL_TASK_MANAGER.free_port(port_num, cmd_hint=disp_cmd)
                    time.sleep(0.3)
                    proc = subprocess.Popen(
                        bg_cmd,
                        shell=True,
                        cwd=server_cwd,
                        env=sub_env,
                        stdout=log_out,
                        stderr=subprocess.STDOUT,
                        text=True,
                        preexec_fn=os.setsid if hasattr(os, "setsid") else None
                    )
                    time.sleep(0.5)
                    ret_code = proc.poll()

                if ret_code is not None:
                    # Still failed
                    fail_err = stderr_out.strip() or f"Exited immediately with code {ret_code}"
                    return f"[Error: Server failed to start on port {port}: {fail_err}]", False

            # Step C: Mandatory HTTP 200 OK Verification
            # Poll localhost:<port> for up to 4.0 seconds (16 iterations x 0.25s) to guarantee server returns HTTP 200 OK
            http_verified = False
            http_status = None
            for _ in range(16):
                if proc.poll() is not None:
                    break
                try:
                    _, code = python_curl(f"http://127.0.0.1:{port}", timeout=1)
                    if code == 200 or (200 <= code < 400):
                        http_verified = True
                        http_status = code
                        break
                except Exception:
                    pass
                time.sleep(0.25)

            if proc.poll() is not None:
                stderr_out = ""
                try:
                    with open(server_log_file, "r", encoding="utf-8", errors="replace") as lf:
                        lines = lf.readlines()
                        stderr_out = "".join(lines[-10:])
                except Exception:
                    pass
                return f"[Error: Server process exited unexpectedly during health check: {stderr_out.strip()}]", False

            if not http_verified:
                # Do NOT confirm server is running if HTTP 200 OK was not received!
                return (
                    f"[Error: Server PID {proc.pid} launched on port {port}, but did not return HTTP 200 OK within health check timeout. "
                    f"Check startup errors in logs/tasks/server_{port}.log]",
                    False
                )

            # ONLY confirm and print card when HTTP 200 OK is verified!
            task = GLOBAL_TASK_MANAGER.register_task(bg_cmd, proc, disp_cmd, port)
            if navigation_target:
                AGENT_STATE["working_directory"] = navigation_target
            print_server_card(disp_cmd, port, proc.pid)
            return (
                f"[Background server started with PID {proc.pid} on port {port} "
                f"(HTTP {http_status} OK verified at http://127.0.0.1:{port})]",
                True
            )
        except Exception as e:
            return f"[Failed to start server: {e}]", False
    else:
        is_curl_cmd = clean_cmd.strip().startswith("curl ")

        try:
            res = subprocess.run(
                clean_cmd,
                shell=True,
                cwd=run_cwd,
                env=sub_env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=120
            )
            out = res.stdout or ""
            result_returncode = res.returncode
            if navigation_target and result_returncode == 0:
                AGENT_STATE["working_directory"] = navigation_target
                out = f"[Working directory: {navigation_target}]" + (f"\n{out}" if out.strip() else "")

            # Check for curl missing or failing in Termux: fallback to Python curl
            if is_curl_cmd and (res.returncode != 0 or not out.strip() or any(err in out.lower() for err in ("command not found", "not found", "curl: (", "curl: error"))):
                py_curl_res = parse_and_run_curl_command(clean_cmd)
                if py_curl_res is not None:
                    c_body, c_error = py_curl_res
                    if c_body and not c_error and not c_body.startswith("[Python Curl Error"):
                        out = f"[Executed via Python Curl (urllib)]:\n{c_body}"
                        result_returncode = 0
                    elif res.returncode != 0:
                        out = c_body
                    if c_error:
                        result_returncode = result_returncode or 1

            # Android symlink tolerance: Auto-retry find with -L on /sdcard symlink
            if not out.strip() and "find " in clean_cmd and "/sdcard" in clean_cmd and " -L " not in clean_cmd:
                try:
                    fixed_cmd = clean_cmd.replace("find ", "find -L ", 1)
                    res_retry = subprocess.run(
                        fixed_cmd,
                        shell=True,
                        cwd=run_cwd,
                        env=sub_env,
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        timeout=120
                    )
                    if res_retry.stdout and res_retry.stdout.strip():
                        out = res_retry.stdout
                        result_returncode = res_retry.returncode
                    elif "-type d" in clean_cmd:
                        res_ls = subprocess.run(
                            "ls -d /sdcard/*/ 2>/dev/null",
                            shell=True,
                            env=sub_env,
                            stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT,
                            text=True,
                            timeout=30
                        )
                        if res_ls.stdout and res_ls.stdout.strip():
                            out = res_ls.stdout
                            result_returncode = res_ls.returncode
                except Exception:
                    pass

            # Android path tolerance: Auto-resolve paths with leading/trailing spaces if command failed
            if res.returncode != 0 and "/sdcard" in clean_cmd:
                try:
                    path_matches = re.findall(r'(/sdcard/[^\s\'"|;&]+)', clean_cmd)
                    for target_path in path_matches:
                        p_dir = os.path.dirname(target_path)
                        p_base = os.path.basename(target_path)
                        if os.path.exists(p_dir):
                            for real_entry in os.listdir(p_dir):
                                if real_entry.strip() == p_base or real_entry.strip().lower() == p_base.lower():
                                    if real_entry != p_base:
                                        real_full = os.path.join(p_dir, real_entry)
                                        fixed_cmd = clean_cmd.replace(target_path, f'"{real_full}"')
                                        res_retry = subprocess.run(
                                            fixed_cmd,
                                            shell=True,
                                            cwd=run_cwd,
                                            env=sub_env,
                                            stdin=subprocess.DEVNULL,
                                            stdout=subprocess.PIPE,
                                            stderr=subprocess.STDOUT,
                                            text=True,
                                            timeout=120
                                        )
                                        if res_retry.returncode == 0 or (res_retry.stdout and "cannot access" not in res_retry.stdout):
                                            out = (
                                                f"[Galam.ai Path Auto-Resolved: '{target_path}' -> '{real_full}']\n" +
                                                (res_retry.stdout or "")
                                            )
                                            result_returncode = res_retry.returncode
                                            break
                except Exception:
                    pass

            if result_returncode != 0:
                out = f"[Exit status {result_returncode}]\n{out}"
            return ActionResult(out, result_returncode == 0, result_returncode,
                                status="completed" if result_returncode == 0 else "failed")
        except subprocess.TimeoutExpired:
            return "[Error: Command timed out after 120s]", False
        except Exception as ex:
            return f"[Execution Error: {ex}]", False
