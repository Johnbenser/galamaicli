import requests
from config import KEYS
from tools.bash import TOOLS_SCHEMA

def call_openrouter(messages, model_id, enable_tools=True):
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {KEYS['openrouter_api_key']}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://galam.ai.local",
        "X-Title": "GALAM.AI",
    }
    payload = {
        "model": model_id,
        "messages": messages,
    }
    if enable_tools:
        payload["tools"] = TOOLS_SCHEMA

    return requests.post(url, headers=headers, json=payload, timeout=60)
