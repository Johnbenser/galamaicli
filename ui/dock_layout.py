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
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.layout.containers import ConditionalContainer, Window
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.filters import to_filter, Condition
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
CLI_OPENED_AT = time.monotonic()
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
    ("/local", "Test local server and manage owned startup"),
    ("/model", "Switch Models under Active Endpoint"),
    ("/mode", "Toggle Review / Auto / Always-Proceed mode"),
    ("/plan", "Generate implementation plan"),
    ("/queue", "Show queued prompts; /queue clear removes pending prompts"),
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
    if AGENT_STATE.get('in_btw_mode'):
        if AGENT_STATE.get('close_btw'):
            AGENT_STATE['close_btw']()
        buf.reset()
        event.app.invalidate()
        return

    if AGENT_STATE.get("is_thinking", False):
        from engine.cancellation import cancel_current
        cancel_current()
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
    if AGENT_STATE.get('in_btw_mode'):
        if AGENT_STATE.get('close_btw'):
            AGENT_STATE['close_btw']()
        else:
            AGENT_STATE['in_btw_mode'] = False
        event.current_buffer.reset()
        event.app.invalidate()
        return
    if AGENT_STATE.get('task_active') or AGENT_STATE.get('is_thinking') or AGENT_STATE.get('review_pending'):
        now = time.monotonic()
        armed = AGENT_STATE.get('escape_armed_at')
        if armed is not None and now - armed <= 1.0:
            from engine.cancellation import cancel_current
            cancel_current()
            AGENT_STATE['escape_armed_at'] = None
        else:
            AGENT_STATE['escape_armed_at'] = now
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


@kb.add('escape', 'escape')
def handle_double_escape(event):
    if AGENT_STATE.get('in_btw_mode'):
        handle_escape(event)
        return
    if AGENT_STATE.get('task_active') or AGENT_STATE.get('is_thinking') or AGENT_STATE.get('review_pending'):
        from engine.cancellation import cancel_current
        cancel_current()
        AGENT_STATE['escape_armed_at'] = None
        event.app.invalidate()
    else:
        handle_escape(event)

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
    AGENT_STATE["thought_scroll"] = 0
    event.app.invalidate()


@kb.add('c-pageup')
def thought_page_up(event):
    if AGENT_STATE.get('show_thoughts'):
        AGENT_STATE['thought_scroll'] = AGENT_STATE.get('thought_scroll', 0) + 6
        event.app.invalidate()


@kb.add('c-pagedown')
def thought_page_down(event):
    if AGENT_STATE.get('show_thoughts'):
        AGENT_STATE['thought_scroll'] = max(0, AGENT_STATE.get('thought_scroll', 0) - 6)
        event.app.invalidate()


def get_thought_panel_tokens():
    """A bounded live viewport. Hidden text never enters terminal scrollback."""
    from engine.transparency import trace_text, safe_display
    from prompt_toolkit.utils import get_cwidth
    size = prompt_session.app.output.get_size()
    width = max(8, size.columns - 3)
    height = max(1, min(12, size.rows // 3) - 1)
    lines = []
    for line in safe_display(trace_text()).expandtabs(4).split('\n'):
        part, cells = '', 0
        for char in line:
            count = get_cwidth(char)
            if cells + count > width:
                lines.append(part);part, cells = '', 0
            part += char;cells += count
        lines.append(part)
    offset = min(max(0, AGENT_STATE.get('thought_scroll', 0)), max(0, len(lines) - height))
    AGENT_STATE['thought_scroll'] = offset
    end = len(lines) - offset
    start = max(0, end - height)
    state = 'Live' if (_thinking_active() or _btw_active()) else 'Completed'
    title = f' {state} trace {start+1}–{end}/{len(lines)} · Ctrl+O hide · Ctrl+PgUp/PgDn scroll'
    return [('fg:#a78bfa', title[:width] + '\n'),
            ('fg:#a1a1aa', '\n'.join(' ' + line for line in lines[start:end]))]

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
    text = buf.text.strip()
    first = text.split(maxsplit=1)[0] if text else ''
    command = first.startswith('!') or (first.startswith('/') and '/' not in first[1:])
    review_answer = AGENT_STATE.get('review_pending') and text.lower() in ('','y','yes','n','no','cancel','c')
    enqueue = AGENT_STATE.get('enqueue_prompt')
    if (AGENT_STATE.get('task_active') or AGENT_STATE.get('active_message')) and enqueue and not command and not review_answer and not AGENT_STATE.get('in_btw_mode'):
        if text:
            enqueue(text)
            buf.reset()
        event.app.invalidate()
        return
    AGENT_STATE["slash_idx"] = 0
    buf.validate_and_handle()


def get_pending_prompt_tokens(cols):
    """Pending text is a live dock element, never an automatic console notice."""
    from engine.transparency import safe_display
    from prompt_toolkit.utils import get_cwidth
    pending = AGENT_STATE.get('queued_messages') or []
    tokens = []
    width = max(8, cols-5)
    for item in pending[:2]:
        text = re.sub(r'\s+', ' ', safe_display(item['text'])).strip()
        shown, cells = '', 0
        for char in text:
            cells += get_cwidth(char)
            if cells > width-1:
                shown += '…'
                break
            shown += char
        tokens.append(('fg:#94a3b8', f'  ↳ {shown}\n'))
    if len(pending)>2:
        tokens.append(('fg:#71717a', f'  +{len(pending)-2} more pending\n'))
    return tokens


def completed_task_text():
    timing = AGENT_STATE.get('last_task_timing')
    if not timing:
        return ''
    seconds = max(0,int(timing['elapsed']))
    minutes, seconds = divmod(seconds,60)
    label = 'Cancelled after' if timing.get('cancelled') else 'Worked for'
    clock=time.strftime('%H:%M:%S',time.localtime(timing.get('finished_at',0)))
    return f'{label} {minutes}m {seconds:02d}s • {clock}'

def _thinking_active():
    if AGENT_STATE.get('abort',False):return False
    if AGENT_STATE.get('task_active',False):return True
    handler=AGENT_STATE.get('active_thought_handler')
    return bool(AGENT_STATE.get('is_thinking') and handler and handler.row.get('phase')!='Generation ended')


def _btw_active():
    return AGENT_STATE.get('in_btw_mode') and any(
        entry.get('task')=='btw' for entry in AGENT_STATE.get('active_subagents',[]))


def shortcut_hint_visible(now=None):
    elapsed = (time.monotonic() if now is None else now) - CLI_OPENED_AT
    return elapsed >= 30 and (elapsed - 30) % 210 < 30


def _refresh_thinking_animation():
    """The animation clock is independent of network responses and token arrivals."""
    app = AGENT_STATE.get('app_ref')
    if (_thinking_active() or _btw_active()) and app and not AGENT_STATE.get('review_pending'):
        try:
            app.invalidate()
        except Exception:
            pass  # A closing display must not stop the animation thread.


def get_prompt_text():
    """Renders the thinking status directly above the stagnant dock's top divider."""
    cols = shutil.get_terminal_size((44, 24)).columns
    divider = "─" * max(10, cols - 1)
    tokens = []
    live = not prompt_session.app.is_done

    # 1. Live Thinking Header (animated loading spinner with dynamic dots)
    if (_thinking_active() or _btw_active()) and live and not AGENT_STATE.get('review_pending'):
        SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
        DOT_FRAMES = [".  ", ".. ", "...", " ..", "  .", "   "]
        t = time.time()
        spinner_char = SPINNER_FRAMES[int(t * 10) % len(SPINNER_FRAMES)]
        dots = DOT_FRAMES[int(t * 3) % len(DOT_FRAMES)]

        tokens.append(("fg:#38bdf8 bold", f" {spinner_char} "))
        activity = (AGENT_STATE.get('activity_label') or 'Thinking') if _thinking_active() else 'Thinking'
        tokens.append(("fg:#ffffff bold", f"{activity}{dots} "))

        topic = AGENT_STATE.get("current_thought", "") if _thinking_active() else 'Answering the side question.'
        clean_topic = topic.strip()
        if clean_topic:
            max_topic = max(18, cols - 4)
            shown_topic = clean_topic if len(clean_topic) <= max_topic else clean_topic[:max_topic - 1] + "…"
            tokens.append((f"fg:{THEME['status_text']}", f"\n {shown_topic}\n"))
        else:
            tokens.append(("", "\n"))

    if live:
        tokens.extend(get_pending_prompt_tokens(cols))

    # 2. Top Divider Line
    tokens.append((f"fg:{THEME['border']}", f"{divider}\n"))

    # 3. Prompt Arrow
    if AGENT_STATE.get("in_btw_mode", False):
        tokens.append((f"fg:{THEME['shortcut']} bold", "btw> "))
    elif AGENT_STATE.get("review_pending", False):
        prompt_label = AGENT_STATE.get("review_prompt") or "Action [Y/n]> "
        tokens.append(("fg:#facc15 bold", prompt_label))
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

    armed = AGENT_STATE.get('escape_armed_at')
    if AGENT_STATE.get('in_btw_mode'):
        left_status = 'esc closes /btw'
    elif armed is not None and time.monotonic() - armed <= 1:
        left_status = 'press esc again to cancel'
    elif _thinking_active():
        left_status = "esc twice to cancel"
    elif (time.time() - LAST_CTRL_C_TIME) < 3.5:
        left_status = "press ctrl+c again to exit"
    elif shortcut_hint_visible():
        thought_state = "expanded" if AGENT_STATE.get("show_thoughts", False) else "minimized"
        left_status = f"ctrl+o thoughts ({thought_state}) · /help"
    else:
        left_status = ''

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

thought_panel = ConditionalContainer(
    Window(FormattedTextControl(get_thought_panel_tokens), dont_extend_height=True),
    filter=Condition(lambda: bool(AGENT_STATE.get('show_thoughts', False)) and not prompt_session.app.is_done)
)
prompt_session.layout.container.children.insert(0, thought_panel)
prompt_session.app.min_redraw_interval = 1 / 30
prompt_session.app.ttimeoutlen = .05
prompt_session.app.timeoutlen = .25

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
        _refresh_thinking_animation()
        # The hint schedule must advance even while idle.
        if not _thinking_active() and AGENT_STATE.get('app_ref'):
            now_visible = shortcut_hint_visible()
            if now_visible != AGENT_STATE.get('_hint_visible'):
                AGENT_STATE['_hint_visible'] = now_visible
                try:
                    AGENT_STATE['app_ref'].invalidate()
                except Exception:
                    pass
        threading.Event().wait(0.08)

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
        sys.stdout.write(prompt_text.rstrip()+'\n')
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

        while not evt.wait(.05):
            if AGENT_STATE.get('abort'):
                AGENT_STATE['review_decision'] = 'cancel'
                break
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

def render_output_card(output_text: str, title: str = "Output", max_lines: int = 14, success=None):
    """Renders a clean, transparent container displaying command or tool execution output, margin-aligned."""
    cols = shutil.get_terminal_size((44, 24)).columns
    box_w = max(20, cols - 4)
    inner_w = box_w - 4
    div = "─" * (box_w - 2)

    clean = (output_text or "").strip('\n')
    if not clean.strip():
        label='Tool failed with no output' if success is False else 'Tool finished with no output' if success else 'Empty output'
        color='203' if success is False else '120' if success else '244'
        sys.stdout.write(f"\n  \033[38;5;{color}m{label}\033[0m\n\n")
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
        from ui.response_renderer import wrap_ansi
        for part in wrap_ansi(plain,inner_w,preserve_space=True):
            lines.append(color + part + ("\033[0m" if color else ""))
    total_lines = len(lines)
    disp_lines = lines if max_lines is None else lines[:max_lines]

    header = f"╭─ [{title}] "
    if len(header) < box_w - 1:
        header += "─" * (box_w - len(header) - 1) + "╮"
    else:
        header = f"╭{div}╮"

    header_color='203' if success is False else '81' if success else '242'
    out = f"\n  \033[38;5;{header_color}m{header}\033[0m\n"
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
    """Display only output actually supplied by the model, plus execution phases."""
    def __init__(self, model_tag=""):
        from engine.transparency import begin_generation
        self.model_tag = model_tag
        self.row = begin_generation(model_tag)
        self.lock = threading.RLock()
        self.live_thought_started = False
        AGENT_STATE["active_thought_handler"] = self
        AGENT_STATE["is_thinking"] = True
        self.show_spinner("Waiting for model response…")

    def _invalidate(self):
        app = AGENT_STATE.get("app_ref")
        if app:
            try:
                app.invalidate()
            except Exception:
                pass

    def _close_trace(self):
        self.live_thought_started = False

    def toggle(self):
        # Visibility belongs to the shared live layout, across handler lifetimes.
        self._invalidate()

    def show_spinner(self, msg="Receiving model output…"):
        from engine.transparency import progress, concise
        self.row["phase"] = msg.strip()
        progress(msg.strip())
        if self.row["reasoning"] and msg.startswith(('Waiting for model','Receiving model output')):
            from engine.activity import reasoning_activity
            AGENT_STATE['activity_label'],AGENT_STATE['current_thought'] = reasoning_activity(self.row['reasoning'])
        self._invalidate()

    def update_thought(self, token):
        from engine.transparency import append_reasoning, safe_display, text_value
        token = text_value(token)
        if not token:
            return
        with self.lock:
            append_reasoning(self.row, token)
        self._invalidate()

    def complete_reasoning(self, reasoning):
        from engine.transparency import append_reasoning, safe_display, concise, progress
        reasoning = str(reasoning or "")
        with self.lock:
            current = self.row["reasoning"]
            if reasoning.strip() != current.strip():
                if reasoning.startswith(current):
                    self.update_thought(reasoning[len(current):])
                else:
                    # Canonical combined output also covers JSON/non-streaming providers.
                    self.row["reasoning"] = ""
                    append_reasoning(self.row, reasoning)
                    if not current and AGENT_STATE.get("show_thoughts", False):
                        self.toggle()
            if not reasoning.strip():
                progress("Model supplied no separate reasoning output")
            else:
                from engine.activity import reasoning_activity
                AGENT_STATE['activity_label'],AGENT_STATE['current_thought'] = reasoning_activity(reasoning)
        self._invalidate()

    def clear_on_confident_answer(self):
        with self.lock:
            self._close_trace()
            self.row['phase']='Generation ended'
        # The agent may still be inspecting/reviewing/executing after generation ends.
        AGENT_STATE["is_thinking"] = AGENT_STATE.get("task_active", False)
        if not AGENT_STATE["is_thinking"]:
            AGENT_STATE["current_thought"] = ""
        self._invalidate()

    def clear(self):
        self.clear_on_confident_answer()

ThoughtStreamHandler = StructuredThoughtStreamHandler

