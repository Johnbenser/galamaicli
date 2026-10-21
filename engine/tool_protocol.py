"""One tool contract for compatible APIs, bounded repair, and text fallback."""
import json
import re
from engine.tool_registry import TOOLS, REQUIRED

_UNSUPPORTED = set()
DESCRIPTIONS = {
    "make_directory": "Create a directory and its parents. Never use write_file for folders.",
    "execute_bash": "Run a real shell command after review. For any long-running service use background=true; readiness_url may specify a local HTTP(S)/TCP probe. Do not relaunch a live process just because readiness is pending.",
    "view_file": "Read numbered file lines before editing.",
    "write_file": "Create a file with complete literal content.",
    "replace_file_content": "Replace a unique inspected block; preserve other code.",
    "search_files": "Search names or contents in a directory.",
    "manage_tasks": "List, inspect or stop Galam background tasks.",
    "read_action_output": "Retrieve stored output by action ID.",
    "undo_file_change": "Undo a Galam edit only when the user asks.",
    "security_audit": "Read-only local audit for credential files, tracked secrets and unsafe TLS; returns findings without secret values.",
}


def tool_schemas():
    types = {str: "string", int: "integer", bool: "boolean"}
    return [{"type": "function", "function": {"name": name, "description": DESCRIPTIONS[name],
             "parameters": {"type": "object", "properties": {key: {"type": types[kind]} for key, kind in args.items()},
                            "required": list(REQUIRED[name]), "additionalProperties": False}}}
            for name, args in TOOLS.items()]


def _key(endpoint, model, config):
    from storage.endpoints import ENDPOINT_REGISTRY
    connection=ENDPOINT_REGISTRY.get(endpoint,{}).get('models',{}).get(model,{}).get('custom_connection')
    if connection:return endpoint,model,connection['url']
    if endpoint == "Local Endpoint":
        from engine.local_models import local_base_url
        base = local_base_url(config)
    else:
        base = ENDPOINT_REGISTRY.get(endpoint, {}).get("base_url", "")
    return endpoint, model, base


def options(endpoint, model, messages, config):
    from storage.conversations import scoped_messages
    messages = scoped_messages(messages)
    # Providers with different tool wire formats keep Galam's text protocol.
    from storage.endpoints import ENDPOINT_REGISTRY
    custom=ENDPOINT_REGISTRY.get(endpoint,{}).get('models',{}).get(model,{}).get('custom_connection')
    if not custom and endpoint not in {"Local Endpoint", "OpenRouter", "NVIDIA", "Groq", "ModelScope", "Cloudflare", "Agent Router"}:
        return {}
    if endpoint == "Cloudflare" and not custom:
        from storage.endpoints import ENDPOINT_REGISTRY
        if ENDPOINT_REGISTRY.get(endpoint, {}).get("models", {}).get(model, {}).get("route_type") != "v1":
            return {}
    if _key(endpoint, model, config) in _UNSUPPORTED:
        return {}
    from engine.context_manager import latest_request, contextual_action
    from engine.action_results import request_intent
    mode = next((m["_galam_tool_mode"] for m in reversed(messages) if m.get("_galam_tool_mode")), None)
    if contextual_action(messages) and mode == 'none' and not request_intent(latest_request(messages))[0]:
        mode = 'auto'
    if mode == "none" or mode is None and not contextual_action(messages):
        return {}
    schemas = tool_schemas()
    if not re.search(r'\b(?:security|secrets?|credentials?|audit)\b', latest_request(messages), re.I):
        schemas = [tool for tool in schemas if tool['function']['name'] != 'security_audit']
    from engine.token_policy import enabled
    from engine.completion import explicit_report_command
    from engine.context_manager import is_user_request
    last_human = max((i for i,m in enumerate(messages) if is_user_request(m)),default=-1)
    feedback = messages[last_human+1:]
    recovery = any(m.get('_galam_internal') or m.get('_galam_action',{}).get('status')=='failed' for m in feedback)
    if enabled(config,'tools') and explicit_report_command(latest_request(messages)) and not recovery:
        schemas = [tool for tool in schemas if tool['function']['name'] in ('execute_bash','read_action_output')]
    from config import AGENT_STATE
    from engine.task_contract import TaskContract
    contract = TaskContract(latest_request(messages), AGENT_STATE.get("working_directory") or ".")
    if contract.complete_scope:
        kinds = {r["kind"] for r in contract.requirements}
        allowed = ({"make_directory"} if "directory" in kinds else set())
        if "file" in kinds:
            allowed.update(("write_file", "view_file", "replace_file_content"))
        schemas = [tool for tool in schemas if tool["function"]["name"] in allowed]
    return {"tools": schemas, "tool_choice": "required" if mode == "required" else "auto"}


def reject_unsupported(response, endpoint, model, config, payload):
    if "tools" not in payload or getattr(response, "status_code", 0) not in (400, 422):
        return False
    try:
        error = response.json().get("error", {})
        text = error.get("message", "") if isinstance(error, dict) else str(error)
    except (ValueError, AttributeError):
        text = ""
    text = (text or getattr(response, "text", ""))[:2000].lower()
    tool_error = any(word in text for word in ("tools", "tool_choice", "tool calls", "tool call", "function calling"))
    unavailable = any(word in text for word in ("unsupported", "not support", "does not support", "not allowed", "unknown", "unrecognized", "not implemented", "template"))
    if tool_error and unavailable:
        _UNSUPPORTED.add(_key(endpoint, model, config))
        return True
    return False


def needs_clarification(text):
    return bool("?" in text and re.search(
        r"\b(?:which|what|where)\s+(?:file|folder|path|directory|name|command|model|endpoint|content)|"
        r"\b(?:could|can)\s+you\s+(?:clarify|provide|specify)|\bplease\s+(?:clarify|provide|specify)", text, re.I))


class ActionRepair:
    """Change the protocol once; do not repeat the same no-progress exchange."""
    def __init__(self):
        self.responses = set()
        self.attempts = {}

    def next_message(self, request, response, progress):
        signature = (progress, re.sub(r"\s+", " ", response).strip().lower())
        count = self.attempts.get(progress, 0)
        if signature in self.responses or count >= 2:
            return None
        self.responses.add(signature)
        self.attempts[progress] = count + 1
        return {"role": "user", "_galam_internal": True, "_galam_tool_mode": "required", "content": (
            "[Action protocol repair] The task is not complete. No complete tool call was returned for the remaining task. "
            "Do not repeat an announcement. Use the tool interface now. If native tools are unavailable, "
            "return exactly one complete <tool_call>{\"name\":\"TOOL_NAME\",\"arguments\":{...}}</tool_call>. "
            "Choose a real tool and all required arguments from the available contract. "
            "Use literal file content, not undefined shell variables. Preserve unrelated files. "
            "If essential input is missing, ask one concrete question rather than guessing. "
            f"Original request: {request}")}


def wire_text_messages(messages):
    """Keep historical native arguments visible when switching to text tools."""
    result=[]
    for message in wire_messages(messages):
        row=dict(message)
        calls=row.pop('tool_calls',None)
        row.pop('_galam_tool_calls',None)
        if calls:
            blocks=[]
            for call in calls:
                function=call.get('function',{})
                args=function.get('arguments',{})
                if isinstance(args,str):
                    try:
                        args=json.loads(args)
                    except ValueError:
                        pass
                blocks.append('<tool_call>'+json.dumps({'name':function.get('name',''),'arguments':args})+'</tool_call>')
            row['content']=(row.get('content','')+'\n'+'\n'.join(blocks)).strip()
        if row.get('role')=='tool':
            row['role']='user';row['_galam_internal']=True
        row.pop('tool_call_id',None)
        result.append(row)
    return result


def wire_messages(messages):
    """Represent native calls and every result in API order, including calls not executed."""
    from engine.context_manager import is_user_request
    from storage.conversations import scoped_messages
    messages = scoped_messages(messages)
    result, index = [], 0
    while index < len(messages):
        message = messages[index]
        calls = message.get("_galam_tool_calls") if message.get("role") == "assistant" else None
        if not calls:
            result.append(message)
            index += 1
            continue
        end = index + 1
        while end < len(messages) and messages[end].get("role") != "assistant" and not is_user_request(messages[end]):
            end += 1
        following = messages[index + 1:end]
        observations = {m.get("_galam_call_id"): m for m in following if m.get("_galam_call_id")}
        from engine.tool_parser import clean_response_text
        result.append({**message, "content": clean_response_text(message.get("content", "")), "tool_calls": calls})
        for call in calls:
            call_id = call["id"]
            observation = observations.get(call_id)
            if observation:
                result.append({**observation, "role": "tool", "tool_call_id": call_id})
            else:
                result.append({"role": "tool", "tool_call_id": call_id, "_galam_internal": True,
                               "content": "Not executed. See validation, approval, or file inspection feedback before proposing another action."})
        result.extend(m for m in following if not m.get("_galam_call_id"))
        index = end
    return result
