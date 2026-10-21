"""Local diagnostics and ownership tests; no real settings or servers are changed."""
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

import requests

from engine import local_profiles as lp
from engine import local_tuning as tuning


class LocalWorkspace(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.model = self.root / "qwen-3B-Q4.gguf"
        self.model.write_bytes(b"GGUF fixture")
        self.config = {"local_manager": True, "local_models": [], "local_endpoint_url": "http://127.0.0.1:8080/v1"}
        self.save = Mock(side_effect=lambda **kw: self.config.update(kw))
        self.profile = lp.add_profile(self.model, self.config, self.save)
        self.config.update(active_endpoint="Local Endpoint", active_model=str(self.model))
        self.auth = patch("engine.payload_builder.get_endpoint_api_key", return_value="")
        self.auth.start()
        from engine.local_models import _MODEL_INFO
        self.catalog = patch.dict(_MODEL_INFO, {}, clear=True)
        self.catalog.start()
        self.env = patch.dict(os.environ, {"GALAM_STATE_DIR": str(self.root / "runtime")})
        self.env.start()

    def tearDown(self):
        self.catalog.stop()
        self.env.stop()
        self.auth.stop()
        self.temp.cleanup()

    @staticmethod
    def response(body, status=200):
        result = Mock(ok=status < 400, status_code=status, text=json.dumps(body))
        result.json.return_value = body
        return result

    def session(self, model=None, ctx=8192):
        session = Mock()
        session.get.side_effect = [self.response({"status": "ok"}), self.response({"data": [{"id": model or str(self.model), "meta": {"n_ctx": ctx}}]})]
        session.post.return_value = self.response({"choices": [{"message": {"content": "OK"}}], "usage": {"completion_tokens": 1}})
        return session


class LocalProfilesTests(LocalWorkspace):
    def test_profile_validation_duplicate_and_no_secret_settings(self):
        self.assertEqual(self.profile["ctx"], 8192)
        self.assertEqual(lp.add_profile(self.model, self.config, self.save), self.profile)
        self.assertEqual(len(self.config["local_models"]), 1)
        for name in ("missing.gguf", "not.txt", "empty.gguf"):
            path = self.root / name
            if name != "missing.gguf":
                path.touch()
            with self.assertRaises(ValueError):
                lp.add_profile(path, self.config, self.save)
        self.assertEqual(set(self.profile), {"name", "path", "ctx", "threads"})

    def test_scan_includes_sizes_and_deduplicates_symlink_roots(self):
        result = lp.scan_gguf([self.root, self.root], seconds=2)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["path"], str(self.model))
        self.assertIn("MiB", result[0]["size_label"])

    def test_command_quotes_paths_and_preserves_exact_context(self):
        import shlex
        profile = dict(self.profile, path=str(self.root / "my model; echo wrong.gguf"))
        argv = lp.server_argv(profile, self.config)
        self.assertEqual(argv[argv.index("-m") + 1], profile["path"])
        self.assertIn(shlex.quote(profile["path"]), lp.server_command(profile, self.config))
        self.assertEqual(argv[argv.index("-c") + 1], "8192")
        with self.assertRaises(ValueError):
            lp.server_argv(profile, {"local_endpoint_url": "http://remote.example:8080/v1"})

    def test_connection_ok_and_tiny_chat(self):
        session = self.session()
        report = lp.test_connection(self.profile, self.config, session=session)
        self.assertTrue(report["ok"])
        self.assertEqual(len(report["steps"]), 3)
        self.assertEqual(session.post.call_args.kwargs["json"]["max_tokens"], 8)
        self.assertEqual(session.post.call_args.kwargs["timeout"], (5, 180))
        self.assertGreater(report["tokens_per_second"], 0)
        self.assertIn("✓ Chat", lp.format_report(report))

    def test_connection_server_down(self):
        session = Mock()
        session.get.side_effect = requests.ConnectionError("private URL not displayed")
        report = lp.test_connection(self.profile, self.config, session=session)
        self.assertFalse(report["ok"])
        self.assertIn("Server down", report["diagnosis"])
        session.post.assert_not_called()
        self.assertEqual(len(report["steps"]), 3)

    def test_connection_model_mismatch_does_not_chat(self):
        session = self.session(model="other.gguf")
        report = lp.test_connection(self.profile, self.config, session=session)
        self.assertIn("Model mismatch", report["diagnosis"])
        session.post.assert_not_called()

    def test_connection_timeout_is_distinct_from_down(self):
        session = Mock()
        session.get.side_effect = requests.Timeout()
        self.assertIn("timed out", lp.test_connection(self.profile, self.config, session=session)["diagnosis"])

    def test_connection_small_context_error_and_loading(self):
        session = self.session()
        session.post.return_value = self.response({"error": "context too small"}, 400)
        self.assertIn("Context too small", lp.test_connection(self.profile, self.config, session=session)["diagnosis"])
        session = Mock()
        session.get.return_value = self.response({}, 503)
        self.assertIn("loading", lp.test_connection(self.profile, self.config, session=session)["diagnosis"])

    def test_diagnostic_is_read_only_and_runs_in_background(self):
        from engine.local_commands import background_job
        before = copy.deepcopy(self.config)
        completed = threading.Event()
        reports = []
        worker = background_job(lambda: lp.test_connection(self.profile, self.config, session=self.session()),
                                lambda report: (reports.append(report), completed.set()))
        self.assertTrue(completed.wait(3))
        worker.join(1)
        self.assertTrue(reports[0]["ok"])
        self.assertEqual(before, self.config)

    def test_unowned_server_is_never_signalled(self):
        from engine import local_process
        with patch.object(local_process, "owned_server", return_value=None), patch.object(local_process.os, "kill") as kill:
            ok, message = local_process.start_profile(self.profile, self.config, restart=True)
        self.assertFalse(ok)
        self.assertIn("not owned", message)
        kill.assert_not_called()

    def test_port_busy_prevents_launch(self):
        from engine import local_process
        with patch.object(local_process, "owned_server", return_value=None), patch.object(local_process, "_port_busy", return_value=True), patch.object(local_process.subprocess, "Popen") as spawn:
            ok, message = local_process.start_profile(self.profile, self.config)
        self.assertFalse(ok)
        self.assertIn("Port busy", message)
        spawn.assert_not_called()

    def test_pid_reuse_invalidates_ownership(self):
        from engine import local_process
        record = {"pid": 123, "identity": {"start": "old", "command_hash": "old"}, "base_url": "http://127.0.0.1:8080/v1"}
        local_process._record_path().write_text(json.dumps(record))
        with patch.object(local_process, "_identity", return_value={"start": "new", "command_hash": "new"}):
            self.assertIsNone(local_process.owned_server(record["base_url"]))

    def test_disabled_feature_has_no_network_or_process_actions(self):
        self.config["local_manager"] = False
        session = Mock()
        self.assertFalse(lp.test_connection(self.profile, self.config, session=session)["ok"])
        session.get.assert_not_called()
        self.assertIsNone(tuning.effective_tuning(self.config))
        with self.assertRaises(ValueError):
            lp.add_profile(self.model, self.config, self.save)


class TuningAndMenuTests(LocalWorkspace):
    def test_auto_estimates_for_model_sizes(self):
        self.assertEqual(tuning.auto_values("model-1.5B.gguf")["context_window"], 2048)
        self.assertEqual(tuning.auto_values("model-3B.gguf")["max_tokens"], 768)
        self.assertEqual(tuning.auto_values("model-7B.gguf")["context_window"], 4096)
        self.assertEqual(tuning.auto_values("unknown.gguf", 4 * 1024**3)["max_tokens"], 512)

    def test_manual_cycle_and_command_agree(self):
        tuning.cycle_tuning(self.config, "mode", self.save)
        self.assertEqual(self.config["local_tuning"]["mode"], "manual")
        tuning.cycle_tuning(self.config, "temperature", self.save)
        self.assertEqual(tuning.effective_tuning(self.config)["temperature"], 0.3)
        tuning.cycle_tuning(self.config, "context_window", self.save)
        selected = lp.selected_profile(self.config)
        argv = lp.server_argv(selected, self.config)
        self.assertEqual(int(argv[argv.index("-c") + 1]), tuning.effective_tuning(self.config)["context_window"])
        tuning.cycle_tuning(self.config, "mode", self.save)
        self.assertEqual(tuning.effective_tuning(self.config)["context_window"], 8192)

    def test_local_request_and_context_clamp_use_same_budget(self):
        from config import CONFIG
        from engine.payload_builder import build_request_data
        from engine.context_manager import model_limits
        session = self.session(ctx=3072)
        lp.test_connection(self.profile, self.config, session=session)
        with patch.dict(CONFIG, self.config, clear=True):
            _, _, payload = build_request_data("Local Endpoint", str(self.model), [{"role": "user", "content": "Hi"}])
        window, output, _ = model_limits("Local Endpoint", str(self.model), self.config)
        self.assertEqual(window, 3072)
        self.assertEqual(payload["max_tokens"], output)
        self.assertEqual(payload["temperature"], 0.2)

    def test_disabled_and_unmanaged_local_payload_keep_original_settings(self):
        from config import CONFIG
        from engine.payload_builder import build_request_data
        with patch.dict(CONFIG, {"local_manager": False, "max_tokens": 321, "context_window": 8192}, clear=True):
            _, _, payload = build_request_data("Local Endpoint", "model", [])
        self.assertEqual(payload["max_tokens"], 321)
        self.assertEqual(payload["temperature"], 0.2)
        self.assertIsNone(tuning.effective_tuning({"active_model": "remote-local-model"}))

    def test_tuning_does_not_change_remote_requests_or_limits(self):
        from config import CONFIG
        from engine.payload_builder import build_request_data
        from engine.context_manager import model_limits
        basic = {"max_tokens": 512, "context_window": 8192}
        managed = {**basic, **self.config, "local_tuning": {"mode": "manual", "temperature": 0.1, "max_tokens": 128, "context_window": 2048}}
        for endpoint, model in [("OpenRouter", "example/model"), ("NVIDIA", "example/model"), ("Groq", "example/model")]:
            with patch.dict(CONFIG, basic, clear=True):
                first = build_request_data(endpoint, model, [])
            with patch.dict(CONFIG, managed, clear=True):
                second = build_request_data(endpoint, model, [])
            self.assertEqual(first, second)
            self.assertEqual(model_limits(endpoint, model, basic), model_limits(endpoint, model, managed))

    @staticmethod
    def press(app, key, event):
        from prompt_toolkit.keys import Keys
        bindings = app.kb.get_bindings_for_keys((Keys.ControlM if key == "enter" else Keys.Escape,))
        bindings[-1].handler(event)

    def test_menu_addition_and_flag_preserve_existing_actions(self):
        from config import CONFIG
        from ui.settings_menu import InlineSettingsApp
        from types import SimpleNamespace
        event = SimpleNamespace(app=Mock())
        with patch.dict(CONFIG, self.config, clear=True):
            menu = InlineSettingsApp()
            items = menu._get_items()
            self.assertEqual(len(items), 8)
            self.assertEqual([p["action"] for p in items[:5]], ["to_endpoints", "to_models", "toggle_mode", "toggle_fallback", "toggle_stream"])
            self.assertEqual(items[-3]["action"], "to_local")
            self.assertEqual(items[-2]["action"], "to_connections")
            self.assertEqual(items[-1]["action"], "exit")
            menu.cursor_idx = 5
            self.press(menu, "enter", event)
            self.assertEqual(menu.screen, "local")
            self.press(menu, "escape", event)
            self.assertEqual((menu.screen, menu.cursor_idx), ("main", 0))
            CONFIG["local_manager"] = False
            self.assertEqual(len(menu._get_items()), 7)
            self.assertEqual(menu._get_items()[-1]["action"], "exit")

    def test_profile_activation_updates_endpoint_and_uses_existing_save_path(self):
        from config import CONFIG
        from ui.settings_menu import InlineSettingsApp
        from storage.endpoints import ENDPOINT_REGISTRY
        from types import SimpleNamespace
        cfg = {**self.config, "active_endpoint": "OpenRouter", "endpoint": "OpenRouter", "active_model": "other"}
        def save(endpoint=None, model=None, **kwargs):
            CONFIG.update(kwargs)
            if endpoint:
                CONFIG.update(active_endpoint=endpoint, endpoint=endpoint, active_model=model, model=model)
        with patch.dict(CONFIG, cfg, clear=True), patch("ui.settings_menu.save_settings", side_effect=save) as saved, patch.dict(ENDPOINT_REGISTRY, copy.deepcopy(ENDPOINT_REGISTRY), clear=True):
            menu = InlineSettingsApp()
            menu.screen = "local"
            event = SimpleNamespace(app=Mock())
            self.press(menu, "enter", event)
            self.assertEqual(CONFIG["active_model"], str(self.model))
            self.assertEqual(menu.selected_ep, "Local Endpoint")
            saved.assert_called_once_with("Local Endpoint", model=str(self.model))
            self.assertEqual(menu.screen, "local_result")
            self.assertTrue(any("llama-server" in row["label"] for row in menu.local_screens.rows))

    def test_local_screens_fit_responsive_menu_and_include_final_item(self):
        from config import CONFIG
        from ui.settings_menu import InlineSettingsApp
        cfg = {**self.config, "local_models": [dict(self.profile, name="profile" + str(i)) for i in range(12)]}
        with patch.dict(CONFIG, cfg, clear=True):
            menu = InlineSettingsApp()
            menu.screen = "local"
            menu.cursor_idx = len(menu._get_items()) - 1
            with patch("ui.settings_menu.get_app") as current:
                current.return_value.output.get_size.return_value = Mock(rows=24, columns=44)
                shown = "".join(text for _, text in menu.get_formatted_text())
            self.assertLessEqual(shown.count("\n"), 12)
            self.assertIn("Back to Settings", shown)
            self.assertNotIn("profile0", shown)

    def test_stale_background_result_cannot_replace_new_screen(self):
        from engine.local_ui import LocalScreens
        view = LocalScreens(self.config, self.save)
        callbacks = []
        def fake_background(work, done):
            callbacks.append(done)
        with patch("engine.local_ui.background_job", side_effect=fake_background):
            view.enter({"action": "local_scan"}, Mock())
            view.enter({"action": "local_back"}, Mock())
            view.enter({"action": "local_test"}, Mock())
        callbacks[0]([])
        self.assertTrue(view.busy)
        self.assertEqual(view.rows[0]["label"], "Working…")
        callbacks[1]({"steps": [], "diagnosis": "Connection ready."})
        self.assertFalse(view.busy)
        self.assertIn("Connection ready.", view.rows[0]["label"])

    def test_standalone_command_bypasses_model_router(self):
        from engine.local_commands import handle_local_command
        with patch("engine.local_commands.background_job", return_value="thread") as job, contextlib.redirect_stdout(io.StringIO()):
            result = handle_local_command("test", self.config)
        self.assertEqual(result, "thread")
        job.assert_called_once()

    def test_owned_restart_checks_identity_before_signal(self):
        from engine import local_process
        record = {"pid": 123, "identity": {"start": "old", "command_hash": "old"}}
        with patch.object(local_process, "owned_server", return_value=record), patch.object(local_process, "_identity", return_value=None), patch.object(local_process.os, "kill") as kill:
            ok, message = local_process.start_profile(self.profile, self.config, restart=True)
        self.assertFalse(ok)
        self.assertIn("identity changed", message)
        kill.assert_not_called()

    def test_connection_port_busy_and_empty_chat_are_clear(self):
        session = Mock()
        session.get.return_value = self.response({}, 404)
        self.assertIn("Port busy", lp.test_connection(self.profile, self.config, session=session)["diagnosis"])
        session = self.session()
        session.post.return_value = self.response({"choices": [{"message": {"content": ""}}]})
        self.assertIn("no visible answer", lp.test_connection(self.profile, self.config, session=session)["diagnosis"])


    def test_owned_start_records_pid_and_restart_terminates_only_that_pid(self):
        from engine import local_process
        import signal
        binary = self.root / "llama-server"
        binary.write_text("#!/bin/sh\nexit 0\n")
        binary.chmod(0o755)
        identity = {"start": "1234", "command_hash": "test-hash"}
        child = Mock(pid=23456)
        child.poll.return_value = None
        with patch.dict(os.environ, {"GALAM_LLAMA_SERVER_BIN": str(binary)}), patch.object(local_process, "_port_busy", return_value=False), patch("engine.local_server._healthy", return_value=True), patch.object(local_process.subprocess, "Popen", return_value=child) as spawn, patch.object(local_process, "_identity", return_value=identity):
            ok, message = local_process.start_profile(self.profile, self.config)
            self.assertTrue(ok, message)
            record = local_process.owned_server(self.config["local_endpoint_url"])
            self.assertEqual(record["pid"], 23456)
            self.assertEqual(spawn.call_args.args[0], lp.server_argv(self.profile, self.config))
        # Ownership is checked again immediately before SIGTERM; a stopped process is never re-signalled.
        with patch.dict(os.environ, {"GALAM_LLAMA_SERVER_BIN": str(binary)}), patch.object(local_process, "owned_server", return_value=record), patch.object(local_process, "_identity", side_effect=[identity, None, None]), patch.object(local_process.os, "kill") as kill, patch.object(local_process, "_port_busy", return_value=True):
            ok, message = local_process.start_profile(self.profile, self.config, restart=True)
            kill.assert_called_once_with(23456, signal.SIGTERM)
            self.assertIn("Port busy", message)

    def test_missing_replacement_file_does_not_stop_running_owned_server(self):
        from engine import local_process
        identity = {"start": "1234", "command_hash": "test-hash"}
        record = {"pid": 23456, "identity": identity}
        binary = self.root / "llama-server"
        binary.touch(); binary.chmod(0o755)
        with patch.dict(os.environ, {"GALAM_LLAMA_SERVER_BIN": str(binary)}), patch.object(local_process, "owned_server", return_value=record), patch.object(local_process, "_identity", return_value=identity), patch.object(local_process.os, "kill") as kill:
            ok, message = local_process.start_profile(dict(self.profile, path=str(self.root / "missing.gguf")), self.config, restart=True)
            self.assertFalse(ok)
            self.assertIn("left running", message)
            kill.assert_not_called()



if __name__ == "__main__":
    unittest.main()
