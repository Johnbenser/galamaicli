"""Local GGUF profiles and read-only connection diagnostics."""
import os
import shlex
import time
from pathlib import Path
from urllib.parse import urlsplit

import requests


def enabled(config):
    return config.get("local_manager", True) is not False


def profiles(config):
    return [dict(p) for p in config.get("local_models", [])
            if isinstance(p, dict) and isinstance(p.get("path"), str)]


def selected_profile(config, model=None):
    wanted = model or config.get("active_model") or config.get("model")
    return next((p for p in profiles(config) if p["path"] == wanted), None)


def size_label(size):
    return f"{size / (1024 ** 3):.2f} GiB" if size >= 1024 ** 3 else f"{size / (1024 ** 2):.1f} MiB"


def add_profile(path, config=None, save=None):
    if config is None:
        from config import CONFIG, save_settings
        config, save = CONFIG, save_settings
    if not enabled(config):
        raise ValueError("Local model manager is disabled.")
    path = Path(path).expanduser().resolve()
    if path.suffix.lower() != ".gguf" or not path.is_file():
        raise ValueError("Select an existing .gguf file.")
    size = path.stat().st_size
    if size <= 0:
        raise ValueError("The GGUF file is empty.")
    existing = profiles(config)
    found = next((p for p in existing if p["path"] == str(path)), None)
    if found:
        return found
    from engine.local_tuning import auto_values
    defaults = auto_values(str(path), size)
    profile = {"name": path.stem, "path": str(path), "ctx": defaults["context_window"],
               "threads": max(1, min(4, os.cpu_count() or 1))}
    existing.append(profile)
    if save:
        save(local_models=existing)
    config["local_models"] = existing
    return dict(profile)


def scan_gguf(roots=None, limit=200, seconds=8):
    """Bound traversal, skip symlink loops/dependencies, return paths and visible sizes."""
    roots = roots or [Path.home(), Path("/data/data/com.termux/files/home"), Path("/sdcard")]
    found, visited = {}, set()
    deadline = time.monotonic() + seconds
    skip = {".git", "__pycache__", "node_modules", ".cache", ".venv", "venv", "Android"}
    for base in roots:
        for root, dirs, files in os.walk(os.path.expanduser(str(base)), followlinks=False):
            if time.monotonic() >= deadline or len(found) >= limit:
                return sorted(found.values(), key=lambda p: p["path"])
            real = os.path.realpath(root)
            if real in visited:
                dirs[:] = []
                continue
            visited.add(real)
            dirs[:] = sorted(d for d in dirs if d not in skip and not d.startswith("."))
            for name in sorted(files):
                if not name.lower().endswith(".gguf"):
                    continue
                path = Path(root, name)
                try:
                    actual, size = str(path.resolve()), path.stat().st_size
                    if size > 0:
                        found[actual] = {"path": actual, "name": name, "size": size,
                                         "size_label": size_label(size)}
                except OSError:
                    pass
                if len(found) >= limit:
                    break
    return sorted(found.values(), key=lambda p: p["path"])


def server_argv(profile, config=None):
    config = config or {}
    from engine.local_server import _SERVER_PATH
    from engine.local_models import local_base_url
    parsed = urlsplit(local_base_url(config))
    if parsed.scheme != "http" or parsed.hostname not in ("localhost", "127.0.0.1", "::1"):
        raise ValueError("Server startup is supported only for a local HTTP endpoint.")
    from engine.local_tuning import effective_tuning
    tuning = effective_tuning(config, profile["path"])
    ctx, threads = int((tuning or {}).get("context_window", profile["ctx"])), int(profile["threads"])
    if not 512 <= ctx <= 1048576 or not 1 <= threads <= 1024:
        raise ValueError("Invalid context size or thread count in profile.")
    server = os.environ.get("GALAM_LLAMA_SERVER_BIN", _SERVER_PATH)
    return [server, "-m", profile["path"], "--host", parsed.hostname,
            "--port", str(parsed.port or 8080), "-c", str(ctx), "-t", str(threads)]


def server_command(profile, config=None):
    argv = server_argv(profile, config)
    # Termux llama.cpp may need sibling shared libraries when launched from another directory.
    library = str(Path(argv[0]).parent)
    return 'LD_LIBRARY_PATH=' + shlex.quote(library) + ':"${LD_LIBRARY_PATH:-}" ' + shlex.join(argv)


def model_matches(model_id, profile):
    wanted = profile["path"]
    actual = str(model_id)
    if "/" in actual:
        return os.path.realpath(actual) == os.path.realpath(wanted)
    return actual == os.path.basename(wanted)


def test_connection(profile=None, config=None, timeout=180, session=None):
    """Never starts/stops a server. Call from a worker thread, not the menu renderer."""
    config = config or {}
    if not enabled(config):
        return {"ok": False, "steps": [], "diagnosis": "Local model manager is disabled."}
    from engine.local_models import local_models_url, local_chat_url
    from engine.payload_builder import get_endpoint_api_key
    headers = {"Accept": "application/json"}
    key = get_endpoint_api_key("Local Endpoint")
    if key:
        headers["Authorization"] = "Bearer " + key
    http = session or requests.Session()
    owned_session = session is None
    report = {"ok": False, "steps": [], "diagnosis": ""}
    stage = "Models GET"
    started = time.monotonic()
    try:
        for stage, method, url in [("Models GET", "get", local_models_url(config)),
                                   ("Chat POST", "post", local_chat_url(config))]:
            started = time.monotonic()
            kwargs = {"headers": headers, "timeout": (5, timeout)}
            if stage == "Chat POST":
                kwargs["json"] = {"model": loaded_id, "messages": [{"role": "user", "content": "Reply OK"}],
                                  "max_tokens": 8, "temperature": 0, "stream": False}
            response = getattr(http, method)(url, **kwargs)
            if not response.ok:
                if response.status_code == 503:
                    diagnosis = "Server is loading; wait for startup and test again."
                elif response.status_code == 404:
                    diagnosis = f"{stage} path was not found; check the configured local API route."
                elif response.status_code in (401, 403):
                    diagnosis = "Server requires a valid local API key."
                elif stage == "Chat POST" and any(word in response.text.lower() for word in ("context", "n_ctx")):
                    diagnosis = "Context too small for the test request."
                else:
                    diagnosis = f"{stage} failed (HTTP {response.status_code}); check the endpoint and server log."
                raise ValueError(diagnosis)
            body = response.json()
            detail = "Ready"
            if stage == "Models GET":
                catalog = body.get("data", [])
                if not isinstance(catalog, list):
                    raise ValueError("Invalid model catalog from endpoint.")
                candidates = [m for m in catalog if isinstance(m, dict) and m.get("id")]
                match = next((m for m in candidates if profile and model_matches(m["id"], profile)), None)
                if profile and match is None:
                    raise ValueError("Model mismatch: the server is not advertising the selected GGUF. Run its displayed command (aliases cannot prove a match).")
                if not candidates:
                    raise ValueError("Server has no loaded model.")
                loaded = match or candidates[0]
                loaded_id = loaded["id"]
                report["model"] = loaded_id
                meta = loaded.get("meta") or {}
                context = meta.get("n_ctx") or loaded.get("context_window")
                report["context_window"] = context if isinstance(context, int) and context > 0 else None
                if report["context_window"]:
                    from engine.local_models import remember_model_context
                    remember_model_context(config, loaded_id, context)
                detail = os.path.basename(loaded_id)
            if stage == "Chat POST":
                choices = body.get("choices") or []
                text = (choices[0].get("message", {}).get("content") or "") if choices else ""
                if not text.strip():
                    raise ValueError("Chat returned no visible answer; increase test output allowance or check the server template.")
                tokens = body.get("usage", {}).get("completion_tokens")
                measured = isinstance(tokens, (int, float)) and tokens > 0
                if not measured:
                    from engine.context_manager import estimate_tokens
                    tokens = estimate_tokens(text)
                elapsed = max(0.001, time.monotonic() - started)
                report["tokens_per_second"] = tokens / elapsed
                report["tokens_estimated"] = not measured
                detail = f"{text.strip()[:40]} · ~{tokens / elapsed:.2f} tokens/s (request wall time)"
            report["steps"].append({"name": stage, "ok": True,
                                    "latency_ms": (time.monotonic() - started) * 1000, "detail": detail})
        report["ok"] = True
        report["diagnosis"] = "Connection ready."
        if profile and report.get("context_window") and report["context_window"] < int(profile["ctx"]):
            report["diagnosis"] = "Connected; server context is smaller than the profile. Requests are capped to the advertised context."
    except requests.Timeout:
        report["diagnosis"] = "Server timed out; it may still be loading or generating. Try again after startup."
    except requests.ConnectionError:
        report["diagnosis"] = "Server down or unreachable at the configured address."
    except (ValueError, TypeError, KeyError, IndexError, AttributeError):
        import sys
        error = sys.exc_info()[1]
        # Error messages here are locally generated or parser errors, never response headers/credentials.
        report["diagnosis"] = str(error) if isinstance(error, ValueError) and not isinstance(error, requests.exceptions.JSONDecodeError) else "Invalid response from local server."
    finally:
        if owned_session:
            http.close()
    if not report["ok"]:
        report["steps"].append({"name": stage, "ok": False,
                                "latency_ms": (time.monotonic() - started) * 1000, "detail": report["diagnosis"]})
        for name in ("Models GET", "Chat POST"):
            if not any(step["name"] == name for step in report["steps"]):
                report["steps"].append({"name": name, "ok": None, "latency_ms": 0, "detail": "Skipped after failed check"})
    return report


def format_report(report):
    rows = [f"{'✓' if s['ok'] else '✗' if s['ok'] is False else '–'} {s['name']}: {s['latency_ms']:.0f} ms · {s['detail']}" for s in report["steps"]]
    return "\n".join(rows + [report["diagnosis"]])
