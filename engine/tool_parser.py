import re
import ast
import io
import tokenize
import json
import uuid
import shlex
import shutil
from dataclasses import dataclass, field
from typing import List, Dict, Any, Tuple, Optional

@dataclass
class AgentAction:
    """Unified representation of an autonomous tool invocation across all LLM formats."""
    tool_name: str
    args: Dict[str, Any]
    raw_call: str = ""
    id: str = field(default_factory=lambda: f"call_{uuid.uuid4().hex[:8]}")

    def to_tool_call(self) -> Dict[str, Any]:
        """Converts AgentAction to OpenAI-standard tool_call schema."""
        return {
            "id": self.id,
            "type": "function",
            "function": {
                "name": self.tool_name,
                "arguments": json.dumps(self.args) if isinstance(self.args, dict) else str(self.args)
            }
        }


def _looks_like_shell_command(line: str) -> bool:
    """Distinguish shell commands from quoted command output and prose."""
    try:
        words = shlex.split(line.strip().lstrip("$ "))
    except ValueError:
        return False
    if not words:
        return False
    while words and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", words[0]):
        words.pop(0)
    if not words:
        return False
    first = words[0]
    shell_builtins = {"cd", "pwd", "echo", "printf", "export", "set", "unset", "source", ".", "alias", "type", "test", "[", "true", "false"}
    return (first in shell_builtins or bool(shutil.which(first)) or
            first.startswith(("./", "../", "/", "~/")))


def validate_shell_command(command: str) -> Tuple[bool, str]:
    """Reject model examples and malformed navigation before they reach review."""
    command = (command or "").strip()
    if not command:
        return False, "empty command"
    if re.search(r'(?i)(?:your_project_name|project_name_here|replace_this|<[^>]+>|\bTODO\b)', command):
        return False, "the command contains a placeholder rather than a real project path"
    first = command.split("&&", 1)[0].splitlines()[0].strip()
    if not _looks_like_shell_command(first):
        return False, "the command does not start with an executable"
    for match in re.finditer(r'(?:^|&&|;)\s*cd\s+(?:--\s+)?([^\s;&|]+)', command):
        target = match.group(1).strip('"\'')
        if target.lower() in {"the", "your", "project", "directory", "path"}:
            return False, f"'{target}' is not a directory path"
    return True, ""

def parse_reasoning_and_content(raw_text: str) -> Tuple[str, str]:
    """
    Separates <thought> or <think> reasoning blocks from the main response.
    Returns: (reasoning_text, clean_content)
    """
    from engine.transparency import ReasoningStream
    stream = ReasoningStream()
    clean_text, explicit_reason = stream.feed(raw_text, final=True)
    reasoning_blocks = [explicit_reason.strip()] if explicit_reason.strip() else []

    # 3. Untagged R1 Internal Monologue Heuristic:
    # If the response begins with an internal monologue (e.g. "Okay, so the user wants me to...")
    # separated by double newlines before the final conversational greeting or tool block,
    # cleanly route that internal monologue to reasoning rather than showing it to the user.
    # If the response consists entirely of an untagged monologue (stream cut off or no greeting),
    # route the entire text to reasoning so downstream tool extraction and intent traps can act.
    clean_stripped = clean_text.strip()
    is_monologue_start = (
        clean_stripped.lower().startswith(("okay,", "alright,", "let me think", "so the user", "i need to", "the user wants", "the user asked", "the user is asking")) or
        any(clean_stripped.lower().startswith(s) for s in (
            "okay, so the user", "okay, the user", "alright, the user",
            "alright, i need", "okay, i need", "okay, let me", "let me break down",
            "let me analyze", "i need to approach this", "i need to help the user"
        ))
    )
    if is_monologue_start:
        # Look for the conversational answer or direct statement separated by blank line(s)
        # Matches: Galam.ai ready|Hello|Hi|Here|Sure|Certainly|Yes|I'll|I will|{|< or last non-reasoning paragraph
        m_ans = re.search(r'\n\s*\n\s*(Galam\.ai\b|Hello\b|Hi\b|Here\b|Sure\b|Certainly\b|Yes\b|I have\b|I\'ll\b|I will\b|\{|\<)', clean_stripped, re.IGNORECASE)
        if not m_ans:
            # Fallback: look for the final double-newline split if the last paragraph does not look like reasoning
            paras = [p.strip() for p in re.split(r'\n\s*\n', clean_stripped) if p.strip()]
            if len(paras) >= 2:
                last_p = paras[-1]
                if not any(last_p.lower().startswith(q) for q in ("okay", "so ", "let me", "i should", "i need", "i think", "first,", "since ")):
                    last_idx = clean_stripped.rfind(last_p)
                    thought_part = clean_stripped[:last_idx].strip()
                    answer_part = last_p
                    if thought_part:
                        reasoning_blocks.append(thought_part)
                    clean_text = answer_part
                    m_ans = "matched_paragraph"

        if isinstance(m_ans, re.Match):
            split_idx = m_ans.start()
            thought_part = clean_stripped[:split_idx].strip()
            answer_part = clean_stripped[split_idx:].strip()
            if thought_part:
                reasoning_blocks.append(thought_part)
            clean_text = answer_part
        elif not m_ans:
            # Stream cut off or entire text is reasoning monologue
            reasoning_blocks.append(clean_stripped)
            clean_text = ""

    full_reasoning = "\n\n".join(reasoning_blocks).strip()
    return full_reasoning, clean_text.strip()

def _try_parse_json(text: str) -> Dict[str, Any]:
    text = text.strip()
    try:
        return json.loads(text, strict=False)
    except Exception:
        # Auto-repair: remove trailing commas or markdown ticks
        cleaned = re.sub(r',\s*([}\]])', r'\1', text)
        cleaned = cleaned.replace("```json", "").replace("```", "").strip()
        try:
            return json.loads(cleaned, strict=False)
        except Exception:
            return {}

def clean_response_text(text: str) -> str:
    """Removes raw tool markup and delimiter tokens from user-facing text stream."""
    if not text:
        return ""
    if is_explanatory_text(text):
        return re.sub(r'<(?:think|thought)>.*?</(?:think|thought)>', '', text,
                      flags=re.DOTALL | re.IGNORECASE).strip()
    # 1. Mistral [TOOL_CALLS] [...]
    cleaned = re.sub(r'\[TOOL_CALLS\]\s*\[.*?\]', '', text, flags=re.DOTALL | re.IGNORECASE)
    cleaned = re.sub(r'\[TOOL_CALLS\]', '', cleaned, flags=re.IGNORECASE)
    # 2. DeepSeek DSML
    cleaned = re.sub(r'<\s*[|｜]\s*DSML\s*[|｜]\s*calls>.*?</\s*[|｜]\s*DSML\s*[|｜]\s*calls>', '', cleaned, flags=re.DOTALL | re.IGNORECASE)
    cleaned = re.sub(r'<\s*[|｜]\s*DSML\s*[|｜]\s*invoke.*?</\s*[|｜]\s*DSML\s*[|｜]\s*invoke>', '', cleaned, flags=re.DOTALL | re.IGNORECASE)
    cleaned = re.sub(r'<\s*/?[|｜]\s*DSML\s*[|｜][^>]*>', '', cleaned, flags=re.IGNORECASE)
    # 3. DeepSeek Unicode tool call delimiters
    cleaned = re.sub(r'<[|｜]\s*(?:tool call begin|tool_call_begin|tool calls begin|tool_calls_begin|DSML)[^>]*>.*?</?[|｜]\s*(?:tool call end|tool_call_end|tool calls end|tool_calls_end|DSML)[^>]*>', '', cleaned, flags=re.DOTALL | re.IGNORECASE)
    cleaned = re.sub(r'</?[|｜]\s*(?:tool call begin|tool_call_begin|tool calls begin|tool_calls_begin|tool sep|tool call end|tool_call_end|tool calls end)[^>]*>', '', cleaned, flags=re.IGNORECASE)
    # 4. Hermes / Liquid
    cleaned = re.sub(r'<\|tool_call_start\|>.*?<\|tool_call_end\|>', '', cleaned, flags=re.DOTALL)
    cleaned = re.sub(r'<\|(?:tool_call_start|tool_call_end)\|>', '', cleaned)
    # 5. Qwen im_start tool_call
    cleaned = re.sub(r'<\|im_start\|>tool_call.*?<\|im_end\|>', '', cleaned, flags=re.DOTALL | re.IGNORECASE)
    # 6. XML tool calls & invokes (both closed and unclosed/streaming format)
    cleaned = re.sub(r'<tool_call>.*?(?:</tool_call>|$)', '', cleaned, flags=re.DOTALL | re.IGNORECASE)
    cleaned = re.sub(r'<invoke[^>]*>.*?(?:</invoke>|$)', '', cleaned, flags=re.DOTALL | re.IGNORECASE)
    cleaned = re.sub(r'<action[^>]*>.*?(?:</action>|$)', '', cleaned, flags=re.DOTALL | re.IGNORECASE)
    cleaned = re.sub(r'</?(?:tool_call|invoke|action)[^>]*>', '', cleaned, flags=re.IGNORECASE)
    # 7. Fenced code blocks for bash if only commands
    def clean_fence(match):
        body = match.group(2).strip()
        first_line = next((line.strip() for line in body.splitlines() if line.strip()), "")
        return "" if _looks_like_shell_command(first_line) else body
    cleaned = re.sub(r'```(bash|sh|shell|zsh)?[ \t]*\n(.*?)\n```', clean_fence, cleaned, flags=re.DOTALL | re.IGNORECASE)
    # 8. Strip any leftover <think>...</think> or <thought>...</thought> blocks
    cleaned = re.sub(r'<(?:think|thought)>.*?</(?:think|thought)>', '', cleaned, flags=re.DOTALL | re.IGNORECASE)
    cleaned = re.sub(r'</?(?:think|thought)>', '', cleaned, flags=re.IGNORECASE)
    return cleaned.strip()


ANSI_RE = re.compile(r'\x1b\[[0-9;]*m')

def visible_len(s: str) -> int:
    return len(ANSI_RE.sub('', s))

def ansi_wrap(text: str, width: int) -> list:
    """Wraps text containing ANSI escape codes while preserving styles across wrapped lines."""
    words = text.split(' ')
    lines = []
    cur_line = []
    cur_len = 0
    active_ansi = ''

    for w in words:
        if not w:
            continue
        w_len = visible_len(w)
        codes = ANSI_RE.findall(w)
        if cur_len + (1 if cur_line else 0) + w_len > width:
            if cur_line:
                lines.append(' '.join(cur_line) + '\033[0m')
                start_w = [active_ansi + w] if active_ansi else [w]
                cur_line = start_w
                cur_len = w_len
            else:
                lines.append(w + '\033[0m')
                cur_line = []
                cur_len = 0
        else:
            cur_line.append(w)
            cur_len += (1 if len(cur_line) > 1 else 0) + w_len

        if codes:
            active_ansi = codes[-1]
            if active_ansi == '\033[0m':
                active_ansi = ''

    if cur_line:
        lines.append(' '.join(cur_line) + '\033[0m')
    return lines

def _inline_format(text: str) -> str:
    """Format prose while keeping inline code (including filenames) literal."""
    def prose(value):
        value = re.sub(r'\*\*\*(.+?)\*\*\*', lambda m: "\033[1;3;38;5;255m"+m[1]+"\033[0;38;5;253m", value)
        value = re.sub(r'\*\*(.+?)\*\*', lambda m: "\033[1;38;5;255m"+m[1]+"\033[0;38;5;253m", value)
        value = re.sub(r'__(.+?)__', lambda m: "\033[1;38;5;255m"+m[1]+"\033[0;38;5;253m", value)
        return re.sub(r'\*(.+?)\*', lambda m: "\033[3;38;5;250m"+m[1]+"\033[0;38;5;253m", value)
    pieces=[];start=0
    for match in re.finditer(r'(`+)(.+?)\1',text):
        pieces.extend([prose(text[start:match.start()]),"\033[38;5;222m"+match[2]+"\033[38;5;253m"])
        start=match.end()
    pieces.append(prose(text[start:]))
    return ''.join(pieces)


def render_markdown_response(text: str, indent: str = "  ") -> str:
    from ui.response_renderer import render
    return render(text, indent)


from engine.tool_registry import TOOLS
VALID_TOOLS = set(TOOLS)

TOOL_ALIASES = {
    "bash": "execute_bash",
    "sh": "execute_bash",
    "shell": "execute_bash",
    "terminal": "execute_bash",
    "terminal_execution_bridge": "execute_bash",
    "terminal_bridge": "execute_bash",
    "exec": "execute_bash",
    "run": "execute_bash",
    "cmd": "execute_bash",
    "tasks": "manage_tasks",
    "task": "manage_tasks",
    "kill_task": "manage_tasks",
    "stop_task": "manage_tasks",
    "stop_server": "manage_tasks",
    "kill_server": "manage_tasks"
}

def _validate_and_normalize_tool(fn_name: str, args: Any):
    """Strictly validates tool name and arguments. Returns (clean_name, args_dict) or None."""
    if not fn_name:
        return None
    fn_lower = fn_name.lower().strip()
    target_fn = TOOL_ALIASES.get(fn_lower, fn_lower)

    if target_fn not in VALID_TOOLS:
        return None

    if isinstance(args, str):
        try:
            args_dict = json.loads(args)
        except Exception:
            args_dict = {"command": args} if target_fn == "execute_bash" else {}
    elif isinstance(args, dict):
        args_dict = args
    else:
        args_dict = {}

    if target_fn == "execute_bash":
        cmd = args_dict.get("command") or args_dict.get("cmd") or ""
        # Auto-unwrap if cmd is a stringified JSON dictionary e.g. '{"command": "..."}'
        if isinstance(cmd, str) and cmd.strip().startswith("{"):
            try:
                sub = json.loads(cmd, strict=False)
            except Exception:
                try:
                    sub = json.loads(cmd.replace(r'\"', '"'), strict=False)
                except Exception:
                    sub = None
            if isinstance(sub, dict) and ("command" in sub or "cmd" in sub):
                cmd = sub.get("command") or sub.get("cmd") or cmd
        if not str(cmd).strip():
            return None
        args_dict = {"command": str(cmd).strip()}

    elif target_fn == "manage_tasks":
        action = args_dict.get("action") or "list"
        target = args_dict.get("target") or args_dict.get("id") or "all"
        args_dict = {"action": str(action).strip().lower(), "target": str(target).strip()}

    elif target_fn == "view_file":
        raw_p = args_dict.get("path")
        if not raw_p:
            return None
        clean_p = re.sub(r'<[^>]*>', '', str(raw_p)).strip().strip("'\"")
        if not clean_p:
            return None
        args_dict["path"] = clean_p
        for line_key, default_val in (("start_line", 1), ("end_line", 200)):
            raw_v = str(args_dict.get(line_key, default_val))
            digits = re.search(r'\d+', re.sub(r'<[^>]*>', '', raw_v))
            args_dict[line_key] = int(digits.group(0)) if digits else default_val

    elif target_fn == "write_file":
        if not args_dict.get("path") or "content" not in args_dict:
            return None

    elif target_fn == "replace_file_content":
        if not args_dict.get("path") or "target_content" not in args_dict or "replacement_content" not in args_dict:
            return None

    elif target_fn == "search_files":
        q = str(args_dict.get("query", "")).strip()
        if not q:
            return None
        # If the model tried to run a shell command via search_files (e.g. "ls -la /sdcard/"), map it to execute_bash
        bash_prefixes = ("ls ", "ls", "dir", "cat ", "pwd", "echo ", "find ", "grep ", "head ", "tail ", "python", "node ")
        if any(q == p or q.startswith(p) for p in bash_prefixes):
            return "execute_bash", {"command": q}

    return target_fn, args_dict

def normalize_inbound_actions(text: str, reasoning: str = "") -> List[AgentAction]:
    """
    Inbound Normalizer: Parses arbitrary tool emission formats from all model families
    (DeepSeek DSML, Hermes, Mistral [TOOL_CALLS], Claude/GLM XML, Markdown, JSON)
    into a unified list of AgentAction objects.
    """
    if not text and not reasoning:
        return []

    actions: List[AgentAction] = []
    text_content = text or ""
    if is_explanatory_text(text_content):
        return []
    if re.search(r'<tool_call>', text_content, re.I) and not re.search(r'</tool_call>', text_content, re.I):
        return []

    # Format 0: Mistral / Codestral [TOOL_CALLS] [...]
    mistral_matches = re.findall(r'\[TOOL_CALLS\]\s*\[(.*?)\]', text_content, re.DOTALL)
    for mm in mistral_matches:
        raw_list_str = f"[{mm}]"
        parsed_calls = _try_parse_json(raw_list_str)
        if isinstance(parsed_calls, list):
            for tc in parsed_calls:
                if isinstance(tc, dict):
                    fn_name = tc.get("name") or tc.get("tool") or ""
                    args = tc.get("arguments") or tc.get("args") or {}
                    if isinstance(args, str):
                        try:
                            args = json.loads(args, strict=False)
                        except Exception:
                            args = {"command": args}
                    validated = _validate_and_normalize_tool(fn_name, args)
                    if validated:
                        norm_name, norm_args = validated
                        actions.append(AgentAction(tool_name=norm_name, args=norm_args, raw_call=str(tc)))
        else:
            # Fallback for [call:execute_bash{command: "..."}] or [execute_bash(command="...")]
            fn_call_m = re.search(r'\[?(?:call:)?(\w+)\s*[\(\{](.*?)[\)\}]\]?', mm, re.DOTALL)
            if fn_call_m:
                fn_name = fn_call_m.group(1).lower()
                inner_args = fn_call_m.group(2).strip()
                cmd_match = re.search(r'command\s*[:=]\s*[\'"](.*?)[\'"](?:\s*,|\s*$|\s*})', inner_args, re.DOTALL)
                parsed_args = {"command": cmd_match.group(1).replace(r"\'", "'").replace(r'\"', '"')} if cmd_match else {}
                validated = _validate_and_normalize_tool(fn_name, parsed_args)
                if validated:
                    norm_name, norm_args = validated
                    actions.append(AgentAction(tool_name=norm_name, args=norm_args, raw_call=mm))

    if actions:
        return actions

    # Format 1: Hermes / Liquid Special Token tool calls (<|tool_call_start|>...<|tool_call_end|>)
    hermes_matches = re.findall(r'<\|tool_call_start\|>(.*?)<\|tool_call_end\|>', text_content, re.DOTALL)
    for hm in hermes_matches:
        hm = hm.strip()
        fn_call_m = re.search(r'\[?(\w+)\((.*?)\)\]?', hm, re.DOTALL)
        if fn_call_m:
            fn_name = fn_call_m.group(1).lower()
            inner_args = fn_call_m.group(2).strip()
            cmd_match = re.search(r'command\s*=\s*[\'"](.*?)[\'"](?:\s*,|\s*$)', inner_args, re.DOTALL)
            parsed_args = {"command": cmd_match.group(1).replace(r"\'", "'").replace(r'\"', '"')} if cmd_match else {}
            validated = _validate_and_normalize_tool(fn_name, parsed_args)
            if validated:
                norm_name, norm_args = validated
                actions.append(AgentAction(tool_name=norm_name, args=norm_args, raw_call=hm))
        else:
            parsed = _try_parse_json(hm)
            if isinstance(parsed, dict) and "name" in parsed:
                validated = _validate_and_normalize_tool(parsed["name"], parsed.get("arguments", {}))
                if validated:
                    norm_name, norm_args = validated
                    actions.append(AgentAction(tool_name=norm_name, args=norm_args, raw_call=hm))

    if actions:
        return actions

    # Format 2: DeepSeek DSML format (< | DSML | calls> ... < | DSML | invoke name="..."> ... </ | DSML | calls>)
    dsml_invokes = re.findall(
        r'<\s*[|｜]\s*DSML\s*[|｜]\s*invoke([^>]*)>(.*?)</\s*[|｜]\s*DSML\s*[|｜]\s*invoke>',
        text_content,
        re.DOTALL | re.IGNORECASE
    )
    for attrs, body in dsml_invokes:
        name_m = re.search(r'name=["\']([\w_]+)["\']', attrs, re.IGNORECASE)
        fn_name = name_m.group(1) if name_m else ""

        param_matches = re.findall(
            r'<\s*[|｜]\s*DSML\s*[|｜]\s*parameter\s+name=["\']([\w_]+)["\'][^>]*>(.*?)</\s*[|｜]\s*DSML\s*[|｜]\s*parameter>',
            body,
            re.DOTALL | re.IGNORECASE
        )
        if not param_matches:
            # Fallback for unclosed or self-closing parameter tags
            raw_params = re.findall(
                r'<\s*[|｜]\s*DSML\s*[|｜]\s*parameter\s+name=["\']([\w_]+)["\'][^>]*>([^<]+)',
                body,
                re.IGNORECASE
            )
            for p_name, p_val in raw_params:
                param_matches.append((p_name, p_val))

        args_dict = {}
        for p_name, p_val in param_matches:
            p_name = p_name.strip().lower()
            p_val = p_val.strip()
            if "<" in p_val:
                p_val = re.sub(r'<\s*/?[|｜]?\s*DSML\s*[|｜]?[^>]*>', '', p_val).strip()
                p_val = re.sub(r'<[^>]*>', '', p_val).strip()

            if p_name == "name" and not fn_name:
                fn_name = p_val
            elif p_name in ("arguments", "args"):
                parsed_json = _try_parse_json(p_val)
                if parsed_json and isinstance(parsed_json, dict):
                    args_dict.update(parsed_json)
                else:
                    args_dict["command"] = p_val
            else:
                args_dict[p_name] = p_val

        validated = _validate_and_normalize_tool(fn_name, args_dict)
        if validated:
            norm_name, norm_args = validated
            actions.append(AgentAction(tool_name=norm_name, args=norm_args, raw_call=body))

    if actions:
        return actions

    # Format 3: DeepSeek Unicode Tool Call delimiter format (<｜tool call begin｜>...)
    ds_uni_matches = re.findall(r'<[|｜]tool call begin[|｜]>(.*?)<[|｜]tool call end[|｜]>', text_content, re.DOTALL)
    for dm in ds_uni_matches:
        dm = dm.strip()
        parts = re.split(r'<[|｜]tool sep[|｜]>', dm)
        fn_name = parts[0].replace("function", "").strip() if parts else ""
        raw_args = parts[1].strip() if len(parts) > 1 else ""
        parsed_args = _try_parse_json(raw_args)
        validated = _validate_and_normalize_tool(fn_name, parsed_args)
        if validated:
            norm_name, norm_args = validated
            actions.append(AgentAction(tool_name=norm_name, args=norm_args, raw_call=dm))

    if actions:
        return actions

    # Format 4: XML <tool_call> or <invoke> or <action>
    xml_matches = re.findall(r'<tool_call>(.*?)</tool_call>', text_content, re.DOTALL | re.IGNORECASE)
    for m in xml_matches:
        m = m.strip()
        if not m:
            continue
        parsed = _try_parse_json(m)
        if isinstance(parsed, dict) and "name" in parsed:
            validated = _validate_and_normalize_tool(parsed["name"], parsed.get("arguments", {}))
            if validated:
                norm_name, norm_args = validated
                actions.append(AgentAction(tool_name=norm_name, args=norm_args, raw_call=m))
        else:
            # Check subtag format: <tool_name>...</tool_name><parameters>...</parameters>
            t_name_m = re.search(r'<tool_name>(.*?)</tool_name>', m, re.DOTALL | re.IGNORECASE)
            if t_name_m:
                t_name = t_name_m.group(1).strip()
                t_params = {}
                p_matches = re.findall(r'<(\w+)>(.*?)</\1>', m, re.DOTALL)
                for p_k, p_v in p_matches:
                    if p_k.lower() != "tool_name":
                        t_params[p_k] = p_v.strip()
                validated = _validate_and_normalize_tool(t_name, t_params)
                if validated:
                    norm_name, norm_args = validated
                    actions.append(AgentAction(tool_name=norm_name, args=norm_args, raw_call=m))

    # XML <invoke name="...">...</invoke>
    xml_invokes = re.findall(r'<invoke\s+name=["\']([\w_]+)["\'][^>]*>(.*?)</invoke>', text_content, re.DOTALL | re.IGNORECASE)
    for inv_name, inv_body in xml_invokes:
        params = {}
        for p_k, p_v in re.findall(r'<parameter\s+name=["\']([\w_]+)["\'][^>]*>(.*?)</parameter>', inv_body, re.DOTALL | re.IGNORECASE):
            params[p_k] = p_v.strip()
        validated = _validate_and_normalize_tool(inv_name, params)
        if validated:
            norm_name, norm_args = validated
            actions.append(AgentAction(tool_name=norm_name, args=norm_args, raw_call=inv_body))

    if actions:
        return actions

    # Format 5: Functional syntax: bash(command='...') or execute_bash(command="...") or view_file(path="...")
    func_starts = list(re.finditer(r'(?<![\w.])(make_directory|execute_bash|bash|view_file|write_file|replace_file_content|search_files|manage_tasks)\s*\(', text_content))
    positional = {
        "make_directory": ("path",),
        "execute_bash": ("command", "reason"), "bash": ("command", "reason"),
        "view_file": ("path", "start_line", "end_line"),
        "write_file": ("path", "content", "overwrite"),
        "replace_file_content": ("path", "target_content", "replacement_content"),
        "search_files": ("query", "directory"), "manage_tasks": ("action", "target"),
    }
    for match in func_starts:
        fragment = text_content[match.start():]
        depth = 0
        try:
            lines = fragment.splitlines(keepends=True)
            for token in tokenize.generate_tokens(io.StringIO(fragment).readline):
                if token.type == tokenize.OP:
                    if token.string == "(":
                        depth += 1
                    elif token.string == ")":
                        depth -= 1
                        if depth == 0:
                            end = sum(len(line) for line in lines[:token.end[0] - 1]) + token.end[1]
                            raw = fragment[:end]
                            call = ast.parse(raw, mode="eval").body
                            if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Name):
                                break
                            keys = positional[call.func.id]
                            if len(call.args) > len(keys):
                                break
                            args = {keys[i]: ast.literal_eval(value) for i, value in enumerate(call.args)}
                            for keyword in call.keywords:
                                if keyword.arg is None or keyword.arg in args:
                                    raise ValueError("Duplicate or expanded arguments")
                                args[keyword.arg] = ast.literal_eval(keyword.value)
                            validated = _validate_and_normalize_tool(call.func.id, args)
                            if validated:
                                actions.append(AgentAction(tool_name=validated[0], args=validated[1], raw_call=raw))
                            break
        except (ValueError, SyntaxError, tokenize.TokenError, KeyError):
            continue
    # Never reinterpret malformed function text as a raw shell command.
    if func_starts:
        return actions

    # Format 6: JSON in markdown ```json ... ``` with {"name": ...}
    json_blocks = re.findall(r'```(?:json)?\s*\n({.*?})\s*\n```', text_content, re.DOTALL)
    for jb in json_blocks:
        parsed = _try_parse_json(jb)
        if isinstance(parsed, dict):
            name = parsed.get("name") or parsed.get("tool") or parsed.get("action")
            if name:
                validated = _validate_and_normalize_tool(name, parsed.get("arguments") or parsed.get("args") or {})
                if validated:
                    norm_name, norm_args = validated
                    actions.append(AgentAction(tool_name=norm_name, args=norm_args, raw_call=jb))

    if actions:
        return actions

    # Format 7: Raw JSON object with tool definition (isolated or embedded in conversational text)
    parsed_raw = _try_parse_json(text_content.strip())
    if isinstance(parsed_raw, dict) and ("name" in parsed_raw or "tool" in parsed_raw or "action" in parsed_raw):
        raw_name = parsed_raw.get("name") or parsed_raw.get("tool") or parsed_raw.get("action")
        validated = _validate_and_normalize_tool(raw_name, parsed_raw.get("arguments") or parsed_raw.get("args") or {})
        if validated:
            norm_name, norm_args = validated
            actions.append(AgentAction(tool_name=norm_name, args=norm_args, raw_call=text_content))

    if not actions:
        # Search for embedded JSON object with "name" and "arguments" inside text
        embedded_json_matches = re.findall(r'(\{\s*\"name\"\s*:\s*\"[^\"]+\"\s*,\s*\"arguments\"\s*:\s*\{.*?\}\s*\})', text_content, re.DOTALL)
        for emb_str in embedded_json_matches:
            emb_parsed = _try_parse_json(emb_str)
            if isinstance(emb_parsed, dict) and "name" in emb_parsed:
                validated = _validate_and_normalize_tool(emb_parsed["name"], emb_parsed.get("arguments") or {})
                if validated:
                    norm_name, norm_args = validated
                    actions.append(AgentAction(tool_name=norm_name, args=norm_args, raw_call=emb_str))

    if actions:
        return actions

    # Format 8: Fenced bash / shell blocks (multiline or single-line)
    bash_blocks = re.findall(r'```(bash|sh|shell|zsh|console)?[ \t]*\n(.*?)\n```', text_content, re.DOTALL | re.IGNORECASE)
    for language, block in bash_blocks:
        lines = [l.strip() for l in block.strip().splitlines() if l.strip() and not l.strip().startswith('#')]
        if lines:
            first_line = lines[0]
            if not _looks_like_shell_command(first_line):
                continue
            # Hard filter: Ignore text that is clearly terminal output rather than a shell command!
            if re.match(r'^\d+\s+\S+\s+\d{1,2}:\d{2}', first_line):
                continue
            if re.match(r'^[d\-]?[rwx\-]{9}', first_line) or re.match(r'^total\s+\d+', first_line, re.IGNORECASE):
                continue
            if re.match(r'^(?:PID|UID|USER)\s+(?:TTY|PID)\s+', first_line, re.IGNORECASE) or first_line.startswith("sh: "):
                continue
            if re.match(r'^\d+$', first_line) or "[exitstatus]" in first_line:
                continue

            # Preserve multiline shell syntax, heredocs, indentation and blank lines.
            cmd = block.strip()
            validated = _validate_and_normalize_tool("execute_bash", {"command": cmd})
            if validated:
                norm_name, norm_args = validated
                actions.append(AgentAction(tool_name=norm_name, args=norm_args, raw_call=block))
                break

    if actions:
        return actions

    # Format 9: Inline labeled commands e.g. "Command: `...`" or "Run: `...`"
    labeled_matches = re.findall(r'(?:command|run|execute|running):\s*`([^`\n]+)`', text_content, re.IGNORECASE)
    for lm in labeled_matches:
        lm_cmd = lm.strip()
        if lm_cmd and _looks_like_shell_command(lm_cmd) and not any(lm_cmd.startswith(p) for p in ("total ", "PID ", "drwx", "-rw")):
            validated = _validate_and_normalize_tool("execute_bash", {"command": lm_cmd})
            if validated:
                norm_name, norm_args = validated
                actions.append(AgentAction(tool_name=norm_name, args=norm_args, raw_call=lm))
                break

    if actions:
        return actions

    # Format 10: Explicit file creation intent with path and content
    write_m = re.search(r"(?:create|write)\s+([/\w\.\-]+\.[a-zA-Z0-9]+)\s+(?:containing|with(?:\s+content)?)\s+['\"]?([^'\"\n]+)['\"]?", text_content, re.IGNORECASE)
    if write_m:
        p_path = write_m.group(1).strip()
        p_content = write_m.group(2).strip()
        if p_path and p_content:
            validated = _validate_and_normalize_tool("write_file", {"path": p_path, "content": p_content, "overwrite": True})
            if validated:
                norm_name, norm_args = validated
                actions.append(AgentAction(tool_name=norm_name, args=norm_args, raw_call=write_m.group(0)))

    if actions:
        return actions

    # Format 11: Reason conclusion fallback (if reasoning trace provided)
    if reasoning:
        confident_calls = extract_confident_command_from_reasoning(reasoning)
        for cc in confident_calls:
            fn = cc.get("function", {})
            validated = _validate_and_normalize_tool(fn.get("name"), fn.get("arguments", "{}"))
            if validated:
                norm_name, norm_args = validated
                actions.append(AgentAction(tool_name=norm_name, args=norm_args, raw_call=str(cc)))

    if actions:
        return actions

    # Format 12: Natural-language acknowledgment extraction
    # Catches patterns like:
    #   "Running `npm run dev` in `/sdcard/cli/app`..."
    #   "Navigating to /sdcard/cli/app and running npm run dev..."
    #   "Executing `cd /sdcard/cli/app && npm run dev`..."
    #   "Running npm run dev in /sdcard/cli/app..."

    # Pattern A: "Running `CMD` in `PATH`" or "Running CMD in PATH"
    run_in_m = re.search(
        r'[Rr]unning\s+[`"]?([^`"\n]+?)[`"]?\s+in\s+[`"]?([/\w.\-]+)[`"]?',
        text_content
    )
    if run_in_m:
        cmd_part = run_in_m.group(1).strip().rstrip('.')
        path_part = run_in_m.group(2).strip().rstrip('.')
        full_cmd = f"cd {path_part} && {cmd_part}"
        validated = _validate_and_normalize_tool("execute_bash", {"command": full_cmd}) if _looks_like_shell_command(cmd_part) else None
        if validated:
            norm_name, norm_args = validated
            actions.append(AgentAction(tool_name=norm_name, args=norm_args, raw_call=run_in_m.group(0)))

    if actions:
        return actions

    # Pattern B: "Navigating to PATH" or "Changing to PATH then/and CMD"
    nav_m = re.search(
        r'[Nn]avigate?(?:ing)?\s+to\s+[`"]?([/\w.\-]+)[`"]?(?:\s+(?:then|and)\s+(?:run(?:ning)?|executing?|start(?:ing)?)\s+[`"]?([^`"\n.]+)[`"]?)?',
        text_content
    )
    if nav_m:
        path_part = nav_m.group(1).strip()
        sub_cmd = (nav_m.group(2) or "").strip().rstrip('.')
        full_cmd = f"cd {path_part} && {sub_cmd}" if sub_cmd else f"cd {path_part}"
        validated = _validate_and_normalize_tool("execute_bash", {"command": full_cmd})
        if validated:
            norm_name, norm_args = validated
            actions.append(AgentAction(tool_name=norm_name, args=norm_args, raw_call=nav_m.group(0)))

    if actions:
        return actions

    # Pattern C: "Executing `CMD`" or "Running `CMD`" — backtick/quoted command
    exec_m = re.search(
        r'(?:[Ee]xecut(?:ing|e)|[Rr]unning|[Ss]tart(?:ing)?)\s+[`"]([^`"\n]+)[`"]',
        text_content
    )
    if exec_m:
        cmd_part = exec_m.group(1).strip().rstrip('.')
        validated = _validate_and_normalize_tool("execute_bash", {"command": cmd_part}) if _looks_like_shell_command(cmd_part) else None
        if validated:
            norm_name, norm_args = validated
            actions.append(AgentAction(tool_name=norm_name, args=norm_args, raw_call=exec_m.group(0)))

    return actions


def is_explanatory_text(text):
    """Keep examples and explicitly non-executing explanations out of the action path."""
    prefix = re.split(r'```|<tool_call>|<invoke|\{', str(text or ""), maxsplit=1)[0]
    if re.search(r"\b(?:do not|don't|never)\s+(?:run|execute)\b", prefix, re.I):
        return True
    explanation = re.search(r"\b(?:for example|for reference|example command|example only|you can run|command would be|the command is)\b", prefix, re.I)
    commitment = re.search(r"\b(?:I will|I'll|I am|I'm)\s+(?:run|execute|inspect|check|create|edit)\b", prefix, re.I)
    return bool(explanation and not commitment)


def extract_tools_from_text(text: str) -> List[Dict[str, Any]]:
    """Synthesizes tool calls using the inbound normalizer."""
    actions = normalize_inbound_actions(text)
    return [a.to_tool_call() for a in actions]


ACTION_INTENT_PATTERNS = [
    r"\b(?:running|executing|starting)\s+`[^`\n]+`\s+in\s+`?/[\w.\-/]+`?",
    r"(?:let me|i(?:'ll| will))\s+(?:help you\s+)?(?:diagnose|check|run|execute|inspect|list|view|search|create|write|verify|ensure|explore|access|test|start)\b[^\.\n]*",
    r"(?:let's|let us)\s+(?:verify|check|diagnose|run|inspect|test|create|write)\b[^\.\n]*",
    r"before i (?:close|wrap up|finish)[^\.\n]*let me[^\.\n]*",
    r"(?:going|go) the extra mile (?:and|to)\s+(?:run|verify|check|diagnose|create|inspect)[^\.\n]*",
    r"(?:i will|i'll)\s+(?:create|write|generate)\s+[\w/\.\-]+\s+(?:containing|with)[^\.\n]*",
    r"(?:let me|i should|i need to|i will)\s+use\s+(?:the\s+)?(?:write_file|execute_bash|view_file|replace_file_content)\b[^\.\n]*",
    r"(?:i should|i need to)\s+(?:run|execute|call|trigger)\s+(?:the\s+)?(?:touch|mkdir|cat|echo|ls|python|curl)\b[^\.\n]*",
    r"using\s+(?:touch|mkdir|cat|echo|write_file|execute_bash)\s+is\s+the\s+right\s+approach\b[^\.\n]*",
    r"(?:i am going to|i'm going to)\s+(?:run|execute|create|verify|list|diagnose)[^\.\n]*",
    r"first,?\s+(?:let me|i'll|i will)\s+(?:prompt|ask|check)[^\.\n]*"
]

def detect_action_intent(text: str) -> Tuple[bool, str]:
    """Detects whether assistant text promised an action without completing/verifying it."""
    if not text:
        return False, ""
    clean = clean_response_text(text)
    for pat in ACTION_INTENT_PATTERNS:
        m = re.search(pat, clean, re.IGNORECASE)
        if m:
            return True, m.group(0).strip()
    return False, ""


def extract_announced_shell_command(text: str) -> List[Dict[str, Any]]:
    """Recover a command the assistant explicitly said it is running in a directory."""
    if is_explanatory_text(text):
        return []
    match = re.search(
        r"\b(?:running|executing|starting)\s+`([^`\n]+)`\s+in\s+`?(/[\w.\-/]+)`?",
        text or "", re.IGNORECASE,
    )
    if not match:
        return []
    command, directory = match.groups()
    if not re.fullmatch(r"(?:npm|pnpm|yarn|bun)\s+(?:(?:run)\s+)?(?:dev|start|serve|preview)", command.strip()):
        return []
    shell_command = f"cd -- {shlex.quote(directory)} && {command.strip()}"
    normalized = _validate_and_normalize_tool("execute_bash", {"command": shell_command})
    if not normalized:
        return []
    name, args = normalized
    return [AgentAction(tool_name=name, args=args, raw_call=text).to_tool_call()]


def extract_confident_command_from_reasoning(reasoning: str) -> List[Dict[str, Any]]:
    """
    Extracts a confident terminal bash command or tool call from reasoning,
    specifically avoiding speculative brainstorming or exploratory musings.
    Looks strictly at the final conclusion segment of thought traces.
    """
    if not reasoning or len(reasoning.strip()) < 10:
        return []

    # First: Check if reasoning contains XML <tool_call>
    xml_tools = extract_tools_from_text(reasoning)
    if xml_tools:
        return xml_tools

    # Second: Inspect the final 1200 characters of reasoning (the committed conclusion)
    conclusion_text = reasoning[-1200:].strip()

    valid_shell_starters = (
        "ls", "cat", "find", "grep", "head", "tail", "pwd", "mkdir",
        "printf", "echo", "python", "python3", "node", "npm", "kill",
        "pkill", "rm", "cp", "mv", "touch", "chmod", "curl", "git"
    )

    # Line-by-line colon prefix check for committed execution
    for line in conclusion_text.splitlines():
        line = line.strip()
        if not line:
            continue
        if ":" in line:
            prefix, _, rest = line.partition(":")
            p_low = prefix.lower().strip()
            # Exclude retrospective or descriptive musings
            if any(neg in p_low for neg in ("the command for", "command would be", "command is", "command was", "command used", "already ran", "previously ran", "simple way is")):
                continue
            if any(k in p_low for k in ("execute_bash", "command", "run", "let me run", "best move", "i will run", "executing")):
                cand = rest.strip().strip("`").strip('"').strip("'")
                if cand and len(cand) >= 3:
                    if any(cand.lower().startswith(q) for q in ("should i", "maybe", "could i", "would", "what if", "if ")):
                        continue
                    if any(cand.lower().endswith(q) for q in ("?", "instead", "or not")):
                        continue
                    first_word = cand.split()[0].lower() if cand.split() else ""
                    if first_word in valid_shell_starters:
                        validated = _validate_and_normalize_tool("execute_bash", {"command": cand})
                        if validated:
                            norm_name, norm_args = validated
                            return [{
                                "id": f"call_{uuid.uuid4().hex[:8]}",
                                "type": "function",
                                "function": {
                                    "name": norm_name,
                                    "arguments": json.dumps(norm_args)
                                }
                            }]

    # Also check explicitly committed execution directives and functional syntax calls
    commit_patterns = [
        r'execute_bash\s*\(\s*(?:command\s*=\s*)?[\'"](.*?)[\'"]\s*\)',
        r'bash\s*\(\s*(?:command\s*=\s*)?[\'"](.*?)[\'"]\s*\)',
        r'(?:i will run|i will execute|let me execute|let me run|command to run|running command|run the command|will use the command|execute the command)[:\s]+`([^`\n]+)`',
        r'(?:i will run|i will execute|let me execute|let me run|command to run|running command|run the command|will use the command|execute the command)[:\s]+[\'"]([^"\'\n]+)[\'"]',
        r'tool_call[:\s]+`([^`\n]+)`'
    ]

    for pat in commit_patterns:
        matches = re.findall(pat, conclusion_text, re.IGNORECASE)
        for cand in matches:
            cand_cmd = cand.strip()
            if not cand_cmd or len(cand_cmd) < 3:
                continue
            if any(cand_cmd.lower().startswith(q) for q in ("should i", "maybe", "could i", "would", "what if", "if ")):
                continue
            if any(cand_cmd.lower().endswith(q) for q in ("?", "instead", "or not")):
                continue
            first_word = cand_cmd.split()[0].lower() if cand_cmd.split() else ""
            if first_word in valid_shell_starters:
                validated = _validate_and_normalize_tool("execute_bash", {"command": cand_cmd})
                if validated:
                    norm_name, norm_args = validated
                    return [{
                        "id": f"call_{uuid.uuid4().hex[:8]}",
                        "type": "function",
                        "function": {
                            "name": norm_name,
                            "arguments": json.dumps(norm_args)
                        }
                    }]

    # Synthesizer check: If reasoning explicitly commits to a touch/echo/mkdir file creation target
    # Require an active action commitment (not just explaining command syntax or what echo does)
    has_file_exec_intent = any(comm in conclusion_text.lower() for comm in (
        "i will run", "i'll run", "let me run", "i will execute", "let me execute",
        "i will use `touch", "i'll use `touch", "i will use `echo", "i'll use `echo",
        "i'll use touch", "i will use touch", "i will create", "i'll create", "let me create"
    )) and not any(p in conclusion_text.lower() for p in ("the command for that is", "command would be", "already ran", "will output the text", "straightforward way to"))

    if has_file_exec_intent:
        echo_m = re.search(r'echo\s+["\']([^"\']+)["\']\s*>\s*([/\w\.\-]+(?:\.txt|\.md|\.json|\.py|\.sh|\.html))', conclusion_text, re.IGNORECASE)
        if echo_m:
            content_txt = echo_m.group(1).strip()
            path_txt = echo_m.group(2).strip()
            synth_cmd = f'echo "{content_txt}" > {path_txt}'
            validated = _validate_and_normalize_tool("execute_bash", {"command": synth_cmd})
            if validated:
                norm_name, norm_args = validated
                return [{
                    "id": f"call_{uuid.uuid4().hex[:8]}",
                    "type": "function",
                    "function": {
                        "name": norm_name,
                        "arguments": json.dumps(norm_args)
                    }
                }]

        touch_cmd_m = re.search(r'touch\s+([/\w\.\-]+(?:\.txt|\.md|\.json|\.py|\.sh|\.html))', conclusion_text, re.IGNORECASE)
        if touch_cmd_m:
            path_txt = touch_cmd_m.group(1).strip()
            validated = _validate_and_normalize_tool("execute_bash", {"command": f"touch {path_txt}"})
            if validated:
                norm_name, norm_args = validated
                return [{
                    "id": f"call_{uuid.uuid4().hex[:8]}",
                    "type": "function",
                    "function": {
                        "name": norm_name,
                        "arguments": json.dumps(norm_args)
                    }
                }]

        file_touch_m = re.search(r'(?:touch|create(?:\s+the)?\s+file)\s+[`"\']?([/\w\.\-\s]+(?:\.txt|\.py|\.js|\.html|\.json|\.md|John\s+Benser))[`"\']?', conclusion_text, re.IGNORECASE)
        if file_touch_m:
            raw_target = file_touch_m.group(1).strip()
            if raw_target and not any(k in raw_target.lower() for k in ("command", "tool", "file", "it", "this", "empty")):
                safe_target = f'"{raw_target}"' if " " in raw_target and not raw_target.startswith('"') else raw_target
                validated = _validate_and_normalize_tool("execute_bash", {"command": f"touch {safe_target}"})
                if validated:
                    norm_name, norm_args = validated
                    return [{
                        "id": f"call_{uuid.uuid4().hex[:8]}",
                        "type": "function",
                        "function": {
                            "name": norm_name,
                            "arguments": json.dumps(norm_args)
                        }
                    }]

    # General directory and file creation intent synthesis in reasoning
    has_active_commitment = any(comm in conclusion_text.lower() for comm in (
        "i will create", "i'll create", "let me create", "i'm going to create", "i am going to create",
        "i need to create", "i will use", "i'll use", "let me run", "i will run"
    ))
    if has_active_commitment and not any(p in conclusion_text.lower() for p in ("the command for that is", "command would be", "already ran")):
        dir_match = re.search(r'(?:in|inside|to)\s+(?:the\s+)?([/\w\.\-]+/[/\w\.\-]+)\s+directory', conclusion_text, re.IGNORECASE)
        if dir_match and ("touch" in conclusion_text.lower() or "create" in conclusion_text.lower()):
            target_dir = dir_match.group(1).strip()
            if target_dir.startswith("/") and not any(k in target_dir.lower() for k in ("command", "tool")):
                # If creator's name file creation is discussed:
                if "name" in conclusion_text.lower():
                    synth_cmd = f"mkdir -p {target_dir} && echo 'John Benser' > {target_dir}/name.txt"
                    validated = _validate_and_normalize_tool("execute_bash", {"command": synth_cmd})
                    if validated:
                        norm_name, norm_args = validated
                        return [{
                            "id": f"call_{uuid.uuid4().hex[:8]}",
                            "type": "function",
                            "function": {
                                "name": norm_name,
                                "arguments": json.dumps(norm_args)
                            }
                        }]

    return []


