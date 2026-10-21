from engine.api_protocols import streaming_response, stream_data
import sys
import os
import re
import json
import time
import uuid
import shutil
import requests
from requests.adapters import HTTPAdapter
from collections import defaultdict
from config import CONFIG, save_settings, AGENT_STATE
from storage.endpoints import ENDPOINT_REGISTRY
from engine.payload_builder import build_request_data
from engine.tool_parser import clean_response_text, render_markdown_response
from engine.logger import log_model_interaction, log_galam_system_event
from ui.dock_layout import StructuredThoughtStreamHandler, request_user_confirmation

from engine.tool_protocol import options as tool_options, reject_unsupported
from engine.transparency import ReasoningStream, text_value
from engine import usage as usage_meter
from engine.context_manager import estimate_tokens
from engine.agent_router import client_rejection, ClientAdmissionError, is_agent_router_url
from engine import cancellation


def terminal_quota_error(error):
    text = str(error).lower()
    return any(term in text for term in ('resourceexhausted', 'insufficient_quota',
        'total request limit reached', 'daily limit reached', 'quota exhausted',
        'free-models-per-day','daily quota','daily rate limit','per-day limit',
        'monthly quota','account quota exceeded'))

STATS = defaultdict(int)


class ProviderStreamError(requests.exceptions.RequestException):
    """An upstream error delivered inside an otherwise successful HTTP response."""


def _raise_provider_error(data):
    if not isinstance(data, dict):
        return
    error = data.get("error") or (data.get("errors") if data.get("success") is False else None)
    if not error and data.get("type") == "error":
        error = data.get("message") or "Provider stream failed"
    if error:
        detail = (error.get("message") or error.get("detail") or error.get("code")) if isinstance(error, dict) else error
        diagnosis = client_rejection(CONFIG.get('active_endpoint'),
            ENDPOINT_REGISTRY.get(CONFIG.get('active_endpoint'), {}).get('base_url', ''), data)
        if diagnosis:
            raise ProviderStreamError(diagnosis)
        raise ProviderStreamError(str(detail or "Provider returned an error")[:1000])

SESSION = requests.Session()
adapter = HTTPAdapter(pool_connections=15, pool_maxsize=15, max_retries=1)
SESSION.mount("https://", adapter)
SESSION.mount("http://", adapter)

def urllib_stream_request(url: str, headers: dict, payload: dict, timeout: int = 60):
    """
    Pure Python standard-library curl fallback via urllib.request.
    Guarantees resilient connectivity when requests encounters socket/cffi issues in Termux.
    """
    import urllib.request
    import urllib.error
    import ssl
    clean_headers = {k: v for k, v in headers.items() if k.lower() != "connection"}
    data_bytes = json.dumps(payload).encode("utf-8") if payload else None
    req = urllib.request.Request(url, data=data_bytes, headers=clean_headers, method="POST")

    try:
        ctx = ssl.create_default_context()
        return urllib.request.urlopen(req, timeout=timeout, context=ctx)
    except urllib.error.HTTPError as error:
        if is_agent_router_url(url):
            try:
                data = json.loads(error.read().decode('utf-8', errors='replace'))
                diagnosis = client_rejection('Agent Router', url, data)
            except ValueError:
                diagnosis = ''
            finally:
                error.close()
            if diagnosis:
                raise ClientAdmissionError(diagnosis) from error
            raise
        unverified_ctx = ssl._create_unverified_context()
        return urllib.request.urlopen(req, timeout=timeout, context=unverified_ctx)
    except Exception:
        if is_agent_router_url(url):
            raise  # Keep certificate verification for Agent Router's fallback transport.
        unverified_ctx = ssl._create_unverified_context()
        return urllib.request.urlopen(req, timeout=timeout, context=unverified_ctx)


def _recorded_post(url, headers, payload, stream_enabled, timeout):
    from engine.api_protocols import prepare_payload
    payload=prepare_payload(url,payload)
    model = payload.get('model') or (url.split('/models/',1)[1].split(':',1)[0] if '/models/' in url else CONFIG.get('active_model',''))
    row = usage_meter.begin_request(CONFIG.get('active_endpoint',''),model,payload,delta='/ai/run/' in url)
    try:
        response = cancellation.call(lambda: SESSION.post(url,headers=headers,json=payload,
            stream=stream_enabled,timeout=timeout), dispose=lambda response: response.close())
        return usage_meter.attach(response,row)
    except Exception as error:
        row['transport_error']=type(error).__name__
        raise


def _recorded_urllib(url,headers,payload,timeout):
    from engine.api_protocols import prepare_payload
    payload=prepare_payload(url,payload)
    row = usage_meter.begin_request(CONFIG.get('active_endpoint',''),payload.get('model',CONFIG.get('active_model','')),payload,delta='/ai/run/' in url)
    try:
        response = cancellation.call(lambda: urllib_stream_request(url,headers,payload,timeout=timeout),
            dispose=lambda response: response.close())
        return usage_meter.attach(response,row)
    except Exception as error:
        row['transport_error']=type(error).__name__
        raise


def _response_json(response,is_urllib=False):
    data = cancellation.call(lambda: json.loads(response.read().decode('utf-8',errors='replace')) if is_urllib else response.json())
    usage_meter.capture(response,data)
    if isinstance(data,dict):
        from engine.api_protocols import remember_reasoning,response_chunk
        remember_reasoning(response,data)
        if data.get('object')=='response' and data.get('status') in ('completed','incomplete','failed'):
            try:response._galam_finish_reason=response_chunk({'type':'response.'+data['status'],'response':data},response)[3]
            except ValueError as error:raise ProviderStreamError(str(error)) from None
    row=getattr(response,'_galam_usage_record',{})
    if isinstance(row,dict):
        content,reasoning=_extract_response_content(row.get('endpoint',''),row.get('model',''),data)
        usage_meter.record_output(response,content,reasoning,_detect_and_synthesize_tool_calls('',data))
    return data


def _post_with_transport_retry(url, headers, payload, stream_enabled, read_timeout, local=False):
    """Retry a transient transport failure without dropping the current task."""
    attempts = 2 if local else 1
    last_error = None
    for attempt in range(attempts):
        try:
            response = _recorded_post(url,headers,payload,stream_enabled,(6,read_timeout))
            delay = _rate_limit_delay(response)
            if delay is not None and not AGENT_STATE.get("abort", False):
                response.close()
                cancellation.wait(delay)
                if AGENT_STATE.get("abort", False):
                    raise requests.exceptions.RequestException("Request cancelled during quota cooldown")
                response = _recorded_post(url,headers,payload,stream_enabled,(6,read_timeout))
                response._galam_rate_limit_retried = True
            return response, False
        except Exception as requests_error:
            if isinstance(requests_error, cancellation.TaskCancelled):
                raise
            if terminal_quota_error(requests_error):
                raise ProviderStreamError(str(requests_error)) from requests_error
            last_error = requests_error
            try:
                return _recorded_urllib(url,headers,payload,read_timeout), True
            except Exception as urllib_error:
                if isinstance(urllib_error, cancellation.TaskCancelled):
                    raise
                last_error = urllib_error
                if attempt + 1 < attempts:
                    time.sleep(0.5)
    raise last_error


def _rate_limit_delay(response):
    """One bounded retry of this same request, only when upstream gives a cooldown."""
    if response.status_code != 429:
        return None
    raw = response.headers.get("Retry-After", "")
    try:
        delay = float(raw)
    except (TypeError, ValueError):
        delay = None
        if raw:
            try:
                from email.utils import parsedate_to_datetime
                delay = parsedate_to_datetime(raw).timestamp() - time.time()
            except (ValueError, TypeError, OverflowError):
                pass
        if delay is None:
            try:
                text = json.dumps(response.json())
            except (ValueError, TypeError):
                text = ""
            match = re.search(r'(?:retryDelay["\s:]+|(?:retry|try again) in\s+)(\d+(?:\.\d+)?)s', text, re.I)
            if match:
                delay = float(match.group(1)) + 0.5
    return max(0.1, delay) if delay is not None and 0 <= delay <= 55 else None

def load_model_rules(model_name: str) -> dict:
    m_lower = model_name.lower()
    cfg_dir = os.path.join(os.path.dirname(__file__), "..", "config", "models")

    if "glm" in m_lower or "zai" in m_lower:
        target_file = "glm.json"
    elif "deepseek" in m_lower:
        target_file = "deepseek.json"
    elif "mistral" in m_lower or "codestral" in m_lower:
        target_file = "mistral.json"
    elif "qwen" in m_lower:
        target_file = "qwen.json"
    elif "nemotron" in m_lower:
        target_file = "nemotron.json"
    elif "gemini" in m_lower:
        target_file = "gemini.json"
    elif "llama" in m_lower:
        target_file = "llama.json"
    elif "grok" in m_lower or "xai" in m_lower:
        target_file = "grok.json"
    elif "kimi" in m_lower or "moonshot" in m_lower:
        target_file = "kimi.json"
    elif "liquid" in m_lower or "lfm" in m_lower:
        target_file = "liquid.json"
    elif "cohere" in m_lower or "north" in m_lower:
        target_file = "cohere.json"
    else:
        target_file = "default.json"

    file_path = os.path.join(cfg_dir, target_file)
    if os.path.exists(file_path):
        try:
            with open(file_path, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

class AgentResponse(dict):
    def __init__(self, content="", role="assistant", reasoning="", tool_calls=None, streamed=False, raw=None):
        super().__init__(role=role, content=content)
        self.content = content
        self.role = role
        self.reasoning = reasoning
        self.tool_calls = tool_calls or []
        self.streamed = streamed
        self["tool_calls"] = self.tool_calls
        self["streamed"] = self.streamed

    def __str__(self):
        return self.content

    def __getitem__(self, key):
        if key in ("content", "role", "reasoning", "tool_calls", "streamed"):
            return getattr(self, key)
        return super().__getitem__(key)

    def get(self, key, default=None):
        if key in ("content", "role", "reasoning", "tool_calls", "streamed"):
            return getattr(self, key)
        return super().get(key, default)


def _detect_and_synthesize_tool_calls(text: str, resp_json: dict = None) -> list:
    if resp_json and isinstance(resp_json, dict):
        data = resp_json.get("result", resp_json)
        if isinstance(data, dict):
            if 'output' in data:
                from engine.api_protocols import output_parts
                tools=output_parts(data)[2]
                if tools:return tools
            if isinstance(data.get('message'),dict) and data['message'].get('tool_calls'):
                from engine.api_protocols import ollama_tools
                return ollama_tools(data['message'])
            choices = data.get("choices", [])
            if choices and isinstance(choices[0], dict):
                msg = choices[0].get("message", {})
                if "tool_calls" in msg and msg["tool_calls"]:
                    return msg["tool_calls"]

    from engine.tool_parser import extract_tools_from_text
    return extract_tools_from_text(text)



def _extract_stream_chunk(chunk_json,response=None):
    content_delta = ""
    reasoning_delta = ""
    tool_call_deltas = []
    finish_reason = ""
    if not isinstance(chunk_json, dict):
        return content_delta, reasoning_delta, tool_call_deltas, finish_reason
    _raise_provider_error(chunk_json)
    from engine.api_protocols import response_chunk
    try:converted=response_chunk(chunk_json,response)
    except ValueError as error:raise ProviderStreamError(str(error)) from None
    if converted is not None:return converted

    if isinstance(chunk_json.get('message'),dict):
        from engine.api_protocols import ollama_chunk
        return ollama_chunk(chunk_json,response)

    # Cohere v2 carries text in delta.message.content rather than choices[].
    if chunk_json.get("type") == "content-delta":
        message = chunk_json.get("delta", {}).get("message", {})
        return text_value(message.get("content", "")), "", [], ""
    if chunk_json.get("type") == "message-end":
        reason = chunk_json.get("delta", {}).get("finish_reason", "")
        return "", "", [], "length" if reason == "MAX_TOKENS" else "stop"

    if "candidates" in chunk_json and chunk_json["candidates"]:
        cand = chunk_json["candidates"][0]
        finish_reason = cand.get("finishReason", "") or ""
        parts = cand.get("content", {}).get("parts", [])
        for p in parts:
            if isinstance(p, dict):
                if p.get("thought"):
                    reasoning_delta += p.get("text", "")
                elif "text" in p:
                    content_delta += p.get("text", "")
                elif "functionCall" in p:
                    fc = p["functionCall"]
                    import uuid
                    tool_call_deltas.append({
                        "index": 0,
                        "id": f"call_{uuid.uuid4().hex[:8]}",
                        "type": "function",
                        "function": {
                            "name": fc.get("name", ""),
                            "arguments": json.dumps(fc.get("args", {}))
                        }
                    })
        return content_delta, reasoning_delta, tool_call_deltas, finish_reason

    data = chunk_json.get("result", chunk_json)
    if not isinstance(data, dict):
        return content_delta, reasoning_delta, tool_call_deltas, finish_reason
    _raise_provider_error(data)

    if data.get("type") in ("response.reasoning_text.delta", "response.reasoning_summary_text.delta"):
        return "", text_value(data.get("delta", "")), tool_call_deltas, finish_reason

    if data.get("type") == "response.output_text.delta":
        return data.get("delta", ""), "", tool_call_deltas, finish_reason

    if "response" in data and isinstance(data["response"], (str, int, float, bool)):
        return text_value(data["response"]), "", tool_call_deltas, finish_reason

    if "choices" in data and data["choices"]:
        choice = data["choices"][0]
        if isinstance(choice, dict):
            finish_reason = choice.get("finish_reason", "") or ""
            delta = choice.get("delta", {})
            if isinstance(delta, dict):
                content_delta = text_value(delta.get("content", ""))
                reasoning_delta = (
                    delta.get("reasoning_content") or
                    delta.get("reasoning") or
                    delta.get("thought") or delta.get("reasoning_details") or ""
                )
                reasoning_delta = text_value(reasoning_delta)
                tool_call_deltas = delta.get("tool_calls", []) or []
            elif "text" in choice:
                content_delta = choice.get("text", "") or ""

    return content_delta, reasoning_delta, tool_call_deltas, finish_reason


def _is_stream_truncated(finish_reason: str, full_content: str, full_reasoning: str, streamed_tool_calls: dict) -> bool:
    """
    Detects whether an LLM generation stream terminated prematurely due to free endpoint
    token limits, SSE disconnects, or mid-sentence cut-offs.
    """
    if finish_reason and finish_reason.lower() in ("length", "max_tokens", "length_limit"):
        return True

    # 1. Unclosed thinking tags (monologue cut off mid-thought)
    if "<think>" in full_content and "</think>" not in full_content:
        return True
    if "<thought>" in full_content and "</thought>" not in full_content:
        return True

    # 2. Unclosed tool call tags
    for open_tag, close_tag in [
        ("<tool_call>", "</tool_call>"),
        ("<invoke", "</invoke>"),
        ("<action", "</action>"),
        ("<|tool_call_start|>", "<|tool_call_end|>"),
        ("<|im_start|>tool_call", "<|im_end|>")
    ]:
        if open_tag in full_content and close_tag not in full_content:
            return True

    # 3. Streamed tool call with unclosed arguments JSON
    if streamed_tool_calls:
        for v in streamed_tool_calls.values():
            fn = v.get("function", {})
            args_str = fn.get("arguments", "")
            if args_str and (args_str.count("{") > args_str.count("}") or args_str.count('"') % 2 != 0):
                return True

    # 4. Trailing mid-sentence / connector words indicating truncation
    stripped = full_content.strip()
    if stripped:
        # A sentence ending with comma, dash, ellipsis, or opening bracket is definitely truncated
        if stripped[-1] in (',', '-', '—', '…', ':', ';', '(', '[', '{', '/', '\\'):
            return True
        if stripped[-1] not in ('.', '!', '?', '`', '}', ']', '>', '"', "'"):
            last_word = stripped.split()[-1].lower().rstrip('",\';:')
            if last_word in (
                "to", "have", "and", "the", "that", "with", "is", "a", "an",
                "of", "for", "in", "on", "it", "be", "as", "by", "at", "or",
                "from", "which", "will", "would", "could", "should", "can",
                "if", "not", "but", "so", "because", "also", "way", "then",
                "this", "these", "those"
            ):
                return True

    # 5. Internal thinking consumed token budget without emitting complete answer or tool
    if full_reasoning and len(full_reasoning.strip()) > 100:
        if not stripped or (len(stripped) < 25 and not any(ch in stripped for ch in ('.', '!', '?', '}'))):
            return True

    return False


def _extract_response_content(endpoint_name: str, model_name: str, resp_json):
    if isinstance(resp_json, list):
        text_parts = []
        reasoning_parts = []
        for item in resp_json:
            if isinstance(item, dict):
                cands = item.get("candidates", [])
                if cands and isinstance(cands[0], dict):
                    parts = cands[0].get("content", {}).get("parts", [])
                    for p in parts:
                        if isinstance(p, dict):
                            if p.get("thought"):
                                reasoning_parts.append(p.get("text", ""))
                            elif "text" in p:
                                text_parts.append(p.get("text", ""))
        if text_parts or reasoning_parts:
            return "".join(text_parts).strip(), "".join(reasoning_parts).strip()
        return "", ""

    if not isinstance(resp_json, dict):
        return "", ""
    _raise_provider_error(resp_json)

    if "candidates" in resp_json and resp_json["candidates"]:
        cand = resp_json["candidates"][0]
        parts = cand.get("content", {}).get("parts", [])
        text_parts = []
        reasoning_parts = []
        for p in parts:
            if isinstance(p, dict):
                if p.get("thought"):
                    reasoning_parts.append(p.get("text", ""))
                elif "text" in p:
                    text_parts.append(p.get("text", ""))
        return "".join(text_parts).strip(), "".join(reasoning_parts).strip()

    data = resp_json.get("result", resp_json)
    if not isinstance(data, dict):
        return "", ""
    _raise_provider_error(data)

    if isinstance(data.get("message"), dict):
        message = data["message"]
        return text_value(message.get("content", "")).strip(), text_value(message.get("thinking") or message.get("reasoning", "")).strip()

    if 'output' in data:
        from engine.api_protocols import output_parts
        content,reasoning,_,_=output_parts(data)
        return content.strip(),reasoning.strip()

    if "output_text" in data and isinstance(data["output_text"], str):
        if data["output_text"].strip():
            return data["output_text"].strip(), ""

    if "choices" in data and isinstance(data["choices"], list) and data["choices"]:
        choice = data["choices"][0]
        if isinstance(choice, dict):
            msg = choice.get("message", {})
            if isinstance(msg, dict):
                reasoning = (
                    msg.get("reasoning_content") or
                    msg.get("reasoning") or
                    msg.get("thought") or msg.get("reasoning_details") or ""
                )
                content = msg.get("content") or ""
                if content or reasoning:
                    return text_value(content).strip(), text_value(reasoning).strip()
            if "text" in choice and choice["text"]:
                return str(choice["text"]).strip(), ""

    if "response" in data and isinstance(data["response"], str):
        return data["response"].strip(), ""

    for key in ("generated_text", "completion", "answer", "content"):
        if key in data and isinstance(data[key], str) and data[key].strip():
            return data[key].strip(), ""

    return "", ""


def _legacy_build_enforced_messages(messages, active_ep, active_m, native_tools=True, track_usage=True):
    from storage.conversations import scoped_messages
    messages = scoped_messages(messages)
    clean_model_tag = active_m.split("/")[-1].replace("@cf/", "").replace("meta/", "")
    rules = load_model_rules(active_m)
    custom_sys = rules.get("system_instruction", "")

    from engine.task_runner import GLOBAL_TASK_MANAGER, workspace_snapshot
    active_tasks = GLOBAL_TASK_MANAGER.get_active_tasks()
    if active_tasks:
        task_lines = []
        for t in active_tasks:
            task_lines.append(f"  - [Task ID {t['id']}] PID: {t['pid']} · Command: `{t['cmd']}` (Started: {t['start_time']} · Port: {t.get('port', '5500')})")
        task_context = "ACTIVE BACKGROUND TASKS IN GALAM.AI:\n" + "\n".join(task_lines) + "\nTo stop them, invoke `manage_tasks` or `execute_bash` with `kill <PID>`."
    else:
        task_context = "ACTIVE BACKGROUND TASKS IN GALAM.AI: None"

    identity_prompt = f"""You are Galam.ai, created by John Benser, an autonomous coding and terminal assistant on Android/Termux.
Active runtime: [{active_ep}], model [{clean_model_tag}]. The current user is John Benser.
{task_context}

Answer ordinary questions directly. For requested actions, inspect the real workspace and execute the necessary tools; never claim an action ran without its result.
Use the native tool interface when supplied. Otherwise use complete calls in this format:
<tool_call>{{"name":"execute_bash","arguments":{{"command":"pwd"}}}}</tool_call>
Available tools: make_directory(path), execute_bash(command,reason), view_file(path,start_line,end_line), search_files(query,directory), write_file(path,content,overwrite), replace_file_content(path,target_content,replacement_content), manage_tasks(action,target).
For navigation use cd; never create a directory merely because the user asked to go there. For 'go to PATH then COMMAND' use 'cd PATH && COMMAND'. Inspect package.json or existing entrypoints before starting a project server.
For building or creating code, use write_file(path,content) with actual implementation, not touch or an empty placeholder. Use execute_bash for commands and checks; a successful mkdir/touch does not complete a content request.
Inspect existing source before editing, preserve unrelated code, and use unique search/replace blocks for focused changes. After tool results, acknowledge the actual outcome and continue with the next needed action until the request is satisfied.
If an action fails, use its real error to choose a corrective step. Keep unfinished requirements explicit. Never invent successful commands, files, tests, or server readiness. For completed tasks, report what changed and the verification evidence concisely.
{custom_sys}"""

    working_directory = AGENT_STATE.get("working_directory") or os.getcwd()
    identity_prompt += (
        f"\nCURRENT WORKING DIRECTORY: {working_directory}\n"
        "This directory persists across turns. Resolve relative file paths and commands from here. "
        "If the user says 'there' or names a child as /child after visiting a directory, "
        "check that child inside the current directory when /child does not exist. "
        "Remember paths and results already provided; do not ask the user to repeat them. "
        "After a failed path, inspect the current directory and nearby names, then correct it."
    )
    identity_prompt += "\n\nLIVE WORKSPACE SNAPSHOT (from disk, current turn):\n" + workspace_snapshot(working_directory)
    identity_prompt += ("\nUse these real names before proposing commands. For code changes, inspect the exact existing file "
                        "and use replace_file_content for a small change. Verify the edited file afterward.")
    identity_prompt += ("\nAction output is stored: read_action_output(action_id,start_line,end_line,query) "
                        "retrieves omitted lines. Use undo_file_change(edit_id) only when the user requests "
                        "recovery of a Galam edit; use the returned edit ID or 'last'. "
                        "After an edit, run relevant project checks when available. A saved file is not proof "
                        "that tests passed. Keep unfinished work explicit. Bash examples are not actions.")
    from engine.context_manager import assemble_context
    from engine.context_manager import estimate_tokens
    protocol = tool_options(active_ep, active_m, messages, CONFIG) if native_tools else {}
    context_config = {**CONFIG, "_tool_schema_tokens": estimate_tokens(protocol) + 16 if protocol else 0}
    if protocol:
        from engine.task_contract import TaskContract
        from engine.context_manager import latest_request
        contract = TaskContract(latest_request(messages), working_directory)
        if contract.complete_scope:
            context_config["_simple_filesystem"] = True
            identity_prompt = (f"You are Galam.ai, John Benser's terminal assistant. Endpoint: {active_ep}. "
                               f"Current directory: {working_directory}.\n{custom_sys}\n"
                               "The request names explicit filesystem outputs. Preserve requested content and file/directory types. "
                               "Use make_directory for folders and write_file for complete file content. Inspect existing files before edits. "
                               "A tool result describes an action already executed; do not repeat it or make unrelated changes. "
                               "Only report completion after every requested output is verified. A saved file does not prove tests passed.\n"
                               "Named outputs still missing or mismatched: " + ("; ".join(contract.missing()) or "none"))
        identity_prompt += ("\nNATIVE TOOL PROTOCOL: Prefer the supplied function interface over text/XML wrappers. "
                            "Do not duplicate a native call in prose. Tool results report actions already executed. "
                            "When the user requirements are satisfied, return a concise final answer without further edits.")
        from engine.tool_protocol import wire_messages
        context_messages = wire_messages(messages)
    else:
        context_messages = [m for m in messages if m.get("content") or not m.get("_galam_tool_calls")]
    from engine.action_instructions import response_contract
    from engine.action_results import request_intent
    from engine.context_manager import latest_request
    if request_intent(latest_request(messages))[0]:
        identity_prompt += response_contract(native=bool(protocol))
    enforced, usage = assemble_context(context_messages, identity_prompt, active_ep, active_m, context_config, working_directory)
    if track_usage:
        AGENT_STATE["context_usage"] = usage
    return enforced



def build_enforced_messages(messages, active_ep, active_m, native_tools=True, track_usage=True):
    from engine.token_policy import enabled
    if not enabled(CONFIG, 'prompts'):
        return _legacy_build_enforced_messages(messages, active_ep, active_m, native_tools, track_usage)
    from storage.conversations import scoped_messages
    from engine.context_manager import assemble_context, estimate_tokens, latest_request, contextual_action
    from engine.action_results import request_intent
    from engine.action_instructions import response_contract
    from engine.prompt_context import identity
    from engine.task_runner import GLOBAL_TASK_MANAGER, workspace_snapshot
    from engine.tool_protocol import wire_messages, wire_text_messages
    messages = scoped_messages(messages)
    query = latest_request(messages)
    action = contextual_action(messages) or any(m.get('_galam_tool_mode') == 'required' for m in messages[-2:])
    directory = AGENT_STATE.get('working_directory') or os.getcwd()
    workspace = action or bool(re.search(r'current (?:directory|folder)|\b(?:my|this|our) (?:project|repo|code|files|folder)\b', query, re.I))
    snapshot = workspace_snapshot(directory, limit=16) if workspace else ''
    if not re.search(r'\b(?:go|navigate|find|directory|folder|cd)\b', query, re.I):
        snapshot = snapshot.split('\nNearby directories:', 1)[0]
    protocol = tool_options(active_ep, active_m, messages, CONFIG) if native_tools else {}
    prompt = identity(active_ep, active_m, load_model_rules(active_m).get('system_instruction',''),
                      action, directory, snapshot, GLOBAL_TASK_MANAGER.get_active_tasks() if action else ())
    if action:
        prompt += response_contract(bool(protocol))
    context_config = dict(CONFIG, _tool_schema_tokens=estimate_tokens(protocol)+16 if protocol else 0)
    context_messages = wire_messages(messages) if protocol else wire_text_messages(messages)
    enforced, usage = assemble_context(context_messages, prompt, active_ep, active_m, context_config, directory)
    if track_usage:
        AGENT_STATE['context_usage'] = usage
    return enforced


def query_subagent(messages: list) -> str:
    from storage.conversations import scoped_messages
    messages = scoped_messages(messages)
    stream_enabled = CONFIG.get("stream", True)
    active_ep = CONFIG.get("active_endpoint") or CONFIG.get("endpoint", "Direct")
    active_m = CONFIG.get("active_model") or CONFIG.get("model", "gemini-3.5-flash")

    if active_ep == "Local Endpoint" and not ENDPOINT_REGISTRY[active_ep]["models"].get(active_m,{}).get("custom_connection"):
        from engine.local_models import refresh_local_models
        advertised = refresh_local_models(CONFIG)
        if advertised and active_m not in advertised:
            active_m = advertised[0]

    models_in_ep = list(ENDPOINT_REGISTRY[active_ep]["models"].keys())
    ordered_models = [active_m] + [m for m in models_in_ep if m != active_m]

    for model_name in ordered_models:
        enforced_msgs = build_enforced_messages(messages, active_ep, model_name, native_tools=False, track_usage=False)
        resp = None
        try:
            url, headers, payload = build_request_data(
                endpoint_name=active_ep,
                model_name=model_name,
                messages=enforced_msgs,
                stream=stream_enabled
            )
            headers["Connection"] = "keep-alive"

            rules = load_model_rules(model_name)
            if isinstance(payload, dict):
                if "chat_template_kwargs" in rules:
                    payload["chat_template_kwargs"] = rules["chat_template_kwargs"]
                if "reasoning" in rules:
                    payload["reasoning"] = rules["reasoning"]

            read_timeout = max(90, rules.get("timeout", 60)) if active_ep == "Local Endpoint" else 20
            resp = _recorded_post(url,headers,payload,stream_enabled,(4,read_timeout))
            if not resp.ok:
                try:
                    error_body = resp.json()
                    diagnosis = client_rejection(active_ep, url, error_body)
                except ValueError:
                    diagnosis, error_body = '', {}
                if diagnosis:
                    resp.close()
                    return diagnosis
                if terminal_quota_error(error_body):
                    resp.close()
                    return '/btw stopped: request quota exhausted.'
                continue

            full_content = ""
            from engine.transparency import begin_generation, append_reasoning
            reasoning_row = begin_generation(model_name + " (side request)")
            splitter = ReasoningStream()
            if stream_enabled and streaming_response(resp.headers.get("Content-Type", "")):
                for line in cancellation.lines(resp.iter_lines(), close=getattr(resp, "close", None)):
                    if not line:
                        continue
                    decoded = line.decode("utf-8").strip()
                    if stream_data(decoded) is not None:
                        raw_chunk = stream_data(decoded)
                        if raw_chunk == "[DONE]":
                            break
                        try:
                            chunk_json = json.loads(raw_chunk)
                            usage_meter.capture(resp,chunk_json)
                            c_tok, r_tok, side_tools, _ = _extract_stream_chunk(chunk_json,resp)
                            usage_meter.record_output(resp,c_tok,r_tok,side_tools)
                            c_tok, r_tok = splitter.feed(c_tok, r_tok)
                            full_content += c_tok
                            if r_tok:
                                append_reasoning(reasoning_row, r_tok, update_summary=False)
                        except ProviderStreamError:
                            raise
                        except Exception:
                            continue
            else:
                resp_json = _response_json(resp)
                full_content, r_tok = _extract_response_content(active_ep, model_name, resp_json)
                full_content, r_tok = splitter.feed(full_content, r_tok)
                if r_tok:
                    append_reasoning(reasoning_row, r_tok, update_summary=False)
            tail, r_tail = splitter.feed(final=True)
            full_content += tail
            if r_tail:
                append_reasoning(reasoning_row, r_tail, update_summary=False)

            if full_content.strip():
                return full_content.strip()

        except requests.exceptions.RequestException as error:
            if isinstance(error,cancellation.TaskCancelled):
                raise
            if terminal_quota_error(error):
                return '/btw stopped: request quota exhausted.'
            diagnosis = client_rejection(active_ep, url, str(error))
            if diagnosis:
                return diagnosis
            continue
        except Exception:
            continue
        finally:
            if resp is not None and hasattr(resp,'close'):
                resp.close()

    return "Sub-agent could not complete query under active endpoint."


def query_with_fallback(*args, **kwargs):
    visited = set(kwargs.pop('_visited_endpoints', ()))
    raw_messages = kwargs.get("messages")
    if raw_messages is None and len(args) > 0:
        raw_messages = args[0]
    if raw_messages is None:
        raw_messages = []
    from storage.conversations import scoped_messages
    raw_messages = scoped_messages(raw_messages)
    usage_start = len(usage_meter.ledger()['requests'])

    stream_enabled = CONFIG.get("stream", True)
    active_ep = CONFIG.get("active_endpoint") or CONFIG.get("endpoint", "Direct")
    active_m = CONFIG.get("active_model") or CONFIG.get("model", "gemini-3.5-flash")
    if active_ep in visited:
        return 'EXHAUSTED', active_m
    visited.add(active_ep)

    if active_ep == "Local Endpoint" and not ENDPOINT_REGISTRY[active_ep]["models"].get(active_m,{}).get("custom_connection"):
        from engine.local_models import refresh_local_models
        advertised = refresh_local_models(CONFIG)
        if advertised and active_m not in advertised:
            active_m = advertised[0]
            save_settings(active_model=active_m, model=active_m)

    models_in_ep = [
        m for m in ENDPOINT_REGISTRY[active_ep]["models"].keys()
        if not (active_ep == "Cloudflare" and not m.startswith("@cf/") and not ENDPOINT_REGISTRY[active_ep]["models"][m].get("custom_connection"))
    ]
    ordered_models = [active_m] if active_m in models_in_ep else []
    ordered_models += [m for m in models_in_ep if m not in ordered_models]
    if not ordered_models and models_in_ep:
        ordered_models = list(models_in_ep)
    clean_model_tag = active_m.split("/")[-1].replace("@cf/", "").replace("meta/", "")

    for model_name in ordered_models:
        if AGENT_STATE.get("abort", False):
            break

        messages = build_enforced_messages(raw_messages, active_ep, model_name)
        request_context_usage = dict(AGENT_STATE.get("context_usage", {}))
        thought_ui = StructuredThoughtStreamHandler(model_tag=clean_model_tag)
        thought_ui.show_spinner("Waiting for model response…")

        try:
            url, headers, payload = build_request_data(
                endpoint_name=active_ep,
                model_name=model_name,
                messages=messages,
                stream=stream_enabled
            )
            payload.update(tool_options(active_ep, model_name, raw_messages, CONFIG))
            headers["Connection"] = "keep-alive"

            rules = load_model_rules(model_name)
            if isinstance(payload, dict):
                if "chat_template_kwargs" in rules:
                    payload["chat_template_kwargs"] = rules["chat_template_kwargs"]
                if "reasoning" in rules:
                    payload["reasoning"] = rules["reasoning"]

            stream_mode = rules.get("stream_mode", "medium")
            read_timeout = rules.get("timeout", 60)
            if stream_mode == "high":
                read_timeout = max(read_timeout, 90)
            elif stream_mode == "low":
                read_timeout = min(read_timeout, 40)

            is_urllib = False
            resp = None
            req_start_time = time.time()
            try:
                resp, is_urllib = _post_with_transport_retry(
                    url, headers, payload, stream_enabled, read_timeout,
                    local=active_ep == "Local Endpoint",
                )
            except Exception as conn_err:
                if isinstance(conn_err, cancellation.TaskCancelled):
                    return 'CANCELLED', active_m
                if terminal_quota_error(conn_err):
                    sys.stdout.write(f'\n[!] Request quota exhausted: {conn_err}. Task stopped.\n')
                    sys.stdout.flush()
                    return 'FAILED', active_m
                if isinstance(conn_err, ClientAdmissionError):
                    thought_ui.clear()
                    sys.stdout.write(f'\n[!] {conn_err}\n')
                    sys.stdout.flush()
                    return 'FAILED', active_m
                if active_ep == "Local Endpoint" and not ENDPOINT_REGISTRY[active_ep]["models"].get(model_name,{}).get("custom_connection"):
                    from engine.local_server import ensure_local_server
                    started, start_message = ensure_local_server(url.rsplit("/chat/completions", 1)[0])
                    sys.stdout.write(f"\r\033[38;5;244m    {start_message}\033[0m\n")
                    sys.stdout.flush()
                    if started:
                        try:
                            resp, is_urllib = _post_with_transport_retry(
                                url, headers, payload, stream_enabled, read_timeout, local=True,
                            )
                        except Exception as retry_error:
                            conn_err = retry_error
                        else:
                            conn_err = None
                if conn_err is None:
                    pass
                else:
                    thought_ui.clear()
                    AGENT_STATE["is_streaming"] = False
                    err_str = "timed out" if "timeout" in str(conn_err).lower() else "unreachable"
                    sys.stdout.write(f"\r\033[1;31m[-] [{active_ep}] {model_name} {err_str}: {conn_err}\033[0m\n")
                    sys.stdout.flush()
                    if active_ep == "Local Endpoint":
                        sys.stdout.write("\033[38;5;244m    Local model connection failed after retry; the task is incomplete.\033[0m\n\n")
                        sys.stdout.flush()
                        return "FAILED", active_m
                    cur_idx = ordered_models.index(model_name)
                    next_m = ordered_models[cur_idx + 1] if cur_idx + 1 < len(ordered_models) else None
                    fallback_policy = CONFIG.get("fallback_policy", "ask").lower()
                    if next_m:
                        if fallback_policy == "ask":
                            prompt_str = f"\033[1;37m    Would you like to fallback to [{next_m}]? [Y/n]: \033[0m"
                            u_ans = request_user_confirmation(prompt_str, default="y", prompt_label=f"Fallback [{next_m}] [Y/n]> ")
                            if u_ans in ("n", "no", "cancel"):
                                sys.stdout.write("\033[38;5;244m[-] Model fallback cancelled by user.\033[0m\n\n")
                                sys.stdout.flush()
                                break
                        save_settings(active_model=next_m, model=next_m)
                    continue

            if AGENT_STATE.get("abort", False):
                thought_ui.clear()
                AGENT_STATE["is_streaming"] = False
                sys.stdout.write("\n\033[1;33m[!] Generation cancelled by user.\033[0m\n")
                sys.stdout.flush()
                break

            if not is_urllib and reject_unsupported(resp, active_ep, model_name, CONFIG, payload):
                if hasattr(resp, "close"):
                    resp.close()
                payload.pop("tools", None)
                payload.pop("tool_choice", None)
                payload["messages"] = build_enforced_messages(raw_messages, active_ep, model_name)
                thought_ui.show_spinner("Using the text tool protocol")
                resp, is_urllib = _post_with_transport_retry(url, headers, payload, stream_enabled, read_timeout,
                                                            local=active_ep == "Local Endpoint")

            if not is_urllib and not resp.ok:
                thought_ui.clear()
                AGENT_STATE["is_streaming"] = False
                sc = resp.status_code
                err_detail = ""
                err_json = {}
                try:
                    err_json = resp.json()
                    error = err_json.get('error') or {}
                    err_detail = (error.get('message') if isinstance(error, dict) else str(error)) or err_json.get('message', '')
                except Exception:
                    pass
                if not err_detail and resp.text:
                    err_detail = resp.text.strip()[:100]

                diagnosis = client_rejection(active_ep, url, err_json or err_detail)
                if diagnosis:
                    sys.stdout.write(f'\n[!] HTTP {sc}: {diagnosis}\n')
                    sys.stdout.flush()
                    resp.close()
                    return 'FAILED', active_m
                if terminal_quota_error(err_json or err_detail):
                    sys.stdout.write(f'\n[!] Request quota exhausted (HTTP {sc}): {err_detail}. Task stopped.\n')
                    sys.stdout.flush()
                    resp.close()
                    return 'FAILED', active_m

                if sc == 400 and ("context size" in err_detail.lower() or "exceeds" in err_detail.lower()):
                    status_desc = f"HTTP 400 - Context limit exceeded ({err_detail})"
                elif sc == 401:
                    status_desc = "unauthorized (missing/invalid API key)"
                elif sc == 402:
                    status_desc = "payment required (out of credits)"
                elif sc == 403:
                    status_desc = "forbidden (access denied)"
                elif sc == 404:
                    status_desc = "not found (invalid endpoint or model)"
                elif sc == 410:
                    status_desc = "model unavailable or retired (HTTP 410 Gone)"
                elif sc == 429:
                    status_desc = "rate limited (upstream quota limit)"
                elif sc >= 500:
                    status_desc = f"server error (HTTP {sc})"
                else:
                    status_desc = f"HTTP {sc}" + (f" ({err_detail})" if err_detail else "")
                sys.stdout.write(f"\r\033[38;5;244m[-] [{active_ep}] {model_name} {status_desc}\033[0m\n")
                sys.stdout.flush()

                if active_ep == "Local Endpoint":
                    if sc == 400 and ("context size" in err_detail.lower() or "exceeds" in err_detail.lower()):
                        sys.stdout.write(f"\033[1;33m[!] Context size limit reached on local llama-server.\033[0m\n")
                        sys.stdout.write(f"\033[38;5;244m    Fix: Run llama-server with `-c 32768` for max context, or use /clear to reset history.\033[0m\n\n")
                        sys.stdout.flush()
                    return "FAILED", active_m

                cur_idx = ordered_models.index(model_name)
                next_m = ordered_models[cur_idx + 1] if cur_idx + 1 < len(ordered_models) else None
                fallback_policy = CONFIG.get("fallback_policy", "ask").lower()
                if next_m:
                    if fallback_policy == "ask":
                        prompt_str = f"\033[1;37m    Would you like to fallback to [{next_m}]? [Y/n]: \033[0m"
                        u_ans = request_user_confirmation(prompt_str, default="y", prompt_label=f"Fallback [{next_m}] [Y/n]> ")
                        if u_ans in ("n", "no", "cancel"):
                            sys.stdout.write("\033[38;5;244m[-] Model fallback cancelled by user.\033[0m\n\n")
                            sys.stdout.flush()
                            break
                        else:
                            save_settings(active_model=next_m, model=next_m)
                    else:
                        save_settings(active_model=next_m, model=next_m)
                        sys.stdout.write(f"\033[38;5;120m[⚡] Auto-falling back to [{next_m}]...\033[0m\n")
                        sys.stdout.flush()
                continue

            full_content = ""
            full_reasoning = ""
            reasoning_stream = ReasoningStream()
            streamed_tool_calls = {}
            resp_json = None
            last_finish_reason = ""
            retained_reasoning=[]
            AGENT_STATE["is_streaming"] = True

            c_type = resp.headers.get("Content-Type", "") if hasattr(resp, "headers") else (resp.info().get("Content-Type", "") if hasattr(resp, "info") else "")
            if stream_enabled and streaming_response(c_type):
                try:
                    line_iter = cancellation.lines(resp if is_urllib else resp.iter_lines(), close=getattr(resp, "close", None))
                    for line in line_iter:
                        if AGENT_STATE.get("abort", False):
                            thought_ui.clear()
                            sys.stdout.write("\n\033[1;33m[!] Generation cancelled by user.\033[0m\n")
                            sys.stdout.flush()
                            break

                        if not line:
                            continue
                        decoded = line.decode("utf-8").strip()
                        if stream_data(decoded) is not None:
                            raw_chunk = stream_data(decoded)
                            if raw_chunk == "[DONE]":
                                break
                            try:
                                chunk_json = json.loads(raw_chunk)
                                usage_meter.capture(resp,chunk_json)
                                c_tok, r_tok, tc_deltas, f_reason = _extract_stream_chunk(chunk_json,resp)
                                usage_meter.record_output(resp,c_tok,r_tok,tc_deltas)
                                if f_reason:
                                    last_finish_reason = f_reason

                                # Accumulate streamed tool calls (native OpenAI/OpenRouter stream format)
                                if tc_deltas:
                                    thought_ui.show_spinner("Receiving structured action arguments…")
                                for tc in tc_deltas:
                                    idx = tc.get("index", 0)
                                    if idx not in streamed_tool_calls:
                                        streamed_tool_calls[idx] = {
                                            "id": tc.get("id") or f"call_{uuid.uuid4().hex[:8]}",
                                            "type": "function",
                                            "function": {"name": "", "arguments": ""}
                                        }
                                    fn = tc.get("function", {})
                                    if fn.get("name"):
                                        streamed_tool_calls[idx]["function"]["name"] += fn["name"]
                                    if fn.get("arguments"):
                                        streamed_tool_calls[idx]["function"]["arguments"] += fn["arguments"]

                                c_tok, r_tok = reasoning_stream.feed(c_tok, r_tok)

                                if r_tok:
                                    thought_ui.update_thought(r_tok)
                                    full_reasoning += r_tok

                                if c_tok:
                                    full_content += c_tok
                                    thought_ui.show_spinner("Responding...")

                            except ProviderStreamError:
                                raise
                            except Exception:
                                continue

                except cancellation.TaskCancelled:
                    raise
                except Exception as stream_error:
                    if isinstance(stream_error, ProviderStreamError):
                        raise
                    log_galam_system_event(
                        event_type="MODEL_STREAM_ERROR",
                        summary=f"Stream interrupted for {model_name}: {stream_error}",
                        details={"endpoint": active_ep, "model": model_name, "error": str(stream_error)},
                    )

                if reasoning_stream.in_reasoning or reasoning_stream.pending:
                    last_finish_reason = "length"
                thought_ui.clear()
                AGENT_STATE["is_streaming"] = False

                if (not full_content and not full_reasoning and not streamed_tool_calls
                        and not reasoning_stream.in_reasoning and not reasoning_stream.pending):
                    # A local stream can close before sending any tokens. Retry once
                    # without streaming before giving up on this user request.
                    if active_ep == "Local Endpoint" and not AGENT_STATE.get("abort", False):
                        retry_payload = dict(payload)
                        retry_payload["stream"] = False
                        try:
                            retry_resp, retry_urllib = _post_with_transport_retry(
                                url, headers, retry_payload, False, read_timeout, local=True,
                            )
                            retry_json = _response_json(retry_resp,retry_urllib)
                            resp_json = retry_json
                            full_content, full_reasoning = _extract_response_content(active_ep, model_name, retry_json)
                        except Exception as retry_error:
                            sys.stdout.write(f"\n[!] Local model stream disconnected; retry failed: {retry_error}\n")
                            sys.stdout.flush()
                        if not full_content and not _detect_and_synthesize_tool_calls("", resp_json):
                            return "FAILED", active_m
                    else:
                        continue

            else:
                resp_json = _response_json(resp,is_urllib)
                thought_ui.clear()
                AGENT_STATE["is_streaming"] = False
                full_content, full_reasoning = _extract_response_content(active_ep, model_name, resp_json)
                last_finish_reason=getattr(resp,'_galam_finish_reason','')

            # --- AUTO-CONTINUATION FOR TOKEN-LIMITED FREE ENDPOINTS ---
            retained_reasoning.extend(getattr(resp,'_galam_response_reasoning',[]))
            # If the stream cut off, hit max_tokens, or ended mid-sentence/mid-tool-call,
            # automatically treat the remaining generation as a continuation request so it
            # seamlessly continues all along without aborting or stalling.
            max_continuations = 3
            cont_idx = 0
            while cont_idx < max_continuations and not AGENT_STATE.get("abort", False):
                if not _is_stream_truncated(last_finish_reason, full_content, full_reasoning, streamed_tool_calls):
                    break
                cont_idx += 1
                thought_ui.show_spinner(f"Output truncated by token limit · Auto-continuing ({cont_idx}/{max_continuations})...")
                AGENT_STATE["is_streaming"] = True

                cont_messages = list(raw_messages)
                stripped_content = full_content.strip()
                if stripped_content:
                    cont_messages.append({"role": "assistant", "content": stripped_content})
                    if retained_reasoning:
                        cont_messages[-1]['_galam_response_reasoning']=list(retained_reasoning)
                    tail = stripped_content[-80:] if len(stripped_content) > 80 else stripped_content
                    if any(t in stripped_content for t in ("<tool_call>", "<invoke>", "<action>")) and not any(t in stripped_content for t in ("</tool_call>", "</invoke>", "</action>")):
                        cont_prompt = f"Your tool call was cut off at: '{tail}'. Complete the tool call immediately and close it with </tool_call>."
                    else:
                        cont_prompt = f"Your response was truncated by the token limit at: '{tail}'. Continue directly from where you left off without repeating prior words. Output the remainder or tool call immediately."
                    cont_messages.append({"role": "user", "content": cont_prompt})
                else:
                    if full_reasoning.strip():
                        cont_prompt = "Your internal reasoning reached the token limit before generating your answer. Do not spend further tokens on reasoning. Immediately emit your final answer or autonomous <tool_call> block."
                    else:
                        cont_prompt = "Your stream ended abruptly. Complete your response or autonomous tool call immediately."
                    cont_messages.append({"role": "user", "content": cont_prompt})

                c_resp=None
                try:
                    c_enforced = build_enforced_messages(cont_messages, active_ep, model_name)
                    c_url, c_headers, c_payload = build_request_data(
                        endpoint_name=active_ep,
                        model_name=model_name,
                        messages=c_enforced,
                        stream=stream_enabled
                    )
                    c_payload.update(tool_options(active_ep, model_name, raw_messages, CONFIG))
                    c_headers["Connection"] = "keep-alive"
                    if isinstance(c_payload, dict):
                        if "chat_template_kwargs" in rules:
                            c_payload["chat_template_kwargs"] = rules["chat_template_kwargs"]
                        if "reasoning" in rules:
                            c_payload["reasoning"] = rules["reasoning"]

                    c_resp = None
                    c_is_urllib = False
                    try:
                        c_resp = _recorded_post(c_url,c_headers,c_payload,stream_enabled,(6,read_timeout))
                    except Exception:
                        try:
                            c_resp = _recorded_urllib(c_url,c_headers,c_payload,read_timeout)
                            c_is_urllib = True
                        except Exception:
                            break

                    if not c_is_urllib and not c_resp.ok:
                        break

                    c_type_cont = c_resp.headers.get("Content-Type", "") if hasattr(c_resp, "headers") else (c_resp.info().get("Content-Type", "") if hasattr(c_resp, "info") else "")
                    last_finish_reason = "stop"
                    if stream_enabled and streaming_response(c_type_cont):
                        c_line_iter = cancellation.lines(c_resp if c_is_urllib else c_resp.iter_lines(), close=getattr(c_resp, "close", None))
                        for c_line in c_line_iter:
                            if AGENT_STATE.get("abort", False):
                                break
                            if not c_line:
                                continue
                            c_decoded = c_line.decode("utf-8").strip()
                            if stream_data(c_decoded) is not None:
                                c_raw = stream_data(c_decoded)
                                if c_raw == "[DONE]":
                                    break
                                try:
                                    c_chunk_json = json.loads(c_raw)
                                    usage_meter.capture(c_resp,c_chunk_json)
                                    c_c_tok, c_r_tok, c_tc_deltas, c_f_reason = _extract_stream_chunk(c_chunk_json,c_resp)
                                    usage_meter.record_output(c_resp,c_c_tok,c_r_tok,c_tc_deltas)
                                    if c_f_reason:
                                        last_finish_reason = c_f_reason

                                    if c_tc_deltas:
                                        thought_ui.show_spinner("Receiving structured action arguments…")
                                    for tc in c_tc_deltas:
                                        idx = tc.get("index", 0)
                                        if idx not in streamed_tool_calls:
                                            streamed_tool_calls[idx] = {
                                                "id": tc.get("id") or f"call_{uuid.uuid4().hex[:8]}",
                                                "type": "function",
                                                "function": {"name": "", "arguments": ""}
                                            }
                                        fn = tc.get("function", {})
                                        if fn.get("name"):
                                            streamed_tool_calls[idx]["function"]["name"] += fn["name"]
                                        if fn.get("arguments"):
                                            streamed_tool_calls[idx]["function"]["arguments"] += fn["arguments"]

                                    c_c_tok, c_r_tok = reasoning_stream.feed(c_c_tok, c_r_tok)

                                    if c_r_tok:
                                        thought_ui.update_thought(c_r_tok)
                                        full_reasoning += c_r_tok

                                    if c_c_tok:
                                        full_content += c_c_tok
                                        thought_ui.show_spinner("Continuing response...")
                                except ProviderStreamError:
                                    raise
                                except Exception:
                                    continue
                    else:
                        c_resp_json = _response_json(c_resp,c_is_urllib)
                        if _detect_and_synthesize_tool_calls("", c_resp_json):
                            resp_json = c_resp_json
                        c_part, r_part = _extract_response_content(active_ep, model_name, c_resp_json)
                        if c_part:
                            full_content += (" " + c_part) if full_content else c_part
                        if r_part:
                            full_reasoning += ("\n\n" + r_part) if full_reasoning else r_part
                        last_finish_reason = getattr(c_resp,'_galam_finish_reason','stop')
                except cancellation.TaskCancelled:
                    raise
                except ProviderStreamError:
                    raise
                except Exception:
                    break
                finally:
                    thought_ui.clear()
                    AGENT_STATE["is_streaming"] = False
                    if c_resp is not None:
                        retained_reasoning.extend(getattr(c_resp,'_galam_response_reasoning',[]))


            tail_content, tail_reason = reasoning_stream.feed(final=True)
            full_content += tail_content
            full_reasoning += tail_reason
            if tail_reason:
                thought_ui.update_thought(tail_reason)

            # Assemble streamed tools if any were accumulated
            final_streamed_tools = []
            for k, v in sorted(streamed_tool_calls.items()):
                fn_name = v.get("function", {}).get("name", "")
                args_str = v.get("function", {}).get("arguments", "{}")
                from engine.tool_parser import _validate_and_normalize_tool
                try:
                    complete_args = json.loads(args_str)
                except (ValueError, TypeError):
                    continue
                validated = _validate_and_normalize_tool(fn_name, complete_args)
                if validated:
                    norm_name, norm_args = validated
                    final_streamed_tools.append({
                        "id": v.get("id", f"call_{uuid.uuid4().hex[:8]}"),
                        "type": "function",
                        "function": {
                            "name": norm_name,
                            "arguments": json.dumps(norm_args)
                        }
                    })

            json_tools = _detect_and_synthesize_tool_calls("", resp_json) if resp_json else []
            if last_finish_reason in ("tool_calls", "function_call") and not final_streamed_tools and not json_tools:
                # Some providers finish a stream with tool_calls but omit the
                # call/argument deltas. No action has run: retrieve one complete
                # response from the same model instead of treating reasoning as
                # an action or asking it to repeat its plan.
                thought_ui.show_spinner("Retrieving the complete structured action…")
                if hasattr(resp, "close"):
                    resp.close()
                retry_payload = dict(payload, stream=False)
                retry_resp, retry_urllib = _post_with_transport_retry(
                    url, headers, retry_payload, False, read_timeout,
                    local=active_ep == "Local Endpoint")
                if not retry_urllib and not retry_resp.ok:
                    raise requests.exceptions.RequestException(
                        f"Structured action retry failed (HTTP {retry_resp.status_code})")
                resp_json = _response_json(retry_resp,retry_urllib)
                retry_content, retry_reason = _extract_response_content(active_ep, model_name, resp_json)
                if retry_content:
                    full_content = retry_content
                if retry_reason:
                    full_reasoning = (full_reasoning + "\n\n" + retry_reason).strip()
                json_tools = _detect_and_synthesize_tool_calls("", resp_json)
            if not full_content.strip() and not full_reasoning.strip() and not final_streamed_tools and not json_tools:
                continue

            # Separate untagged internal monologue from user-facing text
            from engine.tool_parser import parse_reasoning_and_content
            parsed_reason, clean_body = parse_reasoning_and_content(full_content)
            if parsed_reason:
                full_reasoning = (full_reasoning + "\n\n" + parsed_reason).strip()
                full_content = clean_body

            thought_ui.complete_reasoning(full_reasoning)
            thought_ui.clear()

            # If full_content is empty (because the entire stream was classified as reasoning)
            # and no tool call is detected, ensure the AI is never silent:
            if not full_content.strip() and not final_streamed_tools and not json_tools:
                full_content = "The model did not provide a final answer or a complete action."

            # Prioritize natively streamed tools, then content parsing, then confident reasoning
            tool_calls = final_streamed_tools or json_tools
            if not tool_calls:
                tool_calls = _detect_and_synthesize_tool_calls(full_content, resp_json)

            # Check if this query turn follows a successful tool observation
            is_post_tool = False
            if raw_messages:
                last_m = raw_messages[-1]
                if last_m.get("role") == "user" and "[Tool Observation for" in str(last_m.get("content", "")):
                    if "Status: Tool step executed successfully" in str(last_m.get("content", "")):
                        is_post_tool = True

            # Reasoning is not an action proposal. The agent loop can request a complete
            # explicit call when an actionable request remains unfinished.

            # Identify redundant behavior or potential improvements
            redundancies = []
            improvements = []
            if full_content.count("<tool_call>") > 1:
                redundancies.append("Multiple tool_call blocks in single response")
            if any(phrase in full_content.lower() for phrase in ("as an ai", "i cannot", "you can run this command yourself")):
                redundancies.append("Passive disclaimer detected")
                improvements.append("Enforce autonomous tool action instead of conversational advice")

            user_prompt = ""
            for m in reversed(raw_messages):
                if m.get("role") == "user":
                    user_prompt = m.get("content", "")
                    break

            req_duration_ms = (time.time() - req_start_time) * 1000.0
            status_code = getattr(resp, "status_code", 200) if not is_urllib else getattr(resp, "status", 200)

            log_model_interaction(
                model_name=model_name,
                endpoint=active_ep,
                user_prompt=user_prompt[:500],
                response_content=full_content,
                reasoning=full_reasoning,
                tool_calls=tool_calls,
                http_metrics={
                    "status_code": status_code,
                    "duration_ms": req_duration_ms,
                    "streamed": stream_enabled,
                    "url": url
                },
                redundancies=redundancies,
                improvements=improvements,
                metadata={"tokens_approx": len(full_content) // 4, "context_budget": request_context_usage,
                          "usage": usage_meter.snapshot(usage_start),
                          "estimated_generated_tokens": estimate_tokens(
                              full_content+full_reasoning+json.dumps(tool_calls))}
            )

            save_settings(active_model=model_name, model=model_name)
            response_obj = AgentResponse(
                content=full_content,
                role="assistant",
                reasoning=full_reasoning,
                tool_calls=tool_calls,
                streamed=True,
                raw={"model": model_name}
            )
            response_obj.response_reasoning=retained_reasoning
            response_obj.response_origin=[active_ep,model_name,url]
            return response_obj, model_name

        except requests.exceptions.RequestException as req_err:
            thought_ui.clear()
            if isinstance(req_err, cancellation.TaskCancelled):
                return 'CANCELLED', active_m
            if terminal_quota_error(req_err):
                sys.stdout.write(f'\n[!] Request quota exhausted: {req_err}. Task stopped.\n')
                sys.stdout.flush()
                return 'FAILED', active_m
            diagnosis = client_rejection(active_ep, url if 'url' in locals() else '', str(req_err))
            if diagnosis:
                AGENT_STATE['is_streaming'] = False
                sys.stdout.write(f'\n[!] {diagnosis}\n')
                sys.stdout.flush()
                if resp is not None and hasattr(resp, 'close'):
                    resp.close()
                return 'FAILED', active_m
            req_duration_ms = (time.time() - req_start_time) * 1000.0 if 'req_start_time' in locals() else 0
            log_model_interaction(
                model_name=model_name,
                endpoint=active_ep,
                user_prompt="",
                response_content="",
                error=str(req_err),
                http_metrics={
                    "status_code": getattr(resp, "status_code", 0) if resp else 0,
                    "duration_ms": req_duration_ms,
                    "streamed": stream_enabled,
                    "url": url if 'url' in locals() else ""
                }
            )
            log_galam_system_event(
                event_type="MODEL_CONNECTION_ERROR",
                summary=f"Failed to connect to {model_name} under {active_ep}: {req_err} ({req_duration_ms:.0f}ms)",
                details={"endpoint": active_ep, "model": model_name, "error": str(req_err), "duration_ms": req_duration_ms}
            )
            print(f"\n\033[1;31m[!] [{active_ep}] {model_name} Connection error: {req_err}\033[0m")
            continue
        finally:
            thought_ui.clear()

    if AGENT_STATE.get("abort", False):
        return "CANCELLED", active_m

    if active_ep == "Local Endpoint":
        sys.stdout.write(f"\n\033[1;31m[!] Local Endpoint encountered an error or is unreachable.\033[0m\n")
        sys.stdout.write(f"\033[38;5;244m    Fallback is disabled for Local Endpoint.\033[0m\n\n")
        sys.stdout.flush()
        return "FAILED", active_m

    fallback_policy = CONFIG.get("fallback_policy", "ask").lower()
    available_endpoints = [ep for ep in list(ENDPOINT_REGISTRY.keys()) if ep not in visited]

    def _has_key_for(ep):
        if ep == "Local Endpoint":
            try:
                from engine.local_models import refresh_local_models
                return bool(refresh_local_models(CONFIG))
            except Exception:
                return False
        if ep == "Direct":
            return True  # Direct includes free zero-config endpoints (kilo-auto/free, minimax-m2.7)
        from engine.payload_builder import get_endpoint_api_key
        return bool(get_endpoint_api_key(ep))

    valid_fallbacks = [ep for ep in available_endpoints if _has_key_for(ep)]

    if valid_fallbacks:
        preferred = [ep for ep in ("Local Endpoint", "OpenRouter", "Groq", "NVIDIA", "Direct", "Cloudflare") if ep in valid_fallbacks]
        new_ep = preferred[0] if preferred else valid_fallbacks[0]
        first_model = list(ENDPOINT_REGISTRY[new_ep]["models"].keys())[0]
        if new_ep == "OpenRouter":
            first_model = "nvidia/nemotron-3.5-lightning:free"
        elif new_ep == "Groq":
            first_model = "qwen/qwen3.8-27b"
        elif new_ep == "Cloudflare":
            first_model = "@cf/deepseek-ai/deepseek-r1-distill-qwen-32b"
        elif new_ep == "Direct":
            if os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"):
                first_model = "gemini-2.5-flash"
            elif os.environ.get("COHERE_API_KEY"):
                first_model = "command-a-plus-07-2025"
            else:
                first_model = "kilo-auto/free"

        # Check fallback policy: if "ask", prompt the user first!
        should_switch = True
        if fallback_policy == "ask":
            sys.stdout.write(f"\n\033[1;33m[!] Active endpoint [{active_ep}] failed or is unresponsive.\033[0m\n")
            sys.stdout.write(f"\033[38;5;250m    Available fallback endpoint: \033[1;36m[{new_ep}]\033[0m \033[38;5;244m({first_model})\033[0m\n")
            prompt_str = f"\033[1;37m    Would you like to switch to [{new_ep}]? [Y/n]: \033[0m"
            user_choice = request_user_confirmation(prompt_str, default="y", prompt_label=f"Switch to [{new_ep}] [Y/n]> ")
            if user_choice in ("n", "no", "cancel"):
                should_switch = False
                sys.stdout.write("\033[38;5;244m[-] Endpoint fallback cancelled by user.\033[0m\n\n")
                sys.stdout.flush()

        if should_switch:
            save_settings(active_endpoint=new_ep, endpoint=new_ep, active_model=first_model, model=first_model)
            sys.stdout.write(f"\n\033[1;32m[✓] Switching active endpoint to [{new_ep}] -> {first_model}...\033[0m\n\n")
            sys.stdout.flush()
            return query_with_fallback(*args, _visited_endpoints=visited, **kwargs)

    sys.stdout.write(f"\n\033[1;31m[!] Transparency Report: All models under [{active_ep}] failed or timed out.\033[0m\n")
    from engine.payload_builder import get_endpoint_api_key
    ep_key = get_endpoint_api_key(active_ep)
    if active_ep == "Local Endpoint":
        sys.stdout.write(f"\033[38;5;214m    Root Cause: Local model server is unreachable on localhost port 8080.\033[0m\n")
        sys.stdout.write(f"\033[38;5;244m    Fix: Start your local llama-server or check if it is running on http://127.0.0.1:8080.\033[0m\n\n")
    elif not ep_key and active_ep not in ("Direct",):
        sys.stdout.write(f"\033[38;5;214m    Root Cause: No API key or token configured for [{active_ep}].\033[0m\n")
        sys.stdout.write(f"\033[38;5;244m    Fix: Add credentials in storage/keys.json or switch to OpenRouter (configured).\033[0m\n\n")
    else:
        sys.stdout.write(f"\033[38;5;244m    Possible causes: Model server overloaded, network latency spike, or rate limit reached.\033[0m\n\n")
    sys.stdout.flush()
    return "EXHAUSTED", active_m
