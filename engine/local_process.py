"""Own only servers launched by Galam; never infer ownership from a port or name."""
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import threading
import time
from urllib.parse import urlsplit

from engine.result_store import atomic_bytes, runtime_dir

_LOCK = threading.RLock()


def _record_path():
    return runtime_dir("servers") / "local.json"


def _identity(pid):
    """PID reuse and exec changes invalidate ownership. Unsupported platforms fail closed."""
    try:
        stat = Path(f"/proc/{int(pid)}/stat").read_text()
        fields = stat[stat.rfind(")") + 2:].split()
        if fields[0] == "Z":
            return None
        command = Path(f"/proc/{int(pid)}/cmdline").read_bytes()
        return {"start": fields[19], "command_hash": hashlib.sha256(command).hexdigest()}
    except (OSError, ValueError, IndexError):
        return None


def remember_server(process, argv, base_url):
    identity = _identity(process.pid)
    if identity:
        record = {"pid": process.pid, "identity": identity, "argv": list(argv), "base_url": base_url}
        atomic_bytes(_record_path(), json.dumps(record).encode())
        return record
    return None


def owned_server(base_url):
    try:
        record = json.loads(_record_path().read_text())
        if record["base_url"].rstrip("/") != base_url.rstrip("/"):
            return None
        if _identity(record["pid"]) == record["identity"] and record["identity"]:
            return record
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def _port_busy(host, port):
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


def start_profile(profile, config, restart=False, timeout=120):
    from engine.local_profiles import enabled, server_argv, server_command
    from engine.local_models import local_base_url
    from engine.local_server import _healthy
    if not enabled(config):
        return False, "Local model manager is disabled."
    base = local_base_url(config)
    try:
        argv = server_argv(profile, config)
    except (ValueError, KeyError, TypeError) as error:
        return False, str(error)
    parsed = urlsplit(base)
    with _LOCK:
        owned = owned_server(base)
        if restart:
            if not owned:
                return False, "Server is not owned by Galam; stop it yourself in its Termux session. Run: " + server_command(profile, config)
            # Recheck immediately before signalling; never pkill or kill unrelated processes.
            if _identity(owned["pid"]) != owned["identity"]:
                return False, "Server identity changed; restart refused."
            if not Path(argv[0]).is_file() or not os.access(argv[0], os.X_OK):
                return False, "llama-server executable not found; current server was left running."
            if not Path(profile["path"]).is_file():
                return False, "GGUF file not found; current server was left running."
            try:
                os.kill(owned["pid"], signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                return False, "Owned process exited or cannot be signalled; no new process was started."
            deadline = time.monotonic() + 10
            while _identity(owned["pid"]) == owned["identity"] and time.monotonic() < deadline:
                time.sleep(0.1)
            if _identity(owned["pid"]) == owned["identity"]:
                return False, "Owned server did not stop; no new process was started."
            _record_path().unlink(missing_ok=True)
        elif owned:
            if owned.get("argv") != argv:
                return False, "Another Galam-owned model is running. Use /local restart for the selected profile."
            if _healthy(base):
                return True, "Selected Galam-owned server is healthy."
            return False, "Owned server is still loading or unhealthy; wait, test, or use /local restart."
        if _port_busy(parsed.hostname, parsed.port or 8080):
            return False, "Port busy: an existing server owns this address. It was left running. Use /local test."
        if not Path(argv[0]).is_file() or not os.access(argv[0], os.X_OK):
            return False, "llama-server executable not found: " + argv[0]
        if not Path(profile["path"]).is_file():
            return False, "GGUF file not found: " + profile["path"]
        env = os.environ.copy()
        env["LD_LIBRARY_PATH"] = str(Path(argv[0]).parent) + os.pathsep + env.get("LD_LIBRARY_PATH", "")
        log_path = runtime_dir("servers") / "llama-server.log"
        try:
            with log_path.open("ab", buffering=0) as log:
                process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                           close_fds=True, start_new_session=True, env=env)
            remember_server(process, argv, base)
        except OSError as error:
            return False, f"Could not start local server: {error}"
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if process.poll() is not None:
                return False, f"Server exited with status {process.returncode}; see {log_path}"
            if _healthy(base):
                return True, f"Galam-owned server ready (PID {process.pid})."
            time.sleep(0.5)
        return False, f"Server is still loading after {timeout}s (PID {process.pid}); use /local test. Log: {log_path}"
