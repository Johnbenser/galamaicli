"""Restart the bundled llama.cpp server when Galam's loopback endpoint is down."""

import os
import subprocess
import threading
import time
import urllib.parse
import urllib.request


_START_LOCK = threading.Lock()
_MODEL_PATH = "/data/data/com.termux/files/home/Qwen2.5-Coder-7B-Instruct-abliterated.i1-Q4_K_M.gguf"
_SERVER_PATH = "/data/data/com.termux/files/home/llama.cpp/build/bin/llama-server"


def _healthy(base_url: str) -> bool:
    try:
        with urllib.request.urlopen(base_url.rstrip("/").removesuffix("/v1") + "/health",
                                    timeout=2) as response:
            return response.status == 200
    except Exception:
        return False


def ensure_local_server(base_url: str) -> tuple[bool, str]:
    """Start a known local server only for a loopback endpoint."""
    from config import CONFIG
    from engine.local_profiles import enabled, selected_profile
    profile = selected_profile(CONFIG) if enabled(CONFIG) else None
    if profile:
        from engine.local_process import start_profile
        return start_profile(profile, {**CONFIG, "local_endpoint_url": base_url})
    parsed = urllib.parse.urlsplit(base_url)
    if parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost", "::1"):
        return False, "Automatic start applies only to a local HTTP endpoint."
    if _healthy(base_url):
        return True, "Local model server is already healthy."

    server = os.environ.get("GALAM_LLAMA_SERVER_BIN", _SERVER_PATH)
    model = os.environ.get("GALAM_LOCAL_MODEL_PATH", _MODEL_PATH)
    if not os.path.isfile(server) or not os.access(server, os.X_OK):
        return False, f"llama-server executable not found: {server}"
    if not os.path.isfile(model):
        return False, f"Local model file not found: {model}"

    with _START_LOCK:
        if _healthy(base_url):
            return True, "Local model server is already healthy."
        library_dir = os.path.dirname(server)
        env = os.environ.copy()
        env["LD_LIBRARY_PATH"] = library_dir + os.pathsep + env.get("LD_LIBRARY_PATH", "")
        log_path = os.path.expanduser("~/galam-llama-server.log")
        try:
            with open(log_path, "ab", buffering=0) as log:
                process = subprocess.Popen(
                    [server, "-m", model, "--host", parsed.hostname,
                     "--port", str(parsed.port or 8080), "-c", "8192", "-t", "4"],
                    stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                    close_fds=True, start_new_session=True, env=env,
                )
            if enabled(CONFIG):
                from engine.local_process import remember_server
                try:
                    remember_server(process, [server, "-m", model, "--host", parsed.hostname,
                                              "--port", str(parsed.port or 8080), "-c", "8192", "-t", "4"], base_url)
                except OSError:
                    pass  # A missing ownership record must refuse restarts, not fail this start.
        except OSError as error:
            return False, f"Could not start local model server: {error}"

        for _ in range(35):
            if _healthy(base_url):
                return True, f"Local model server started (PID {process.pid})."
            if process.poll() is not None:
                return False, f"Local model server exited with status {process.returncode}; see {log_path}"
            time.sleep(1)
        return False, f"Local model server did not become healthy; see {log_path}"
