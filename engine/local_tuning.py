"""Local-only tuning; estimates never change remote-provider settings."""
import os
import re

PRESETS = {"temperature": (0.1, 0.2, 0.3, 0.5, 0.7),
           "max_tokens": (128, 256, 512, 768, 1024, 2048),
           "context_window": (2048, 4096, 8192, 16384, 32768)}


def auto_values(path, size=None):
    if size is None:
        try:
            size = os.path.getsize(path)
        except OSError:
            size = 0
    # Prefer an explicit size in the filename; quantization makes file bytes ambiguous.
    match = re.search(r"(?:^|[-_.])(\d+(?:\.\d+)?)b(?:[-_.]|$)", os.path.basename(path), re.I)
    params = float(match.group(1)) if match else (size / (1024 ** 3) / 0.6 if size else 7)
    if params <= 2:
        return {"temperature": 0.2, "max_tokens": 512, "context_window": 2048}
    if params <= 4:
        return {"temperature": 0.2, "max_tokens": 768, "context_window": 8192}
    return {"temperature": 0.2, "max_tokens": 512, "context_window": 4096}


def effective_tuning(config, model=None):
    from engine.local_profiles import enabled, selected_profile
    if not enabled(config):
        return None
    profile = selected_profile(config, model)
    saved = config.get("local_tuning") or {}
    if not isinstance(saved, dict):
        saved = {}
    # Unmanaged local servers retain their settings until tuning is explicitly configured.
    if not profile and not saved:
        return None
    defaults = auto_values(profile["path"]) if profile else {"temperature": 0.2, "max_tokens": 512, "context_window": 4096}
    if profile:
        defaults["context_window"] = int(profile["ctx"])
    values = dict(defaults)
    if saved.get("mode", "auto") == "manual":
        for key in PRESETS:
            value = saved.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                values[key] = value
    values["temperature"] = max(0, min(2, float(values["temperature"])))
    values["context_window"] = max(512, min(1048576, int(values["context_window"])))
    values["max_tokens"] = max(1, min(int(values["max_tokens"]), values["context_window"] // 4))
    return values


def cycle_tuning(config, field, save):
    saved = dict(config.get("local_tuning") or {})
    values = effective_tuning(config) or auto_values("")
    if field == "mode":
        saved.update(values)
        saved["mode"] = "manual" if saved.get("mode", "auto") == "auto" else "auto"
    else:
        choices = PRESETS[field]
        current = saved.get(field, values[field])
        saved[field] = choices[(choices.index(current) + 1) % len(choices)] if current in choices else choices[0]
        saved["mode"] = "manual"
    save(local_tuning=saved)
    config["local_tuning"] = saved
    # The profile command and request budget must agree on requested context.
    from engine.local_profiles import profiles, selected_profile
    profile = selected_profile(config)
    if profile:
        items = profiles(config)
        if field == "context_window" or field == "mode":
            context = (saved.get("context_window", profile["ctx"]) if saved["mode"] == "manual"
                       else auto_values(profile["path"])["context_window"])
            for p in items:
                if p["path"] == profile["path"]:
                    p["ctx"] = context
            save(local_models=items)
            config["local_models"] = items
    return saved
