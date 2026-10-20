import sys
import os
import re
import time
import shutil
import threading
import textwrap
from prompt_toolkit import PromptSession
from prompt_toolkit.styles import Style
from prompt_toolkit.lexers import Lexer
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout.controls import BufferControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.filters import to_filter
from prompt_toolkit.patch_stdout import patch_stdout
from config import CONFIG, AGENT_STATE
from engine.task_runner import GLOBAL_TASK_MANAGER

THEME = {
    "border": "#3f3f46",         # Dark zinc
    "prompt_arrow": "#818cf8",   # Indigo accent
    "shortcut": "#38bdf8",       # Sky cyan
    "status_text": "#71717a",     # Muted gray
}

LAST_CTRL_C_TIME = 0
SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

SLASH_COMMANDS = [
    ("/add-dir", "Add a directory to the workspace context"),
    ("/agents", "List available custom agent personas"),
    ("/artifact", "View and review artifacts"),
    ("/btw", "Ask a side question without interrupting the current agent"),
    ("/cancel", "Cancel current active AI task"),
    ("/changelog", "Show release notes and changelog"),
    ("/clear", "Clear screen and history"),
    ("/edit", "Open in-terminal code editor"),
    ("/exit", "Exit Galam.AI session"),
    ("/help", "List available commands"),
    ("/model", "Switch Models under Active Endpoint"),
    ("/mode", "Toggle Review / Auto / Always-Proceed mode"),
    ("/plan", "Generate implementation plan"),
    ("/settings", "Switch Endpoints and Options"),
    ("/status", "Inspect storage and tokens"),
    ("/tasks", "View and manage active background server tasks")
]

class AntigravityLexer(Lexer):
    def lex_document(self, document):
        def get_line(lineno):
            line = document.lines[lineno]
            if line.startswith("/"):
                parts = line.split(" ", 1)
                cmd = parts[0]
                rest = (" " + parts[1]) if len(parts) > 1 else ""
                return [("class:shortcut", cmd), ("class:normal", rest)]
            return [("class:normal", line)]
        return get_line

kb = KeyBindings()

@kb.add('c-c')
def handle_ctrl_c(event):
    global LAST_CTRL_C_TIME
    buf = event.current_buffer

    if AGENT_STATE.get("is_thinking", False):
        AGENT_STATE["abort"] = True
        AGENT_STATE["is_thinking"] = False
        event.app.invalidate()
        return

    if AGENT_STATE.get("review_pending", False):
        AGENT_STATE["review_decision"] = "cancel"
        if AGENT_STATE.get("review_event"):
            AGENT_STATE["review_event"].set()
        AGENT_STATE["review_pending"] = False
        event.app.invalidate()
        return

    if buf.text:
        buf.text = ""
        buf.cursor_position = 0
        event.app.invalidate()
        return

    now = time.time()
    if (now - LAST_CTRL_C_TIME) < 3.5:
        event.app.exit(result="/exit")
    else:
        LAST_CTRL_C_TIME = now
        event.app.invalidate()

@kb.add('escape')
def handle_escape(event):
    if AGENT_STATE.get("is_thinking", False):
        AGENT_STATE["abort"] = True
        AGENT_STATE["is_thinking"] = False
        event.app.invalidate()
        return

    if AGENT_STATE.get("review_pending", False):
        AGENT_STATE["review_decision"] = "cancel"
        if AGENT_STATE.get("review_event"):
            AGENT_STATE["review_event"].set()
        AGENT_STATE["review_pending"] = False
        event.app.invalidate()
        return

    if AGENT_STATE.get("in_btw_mode", False):
        AGENT_STATE["in_btw_mode"] = False
        buf = event.current_buffer
        buf.text = ""
        buf.cursor_position = 0
        event.app.invalidate()
        return

    buf = event.current_buffer
    if buf.text.startswith('/') and ' ' not in buf.text:
        buf.text = ""
        buf.cursor_position = 0
        AGENT_STATE["slash_idx"] = 0
        event.app.invalidate()
        return

    if buf.text:
        buf.text = ""
        buf.cursor_position = 0
        event.app.invalidate()

@kb.add('up')
def handle_up(event):
    buf = event.current_buffer
    if buf.text.startswith('/') and ' ' not in buf.text:
        q = buf.text.lower()
        matching = [c for c, _ in SLASH_COMMANDS if c.lower().startswith(q)]
        if matching:
            AGENT_STATE["slash_idx"] = (AGENT_STATE.get("slash_idx", 0) - 1) % len(matching)
            event.app.invalidate()
            return
    buf.auto_up()

@kb.add('down')
def handle_down(event):
    buf = event.current_buffer
    if buf.text.startswith('/') and ' ' not in buf.text:
        q = buf.text.lower()
        matching = [c for c, _ in SLASH_COMMANDS if c.lower().startswith(q)]
        if matching:
            AGENT_STATE["slash_idx"] = (AGENT_STATE.get("slash_idx", 0) + 1) % len(matching)
            event.app.invalidate()
            return
    buf.auto_down()

@kb.add('c-o')
def handle_ctrl_o(event):
    AGENT_STATE["show_thoughts"] = not AGENT_STATE.get("show_thoughts", False)

    # Expanded → print the thought trace box if there's content
    if AGENT_STATE["show_thoughts"]:
        past = AGENT_STATE.get("last_reasoning", "").strip()
        if past:
            cols = shutil.get_terminal_size((44, 24)).columns
            div = "─" * min(cols - 2, 42)
            sys.stdout.write(f"\n\033[38;5;141m╭─ [Thought Trace]{div[len('╭─ [Thought Trace]'):]}╮\033[0m\n")
            for line in past.splitlines():
                sys.stdout.write(f"\033[38;5;244m│ {line}\033[0m\n")
            sys.stdout.write(f"\033[38;5;141m╰{div}╯\033[0m\n\n")
            sys.stdout.flush()
    # Collapsed → silently hide; no stdout message at all

    event.app.invalidate()

@kb.add('tab')
def handle_tab(event):
    buf = event.current_buffer
    if buf.text.startswith('/') and ' ' not in buf.text:
        q = buf.text.lower()
        matching = [c for c, _ in SLASH_COMMANDS if c.lower().startswith(q)]
        if matching:
            idx = AGENT_STATE.get("slash_idx", 0) % len(matching)
            buf.text = matching[idx]
            buf.cursor_position = len(buf.text)
            event.app.invalidate()
            return

@kb.add('enter')
def handle_enter(event):
    buf = event.current_buffer
    cur_text = buf.text.strip().lower()
    if cur_text.startswith("/") and " " not in cur_text:
        matching = [cmd for cmd, _ in SLASH_COMMANDS if cmd.lower().startswith(cur_text)]
        if matching:
            idx = AGENT_STATE.get("slash_idx", 0) % len(matching)
            buf.text = matching[idx]
            buf.cursor_position = len(buf.text)
    AGENT_STATE["slash_idx"] = 0
    buf.validate_and_handle()

def get_prompt_text():
    """Renders the thinking status directly above the stagnant dock's top divider."""
    cols = shutil.get_terminal_size((44, 24)).columns
    divider = "─" * max(10, cols - 1)
    tokens = []

    # 1. Live Thinking Header (animated loading spinner with dynamic dots)
    if AGENT_STATE.get("is_thinking", False):
        SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
        DOT_FRAMES = [".  ", ".. ", "...", " ..", "  .", "   "]
        t = time.time()
        spinner_char = SPINNER_FRAMES[int(t * 10) % len(SPINNER_FRAMES)]
        dots = DOT_FRAMES[int(t * 3) % len(DOT_FRAMES)]

        tokens.append(("fg:#38bdf8 bold", f" {spinner_char} "))
        tokens.append(("fg:#ffffff bold", f"Loading{dots} "))

        topic = AGENT_STATE.get("current_thought", "")
        clean_topic = topic.strip()
        if clean_topic and clean_topic not in ("Analyzing query...", "Executing execute_bash...", "Responding..."):
            max_topic = max(10, cols - 24)
            tokens.append((f"fg:{THEME['status_text']}", f"({clean_topic[:max_topic]})\n"))
        else:
            tokens.append(("", "\n"))

    # 2. Top Divider Line
    tokens.append((f"fg:{THEME['border']}", f"{divider}\n"))

    # 3. Prompt Arrow
    if AGENT_STATE.get("review_pending", False):
        prompt_label = AGENT_STATE.get("review_prompt") or "Action [Y/n]> "
        tokens.append(("fg:#facc15 bold", prompt_label))
    elif AGENT_STATE.get("in_btw_mode", False):
        tokens.append((f"fg:{THEME['shortcut']} bold", "btw> "))
    else:
        tokens.append((f"fg:{THEME['prompt_arrow']}", "> "))

    return tokens

def get_bottom_toolbar_tokens():
    """Renders the stagnant dock's bottom divider and status toolbar."""
    cols = shutil.get_terminal_size((44, 24)).columns
    divider = "─" * max(10, cols - 1)

    raw_m = CONFIG.get("active_model") or CONFIG.get("model", "")
    clean_m = raw_m.split("/")[-1].replace("@cf/", "").replace("meta/", "")
    mode_str = CONFIG.get("mode", "review").lower().replace("_", "-")

    tasks = GLOBAL_TASK_MANAGER.get_active_tasks()
    task_tally = f" · {len(tasks)} task(s) · /tasks" if tasks else ""
    sub_agents = AGENT_STATE.get("active_subagents", [])
    sub_tag = f" · {len(sub_agents)} subagent(s)" if sub_agents else ""

    right_status = f"{clean_m} · {mode_str}{task_tally}{sub_tag}"

    buf = getattr(prompt_session, "default_buffer", None)
    cur_text = buf.text if buf else ""
    tokens = []

    # 1. Suggestions Menu
    if cur_text.startswith("/") and " " not in cur_text:
        q = cur_text.lower()
        matching = [(c, d) for c, d in SLASH_COMMANDS if c.lower().startswith(q)]
        if matching:
            sel_idx = AGENT_STATE.get("slash_idx", 0) % len(matching)
            max_visible = 5
            start = max(0, min(sel_idx - 2, len(matching) - max_visible))
            visible_items = matching[start:start + max_visible]

            tokens.append((f"fg:{THEME['border']}", f"{divider}\n"))

            for i, (cmd, desc) in enumerate(visible_items):
                actual_idx = start + i
                max_desc = max(8, cols - len(cmd) - 6)
                short_desc = desc[:max_desc]
                if len(desc) > max_desc:
                    short_desc = short_desc[:-3] + "..."

                if actual_idx == sel_idx:
                    tokens.append((f"fg:{THEME['shortcut']} bold", f"> {cmd:<14}"))
                    tokens.append((f"fg:#cbd5e1", f"  {short_desc}\n"))
                else:
                    tokens.append((f"fg:#94a3b8", f"  {cmd:<14}"))
                    tokens.append((f"fg:{THEME['status_text']}", f"  {short_desc}\n"))

            if len(matching) > max_visible:
                remaining = len(matching) - (start + len(visible_items))
                if remaining > 0:
                    tokens.append((f"fg:{THEME['status_text']}", f"  ↓ {remaining} more\n"))
                else:
                    tokens.append((f"fg:{THEME['status_text']}", f"  ↑ {start} more\n"))

            tokens.append((f"fg:{THEME['border']}", f"{divider}\n"))
            tokens.append((f"fg:{THEME['shortcut']}", "↑/↓ Navigate · "))
            tokens.append((f"fg:{THEME['shortcut']}", "enter Select · "))
            tokens.append((f"fg:{THEME['shortcut']}", "tab Complete\n"))

            left_status = "esc to cancel"
            gap_len = max(1, cols - len(left_status) - len(right_status))
            tokens.append((f"fg:{THEME['status_text']}", left_status))
            tokens.append(("", " " * gap_len))
            tokens.append((f"fg:{THEME['status_text']}", right_status))
            return tokens

    # 2. Bottom Divider Line
    tokens.append((f"fg:{THEME['border']}", f"{divider}\n"))

    # 3. Active Background Server Pill
    pill_tokens = GLOBAL_TASK_MANAGER.get_task_pill_tokens()
    if pill_tokens:
        tokens.extend(pill_tokens)
        tokens.append((f"fg:{THEME['border']}", f"{divider}\n"))

    if AGENT_STATE.get("is_thinking", False):
        left_status = "esc to cancel"
    elif (time.time() - LAST_CTRL_C_TIME) < 3.5:
        left_status = "press ctrl+c again to exit"
    else:
        thought_state = "expanded" if AGENT_STATE.get("show_thoughts", False) else "minimized"
        left_status = f"ctrl+o thoughts ({thought_state}) · /help"

    gap_len = max(1, cols - len(left_status) - len(right_status))
    tokens.append((f"fg:{THEME['status_text']}", left_status))
    tokens.append(("", " " * gap_len))
    tokens.append((f"fg:{THEME['status_text']}", right_status))
    return tokens

STYLE_SHEET = Style.from_dict({
    'shortcut': f"fg:{THEME['shortcut']} bold",
    'normal': '#f1f5f9',
    'bottom-toolbar': 'noreverse bg:#000000 fg:#71717a',
})

prompt_session = PromptSession(
    lexer=AntigravityLexer(),
    key_bindings=kb,
    style=STYLE_SHEET,
    multiline=False,
    reserve_space_for_menu=0
)

try:
    prompt_session.layout.container.dont_extend_height = to_filter(True)
    for w in prompt_session.layout.find_all_windows():
        if isinstance(w.content, BufferControl):
            w.wrap_lines = to_filter(True)
            w.dont_extend_height = to_filter(True)
            w.height = Dimension(min=1, max=8)
except Exception:
    pass

def _ui_animator():
    """Continuously refreshes the spinner icon while thinking without touching stdout."""
    while True:
        if AGENT_STATE.get("is_thinking", False) and not AGENT_STATE.get("is_streaming", False) and AGENT_STATE.get("app_ref"):
            try:
                AGENT_STATE["app_ref"].invalidate()
            except Exception:
                pass
        time.sleep(0.08)

threading.Thread(target=_ui_animator, daemon=True).start()

def render_action_card(cmd: str, reason: str = "Automated terminal execution", mode: str = "AUTO"):
    """Displays an Action card matching the container style, margin-aligned."""
    cols = shutil.get_terminal_size((44, 24)).columns
    box_w = max(34, min(cols - 4, 46))
    inner_w = box_w - 4
    div = "─" * (box_w - 2)
    lines = cmd.strip().splitlines()

    out = f"\n  \033[38;5;238m╭{div}╮\033[0m\n"
    out += f"  \033[38;5;238m│\033[0m \033[1;36m⬡ [Galam.AI Action]\033[0m \033[38;5;244m({mode})\033[0m\n"
    out += f"  \033[38;5;238m│\033[0m \033[38;5;244mReason:\033[0m  \033[1;37m{reason}\033[0m\n"
    out += f"  \033[38;5;238m│\033[0m \033[38;5;244mCommand:\033[0m\n"
    for cl in lines:
        disp_cl = cl[:inner_w] if len(cl) > inner_w else cl
        out += f"  \033[38;5;238m│\033[0m   \033[1;33m{disp_cl}\033[0m\n"
    out += f"  \033[38;5;238m╰{div}╯\033[0m\n"
    sys.stdout.write(out)
    sys.stdout.flush()

def display_review_card(cmd: str, reason: str = "Manual Approval Required"):
    """Displays the Action Review card above the dock, margin-aligned."""
    cols = shutil.get_terminal_size((44, 24)).columns
    box_w = max(34, min(cols - 4, 46))
    inner_w = box_w - 4
    div = "─" * (box_w - 2)
    lines = cmd.strip().splitlines()

    out = f"\n  \033[38;5;214m╭{div}╮\033[0m\n"
    out += f"  \033[38;5;214m│\033[0m \033[1;33m⬡ [Galam.AI Action: Review]\033[0m\n"
    out += f"  \033[38;5;214m│\033[0m \033[38;5;244mReason:\033[0m  \033[1;37m{reason}\033[0m\n"
    out += f"  \033[38;5;214m│\033[0m \033[38;5;244mCommand:\033[0m\n"
    for cl in lines:
        disp_cl = cl[:inner_w] if len(cl) > inner_w else cl
        out += f"  \033[38;5;214m│\033[0m   \033[1;36m{disp_cl}\033[0m\n"
    out += f"  \033[38;5;214m├{div}┤\033[0m\n"
    out += f"  \033[38;5;214m│\033[0m \033[1;37mProceed with execution?\033[0m \033[1;33m[Y/n]\033[0m \033[38;5;244m· [Esc] to cancel\033[0m\n"
    out += f"  \033[38;5;214m╰{div}╯\033[0m\n"
    sys.stdout.write(out)
    sys.stdout.flush()

def request_user_confirmation(prompt_text: str = "", default: str = "y", prompt_label: str = "Confirm [Y/n]> ") -> str:
    """
    Safely requests user confirmation ([Y/n]) through Galam's stagnant prompt dock.
    Guarantees zero stdin deadlocks between worker threads and PromptSession.
    """
    if prompt_text:
        sys.stdout.write(prompt_text)
        sys.stdout.flush()

    if AGENT_STATE.get("app_ref") or threading.current_thread() != threading.main_thread():
        AGENT_STATE["review_prompt"] = prompt_label
        AGENT_STATE["review_pending"] = True
        AGENT_STATE["review_decision"] = None
        evt = threading.Event()
        AGENT_STATE["review_event"] = evt
        if AGENT_STATE.get("app_ref"):
            try:
                AGENT_STATE["app_ref"].invalidate()
            except Exception:
                pass

        evt.wait()
        decision = str(AGENT_STATE.get("review_decision", default)).strip().lower()
        AGENT_STATE["review_pending"] = False
        AGENT_STATE["review_event"] = None
        AGENT_STATE["review_prompt"] = None
        if AGENT_STATE.get("app_ref"):
            try:
                AGENT_STATE["app_ref"].invalidate()
            except Exception:
                pass
        return decision if decision else default
    else:
        try:
            val = input().strip().lower()
            return val if val else default
        except Exception:
            return default

def render_output_card(output_text: str, title: str = "Output", max_lines: int = 14):
    """Renders a clean, transparent container displaying command or tool execution output, margin-aligned."""
    cols = shutil.get_terminal_size((44, 24)).columns
    box_w = max(34, min(cols - 4, 48))
    inner_w = box_w - 4
    div = "─" * (box_w - 2)

    clean = (output_text or "").strip()
    if not clean:
        sys.stdout.write(f"\n  \033[38;5;120m✓ Tool executed successfully (exit status 0, no output)\033[0m\n\n")
        sys.stdout.flush()
        return

    lines = []
    for line in clean.splitlines():
        # Wrap visible text only: splitting escape sequences corrupts colors on narrow terminals.
        plain = re.sub(r'\x1b\[[0-9;]*m', '', line)
        codes = re.findall(r'\x1b\[[0-9;]*m', line)
        color = ("\033[38;5;203m" if plain.lstrip().startswith("- ") else
                 "\033[38;5;120m" if plain.lstrip().startswith("+ ") else
                 codes[0] if codes else "")
        for part in (textwrap.wrap(plain, width=inner_w, replace_whitespace=False,
                                   drop_whitespace=False) or [""]):
            lines.append(color + part + ("\033[0m" if color else ""))
    total_lines = len(lines)
    disp_lines = lines if max_lines is None else lines[:max_lines]

    header = f"╭─ [{title}] "
    if len(header) < box_w - 1:
        header += "─" * (box_w - len(header) - 1) + "╮"
    else:
        header = f"╭{div}╮"

    out = f"\n  \033[38;5;242m{header}\033[0m\n"
    for l in disp_lines:
        disp_l = l
        clean_strip = re.sub(r'\x1b\[[0-9;]*m', '', disp_l).strip()
        if clean_strip.startswith("- L") or clean_strip.startswith("- "):
            line_str = f"\033[38;5;203m{disp_l}\033[0m"
        elif clean_strip.startswith("+ L") or clean_strip.startswith("+ "):
            line_str = f"\033[38;5;120m{disp_l}\033[0m"
        elif "\033[" in disp_l:
            line_str = disp_l
        else:
            line_str = f"\033[38;5;253m{disp_l}\033[0m"
        out += f"  \033[38;5;242m│\033[0m {line_str}\n"

    if max_lines is not None and total_lines > max_lines:
        truncated_count = total_lines - max_lines
        out += f"  \033[38;5;242m│\033[0m \033[38;5;245m... (+{truncated_count} more lines) ...\033[0m\n"

    out += f"  \033[38;5;242m╰{div}╯\033[0m\n"
    sys.stdout.write(out)
    sys.stdout.flush()

def get_interactive_input():
    try:
        AGENT_STATE["app_ref"] = prompt_session.app
        with patch_stdout(raw=True):
            user_text = prompt_session.prompt(
                get_prompt_text,
                bottom_toolbar=get_bottom_toolbar_tokens
            )
            return user_text.strip()
    except (KeyboardInterrupt, EOFError):
        return "/exit"

class StructuredThoughtStreamHandler:
    def __init__(self, model_tag=""):
        self.model_tag = model_tag[:20]
        AGENT_STATE["is_thinking"] = True
        AGENT_STATE["current_thought"] = "Analyzing query..."
        AGENT_STATE["last_reasoning"] = ""
        self.live_thought_started = False
        self.last_topic = ""
        self.recent_tokens = ""
        if AGENT_STATE.get("app_ref"):
            try:
                AGENT_STATE["app_ref"].invalidate()
            except Exception:
                pass

    def show_spinner(self, msg="Analyzing query..."):
        clean_msg = msg.strip()
        if clean_msg:
            AGENT_STATE["current_thought"] = clean_msg[:32]
        if AGENT_STATE.get("app_ref"):
            try:
                AGENT_STATE["app_ref"].invalidate()
            except Exception:
                pass

    def update_thought(self, token: str):
        self.recent_tokens += token
        AGENT_STATE["last_reasoning"] = AGENT_STATE.get("last_reasoning", "") + token

        # Extract clean 1-liner topic summary if topic changes
        topic_triggers = ["looking", "checking", "inspecting", "analyzing", "searching", "locating", "verifying", "terminating", "running", "editing", "modifying"]
        lower_recent = self.recent_tokens.lower()
        for trig in topic_triggers:
            if trig in lower_recent:
                # Find phrase starting with trig
                idx = lower_recent.rfind(trig)
                phrase = self.recent_tokens[idx:idx + 32].replace("\n", " ").strip()
                if phrase and phrase != self.last_topic:
                    self.last_topic = phrase
                    AGENT_STATE["current_thought"] = f"{phrase.capitalize()}"
                    if AGENT_STATE.get("app_ref"):
                        try:
                            AGENT_STATE["app_ref"].invalidate()
                        except Exception:
                            pass
                break

        # If user explicitly expanded thoughts via Ctrl+O, stream them in real time
        if AGENT_STATE.get("show_thoughts", False):
            if not self.live_thought_started:
                sys.stdout.write("\n  \033[38;5;141m╭─ [Live Thought Trace (Ctrl+O to minimize)]\033[0m\n")
                self.live_thought_started = True
            sys.stdout.write(f"  \033[38;5;244m{token}\033[0m")
            sys.stdout.flush()

    def clear_on_confident_answer(self):
        if self.live_thought_started:
            sys.stdout.write("\n  \033[38;5;141m╰──────────────────────────────────────────\033[0m\n\n")
            sys.stdout.flush()
            self.live_thought_started = False

        AGENT_STATE["is_thinking"] = False
        AGENT_STATE["current_thought"] = ""
        if AGENT_STATE.get("app_ref"):
            try:
                AGENT_STATE["app_ref"].invalidate()
            except Exception:
                pass

    def clear(self):
        self.clear_on_confident_answer()

ThoughtStreamHandler = StructuredThoughtStreamHandler

