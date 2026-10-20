"""Data/actions for additive local settings screens; no terminal layout changes."""
import copy
import re
from pathlib import Path
import threading
import shutil
import textwrap

from engine.local_commands import background_job
from engine.local_profiles import (add_profile, enabled, profiles, selected_profile, scan_gguf,
                                   server_command, size_label, test_connection)
from engine.local_tuning import cycle_tuning, effective_tuning


def _safe(value):
    return "".join(c if c.isprintable() else "?" for c in str(value))


def activate_profile(profile, config, save):
    if not enabled(config):
        raise ValueError("Local model manager is disabled.")
    if not Path(profile["path"]).is_file():
        raise ValueError("Model file is missing. Add its current path first.")
    command = server_command(profile, {**config, "active_model": profile["path"], "model": profile["path"]})
    save("Local Endpoint", model=profile["path"])
    config.update(active_endpoint="Local Endpoint", endpoint="Local Endpoint",
                  active_model=profile["path"], model=profile["path"])
    from storage.endpoints import ENDPOINT_REGISTRY
    ENDPOINT_REGISTRY["Local Endpoint"]["models"].setdefault(profile["path"], {
        "name": profile["path"], "display_name": profile["name"], "supports_streaming": True})
    return "Selected " + _safe(profile["name"]) + ". Run in Termux:\n" + command


def message_rows(message):
    width = max(20, min(72, shutil.get_terminal_size((44, 24)).columns - 8))
    return [{"label": part, "action": "local_noop"}
            for line in str(message).splitlines()
            for part in (textwrap.wrap(_safe(line), width=width, replace_whitespace=False,
                                       drop_whitespace=False) or [""])]


class LocalScreens:
    def __init__(self, config, save):
        self.config, self.save = config, save
        self.rows = []
        self.busy = False
        self._lock = threading.Lock()
        self._generation = 0

    def main_item(self):
        profile = selected_profile(self.config)
        label = f"{len(profiles(self.config))} profiles"
        if profile:
            match = re.search(r"(?:^|[-_.])(\d+(?:\.\d+)?)b(?:[-_.]|$)", profile["name"], re.I)
            label = (match.group(1) + "B" if match else _safe(profile["name"])[:6]) + " selected"
        width = max(5, shutil.get_terminal_size((44, 24)).columns - 31)
        label = label if len(label) <= width else label[:width - 1] + "…"
        return {"label": "Local Models", "hint": f"[{label}]", "action": "to_local"}

    def items(self, screen):
        rows = self._items(screen)
        # Only new screens are fitted to the original seven single-line slots.
        width = max(28, shutil.get_terminal_size((44, 24)).columns - 6)
        for item in rows:
            label = _safe(item.get("label", ""))
            if len(label) > width:
                label = label[:width - 1] + "…"
            hint_width = max(0, width - max(24, len(label)) - 1)
            hint = _safe(item.get("hint", ""))
            if len(hint) > hint_width:
                hint = hint[:max(0, hint_width - 1)] + ("…" if hint_width else "")
            item["label"], item["hint"] = label, hint
        return rows

    def _items(self, screen):
        if not enabled(self.config):
            return [{"label": "Manager disabled", "action": "local_back"}]
        if screen in ("local_result", "local_add"):
            with self._lock:
                rows = copy.deepcopy(self.rows)
            return rows + [{"label": "Back to Local Models", "action": "local_back"}]
        if screen != "local":
            return []
        selected = selected_profile(self.config)
        rows = []
        for profile in profiles(self.config):
            try:
                size = size_label(Path(profile["path"]).stat().st_size)
            except OSError:
                size = "missing"
            rows.append({"label": _safe(profile.get("name", Path(profile["path"]).name)),
                         "hint": ("✓ selected · " if selected and selected["path"] == profile["path"] else "") + size,
                         "action": "local_activate", "profile": profile})
        saved = self.config.get("local_tuning") or {}
        mode = saved.get("mode", "auto") if isinstance(saved, dict) else "auto"
        rows.extend([{"label": "+ Add model", "action": "local_scan"},
                     {"label": "Test connection", "action": "local_test"},
                     {"label": "Tuning: " + mode.upper(), "action": "local_tune", "field": "mode"}])
        values = effective_tuning(self.config)
        if mode == "manual":
            values = values or {"temperature": .2, "max_tokens": 512, "context_window": 4096}
            from engine.context_manager import model_limits
            model = (selected or {}).get("path", self.config.get("active_model", ""))
            window, output, _ = model_limits("Local Endpoint", model, self.config)
            effective = {**values, "context_window": window, "max_tokens": output}
            for field, label in [("temperature", "Temperature"), ("max_tokens", "Max tokens"), ("context_window", "Context window")]:
                configured = saved.get(field, values[field])
                hint = str(configured) if configured == effective[field] else f"{configured}→{effective[field]}"
                rows.append({"label": label, "hint": hint, "action": "local_tune", "field": field})
        elif values:
            rows.append({"label": "Auto estimate", "hint": f"temp {values['temperature']} · {values['max_tokens']} tokens · ctx {values['context_window']}", "action": "local_noop"})
        rows.append({"label": "Back to Settings", "action": "local_settings"})
        return rows

    def _job(self, work, transform, invalidate):
        with self._lock:
            if self.busy:
                return False
            self.busy = True
            self._generation += 1
            generation = self._generation
            self.rows = [{"label": "Working…", "hint": "Esc goes back", "action": "local_noop"}]
        def done(result):
            with self._lock:
                if generation != self._generation:
                    return
                self.rows = transform(result)
                self.busy = False
            invalidate()
        background_job(work, done)
        return True

    def enter(self, item, invalidate):
        """Return destination screen. Existing menu dispatch stays outside this module."""
        action = item.get("action")
        if not enabled(self.config):
            return "main"
        if action == "local_settings":
            return "main"
        if action == "local_back":
            # Dismiss the old view; a stale worker may finish but cannot replace the next view.
            with self._lock:
                self._generation += 1
                self.busy = False
            return "local"
        if action == "local_noop":
            return None
        if action == "local_tune":
            cycle_tuning(self.config, item["field"], self.save)
            if item["field"] in ("context_window", "mode"):
                profile = selected_profile(self.config)
                if profile:
                    self.rows = message_rows("Tuning saved; context changes require a server restart.\n" + server_command(profile, self.config))
                    return "local_result"
            return "local"
        if action in ("local_activate", "local_add_profile"):
            try:
                profile = item.get("profile") or add_profile(item["path"], self.config, self.save)
                message = activate_profile(profile, self.config, self.save)
            except (ValueError, OSError, KeyError, TypeError) as error:
                message = str(error)
            self.rows = message_rows(message + "\n/local command prints the exact command in chat.")
            return "local_result"
        if action == "local_scan":
            def transform(result):
                if isinstance(result, dict):
                    return [{"label": result["diagnosis"], "action": "local_noop"}]
                return ([{"label": _safe(p["name"]), "hint": p["size_label"] + " · " + _safe(p["path"]),
                          "path": p["path"], "action": "local_add_profile"} for p in result]
                        or [{"label": "No GGUF files found (bounded scan)", "action": "local_noop"}])
            if self._job(scan_gguf, transform, invalidate):
                return "local_add"
            return "local_result"
        if action == "local_test":
            snapshot = copy.deepcopy(dict(self.config))
            profile = selected_profile(snapshot)
            def transform(report):
                lines = [f"{'✓' if s['ok'] else '✗' if s['ok'] is False else '–'} {s['name']} · {s['latency_ms']:.0f}ms · {s['detail']}" for s in report["steps"]]
                return message_rows("\n".join(lines + [report["diagnosis"]]))
            if self._job(lambda: test_connection(profile, snapshot), transform, invalidate):
                return "local_result"
        return None
