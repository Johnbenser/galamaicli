import os
import json
from storage.endpoints import ENDPOINT_REGISTRY

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ARTIFACTS_DIR = os.path.join(BASE_DIR, "artifacts")
CONVERSATIONS_DIR = os.path.join(BASE_DIR, "conversations")
STORAGE_DIR = os.path.join(BASE_DIR, "storage")
KEYS_FILE = os.path.join(STORAGE_DIR, "keys.json")

os.makedirs(ARTIFACTS_DIR, exist_ok=True)
os.makedirs(CONVERSATIONS_DIR, exist_ok=True)
os.makedirs(STORAGE_DIR, exist_ok=True)

ENDPOINTS_CATALOG = ENDPOINT_REGISTRY
CONFIG_FILE = os.path.expanduser("~/.galam_ai_settings.json")

CONFIG_CANDIDATES = [
    CONFIG_FILE,
    "/data/data/com.termux/files/home/.galam_ai_settings.json",
    os.path.expanduser("~/.galam_ai_settings.json"),
    "/root/.galam_ai_settings.json",
    os.path.join(STORAGE_DIR, "settings.json")
]

def load_keys():
    """Loads API credentials from storage/keys.json and user dotfiles into KEYS and environment."""
    keys = {}
    candidates = [
        KEYS_FILE,
        os.path.expanduser("~/.galam_keys.json"),
        "/data/data/com.termux/files/home/.galam_keys.json",
        os.path.expanduser("~/.galam_keys"),
        "/data/data/com.termux/files/home/.galam_keys"
    ]
    for c in candidates:
        if os.path.exists(c):
            try:
                with open(c, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        for k, v in data.items():
                            if v and str(v).strip():
                                keys[k.strip().lower()] = str(v).strip()
            except Exception:
                pass

    key_env_mappings = {
        "openrouter_api_key": "OPENROUTER_API_KEY",
        "openrouter": "OPENROUTER_API_KEY",
        "nvidia_api_key": "NVIDIA_API_KEY",
        "nvidia": "NVIDIA_API_KEY",
        "groq_api_key": "GROQ_API_KEY",
        "groq": "GROQ_API_KEY",
        "cloudflare_api_token": "CLOUDFLARE_API_TOKEN",
        "cloudflare_token": "CLOUDFLARE_API_TOKEN",
        "cloudflare_account_id": "CLOUDFLARE_ACCOUNT_ID",
        "gemini_api_key": "GEMINI_API_KEY",
        "google_api_key": "GOOGLE_API_KEY",
        "cohere_api_key": "COHERE_API_KEY",
        "mistral_api_key": "MISTRAL_API_KEY",
        "scope_api_key": "SCOPE_API_KEY",
        "kilo_api_key": "KILO_API_KEY",
        "llm7_api_key": "LLM7_API_KEY",
        "agent_router_api_key": "AGENT_ROUTER_API_KEY"
    }

    for k, env_name in key_env_mappings.items():
        v = keys.get(k)
        if v and str(v).strip() and env_name not in os.environ:
            os.environ[env_name] = str(v).strip()

    return keys

def save_keys(keys_dict: dict):
    global KEYS
    KEYS.update({k.strip().lower(): str(v).strip() for k, v in keys_dict.items() if v})
    try:
        with open(KEYS_FILE, "w", encoding="utf-8") as f:
            json.dump(KEYS, f, indent=2)
    except Exception:
        pass
    return KEYS

KEYS = load_keys()

def _load_env_from_bashrc():
    candidates = [
        os.path.expanduser("~/.bashrc"),
        "/data/data/com.termux/files/home/.bashrc",
        os.path.expanduser("~/.bash_profile"),
        os.path.expanduser("~/.profile"),
        "/data/data/com.termux/files/home/.profile",
        os.path.expanduser("~/.zshrc"),
        "/data/data/com.termux/files/home/.zshrc",
        "/data/data/com.termux/files/usr/etc/bash.bashrc"
    ]
    for bashrc_candidate in candidates:
        if os.path.exists(bashrc_candidate):
            try:
                with open(bashrc_candidate, "r", encoding="utf-8", errors="replace") as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith("export "):
                            line = line[7:].strip()
                            if "=" in line:
                                k, v = line.split("=", 1)
                                k = k.strip()
                                v = v.strip().strip("'\"")
                                if k and k not in os.environ:
                                    os.environ[k] = v
            except Exception:
                pass

_load_env_from_bashrc()


def _load_secret_files(candidates=None):
    """Read literal credential assignments; never source or execute secret files."""
    import re
    import shlex
    if candidates is None:
        candidates = [os.path.expanduser('~/.secrets'), '/data/data/com.termux/files/home/.secrets']
    for path in dict.fromkeys(candidates):
        try:
            with open(path,encoding='utf-8') as source:
                for line in source:
                    match = re.match(r'^\s*(?:export\s+)?([A-Z][A-Z0-9_]*)\s*=\s*(.*)$',line)
                    if not match or match[1] in os.environ:
                        continue
                    if not (match[1] in ('API_KEY','API_KEYS') or match[1].endswith(('_API_KEY','_API_TOKEN','_AUTH_TOKEN','_ACCOUNT_ID'))):
                        continue
                    try:
                        values=shlex.split(match[2],comments=True)
                    except ValueError:
                        continue
                    if len(values)==1 and values[0]:
                        os.environ[match[1]]=values[0]
        except OSError:
            continue


_load_secret_files()

# Re-run load_keys to sync any freshly loaded env keys
KEYS = load_keys()

DEFAULT_VALUES = {
    "local_manager": True,
    "active_endpoint": "OpenRouter" if KEYS.get("openrouter_api_key") else "Groq",
    "endpoint": "OpenRouter" if KEYS.get("openrouter_api_key") else "Groq",
    "active_model": "nvidia/nemotron-3.5-lightning:free" if KEYS.get("openrouter_api_key") else "qwen/qwen3.8-27b",
    "model": "nvidia/nemotron-3.5-lightning:free" if KEYS.get("openrouter_api_key") else "qwen/qwen3.8-27b",
    "mode": "review",
    "fallback_policy": "ask",  # Options: "ask" (Ask Every Time) or "always" (Always Proceed)
    "stream": True,
    "temperature": 0.7,
    "max_tokens": 4096,
    "max_iterations": 25,
    "task_request_limit": 128,
    "task_token_limit": 120000,
    "task_time_limit_seconds": 1800,
    "timeout": 60,
    "auto_execute": False
}

class SafeConfig(dict):
    def __getitem__(self, key):
        if key not in self:
            if key in DEFAULT_VALUES:
                return DEFAULT_VALUES[key]
            return None
        return super().__getitem__(key)

    def get(self, key, default=None):
        val = super().get(key, default)
        if val is None and key in DEFAULT_VALUES:
            return DEFAULT_VALUES[key]
        return val

def load_config():
    cfg = SafeConfig(DEFAULT_VALUES)
    for c_path in CONFIG_CANDIDATES:
        if os.path.exists(c_path):
            try:
                with open(c_path, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                    if isinstance(saved, dict):
                        cfg.update(saved)
                        break
            except Exception:
                pass

    if "active_endpoint" in cfg and "endpoint" not in cfg:
        cfg["endpoint"] = cfg["active_endpoint"]
    if "endpoint" in cfg and "active_endpoint" not in cfg:
        cfg["active_endpoint"] = cfg["endpoint"]
    if "active_model" in cfg and "model" not in cfg:
        cfg["model"] = cfg["active_model"]
    if "model" in cfg and "active_model" not in cfg:
        cfg["active_model"] = cfg["model"]
    if "fallback_policy" not in cfg:
        cfg["fallback_policy"] = "ask"

    # If active_endpoint is set to an endpoint without credentials, and OpenRouter has credentials, auto-switch default
    act_ep = cfg.get("active_endpoint", "")
    if act_ep in ("Cloudflare", "NVIDIA", "Groq") and not os.environ.get(f"{act_ep.upper()}_API_KEY") and not os.environ.get(f"{act_ep.upper()}_API_TOKEN") and not KEYS.get(f"{act_ep.lower()}_api_key"):
        if KEYS.get("openrouter_api_key") or os.environ.get("OPENROUTER_API_KEY"):
            cfg["active_endpoint"] = "OpenRouter"
            cfg["endpoint"] = "OpenRouter"
            cfg["active_model"] = "nvidia/nemotron-3.5-lightning:free"
            cfg["model"] = "nvidia/nemotron-3.5-lightning:free"

    return cfg

def save_settings(endpoint_or_dict=None, model=None, **kwargs):
    global CONFIG
    if isinstance(endpoint_or_dict, dict):
        CONFIG.update(endpoint_or_dict)
    elif endpoint_or_dict and model:
        CONFIG["endpoint"] = str(endpoint_or_dict)
        CONFIG["active_endpoint"] = str(endpoint_or_dict)
        CONFIG["model"] = str(model)
        CONFIG["active_model"] = str(model)
    elif endpoint_or_dict:
        CONFIG["endpoint"] = str(endpoint_or_dict)
        CONFIG["active_endpoint"] = str(endpoint_or_dict)

    if kwargs:
        CONFIG.update(kwargs)
        if "active_model" in kwargs and "model" not in kwargs:
            CONFIG["model"] = kwargs["active_model"]
        if "model" in kwargs and "active_model" not in kwargs:
            CONFIG["active_model"] = kwargs["model"]
        if "active_endpoint" in kwargs and "endpoint" not in kwargs:
            CONFIG["endpoint"] = kwargs["active_endpoint"]
        if "endpoint" in kwargs and "active_endpoint" not in kwargs:
            CONFIG["active_endpoint"] = kwargs["endpoint"]

    # Save to all writable configuration candidate locations to keep Termux and root synced
    target_paths = list(dict.fromkeys([
        CONFIG_FILE,
        "/data/data/com.termux/files/home/.galam_ai_settings.json",
        os.path.join(STORAGE_DIR, "settings.json")
    ]))

    for p in target_paths:
        try:
            p_dir = os.path.dirname(p)
            if os.path.exists(p_dir):
                with open(p, "w", encoding="utf-8") as f:
                    json.dump(dict(CONFIG), f, indent=2)
        except Exception:
            pass

    return CONFIG

save_config = save_settings
CONFIG = load_config()

AGENT_STATE = {
    "current_thought": "",
    "past_thoughts": [],
    "app_ref": None,
    'is_thinking': False,
    'abort': False,
    'working_directory': os.getcwd()
}
