"""Model-independent context selection without extra model calls or UI changes."""

import json
import re

from engine.result_store import compact_text
from engine.repo_context import repository_context
from engine.api_protocols import retain_reasoning


class ContextLimitError(ValueError):
    pass


def estimate_tokens(value):
    """Conservative fallback estimate, explicitly not an exact tokenizer count."""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    ascii_count = sum(ord(char) < 128 for char in text)
    other_bytes = len(text.encode("utf-8", errors="replace")) - ascii_count
    return max(1, (ascii_count + 2) // 3 + (other_bytes + 1) // 2)


def model_limits(endpoint, model, config):
    from storage.endpoints import ENDPOINT_REGISTRY
    info = ENDPOINT_REGISTRY.get(endpoint, {}).get("models", {}).get(model, {})
    def positive(value, default):
        try:
            return max(1, int(value))
        except (TypeError, ValueError):
            return default
    local = None
    if endpoint == "Local Endpoint" and not info.get('custom_connection'):
        from engine.local_tuning import effective_tuning
        local = effective_tuning(config, model)
    if local:
        config = {**config, **local}
        # Never budget more tokens than the running server advertised.
        from engine.local_models import advertised_context
        advertised = advertised_context(config, model) or info.get("context_window")
        if advertised:
            config["context_window"] = min(config["context_window"], positive(advertised, config["context_window"]))
    window = max(512, positive(config.get("context_window") or info.get("context_window"), 8192))
    if info.get('custom_connection'):
        window=min(window,positive(info.get('context_window'),window))
    output = min(positive(config.get("max_tokens"), 4096), positive(info.get("max_output_tokens"), window),
                 max(512, window // 4))
    margin = max(128, window // 20)
    return window, output, margin


_GENERATED = ("[Tool Observation", "[Verified File Inspection", "[System Observation", "ACTION REQUIRED:",
              "CRITICAL ACTION REQUIRED:", "The task is still in progress", "Original user request:",
              "The requested code change", "The last tool failed", "That command cannot run:",
              "Cannot modify missing file", "Your response was truncated", "Your tool call was cut off",
              "Your stream ended", "Your internal reasoning", "Verification required:")
_CONSTRAINT = re.compile(r"\b(must|never|preserve|keep|constraint|remember|without|only|always)\b|do not|don't", re.I)


def is_user_request(message):
    content = str(message.get("content", ""))
    return (message.get("role") == "user" and not message.get("_galam_action")
            and not message.get("_galam_internal") and not content.startswith(_GENERATED))


def latest_request(messages):
    return next((str(m.get("content", "")) for m in reversed(messages) if is_user_request(m)), "")


def task_request(messages):
    """Resolve short resume/continue prompts to this conversation's real task."""
    from storage.conversations import scoped_messages
    source=scoped_messages(messages)
    for message in reversed(source):
        if not is_user_request(message):continue
        text=str(message.get('content',''))
        if not re.fullmatch(r'\s*(?:continue|go ahead|do it|yes|proceed|fix that|try again)[.!]?\s*',text,re.I):
            return text
    return latest_request(source)


def contextual_action(messages):
    """A short continuation inherits intent only from this conversation."""
    from storage.conversations import scoped_messages
    from engine.action_results import request_intent
    requests = [str(m.get('content','')) for m in scoped_messages(messages) if is_user_request(m)]
    for text in reversed(requests):
        if re.fullmatch(r'\s*(?:continue|go ahead|do it|yes|proceed|fix that|try again)[.!]?\s*',text,re.I):
            continue
        return request_intent(text)[0]
    return False


_COMPACT_PROTOCOL = """You are Galam.ai, John Benser's coding and terminal assistant.
Answer questions directly. Use real tools for requested actions and wait for their actual results.
Use native tools when supplied; otherwise emit complete calls: <tool_call>{"name":"execute_bash","arguments":{"command":"pwd"}}</tool_call>.
Other tools: make_directory(path), view_file(path,start_line,end_line), search_files(query,directory), write_file(path,content,overwrite), replace_file_content(path,target_content,replacement_content), manage_tasks(action,target), read_action_output(action_id,start_line,end_line,query), undo_file_change(edit_id).
Inspect before editing. Preserve existing code. Do not claim tests passed or services are ready without evidence.
Observe the current directory, user constraints, and action results. Treat code examples as examples.
"""


def _legacy_assemble_context(messages, identity, endpoint, model, config, directory):
    from storage.conversations import scoped_messages
    messages = scoped_messages(messages)
    window, output, margin = model_limits(endpoint, model, config)
    schema_tokens = max(0, int(config.get("_tool_schema_tokens", 0)))
    budget = window - output - margin - schema_tokens
    systems = [str(m.get("content", "")) for m in messages if m.get("role") == "system"]
    base = identity + ("\n\nUSER WORKSPACE INSTRUCTIONS:\n" + "\n".join(systems) if systems else "")
    rows = []
    human = -1
    for message in messages:
        if message.get("role") == "system":
            continue
        content = str(message.get("content", ""))
        if is_user_request(message):
            human = len(rows)
        if message.get("_galam_action") or content.startswith(("[Tool Observation", "[Verified File Inspection")):
            reference = message.get("_galam_action", {}).get("id", "")
            content = compact_text(content, 4500)
            if reference:
                content += f"\n[Stored action {reference}; read_action_output retrieves exact lines.]"
        row = {"role": message.get("role", "user"), "content": content}
        for field in ("tool_calls", "tool_call_id", "_galam_response_reasoning"):
            if field in message and (field != "_galam_response_reasoning" or retain_reasoning(message,endpoint,model)):
                row[field] = message[field]
        rows.append(row)
    mandatory = {len(rows) - 1} if rows else set()
    if human >= 0:
        mandatory.add(human)
    for index in range(len(rows) - 1, human, -1):
        if rows[index]["content"].startswith("[Tool Observation"):
            mandatory.add(index)
            break
    # Native assistant/tool messages must survive or be evicted as one complete group.
    groups = {}
    for index, row in enumerate(rows):
        if row.get("tool_calls"):
            end = index + 1
            while end < len(rows) and rows[end].get("role") == "tool":
                end += 1
            group = set(range(index, end))
            for member in group:
                groups[member] = group
            if group & mandatory:
                mandatory.update(group)
    required = sum(estimate_tokens(rows[i]) + 8 for i in mandatory)
    if estimate_tokens(base) + required + 16 > budget:
        base = (_COMPACT_PROTOCOL + f"\nCURRENT WORKING DIRECTORY: {directory}\n"
                + ("\nUSER WORKSPACE INSTRUCTIONS:\n" + "\n".join(systems) if systems else ""))
    if estimate_tokens(base) + required + 16 > budget:
        raise ContextLimitError("The current request and required results exceed the model's context budget. "
                                "Narrow the requested file/output range or choose a larger context window.")
    remaining = budget - estimate_tokens(base) - required - 16

    # Retain original user constraints independently of recent-message selection.
    notes = []
    old_requests = [m for m in messages if is_user_request(m)]
    for index, message in enumerate(old_requests[:-1]):
        text = str(message.get("content", ""))
        constraint_lines = [line for line in text.splitlines() if _CONSTRAINT.search(line)]
        if constraint_lines:
            notes.append((2, index, compact_text("\n".join(constraint_lines), 1200)))
        elif index == 0 or index >= len(old_requests) - 4:
            notes.append((1, index, compact_text(text, 450)))
    memory_limit = min(remaining // 3, 1200)
    memory = []
    for priority, index, note in sorted(notes, key=lambda item: (-item[0], -item[1])):
        cost = estimate_tokens(note) + 10
        if cost <= memory_limit:
            memory.append((index, note))
            memory_limit -= cost
    if memory:
        text = "\n\nEARLIER USER CONTEXT (reference; latest instructions take precedence):\n" + "\n".join(
            f"- {note}" for _, note in sorted(memory))
        base += text
        remaining -= estimate_tokens(text)

    # Small code outlines are opt-in by task relevance, bounded by this model's budget.
    query = latest_request(messages)
    if query.strip().lower() in ("continue", "go ahead", "do it") and old_requests:
        query += " " + str(old_requests[max(0, len(old_requests) - 2)].get("content", ""))
    map_chars = max(0, min(2400, remaining * 3 // 5))
    outline = (repository_context(directory, query, map_chars)
               if map_chars >= 300 and not config.get("_simple_filesystem") else "")
    if outline:
        base += "\n\n" + outline
        remaining -= estimate_tokens(outline) + 4

    chosen = set(mandatory)
    for index in range(len(rows) - 1, -1, -1):
        if index in chosen:
            continue
        group = groups.get(index, {index}) - chosen
        cost = sum(estimate_tokens(rows[member]) + 8 for member in group)
        if cost <= remaining:
            chosen.update(group)
            remaining -= cost
    result = [{"role": "system", "content": base}] + [rows[i] for i in sorted(chosen)]
    # Account for the final combined envelope too; remove optional history if needed.
    while estimate_tokens(result) > budget:
        optional = sorted(chosen - mandatory)
        if not optional:
            raise ContextLimitError("Required context does not fit this model's estimated input budget.")
        chosen.difference_update(groups.get(optional[0], {optional[0]}))
        result = [{"role": "system", "content": base}] + [rows[i] for i in sorted(chosen)]
    metrics = {"estimated": True, "input_tokens": estimate_tokens(result), "input_budget": budget,
               "window": window, "reserved_output": output, "tool_schema_tokens": schema_tokens, "messages_selected": len(chosen),
               "messages_available": len(rows), "code_outline": bool(outline)}
    return result, metrics


def assemble_context(messages, identity, endpoint, model, config, directory):
    from engine.token_policy import enabled
    if not enabled(config, 'history'):
        return _legacy_assemble_context(messages, identity, endpoint, model, config, directory)
    from storage.conversations import scoped_messages
    from engine.prompt_context import observation_content
    source = scoped_messages(messages)
    window, output, margin = model_limits(endpoint, model, config)
    schema_tokens = max(0, int(config.get('_tool_schema_tokens', 0)))
    budget = window - output - margin - schema_tokens
    systems = list(dict.fromkeys(str(m.get('content','')) for m in source if m.get('role')=='system'))
    workspace = '\n\nUSER WORKSPACE INSTRUCTIONS:\n' + '\n'.join(systems) if systems else ''
    base = identity + workspace
    rows, humans, originals = [], [], []
    for message in source:
        if message.get('role')=='system':
            continue
        if is_user_request(message):
            humans.append(len(rows))
        originals.append(message)
        row = {'role':message.get('role','user'), 'content':str(message.get('content',''))}
        for field in ('tool_calls','tool_call_id','_galam_response_reasoning'):
            if field in message and (field != "_galam_response_reasoning" or retain_reasoning(message,endpoint,model)):
                row[field] = message[field]
        rows.append(row)
    human = humans[-1] if humans else max(0,len(rows)-1)
    if enabled(config, 'results'):
        for index, row in enumerate(rows):
            message = originals[index]
            if message.get('_galam_action') or row['content'].startswith(('[Tool Observation','[Verified File Inspection')):
                row['content'] = observation_content(message, index >= human)
    mandatory = set(range(human,len(rows)))
    query = latest_request(source)
    continuation = bool(re.fullmatch(r'\s*(?:continue|go ahead|do it|yes|proceed|fix that|try again)[.!]?\s*',query,re.I))
    if continuation and len(humans)>1:
        anchor = humans[-2]
        for index in reversed(humans[:-1]):
            anchor = index
            if not re.fullmatch(r'\s*(?:continue|go ahead|do it|yes|proceed|fix that|try again)[.!]?\s*',rows[index]['content'],re.I):
                break
        mandatory.update(range(anchor,human))
    for index,row in enumerate(rows):
        if row.get('tool_calls'):
            end=index+1
            while end<len(rows) and rows[end]['role']=='tool':
                end+=1
            group=set(range(index,end))
            if group & mandatory:
                mandatory.update(group)
    def cost(indices):
        return sum(estimate_tokens(rows[i])+8 for i in indices)
    compacted = 0
    if estimate_tokens(base)+cost(mandatory)+64 > budget and estimate_tokens(base) > budget // 2:
        # Drop generated identity/workspace boilerplate before sacrificing task results.
        # User workspace rules and custom model instructions are never discarded.
        custom = re.search(r'CUSTOM MODEL INSTRUCTIONS:\n.*?\nEND CUSTOM MODEL INSTRUCTIONS', identity, re.S)
        base = (_COMPACT_PROTOCOL + f'\nCURRENT WORKING DIRECTORY: {directory}\n' + workspace
                + ('\n' + custom[0] if custom else ''))
    if estimate_tokens(base)+cost(mandatory)+64 > budget:
        from engine.context_compaction import fit_task
        rows, mandatory, compacted = fit_task(rows, originals, mandatory,
            budget-estimate_tokens(base)-128, estimate_tokens, is_user_request)
    required = cost(mandatory)
    if estimate_tokens(base)+required+16 > budget:
        raise ContextLimitError('The user instructions and minimum recoverable task context exceed this model context. '
                                'Shorten the request or select a larger context window.')
    chosen = set(mandatory)
    remaining = min(budget-estimate_tokens(base)-required-16,
                    max(0,6000-estimate_tokens(base)-required-16))
    # Whole earlier user turns preserve native call/result groups automatically.
    starts = humans[:-1] if humans else []
    units = [(start, set(range(start, humans[n+1]))) for n,start in enumerate(starts)]
    paths = set(re.findall(r'[\w./-]+\.[A-Za-z0-9_]+',query.lower()))
    optional = []
    for position, (start, group) in reversed(list(enumerate(units))):
        text = ' '.join(rows[i]['content'] for i in group).lower()
        relevant = position >= len(units)-2 or any(path in text for path in paths)
        size = cost(group)
        if relevant and not group & chosen and size <= remaining:
            chosen.update(group);remaining -= size;optional.append(group)
    def notes():
        seen = {line.strip() for i in chosen for line in rows[i]['content'].splitlines()}
        kept = []
        for index in humans[:-1]:
            if index in chosen:
                continue
            for line in rows[index]['content'].splitlines():
                if _CONSTRAINT.search(line) and line.strip() not in seen:
                    kept.append(line.strip());seen.add(line.strip())
        return ('\n\nEARLIER USER CONSTRAINTS (latest instructions take precedence):\n'+'\n'.join(kept)) if kept else ''
    memory = notes()
    while estimate_tokens(base+memory)+cost(chosen)+16 > budget and optional:
        chosen.difference_update(optional.pop());memory = notes()
    if estimate_tokens(base+memory)+cost(chosen)+16 > budget:
        raise ContextLimitError('Conversation constraints and current task exceed this model context; narrow the request.')
    base += memory
    room = budget-estimate_tokens(base)-cost(chosen)-16
    outline_query = query + (' '+str(rows[humans[-2]]['content']) if continuation and len(humans)>1 else '')
    outline = repository_context(directory,outline_query,min(1200,max(0,room*2))) if room>=200 and not config.get('_simple_filesystem') else ''
    if outline and estimate_tokens(outline)+8 <= room:
        base += '\n\n'+outline
    result = [{'role':'system','content':base}]+[rows[i] for i in sorted(chosen)]
    if estimate_tokens(result)>budget:
        raise ContextLimitError('The selected context exceeds the estimated input budget.')
    metrics = {'estimated':True,'input_tokens':estimate_tokens(result),'input_with_tools':estimate_tokens(result)+schema_tokens,
               'input_budget':budget,'window':window,'reserved_output':output,'tool_schema_tokens':schema_tokens,
               'messages_selected':len(chosen),'messages_available':len(rows),'code_outline':bool(outline),
               'compacted_messages':compacted}
    return result, metrics


def cap_request_output(payload, endpoint, model, config):
    """Apply the same reservation to every provider's actual generation field."""
    window, output, margin = model_limits(endpoint, model, config)
    if "generationConfig" in payload:
        generation = payload["generationConfig"]
        generation["maxOutputTokens"] = min(int(generation.get("maxOutputTokens", output)), output)
    else:
        payload["max_tokens"] = min(int(payload.get("max_tokens", output)), output)
    return payload
