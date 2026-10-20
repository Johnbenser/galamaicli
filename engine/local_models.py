"""Discover model IDs advertised by an OpenAI-compatible local server."""

import os
from urllib.request import Request, urlopen
import json
_MODEL_INFO = {}


def local_base_url(config):
    from storage.endpoints import ENDPOINT_REGISTRY

    base = (config.get("local_endpoint_url") or os.environ.get("LOCAL_ENDPOINT_URL")
            or ENDPOINT_REGISTRY["Local Endpoint"]["base_url"]).rstrip("/")
    if base.endswith("/chat/completions"):
        base = base[:-len("/chat/completions")]
    if not base.endswith("/v1"):
        base += "/v1"
    return base


def discover_local_models(config, api_key="", timeout=3):
    """Return server model IDs, or an empty list if discovery is unavailable."""
    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = Request(local_base_url(config) + "/models", headers=headers)
    try:
        with urlopen(request, timeout=timeout) as response:
            data = json.load(response)
    except (OSError, ValueError, TypeError):
        return []
    if not isinstance(data, dict):
        return []
    models = data.get("data") or data.get("models") or []
    if not isinstance(models, list):
        return []
    for item in models:
        if not isinstance(item, dict):
            continue
        model_id = item.get("id") or item.get("model") or item.get("name")
        meta = item.get("meta") if isinstance(item.get("meta"), dict) else {}
        context = meta.get("n_ctx") or item.get("context_window")
        if model_id and isinstance(context, int) and context > 0:
            _MODEL_INFO[(local_base_url(config), str(model_id))] = {"context_window": context}
    return list(dict.fromkeys(
        str(item.get("id") or item.get("model") or item.get("name"))
        for item in models if isinstance(item, dict)
        and (item.get("id") or item.get("model") or item.get("name"))
    ))


def refresh_local_models(config):
    """Replace stale catalog entries when the server reports its loaded models."""
    from storage.endpoints import ENDPOINT_REGISTRY
    from engine.payload_builder import get_endpoint_api_key

    ids = discover_local_models(config, get_endpoint_api_key("Local Endpoint"))
    if ids:
        previous = ENDPOINT_REGISTRY["Local Endpoint"]["models"]
        ENDPOINT_REGISTRY["Local Endpoint"]["models"] = {
            model_id: {
                "name": model_id,
                "display_name": os.path.basename(model_id),
                "supports_streaming": True,
                **({"context_window": previous[model_id]["context_window"]}
                   if model_id in previous and "context_window" in previous[model_id] else {}),
                **_MODEL_INFO.get((local_base_url(config), model_id), {}),
            } for model_id in ids
        }
        ENDPOINT_REGISTRY['Local Endpoint']['models'].update({
            name:info for name,info in previous.items() if info.get('custom_connection')})
    return ids


def remember_model_context(config, model, context):
    if isinstance(context, int) and not isinstance(context, bool) and context > 0:
        _MODEL_INFO[(local_base_url(config), str(model))] = {"context_window": context}


def advertised_context(config, model):
    direct = _MODEL_INFO.get((local_base_url(config), str(model)), {})
    if direct.get("context_window"):
        return direct["context_window"]
    # A bare filename advertised by llama.cpp may correspond to a selected full path.
    for (base, name), info in list(_MODEL_INFO.items()):
        if base == local_base_url(config) and "/" not in name and name == os.path.basename(str(model)):
            return info.get("context_window")
    return None
