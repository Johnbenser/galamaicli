"""Standalone local diagnostics run outside the chat/model request pipeline."""
import threading
from prompt_toolkit.patch_stdout import patch_stdout

from engine.local_profiles import enabled, format_report, selected_profile, server_command, test_connection


def background_job(work, done):
    def run():
        try:
            result = work()
        except Exception:
            result = {"ok": False, "steps": [], "diagnosis": "Local operation failed; check paths, settings, and server logs."}
        done(result)
    thread = threading.Thread(target=run, daemon=True, name="galam-local-manager")
    thread.start()
    return thread


def handle_local_command(arg, config=None):
    if config is None:
        from config import CONFIG
        config = CONFIG
    if not enabled(config):
        print("Local model manager is disabled (local_manager=false).")
        return None
    command = arg.strip().lower() or "help"
    profile = selected_profile(config)
    # Snapshot mutable settings before a worker starts; no settings are saved by diagnostics.
    config = dict(config)
    if command == "test":
        print("Testing local endpoint in the background (up to 180s per request)...")
        def done(report):
            with patch_stdout():
                print("\n" + format_report(report) + "\n")
        return background_job(lambda: test_connection(profile, config), done)
    if command in ("start", "restart"):
        if not profile:
            print("Select a profile in /settings → Local Models first.")
            return None
        from engine.local_process import start_profile
        def done(result):
            with patch_stdout():
                print("\n" + result["diagnosis"] + "\n")
        def work():
            ok, message = start_profile(profile, config, restart=command == "restart")
            return {"ok": ok, "diagnosis": message, "steps": []}
        print("Checking local server ownership and startup in the background...")
        return background_job(work, done)
    if command == "command" and profile:
        print(server_command(profile, config))
    else:
        print("/local test · /local command · /local start · /local restart\nAdd/select GGUF profiles in /settings → Local Models.")
    return None
