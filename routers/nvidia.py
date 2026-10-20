import requests
from config import KEYS
from tools.bash import TOOLS_SCHEMA

def call_nvidia(messages, model_id, enable_tools=True):
    real_model = model_id.replace("nim/", "")
    url = "https://integrate.api.nvidia.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {KEYS['nvidia_api_key']}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": real_model,
        "messages": messages,
    }
    if enable_tools:
        payload["tools"] = TOOLS_SCHEMA

    return requests.post(url, headers=headers, json=payload, timeout=60)
