import os
import json
import time
import threading
from typing import Dict, Any, Optional

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOGS_DIR = os.path.join(BASE_DIR, "logs")
MODELS_LOGS_DIR = os.path.join(LOGS_DIR, "models")
GALAM_LOGS_DIR = os.path.join(LOGS_DIR, "galam")

os.makedirs(MODELS_LOGS_DIR, exist_ok=True)
os.makedirs(GALAM_LOGS_DIR, exist_ok=True)

_LOG_LOCK = threading.Lock()

def _sanitize_filename(name: str) -> str:
    """Sanitizes model name to safe filesystem characters."""
    return name.replace("/", "_").replace(":", "_").replace("@", "_").strip()

def log_model_interaction(
    model_name: str,
    endpoint: str,
    user_prompt: str,
    response_content: str,
    reasoning: str = "",
    tool_calls: Optional[list] = None,
    error: Optional[str] = None,
    http_metrics: Optional[Dict[str, Any]] = None,
    redundancies: Optional[list] = None,
    improvements: Optional[list] = None,
    metadata: Optional[Dict[str, Any]] = None
):
    """
    Logs an isolated record for a specific AI model.
    CRITICAL: Never read into the LLM context to avoid burning tokens.
    Used exclusively for developer inspection and offline performance fixes.
    """
    safe_name = _sanitize_filename(model_name)
    log_path = os.path.join(MODELS_LOGS_DIR, f"{safe_name}.jsonl")
    
    # Calculate load performance diagnosis
    latency_ms = http_metrics.get("duration_ms", 0) if http_metrics else 0
    http_diagnosis = None
    if latency_ms > 15000:
        http_diagnosis = f"High latency ({latency_ms:.0f}ms): Server cold start, queue congestion, or deep reasoning generation"
    elif latency_ms > 8000:
        http_diagnosis = f"Moderate delay ({latency_ms:.0f}ms): Normal for large parameter reasoning models"

    entry = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "epoch": time.time(),
        "model": model_name,
        "endpoint": endpoint,
        "user_prompt": user_prompt,
        "full_thinking": reasoning,
        "response_content": response_content,
        "tool_calls_count": len(tool_calls) if tool_calls else 0,
        "tool_calls": tool_calls or [],
        "http_request": {
            "status_code": http_metrics.get("status_code") if http_metrics else None,
            "duration_ms": latency_ms,
            "streamed": http_metrics.get("streamed", True) if http_metrics else True,
            "diagnosis": http_diagnosis,
            "url": http_metrics.get("url", "") if http_metrics else "",
            "error": error
        },
        "error": error,
        "redundancies": redundancies or [],
        "improvements": improvements or [],
        "metadata": metadata or {}
    }
    
    with _LOG_LOCK:
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:
            pass

def log_tool_execution(
    tool_name: str,
    args: Dict[str, Any],
    stdout_preview: str,
    duration_ms: float,
    success: bool,
    exit_code: int = 0,
    model_name: str = ""
):
    """
    Logs tool and bash command executions with duration, exit status, and output.
    """
    date_str = time.strftime("%Y-%m-%d")
    log_path = os.path.join(GALAM_LOGS_DIR, f"tool_executions_{date_str}.jsonl")

    entry = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "epoch": time.time(),
        "tool_name": tool_name,
        "arguments": args,
        "duration_ms": duration_ms,
        "success": success,
        "exit_code": exit_code,
        "output_preview": stdout_preview[:800] if stdout_preview else "",
        "invoking_model": model_name
    }

    with _LOG_LOCK:
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:
            pass

def log_galam_system_event(
    event_type: str,
    summary: str,
    details: Optional[Dict[str, Any]] = None,
    level: str = "ERROR"
):
    """
    Captures system-level runtime errors in Galam.ai:
    - Unexpected aborts or cancellations
    - Structural and logic failures
    - Handshake and fallback continuity issues
    - Tool execution failures & process clashes
    """
    date_str = time.strftime("%Y-%m-%d")
    log_path = os.path.join(GALAM_LOGS_DIR, f"galam_system_{date_str}.jsonl")
    
    entry = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "epoch": time.time(),
        "level": level.upper(),
        "event_type": event_type,
        "summary": summary,
        "details": details or {}
    }
    
    with _LOG_LOCK:
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:
            pass
