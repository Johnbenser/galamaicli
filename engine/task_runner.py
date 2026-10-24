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
import uuid
from typing import List, Dict, Tuple, Any, Optional

from tools import filesystem
from config import AGENT_STATE
from engine.logger import log_galam_system_event, log_tool_execution
from engine.action_results import ActionResult, as_result
from engine.result_store import store_result, read_action_output
from engine.cancellation import run_process as cancellable_run
from engine.tool_registry import validate_arguments

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

    def stop_task(self, target, pid_only=False) -> bool:
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
                if pid_only:
                    match = str(t.get('pid')) == target_str
                if match and t.get("process") is not None and t["process"].poll() is None:
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
                    # Keep ownership until the tracked leader has actually stopped.
                    # A slow/ignored SIGTERM must not leave an untracked service.
                    try:
                        proc.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        try:
                            os.killpg(pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        try:
                            proc.wait(timeout=2)
                        except subprocess.TimeoutExpired:
                            return False  # Retain ownership if the OS has not completed the stop.
                    t["process"] = None
                    self.tasks.remove(t)
                    return True

        # Processes not launched and tracked by this instance are never killed.
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
        """Stop only live processes launched and tracked by this Galam instance."""
        freed = False
        # 1. Stop internal tasks on this port
        with self._lock:
            for t in list(self.tasks):
                if (str(t.get("port")) == str(port) and t.get("process") is not None
                        and t["process"].poll() is None):
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

        return freed

    def stop_all(self):
        with self._lock:
            for t in list(self.tasks):
                if t.get("process") is None or t["process"].poll() is not None:
                    continue
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
            return active_list

    def is_server_command(self, cmd: str) -> bool:
        from engine.server_commands import server_invocation
        return server_invocation(cmd) is not None

    def extract_server_target(self, cmd: str) -> Tuple[str, str]:
        from engine.server_commands import server_invocation,configured_port
        found=server_invocation(cmd)
        return (' '.join(found[1]) if found else 'server'),configured_port(cmd) or '5500'

    def get_task_pill_tokens(self) -> list:
        tasks = self.get_active_tasks()
        active_ids = {row['id'] for row in AGENT_STATE.get('active_subagents', [])
                      if row.get('task') == 'command' and not row.get('startup_complete')}
        tasks = [task for task in tasks if task.get('agent_id') not in active_ids]
        if not tasks:
            return []
        latest = tasks[-1]
        time_str = latest.get("start_time", "background")
        from engine.transparency import safe_display
        import shutil
        cols = shutil.get_terminal_size((44,24)).columns
        prefix = (latest.get('agent_name', '') + ' · ') if latest.get('agent_name') else ''
        cmd_str = prefix + re.sub(r'\s+', ' ', safe_display(latest.get("cmd", "server")))
        cmd_str = cmd_str[:max(8, cols-26)]
        state = latest.get('state', 'ready')
        label = 'running' if state in ('ready', 'auth_required') else 'unverified' if state == 'running_unverified' else state
        color = 'fg:#4ade80' if label == 'running' else 'fg:#facc15'

        return [
            ("fg:#facc15 bold", "• "),
            ("fg:#94a3b8", f"[{time_str}] "),
            ("fg:#818cf8 bold", f"{cmd_str} "),
            (color, label+"\n")
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
    if tool_name == 'ask_user':
        from engine.security import redact
        from engine.transparency import safe_display
        return '\033[38;5;81m• Asking\033[0m ' + redact(safe_display(args.get('question', '')))
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
                        lines.append(f"  - [Task ID {t['id']}] PID {t['pid']} · Command: `{t['cmd']}` "
                                     f"(Port: {t.get('port') or 'unknown'}; state: {t.get('state', 'running')}; log: {t.get('log_path', 'unavailable')})")
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
                        out = f"Task/PID {target} not found in Galam.ai active tasks or already stopped. Unowned processes are not terminated."
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
    credential_access = bool(args.get('_credential_access')) if isinstance(args, dict) else False
    try:
        args = validate_arguments(tool_name, args)
        from engine.security import sensitive_path, audit_project
        if tool_name in ('view_file', 'write_file', 'replace_file_content') and sensitive_path(resolve_session_path(args['path'])):
            raise ValueError('Credential files are protected from model file tools. Use settings or edit them locally; do not send secret values to a model.')
        if tool_name == 'search_files' and sensitive_path(resolve_session_path(args.get('directory', '.'))):
            raise ValueError('Credential files are excluded from model searches.')
        if tool_name == 'ask_user':
            from engine.clarification import ask_user
            result = ask_user(**args)
        elif tool_name == 'security_audit':
            result = ActionResult(audit_project(resolve_session_path(args.get('directory', '.'))), verified=True)
        elif tool_name == "execute_bash":
            if args.get('background'):
                result = as_result(execute_shell_action(args['command'], allow_credentials=credential_access,
                    background=True, readiness_url=args.get('readiness_url', '')))
            else:
                result = as_result(execute_shell_action(args["command"], allow_credentials=True)
                                   if credential_access else execute_shell_action(args["command"]))
        elif tool_name == "make_directory":
            path = resolve_session_path(args["path"])
            existed = os.path.isdir(path)
            os.makedirs(path, exist_ok=True)
            if not os.path.isdir(path):
                raise ValueError("Directory was not created")
            result = ActionResult(f"{'Verified existing' if existed else 'Created'} directory '{path}'.",
                                  verified=True, metadata={"path": path, "kind": "directory", "changed": not existed})
        elif tool_name == "read_action_output":
            result = ActionResult(read_action_output(**args), metadata={'source_id': args['action_id']})
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
    if tool_name == 'view_file' and result.success:
        result.metadata['path'] = resolve_session_path(args['path'])
    from engine.security import redact
    result.output = redact(result.output)
    result = store_result(result)
    log_tool_execution(tool_name=tool_name, args=args, stdout_preview=result.output,
                       duration_ms=(time.monotonic() - started) * 1000,
                       success=result.success, exit_code=result.exit_code,
                       model_name=invoking_model)
    return result

def get_subprocess_env() -> dict:
    """Returns an enriched environment dictionary ensuring $API_KEYS and all endpoint keys are available."""
    env = os.environ.copy()
    import tempfile
    from engine.result_store import runtime_dir
    candidate = env.get('TMPDIR')
    if not candidate or not os.path.isdir(candidate) or not os.access(candidate, os.W_OK):
        candidate = str(runtime_dir('tmp'))
    try:
        with tempfile.TemporaryFile(dir=candidate):
            env['TMPDIR'] = candidate
    except OSError:
        env.pop('TMPDIR', None)
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
        options = {'context': ssl.create_default_context()} if urllib.parse.urlsplit(url).scheme.lower() == 'https' else {}
        with urllib.request.urlopen(req, timeout=timeout, **options) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return body, resp.status
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace") if hasattr(e, "read") else str(e)
        return f"[HTTP {e.code}]: {err_body}", e.code
    except Exception as error:
        return f"[Python Curl Error: {error}; TLS verification remains enabled]", -1

def parse_and_run_curl_command(cmd_str: str, env=None) -> Optional[Tuple[str, bool]]:
    """
    Parses a curl command string, expands shell variables ($API_KEYS, etc.),
    and executes via Python standard library urllib.request.
    """
    try:
        env = get_subprocess_env() if env is None else env
        expanded_cmd = cmd_str
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
        timeout = 30
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
            elif tok in ('--max-time', '-m', '--connect-timeout') and i + 1 < len(tokens):
                timeout = max(.1, float(tokens[i+1]))
                i += 2
            elif tok == '--url' and i + 1 < len(tokens):
                url = tokens[i+1]
                i += 2
            elif tok in ("-s", "-sS", "-k", "--insecure", "-L", "--location", "-v"):
                i += 1
            elif tok in ('-f', '--fail', '--fail-with-body'):
                i += 1
            elif not tok.startswith("-") and url is None:
                url = tok
                i += 1
            else:
                # Preserve semantics by declining unsupported flags, rather than
                # guessing their arity or turning their values into a URL.
                return None

        if not url:
            return None

        body, status = python_curl(url=url, method=method, headers=headers, data=data, timeout=timeout)
        is_err = status != 200 and not (200 <= status < 300)
        return body, is_err
    except Exception as e:
        return f"[Python Curl Parse Error: {e}]", True

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
    if not os.path.isabs(expanded) and '/' in expanded:
        rooted = os.path.join('/', expanded)
        if os.path.isdir(rooted):
            return rooted
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


def _existing_command_result(command, directory, background=False, readiness_url=''):
    from engine.command_agent import active_agent
    if not active_agent() or not (background or GLOBAL_TASK_MANAGER.is_server_command(command)):
        return None
    from engine.process_runner import inferred_url
    from engine.service_observation import reuse_result
    try:
        return reuse_result(readiness_url or inferred_url(command, directory, os.environ), directory)
    except (ValueError, OSError):
        return None


def preflight_shell_command(command: str, request: str = '', background=False, readiness_url='') -> Tuple[bool, str]:
    """Catch guessed navigation and Python entrypoints before asking for approval."""
    prepared, navigation = _prepare_navigation(command.strip())
    if prepared is None:
        return False, navigation
    from engine.command_reference import assess, feedback
    reference = assess(prepared, session_directory(), request)
    if not reference.allowed and _existing_command_result(prepared, navigation or session_directory(), background, readiness_url):
        return True, ''
    return reference.allowed, feedback(reference)


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


def execute_shell_action(cmd: str, allow_credentials=False, background=False, readiness_url=''):
    from engine.command_reference import assess, feedback
    prepared, navigation = _prepare_navigation(cmd.strip())
    if prepared is None:
        return store_result(ActionResult(navigation, False, 1, status='failed'))
    reference = assess(prepared, session_directory())
    if not reference.allowed:
        existing = _existing_command_result(prepared, navigation or session_directory(), background, readiness_url)
        if existing:
            return store_result(existing)
        return store_result(ActionResult('Error: ' + feedback(reference), False, 1, status='failed',
                                         metadata={'command_reference': reference.evidence, 'rejected_before_execution': True}))
    result = as_result(_execute_shell_action_legacy(cmd, allow_credentials, background, readiness_url))
    if reference.evidence:
        result.metadata['command_reference'] = reference.evidence
    return store_result(result)


def _execute_shell_action_legacy(cmd: str, allow_credentials=False, background=False, readiness_url='') -> Tuple[str, bool]:
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
    if not allow_credentials:
        from engine.security import public_environment
        sub_env = public_environment(sub_env)

    if background or GLOBAL_TASK_MANAGER.is_server_command(clean_cmd):
        from engine.process_runner import launch_process
        from config import CONFIG
        server_cwd = navigation_target or run_cwd
        result = launch_process(re.sub(r'\s*&\s*$', '', clean_cmd).strip(), server_cwd, sub_env,
            GLOBAL_TASK_MANAGER, readiness_url=readiness_url,
            timeout=CONFIG.get('server_startup_timeout', 30))
        if navigation_target and result.is_background:
            AGENT_STATE['working_directory'] = navigation_target
            AGENT_STATE['working_directory_explicit'] = True
        if result.success and result.is_background and result.metadata.get('url') and result.metadata.get('pid'):
            actual_port = str(urllib.parse.urlsplit(result.metadata['url']).port or '')
            print_server_card(clean_cmd, actual_port, result.metadata['pid'])
        return result

    is_curl_cmd = clean_cmd.strip().startswith("curl ")

    try:
        res = cancellable_run(
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
            AGENT_STATE["working_directory_explicit"] = True
            out = f"[Working directory: {navigation_target}]" + (f"\n{out}" if out.strip() else "")

        # Check for curl missing or failing in Termux: fallback to Python curl
        if is_curl_cmd and (res.returncode != 0 or not out.strip() or any(err in out.lower() for err in ("command not found", "not found", "curl: (", "curl: error"))):
            py_curl_res = parse_and_run_curl_command(clean_cmd, env=sub_env)
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
                res_retry = cancellable_run(
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
                    res_ls = cancellable_run(
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
                                    res_retry = cancellable_run(
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
