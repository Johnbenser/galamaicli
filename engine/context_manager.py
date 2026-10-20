"""Model-independent context selection without extra model calls or UI changes."""

import json
import re

from engine.result_store import compact_text
from engine.repo_context import repository_context


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
    window = max(512, positive(config.get("context_window") or info.get("context_window"), 8192))
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


_COMPACT_PROTOCOL = """You are Galam.ai, John Benser's coding and terminal assistant.
Answer questions directly. Use real tools for requested actions and wait for their actual results.
Emit complete calls: <tool_call>{"name":"execute_bash","arguments":{"command":"pwd"}}</tool_call>.
Other tools: view_file(path,start_line,end_line), search_files(query,directory), write_file(path,content,overwrite), replace_file_content(path,target_content,replacement_content), manage_tasks(action,target), read_action_output(action_id,start_line,end_line,query), undo_file_change(edit_id).
Inspect before editing. Preserve existing code. Do not claim tests passed or services are ready without evidence.
Observe the current directory, user constraints, and action results. Treat code examples as examples.
"""


def assemble_context(messages, identity, endpoint, model, config, directory):
    window, output, margin = model_limits(endpoint, model, config)
    budget = window - output - margin
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
        rows.append({"role": message.get("role", "user"), "content": content})
    mandatory = {len(rows) - 1} if rows else set()
    if human >= 0:
        mandatory.add(human)
    for index in range(len(rows) - 1, human, -1):
        if rows[index]["content"].startswith("[Tool Observation"):
            mandatory.add(index)
            break
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
    outline = repository_context(directory, query, map_chars) if map_chars >= 300 else ""
    if outline:
        base += "\n\n" + outline
        remaining -= estimate_tokens(outline) + 4

    chosen = set(mandatory)
    for index in range(len(rows) - 1, -1, -1):
        if index in chosen:
            continue
        cost = estimate_tokens(rows[index]) + 8
        if cost <= remaining:
            chosen.add(index)
            remaining -= cost
    result = [{"role": "system", "content": base}] + [rows[i] for i in sorted(chosen)]
    # Account for the final combined envelope too; remove optional history if needed.
    while estimate_tokens(result) > budget:
        optional = sorted(chosen - mandatory)
        if not optional:
            raise ContextLimitError("Required context does not fit this model's estimated input budget.")
        chosen.remove(optional[0])
        result = [{"role": "system", "content": base}] + [rows[i] for i in sorted(chosen)]
    metrics = {"estimated": True, "input_tokens": estimate_tokens(result), "input_budget": budget,
               "window": window, "reserved_output": output, "messages_selected": len(chosen),
               "messages_available": len(rows), "code_outline": bool(outline)}
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
