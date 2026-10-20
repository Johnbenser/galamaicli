import os
import json
import re
from storage.endpoints import ENDPOINT_REGISTRY

def get_endpoint_api_key(endpoint_name: str, provider: str = None) -> str:
    """
    Dynamically resolves API credentials for any endpoint or direct provider.
    Order of precedence:
    1. Specific environment variable (e.g. NVIDIA_API_KEY, OPENROUTER_API_KEY, GROQ_API_KEY)
    2. Common aliases (e.g. NVIDIA_KEY, OPENROUTER_KEY, NV_API_KEY)
    3. Global exported $API_KEYS or $API_KEY from Termux:
       - JSON payload matching endpoint/provider name
       - Key-value string pairs (e.g. NVIDIA=... or OPENROUTER=...)
       - Token prefix heuristics (nvapi-* -> NVIDIA, sk-or-* -> OpenRouter, gsk_* -> Groq, AIza* -> Gemini)
       - Generic token fallback when a single key is exported in $API_KEYS or $API_KEY
    """
    # Standalone diagnostics can call this before the application imports config.
    # Initialize its literal secret loader before inspecting environment aliases.
    try:
        import config as credential_config
    except ImportError:
        pass
    ep_low = (endpoint_name or "").lower().strip()
    prov_low = (provider or "").lower().strip()

    ENV_MAP = {
        "cloudflare": ["CLOUDFLARE_API_TOKEN", "CLOUDFLARE_AUTH_TOKEN", "CLOUDFLARE_API_KEY", "CLOUDFLARE_TOKEN"],
        "nvidia": ["NVIDIA_API_KEY", "NVIDIA_KEY", "NV_API_KEY", "NVIDIA"],
        "openrouter": ["OPENROUTER_API_KEY", "OPENROUTER_KEY", "OR_API_KEY", "OPEN_ROUTER_API_KEY"],
        "groq": ["GROQ_API_KEY", "GROQ_KEY", "GROQ"],
        "modelscope": ["SCOPE_API_KEY", "MODELSCOPE_API_KEY", "MODELSCOPE_KEY", "SCOPE_KEY"],
        "local endpoint": ["LOCAL_API_KEY", "LOCAL_KEY", "LLAMA_API_KEY"],
        "local": ["LOCAL_API_KEY", "LOCAL_KEY", "LLAMA_API_KEY"],
    }
    PROVIDER_MAP = {
        "google": ["GEMINI_API_KEY", "GOOGLE_API_KEY", "GEMINI_KEY", "GOOGLE_KEY"],
        "cohere": ["COHERE_API_KEY", "COHERE_KEY"],
        "mistral": ["MISTRAL_API_KEY", "CODESTRAL_API_KEY", "MISTRAL_KEY"],
        "kilo": ["KILO_API_KEY", "KILO_KEY"],
        "llm7": ["LLM7_API_KEY", "LLM7_KEY"]
    }

    # Registry-defined endpoints can name their credential without custom routing.
    configured_env = ENDPOINT_REGISTRY.get(endpoint_name, {}).get('api_key_env')
    if configured_env and ep_low not in ENV_MAP:
        value = os.environ.get(configured_env, '').strip()
        if value:
            return value

    # Check direct provider first if specified
    if prov_low and prov_low in PROVIDER_MAP:
        for var in PROVIDER_MAP[prov_low]:
            val = os.environ.get(var)
            if val and val.strip():
                return val.strip()

    # Check endpoint map
    if ep_low in ENV_MAP:
        for var in ENV_MAP[ep_low]:
            val = os.environ.get(var)
            if val and val.strip():
                return val.strip()

    # Check Direct sub-providers when endpoint is Direct
    if ep_low == "direct" and not prov_low:
        for p_vars in PROVIDER_MAP.values():
            for var in p_vars:
                val = os.environ.get(var)
                if val and val.strip():
                    return val.strip()

    # Check exported $API_KEYS and $API_KEY in Termux
    for env_var in ("API_KEYS", "API_KEY"):
        raw_keys = os.environ.get(env_var, "").strip()
        if not raw_keys:
            continue

        # Scenario A: JSON object e.g. {"NVIDIA": "nvapi-...", "OpenRouter": "sk-or-..."}
        if raw_keys.startswith("{"):
            try:
                data = json.loads(raw_keys)
                if isinstance(data, dict):
                    lookup_names = [ep_low]
                    if prov_low:
                        lookup_names.append(prov_low)
                    for k, v in data.items():
                        k_clean = str(k).lower().strip()
                        if k_clean in lookup_names or any(k_clean.startswith(name) for name in lookup_names):
                            if str(v).strip():
                                return str(v).strip()
            except Exception:
                pass

        # Scenario B: Delimited key pairs e.g. "NVIDIA=nvapi-...,OPENROUTER=sk-or-..."
        if "=" in raw_keys or ":" in raw_keys:
            pairs = re.split(r'[,;\n\s]+', raw_keys)
            for pair in pairs:
                delim = "=" if "=" in pair else (":" if ":" in pair else None)
                if delim:
                    p_name, _, p_val = pair.partition(delim)
                    p_name_clean = p_name.lower().strip()
                    if p_name_clean == ep_low or (prov_low and p_name_clean == prov_low):
                        if p_val.strip():
                            return p_val.strip().strip("'\"")

        # Scenario C: Known key token prefix heuristics
        if ep_low == "nvidia" and raw_keys.startswith("nvapi-"):
            return raw_keys
        if ep_low == "openrouter" and (raw_keys.startswith("sk-or-") or raw_keys.startswith("sk-")):
            return raw_keys
        if ep_low == "groq" and raw_keys.startswith("gsk_"):
            return raw_keys
        if (ep_low == "direct" or prov_low == "google") and raw_keys.startswith("AIza"):
            return raw_keys
        if ep_low == "modelscope" and raw_keys.startswith("ms-"):
            return raw_keys

        # Scenario D: Raw single token key exported in $API_KEYS or $API_KEY
        if len(raw_keys) > 10 and not any(ch in raw_keys for ch in " ,;\n\t{"):
            return raw_keys

    # Check storage/keys.json via KEYS in config or direct file fallback
    keys_dict = {}
    try:
        from config import KEYS
        keys_dict = KEYS
    except Exception:
        pass

    if not keys_dict:
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for k_path in [
            os.path.join(base_dir, "storage", "keys.json"),
            os.path.expanduser("~/.galam_keys.json"),
            "/data/data/com.termux/files/home/.galam_keys.json"
        ]:
            if os.path.exists(k_path):
                try:
                    with open(k_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        if isinstance(data, dict):
                            keys_dict.update({str(k).lower(): str(v) for k, v in data.items()})
                except Exception:
                    pass

    if keys_dict:
        candidate_keys = [ep_low, f"{ep_low}_api_key", f"{ep_low}_key", f"{ep_low}_token", f"{ep_low}_api_token"]
        if prov_low:
            candidate_keys.extend([prov_low, f"{prov_low}_api_key", f"{prov_low}_key"])
        for ck in candidate_keys:
            val = keys_dict.get(ck)
            if val and str(val).strip():
                return str(val).strip()

    return ""

def build_request_data(endpoint_name: str, model_name: str, messages: list, stream: bool = True):
    url, headers, payload = _build_request_data(endpoint_name, model_name, messages, stream)
    from config import CONFIG
    from engine.context_manager import cap_request_output
    from engine.agent_router import request_headers
    headers = request_headers(endpoint_name, url, headers, stream, CONFIG)
    return url, headers, cap_request_output(payload, endpoint_name, model_name, CONFIG)


def _build_request_data(endpoint_name: str, model_name: str, messages: list, stream: bool = True):
    ep = ENDPOINT_REGISTRY.get(endpoint_name, {})
    models = ep.get("models", {})
    m_info = models.get(model_name, {})

    headers = {"Content-Type": "application/json"}
    payload = {"stream": stream}

    connection = m_info.get('custom_connection')
    if connection:
        api_key=os.environ.get(connection['api_key_env'],'')
        if api_key:headers['Authorization']='Bearer '+api_key
        return connection['url'],headers,{'model':model_name,'messages':messages,'stream':stream}

    # 1. CLOUDFLARE
    if endpoint_name == "Cloudflare":
        account_id = os.environ.get("CLOUDFLARE_ACCOUNT_ID")
        if not account_id:
            try:
                from config import KEYS
                account_id = KEYS.get("cloudflare_account_id") or KEYS.get("account_id")
            except Exception:
                account_id = None
        if not account_id:
            account_id = "default_account"
        token = get_endpoint_api_key("Cloudflare")
        headers["Authorization"] = f"Bearer {token}"

        route_type = m_info.get("route_type", "v1" if not model_name.startswith("@cf/") else "run")
        if route_type == "run":
            url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/{model_name}"
            payload = {"messages": messages, "stream": stream}
        else:
            url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1/chat/completions"
            payload = {"model": model_name, "messages": messages, "stream": stream}
        return url, headers, payload

    # 2. NVIDIA
    if endpoint_name == "NVIDIA":
        api_key = get_endpoint_api_key("NVIDIA")
        if api_key:
            os.environ["NVIDIA_API_KEY"] = api_key
        headers["Authorization"] = f"Bearer {api_key}"
        url = "https://integrate.api.nvidia.com/v1/chat/completions"
        payload = {
            "model": model_name,
            "messages": messages,
            "stream": stream,
            "temperature": 0.7,
            "max_tokens": 16384
        }
        return url, headers, payload

    # 3. OPENROUTER
    if endpoint_name == "OpenRouter":
        api_key = get_endpoint_api_key("OpenRouter")
        if api_key:
            os.environ["OPENROUTER_API_KEY"] = api_key
        headers["Authorization"] = f"Bearer {api_key}"
        headers["HTTP-Referer"] = "https://galam.ai.local"
        headers["X-Title"] = "GALAM.AI"
        url = "https://openrouter.ai/api/v1/chat/completions"
        payload = {
            "model": model_name,
            "messages": messages,
            "stream": stream
        }
        return url, headers, payload

    # 4. GROQ
    if endpoint_name == "Groq":
        api_key = get_endpoint_api_key("Groq")
        if api_key:
            os.environ["GROQ_API_KEY"] = api_key
        headers["Authorization"] = f"Bearer {api_key}"
        url = "https://api.groq.com/openai/v1/chat/completions"
        payload = {
            "model": model_name,
            "messages": messages,
            "stream": stream,
            "temperature": 0.7
        }
        return url, headers, payload

    # 5. MODELSCOPE
    if endpoint_name == "ModelScope":
        api_key = get_endpoint_api_key("ModelScope")
        if api_key:
            os.environ["SCOPE_API_KEY"] = api_key
        headers["Authorization"] = f"Bearer {api_key}"
        url = "https://api-inference.modelscope.cn/v1/chat/completions"
        payload = {
            "model": model_name,
            "messages": messages,
            "stream": stream
        }
        return url, headers, payload

    # 6. DIRECT SUB-PROVIDERS
    if endpoint_name == "Direct":
        provider = m_info.get("provider", "")
        if not provider:
            if "gemini" in model_name:
                provider = "google"
            elif "command" in model_name or "north-mini" in model_name:
                provider = "cohere"
            elif "mistral" in model_name or "codestral" in model_name:
                provider = "mistral"
            elif "claude" in model_name or "kilo" in model_name or "stepfun" in model_name:
                provider = "kilo"
            else:
                provider = "llm7"

        if provider == "google":
            api_key = get_endpoint_api_key("Direct", "google")
            if api_key:
                os.environ["GEMINI_API_KEY"] = api_key
            if stream:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:streamGenerateContent?alt=sse&key={api_key}"
            else:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"

            contents = []
            system_instruction_text = ""
            for m in messages:
                r = m.get("role", "")
                if r == "system":
                    system_instruction_text += m.get("content", "") + "\n"
                elif r in ("user", "human"):
                    contents.append({"role": "user", "parts": [{"text": m.get("content", "")}]})
                elif r in ("assistant", "model"):
                    contents.append({"role": "model", "parts": [{"text": m.get("content", "")}]})

            if not contents:
                contents.append({"role": "user", "parts": [{"text": "Hello"}]})

            payload = {
                "contents": contents,
                "generationConfig": {
                    "temperature": 0.7,
                    "maxOutputTokens": 8192,
                    "thinkingConfig": {
                        "thinkingBudget": 0
                    }
                }
            }
            if system_instruction_text.strip():
                payload["systemInstruction"] = {
                    "parts": [{"text": system_instruction_text.strip()}]
                }
            return url, headers, payload

        elif provider == "cohere":
            api_key = get_endpoint_api_key("Direct", "cohere")
            if api_key:
                os.environ["COHERE_API_KEY"] = api_key
            headers["Authorization"] = f"Bearer {api_key}"
            url = "https://api.cohere.com/v2/chat"
            payload = {"model": model_name, "messages": messages, "temperature": 0.6}
            return url, headers, payload

        elif provider == "mistral":
            api_key = get_endpoint_api_key("Direct", "mistral")
            if api_key:
                os.environ["MISTRAL_API_KEY"] = api_key
            headers["Authorization"] = f"Bearer {api_key}"
            url = "https://api.mistral.ai/v1/chat/completions"
            payload = {"model": model_name, "messages": messages, "stream": stream}
            return url, headers, payload

        elif provider == "kilo":
            api_key = get_endpoint_api_key("Direct", "kilo")
            if api_key:
                os.environ["KILO_API_KEY"] = api_key
            headers["Authorization"] = f"Bearer {api_key}"
            url = "https://api.kilo.ai/api/gateway/chat/completions"
            payload = {"model": model_name, "messages": messages, "stream": stream}
            return url, headers, payload

        else: # llm7
            api_key = get_endpoint_api_key("Direct", "llm7")
            if api_key:
                os.environ["LLM7_API_KEY"] = api_key
            headers["Authorization"] = f"Bearer {api_key}"
            url = "https://api.llm7.io/v1/chat/completions"
            payload = {"model": model_name, "messages": messages, "stream": stream}
            return url, headers, payload

    # 7. LOCAL ENDPOINT (llama.cpp / Ollama / Localhost)
    if endpoint_name == "Local Endpoint":
        from config import CONFIG
        from engine.local_models import local_base_url
        base_url = local_base_url(CONFIG)
        url = f"{base_url}/chat/completions"
        api_key = get_endpoint_api_key("Local Endpoint")
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        local_max = 8192
        try:
            local_max = int(CONFIG.get("max_tokens", 8192))
        except Exception:
            pass
        payload = {
            "model": model_name,
            "messages": messages,
            "stream": stream,
            "temperature": 0.2,
            "max_tokens": local_max
        }
        from engine.local_tuning import effective_tuning
        tuning = effective_tuning(CONFIG, model_name)
        if tuning:
            payload["temperature"] = tuning["temperature"]
            payload["max_tokens"] = tuning["max_tokens"]
        return url, headers, payload

    api_key = get_endpoint_api_key(endpoint_name)
    if api_key:
        headers['Authorization'] = f'Bearer {api_key}'
    url = f"{ep.get('base_url', '').rstrip('/')}/chat/completions"
    payload = {"model": model_name, "messages": messages, "stream": stream}
    return url, headers, payload
