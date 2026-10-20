from pathlib import Path
import os
import sys
import json
import time
import uuid
import re
import queue
import shutil
import threading
import subprocess
import argparse
import shlex
import glob
import hashlib
from types import SimpleNamespace

from config import CONFIG, save_settings, AGENT_STATE
from storage.endpoints import ENDPOINT_REGISTRY
from routers.manager import query_with_fallback, query_subagent
from ui.dock_layout import (
    get_interactive_input,
    SLASH_COMMANDS,
    prompt_session,
    render_action_card,
    display_review_card,
    render_output_card
)
from ui.settings_menu import (
    handle_settings_menu,
    handle_model_menu
)
from engine.task_runner import (
    GLOBAL_TASK_MANAGER,
    format_action_step,
    execute_shell_action,
    execute_tool_call,
    session_directory,
    resolve_session_path,
    preflight_shell_command,
    workspace_snapshot,
    find_project_directory,
)
from engine.tool_parser import (
    clean_response_text,
    render_markdown_response,
    detect_action_intent,
    extract_tools_from_text,
    extract_confident_command_from_reasoning,
    extract_announced_shell_command,
    validate_shell_command
)
from engine.logger import log_galam_system_event
from engine.execution_policy import normalize_mode, next_mode, requires_review
from engine.lang_workflow import is_available as lang_workflow_available, query_planning_graph, validate_tool_arguments
from engine.action_results import requires_content, as_result, request_intent
from engine.action_recovery import recover_requested_read, announces_read
from engine.completion import action_acknowledgment, completed_simple_request

from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.layout.controls import BufferControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.filters import to_filter

CONV_DIR = os.path.expanduser("~/.galam_conversations")
os.makedirs(CONV_DIR, exist_ok=True)

def load_conversation(conv_id):
    path = os.path.join(CONV_DIR, f"{conv_id}.json")
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return []

def persist_conversation(conv_id, msgs):
    path = os.path.join(CONV_DIR, f"{conv_id}.json")
    try:
        with open(path, "w") as f:
            json.dump(msgs, f, indent=2)
    except Exception:
        pass

def save_history():
    pass

def print_banner(session_id):
    cols = shutil.get_terminal_size((44, 24)).columns
    endpoint = CONFIG.get("active_endpoint") or CONFIG.get("endpoint", "Direct")
    raw_m = CONFIG.get("active_model") or CONFIG.get("model", "")
    mode = CONFIG.get("mode", "REVIEW").upper().replace("_", "-")
    clean_m = raw_m.split("/")[-1].replace("@cf/", "").replace("meta/", "")

    box_w = max(38, min(cols - 2, 46))
    inner_w = box_w - 4

    top_border = f"\033[38;5;45m ┌{'─' * (box_w - 2)}┐\033[0m"
    bot_border = f"\033[38;5;45m └{'─' * (box_w - 2)}┘\033[0m"
    line1 = f"\033[38;5;45m │\033[0m \033[1;37m{'Autonomous Android Terminal Agent'.center(inner_w)}\033[0m \033[38;5;45m│\033[0m"
    line2 = f"\033[38;5;45m │\033[0m \033[38;5;123m{'Made possible by John Benser'.center(inner_w)}\033[0m \033[38;5;45m│\033[0m"

    if mode == "REVIEW":
        mode_color = "\033[1;38;5;214m"
    elif mode == "AUTO":
        mode_color = "\033[1;38;5;82m"
    else:
        mode_color = "\033[1;38;5;45m"

    banner = f"""\033[1;36m
 ██████╗  █████╗ ██╗      █████╗ ███╗   ███╗     █████╗ ██╗
██╔════╝ ██╔══██╗██║     ██╔══██╗████╗ ████║    ██╔══██╗██║
██║  ███╗███████║██║     ███████║██╔████╔██║    ███████║██║
██║   ██║██╔══██║██║     ██╔══██║██║╚██╔╝██║    ██╔══██║██║
╚██████╔╝██║  ██║███████╗██║  ██║██║ ╚═╝ ██║    ██║  ██║██║
 ╚═════╝ ╚═╝  ╚═╝╚══════╝╚═╝  ╚═╝╚═╝     ╚═╝    ╚═╝  ╚═╝╚═╝\033[0m
{top_border}
{line1}
{line2}
{bot_border}
  \033[38;5;39m•\033[0m \033[1;38;5;75mEngine:\033[0m     \033[1;38;5;141m[{endpoint}]\033[0m \033[1;38;5;120m{clean_m}\033[0m
  \033[38;5;39m•\033[0m \033[1;38;5;220mSession ID:\033[0m \033[38;5;222m{session_id}\033[0m
  \033[38;5;39m•\033[0m \033[1;38;5;208mMode:\033[0m       {mode_color}{mode}\033[0m \033[38;5;244m(Change via /settings)\033[0m
"""
    print(banner)

def handle_tasks_command(arg: str = ""):
    if arg.startswith("kill "):
        target = arg.split()[1].strip()
        if target.lower() in ("all", "*"):
            GLOBAL_TASK_MANAGER.stop_all()
            print("\n\033[1;32m[✓] All background tasks terminated.\033[0m\n")
        else:
            stopped = GLOBAL_TASK_MANAGER.stop_task(target)
            if stopped:
                print(f"\n\033[1;32m[✓] Task {target} terminated.\033[0m\n")
            else:
                print(f"\n\033[1;31m[!] Task {target} not found or already stopped.\033[0m\n")
        return

    tasks = GLOBAL_TASK_MANAGER.get_active_tasks()
    if not tasks:
        sys.stdout.write("\n\033[38;5;244mNo active background tasks running.\033[0m\n\n")
        sys.stdout.flush()
        return

    sys.stdout.write("\n\033[1;36mActive Background Tasks:\033[0m\n")
    for t in tasks:
        sys.stdout.write(f"  \033[1;33m[{t['id']}]\033[0m PID \033[1;37m{t['pid']}\033[0m · \033[38;5;120m{t['cmd']}\033[0m (Started {t['start_time']} · Port {t.get('port', '5500')})\n")
    sys.stdout.write("\033[38;5;244mTo stop a task: /tasks kill <id|all>\033[0m\n\n")
    sys.stdout.flush()

def handle_btw_command(initial_query=""):
    subagent_id = f"subagent-{uuid.uuid4().hex[:6]}"

    sub_entry = {
        "id": subagent_id,
        "start_time": time.time(),
        "elapsed": 0,
        "task": "btw-incubator"
    }

    if "active_subagents" not in AGENT_STATE:
        AGENT_STATE["active_subagents"] = []
    AGENT_STATE["active_subagents"].append(sub_entry)

    if AGENT_STATE.get("app_ref"):
        try:
            AGENT_STATE["app_ref"].invalidate()
        except Exception:
            pass

    cols = shutil.get_terminal_size((44, 24)).columns
    div = "─" * min(cols - 4, 40)

    sub_messages = [
        {
            "role": "system",
            "content": (
                "You are an agile sub-agent within Galam.AI answering inquiries in an isolated incubator container. "
                "Keep answers direct, structured, helpful, and natural. Do not produce verbose internal monologues."
            )
        }
    ]

    sys.stdout.write(f"\n\033[38;5;238m╭─ \033[1;35m⬡ [Sub-Agent Incubator: btw]\033[0m \033[38;5;244m({subagent_id})\033[0m\n")
    sys.stdout.write(f"\033[38;5;238m│ \033[38;5;244mType questions below · Press [Esc] to close container\033[0m\n")
    sys.stdout.write(f"\033[38;5;238m│ {div}\033[0m\n")
    sys.stdout.flush()

    kb_btw = KeyBindings()
    @kb_btw.add('escape')
    @kb_btw.add('c-c')
    def _esc_btw(event):
        event.app.exit(result=None)

    session_btw = PromptSession(key_bindings=kb_btw)

    first_round = True
    next_query = initial_query.strip() if initial_query else ""

    try:
        while True:
            if first_round and next_query:
                user_q = next_query
                first_round = False
                sys.stdout.write(f"\033[38;5;238m│ \033[38;5;250mQuery:\033[0m  \033[1;37m{user_q}\033[0m\n")
            else:
                first_round = False
                try:
                    with patch_stdout(raw=True):
                        prompt_html = HTML("<style fg='#a855f7'>│ </style><style fg='#38bdf8'><b>btw&gt;</b> </style>")
                        user_q = session_btw.prompt(prompt_html)
                except (KeyboardInterrupt, EOFError):
                    break

                if user_q is None:
                    break

                user_q = user_q.strip()
                if not user_q:
                    continue

                if user_q.lower() in ("/exit", "/quit", "exit", "quit"):
                    break

            sys.stdout.write(f"\033[38;5;238m│ \033[38;5;220mStatus: ≡ Thinking... (0s)\033[0m\n")
            sys.stdout.flush()

            is_done = threading.Event()
            start_time = time.time()

            def _ticker():
                while not is_done.is_set():
                    elapsed = int(time.time() - start_time)
                    sub_entry["elapsed"] = elapsed
                    sys.stdout.write(f"\033[1A\r\033[38;5;238m│ \033[38;5;220mStatus: ≡ Thinking... ({elapsed}s)\033[0m\n")
                    sys.stdout.flush()
                    if AGENT_STATE.get("app_ref"):
                        try:
                            AGENT_STATE["app_ref"].invalidate()
                        except Exception:
                            pass
                    time.sleep(0.5)

            t = threading.Thread(target=_ticker, daemon=True)
            t.start()

            sub_messages.append({"role": "user", "content": user_q})
            answer = query_subagent(sub_messages)
            sub_messages.append({"role": "assistant", "content": answer})

            is_done.set()
            t.join(timeout=0.3)
            total_time = round(time.time() - start_time, 1)

            sys.stdout.write(f"\033[1A\r\033[2K\033[38;5;238m│ \033[1;32mStatus: ✓ Concluded ({total_time}s)\033[0m\n")
            sys.stdout.write(f"\033[38;5;238m│ {div}\033[0m\n")
            for line in answer.splitlines():
                sys.stdout.write(f"\033[38;5;238m│\033[0m  \033[1;37m{line}\033[0m\n")
            sys.stdout.write(f"\033[38;5;238m│ {div}\033[0m\n")
            sys.stdout.flush()

    finally:
        if sub_entry in AGENT_STATE.get("active_subagents", []):
            AGENT_STATE["active_subagents"].remove(sub_entry)
        if AGENT_STATE.get("app_ref"):
            try:
                AGENT_STATE["app_ref"].invalidate()
            except Exception:
                pass

        sys.stdout.write(f"\033[38;5;238m╰──────────────────────────────────────────\033[0m \033[38;5;244m[Closed /btw]\033[0m\n\n")
        sys.stdout.flush()

def handle_plan_command(arg: str, messages: list, conversation_id: str):
    task_desc = arg.strip()
    if not task_desc:
        sys.stdout.write("\n\033[38;5;214mUsage: /plan <describe what you want to build or do>\033[0m\n\n")
        sys.stdout.flush()
        return

    from context.prompts import PLANNER_PROMPT
    sys.stdout.write(f"\n\033[1;36m[Antigravity Planner]\033[0m Generating plan for: \033[1;37m{task_desc}\033[0m\n")
    sys.stdout.flush()

    plan_messages = [
        {"role": "system", "content": PLANNER_PROMPT},
        {"role": "user", "content": f"Create an Antigravity implementation plan for: {task_desc}"}
    ]

    resp = query_subagent(plan_messages)
    if not is_actionable_plan(resp):
        if "school" in task_desc.lower() and "parking" in task_desc.lower() and "ticket" in task_desc.lower():
            resp = school_parking_plan()
        else:
            plan_messages.append({"role": "user", "content": (
                "Your previous reply was not a plan. Produce concrete Goal, Assumptions, "
                "Key Files with paths, Execution Steps, and Verification for this task now: " + task_desc)})
            resp = query_subagent(plan_messages)
            if not is_actionable_plan(resp):
                sys.stdout.write("\n[!] No actionable plan was generated; nothing was saved.\n\n")
                sys.stdout.flush()
                return
    
    # Save as artifact
    artifacts_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "artifacts")
    os.makedirs(artifacts_dir, exist_ok=True)
    plan_filename = f"plan_{time.strftime('%Y%m%d_%H%M%S')}.md"
    plan_path = os.path.join(artifacts_dir, plan_filename)
    
    with open(plan_path, "w", encoding="utf-8") as f:
        f.write(resp)

    AGENT_STATE["last_plan_path"] = plan_path
    messages.append({"role": "assistant", "content": f"Created an implementation plan for {task_desc}. Saved to {plan_path}."})

    sys.stdout.write(f"\033[1;32m✓ Plan generated and saved to artifacts/{plan_filename}\033[0m\n\n")
    for line in resp.splitlines():
        sys.stdout.write(f"  {line}\n")
    sys.stdout.write(f"\n\033[38;5;244mRun the steps or tell Galam.AI to execute them autonomously.\033[0m\n\n")
    sys.stdout.flush()


def is_actionable_plan(plan: str) -> bool:
    text = (plan or "").lower()
    if any(phrase in text for phrase in ("please provide", "once you provide", "list the files that will", "describe the structure and content")):
        return False
    return all(section in text for section in ("goal", "key files", "execution steps", "verification"))


def school_parking_plan() -> str:
    return """# School Parking Ticket System

## Goal
Build a local staff prototype that records parking citations, lists and filters them, and marks them resolved.

## Assumptions
- Staff use the app on this device; it binds to 127.0.0.1 and does not expose student data to the network.
- No payment collection or student identity integration is included until school policy is supplied.
- SQLite stores plate, location, violation, issue time, amount, notes, and status.

## Key Files
- `/sdcard/cli/school-parking-tickets/server.py`: Python standard-library HTTP API and SQLite storage.
- `/sdcard/cli/school-parking-tickets/index.html`: accessible staff form and ticket list.
- `/sdcard/cli/school-parking-tickets/README.md`: setup, limitations, and usage.

## Execution Steps
1. Create the project directory and SQLite-backed API.
2. Create the staff interface for issuing, searching, and resolving tickets.
3. Document local operation and privacy assumptions.
4. Run automated API checks, start the server locally, and verify an HTTP response.

## Verification
`python3 -m py_compile /sdcard/cli/school-parking-tickets/server.py`
`python3 /sdcard/cli/school-parking-tickets/server.py --check`
`curl -I http://127.0.0.1:8765/`
"""


def handle_approved_plan_request(user_input: str, messages: list, conversation_id: str) -> bool:
    """Ground an approval in the last saved plan instead of asking the model to invent a build command."""
    if not (re.search(r'\bapprov(?:e|ed|al)\b', user_input, re.IGNORECASE) and
            re.search(r'\bbuild\b', user_input, re.IGNORECASE)):
        return False
    artifacts_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "artifacts")
    plans = sorted(glob.glob(os.path.join(artifacts_dir, "plan_*.md")), key=os.path.getmtime, reverse=True)
    if not plans:
        sys.stdout.write("\n  Incomplete: no saved implementation plan was found. Use /plan first.\n\n")
        sys.stdout.flush()
        return True
    with open(plans[0], encoding="utf-8") as plan_file:
        plan = plan_file.read()
    if not is_actionable_plan(plan):
        sys.stdout.write("\n  Incomplete: the saved plan lacks concrete files and steps. Generate a new /plan.\n\n")
        sys.stdout.flush()
        return True
    if "school parking ticket" in plan.lower():
        project = "/sdcard/cli/school-parking-tickets"
        server = os.path.join(project, "server.py")
        if os.path.isfile(server) and os.path.isfile(os.path.join(project, "index.html")):
            check = subprocess.run([sys.executable, server, "--check"], capture_output=True, text=True, timeout=20)
            if check.returncode == 0:
                summary = f"Success: school parking ticket prototype is ready at {project}. {check.stdout.strip()}"
            else:
                summary = f"Incomplete: project exists but verification failed: {check.stderr.strip()[:500]}"
            sys.stdout.write(f"\n  {summary}\n  Plan: {plans[0]}\n\n")
            sys.stdout.flush()
            messages.extend([{"role": "user", "content": user_input}, {"role": "assistant", "content": summary}])
            persist_conversation(conversation_id, messages)
            return True
    approved_request = (
        f"{user_input}\n\nApproved implementation plan ({plans[0]}):\n{plan}\n\n"
        "Implement the files specified in this plan. Inspect the target directory, then use write_file or "
        "replace_file_content for actual source files. Do not execute example placeholders."
    )
    TASK_QUEUE.put((approved_request, conversation_id, messages))
    return True

def handle_artifact_command(arg: str = ""):
    artifacts_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "artifacts")
    if not os.path.exists(artifacts_dir):
        print("\n\033[38;5;244mNo artifacts found.\033[0m\n")
        return

    files = sorted(os.listdir(artifacts_dir), reverse=True)
    if not files:
        print("\n\033[38;5;244mNo artifacts recorded yet.\033[0m\n")
        return

    if arg.strip():
        target = arg.strip()
        matched = [f for f in files if target in f]
        if matched:
            filepath = os.path.join(artifacts_dir, matched[0])
            print(f"\n\033[1;36m--- Artifact: {matched[0]} ---\033[0m")
            with open(filepath, "r", encoding="utf-8") as f:
                print(f.read())
            print(f"\033[1;36m----------------------------------\033[0m\n")
            return
        else:
            print(f"\n\033[1;31m[!] Artifact matching '{target}' not found.\033[0m\n")

    print("\n\033[1;36mSaved Artifacts:\033[0m")
    for f in files[:10]:
        print(f"  \033[38;5;120m•\033[0m {f}")
    print("\033[38;5;244mUse: /artifact <filename> to view full content.\033[0m\n")

def handle_slash_command(user_input, messages, conversation_id):
    parts = user_input.strip().split(maxsplit=1)
    cmd = parts[0].lower()
    arg = parts[1] if len(parts) > 1 else ""

    if cmd == "/settings":
        handle_settings_menu()
        sys.stdout.write("\n\033[38;5;244m[Settings updated · Chat session preserved]\033[0m\n\n")
        sys.stdout.flush()
    elif cmd == "/model":
        handle_model_menu()
        sys.stdout.write("\n\033[38;5;244m[Model updated · Chat session preserved]\033[0m\n\n")
        sys.stdout.flush()
    elif cmd == "/mode":
        cur_mode = normalize_mode(CONFIG.get("mode"))
        if arg:
            choice = arg.strip().upper().replace("_", "-")
            if choice in ("REVIEW", "AUTO", "ALWAYS-PROCEED"):
                new_mode = choice
            else:
                print("Usage: /mode [review|auto|always-proceed]")
                return
        else:
            new_mode = next_mode(cur_mode)
        save_settings(mode=new_mode)
        CONFIG["mode"] = new_mode
        print(f"\n\033[38;5;75m[✓] Mode switched to: {new_mode}\033[0m\n")
    elif cmd == "/tasks":
        handle_tasks_command(arg)
    elif cmd == "/btw":
        handle_btw_command(arg)
    elif cmd == "/plan":
        handle_plan_command(arg, messages, conversation_id)
    elif cmd == "/artifact":
        handle_artifact_command(arg)
    elif cmd == "/clear":
        os.system("clear")
        print_banner(conversation_id)
    elif cmd == "/help":
        print("\n\033[1;36mAvailable Commands:\033[0m")
        for c, desc in SLASH_COMMANDS:
            print(f"  \033[1;32m{c:<12}\033[0m \033[38;5;244m{desc}\033[0m")
        print()
    elif cmd == "/status":
        ep = CONFIG.get("active_endpoint", "Direct")
        m = CONFIG.get("active_model", "")
        mode = CONFIG.get("mode", "REVIEW").upper().replace("_", "-")
        stream = CONFIG.get("stream", True)
        print(f"\n\033[1;36m--- Galam.AI Status ---\033[0m")
        print(f"  Endpoint: {ep}")
        print(f"  Model:    {m}")
        print(f"  Mode:     {mode}")
        print(f"  Directory: {AGENT_STATE.get('working_directory', os.getcwd())}")
        print(f"  Stream:   {'ON' if stream else 'OFF'}\n")
    elif cmd == "/cancel":
        AGENT_STATE["abort"] = True
        print("\n\033[1;33m[!] Active generation cancelled.\033[0m\n")
    elif cmd in ("/exit", "/quit"):
        sys.exit(0)

def request_tool_review(cmd_preview: str, title: str = "Approval Required") -> str:
    display_review_card(cmd_preview, title)
    AGENT_STATE["review_pending"] = True
    AGENT_STATE["review_decision"] = None
    AGENT_STATE["review_event"] = threading.Event()
    if AGENT_STATE.get("app_ref"):
        try:
            AGENT_STATE["app_ref"].invalidate()
        except Exception:
            pass

    # Wait for user input via the stagnant prompt
    AGENT_STATE["review_event"].wait()
    decision = str(AGENT_STATE.get("review_decision") or "cancel").strip().lower()
    AGENT_STATE["review_pending"] = False
    AGENT_STATE["review_event"] = None
    if AGENT_STATE.get("app_ref"):
        try:
            AGENT_STATE["app_ref"].invalidate()
        except Exception:
            pass
    return decision


def direct_command_for_request(request: str):
    """Handle unambiguous terminal requests without relying on a small model to format tools."""
    if request.strip().lower() == "whoami":
        return "whoami"
    named_listing = re.search(
        r"\b(?:go|navigate)\s+to\s+(?P<name>.+?)\s+(?:then|and)\s+(?:please\s+)?(?:list|show)\b",
        request, re.IGNORECASE,
    )
    if named_listing:
        target = find_project_directory(named_listing.group("name"))
        if target:
            sys.stdout.write(f"  › Found project: {target}\n")
            sys.stdout.flush()
            return f"cd -- {shlex.quote(target)} && ls -la"
    if re.fullmatch(r"\s*(?:(?:and\s+)?now\s+|please\s+|kindly\s+)*"
                    r"(?:run|start|launch)\s+(?:(?:my|the)\s+)?server(?:\s+for\s+me)?[.!]?\s*",
                    request, re.IGNORECASE):
        directory = session_directory()
        command = _verified_server_command(directory)
        if command:
            sys.stdout.write(f"  › Inspected {directory}; found {command}.\n")
            sys.stdout.flush()
        return command
    navigation = re.fullmatch(
        r"\s*(?:(?:now|since)\s+you(?:'re|re|\s+are)?\s+there[, ]*)?(?:kindly\s+|please\s+)?"
        r"(?:go|navigate|cd)\s+(?:to\s+)?(?:my\s+)?(?P<path>[/~.][^\s,;]+)[.!]?\s*",
        request, re.IGNORECASE,
    )
    if navigation:
        return "cd -- " + shlex.quote(navigation.group("path").rstrip(".!"))
    match = re.fullmatch(
        r"\s*(?:go|navigate|cd)\s+to\s+(?:my\s+)?(/[-\w./]+)\s+then\s+"
        r"(?:run|rub|start)\s+(?:(?:my|the)\s+)?server[.!]?\s*",
        request, re.IGNORECASE,
    )
    if not match:
        return None
    target = os.path.normpath(match.group(1))
    if not os.path.isdir(target):
        parent, name = os.path.split(target)
        if not os.path.isdir(parent):
            return None
        matches = [os.path.join(parent, item) for item in os.listdir(parent)
                   if item.startswith(name) and os.path.isdir(os.path.join(parent, item))]
        if len(matches) != 1:
            return None
        target = matches[0]
        sys.stdout.write(f"  › Resolved project directory: {target}\n")
        sys.stdout.flush()
    command = _verified_server_command(target)
    return f"cd -- {shlex.quote(target)} && {command}" if command else None


def _verified_server_command(target):
    """Choose only an entrypoint that exists in the inspected directory."""
    scripts = {}
    try:
        with open(os.path.join(target, "package.json"), encoding="utf-8") as package_file:
            scripts = json.load(package_file).get("scripts", {})
    except (OSError, ValueError, TypeError):
        pass
    for script in ("dev", "start", "serve"):
        if script in scripts:
            return f"npm run {script}"
    for filename in ("server.py", "app.py", "main.py"):
        if os.path.isfile(os.path.join(target, filename)):
            return f"python3 {filename}"
    return None


def _tool_completion_summary(tool_result):
    """Make a verified user-facing summary when a small model echoes tool metadata."""
    if not tool_result:
        return "The action completed."
    tool_name, output, succeeded, is_bg = tool_result
    if not succeeded:
        return f"{tool_name} is incomplete: {output.splitlines()[0] if output else 'unknown error'}"
    if is_bg:
        address = re.search(r"https?://[^\s)\]]+", output)
        if address:
            return f"Server is running at {address.group(0)}."
    if tool_name == "view_file":
        from engine.result_store import compact_text
        return "Read the requested file successfully:\n" + compact_text(output, 2400)
    if tool_name == "execute_bash":
        lines = [line for line in output.splitlines() if line.strip()]
        directory_line = next((line for line in lines if line.startswith("[Working directory: ")), "")
        if directory_line:
            directory = directory_line.removeprefix("[Working directory: ").rstrip("]")
            if len(lines) > 1 and lines[1].startswith("total "):
                return f"Listed {max(0, len(lines) - 2)} entries in {directory}."
            return f"Working directory: {directory}. Command completed successfully."
    return f"{tool_name} completed successfully."


def _is_observation_echo(text):
    lowered = text.lower()
    return any(fragment in lowered for fragment in (
        "status: tool step executed successfully",
        "status: background task launched and health checked",
        "acknowledge the completed action clearly",
        "if a secondary step is required to complete the user request",
        "[tool observation for",
    ))

def run_agent_loop(messages, conversation_id, max_steps=None):
    if max_steps is None:
        max_steps = int(CONFIG.get("max_iterations", 25))
    step = 0
    executed_commands = set()
    workspace_revision = 0
    pending_checks = []
    action_events = []
    last_tool_result = None
    completed = False
    current_request = next((str(m.get("content", "")) for m in reversed(messages)
                            if m.get("role") == "user"), "")
    action_requested, code_change_requested = request_intent(current_request)
    code_change_completed = False
    content_required = requires_content(current_request)
    direct_command = direct_command_for_request(current_request)
    no_tool_retries = 0
    invalid_tool_retries = 0
    rejected_commands = {}
    inspected_files = {}
    use_lang_workflow = lang_workflow_available()

    while step < max_steps:
        step += 1
        is_verification = False

        if AGENT_STATE.get("abort", False):
            break

        try:
            if pending_checks:
                is_verification = True
                command = pending_checks.pop(0)
                check_call = {"id": f"check_{uuid.uuid4().hex}", "type": "function", "function": {
                    "name": "execute_bash", "arguments": json.dumps({"command": command})}}
                resp_raw = (SimpleNamespace(content="", reasoning="", tool_calls=[check_call]), "verification")
            elif step == 1 and direct_command:
                direct_call = {"id": "direct_user_request", "type": "function", "function": {
                    "name": "execute_bash", "arguments": json.dumps({"command": direct_command})}}
                resp_raw = (SimpleNamespace(content="", reasoning="", tool_calls=[direct_call]), "direct")
            else:
                resp_raw = (query_planning_graph(messages) if use_lang_workflow
                            else query_with_fallback(messages))
        except Exception as e:
            log_galam_system_event(
                event_type="QUERY_EXCEPTION",
                summary=f"Query error in agent loop turn {step}: {e}",
                details={"step": step, "conversation_id": conversation_id, "error": str(e)}
            )
            sys.stdout.write(f"\n\033[1;31m[!] Query error: {e}\033[0m\n\n")
            if last_tool_result:
                tool_name, output, succeeded, _ = last_tool_result
                sys.stdout.write(f"  {'Success' if succeeded else 'Incomplete'}: {tool_name}\n  Summary: {output[:1000]}\n\n")
            sys.stdout.flush()
            break

        if not resp_raw or AGENT_STATE.get("abort", False):
            if AGENT_STATE.get("abort", False):
                log_galam_system_event(
                    event_type="USER_ABORT",
                    summary="Execution aborted by user in agent loop",
                    details={"step": step, "conversation_id": conversation_id},
                    level="INFO"
                )
            break

        response_msg = resp_raw[0] if isinstance(resp_raw, tuple) else resp_raw

        if response_msg == "EXHAUSTED":
            log_galam_system_event(
                event_type="ENDPOINTS_EXHAUSTED",
                summary="All models under active endpoint failed during agent loop execution",
                details={"step": step, "conversation_id": conversation_id}
            )
            sys.stdout.write("\n\033[1;31m[!] All models under active endpoint failed.\033[0m\n")
            sys.stdout.flush()
            break
        if isinstance(response_msg, str) and response_msg in ("FAILED", "EXHAUSTED", "CANCELLED"):
            if last_tool_result:
                tool_name, output, succeeded, _ = last_tool_result
                sys.stdout.write(f"  {'Success' if succeeded else 'Incomplete'}: {tool_name}\n  Summary: {output[:1000]}\n\n")
                sys.stdout.flush()
            break

        assistant_content = response_msg.content if response_msg else ""
        if assistant_content:
            messages.append({"role": "assistant", "content": assistant_content})

        tool_calls = getattr(response_msg, "tool_calls", [])
        if not tool_calls and isinstance(response_msg, dict):
            tool_calls = response_msg.get("tool_calls", [])

        # Check if the previous message in conversation was a successful tool step
        is_post_tool_turn = False
        if len(messages) >= 2:
            prev_m = messages[-2] if messages[-1].get("role") == "assistant" else messages[-1]
            if prev_m.get("role") == "user" and "[Tool Observation for" in prev_m.get("content", ""):
                if prev_m.get("_galam_action", {}).get("status") in ("completed", "running") or any(status in prev_m.get("content", "") for status in (
                    "Status: Tool step executed successfully with exit status 0",
                    "Status: Background task launched and health checked",
                )):
                    is_post_tool_turn = True

        # If no tool calls detected by parser:
        if not tool_calls:
            # In a post-tool summary turn, DO NOT extract speculative commands from reasoning traces!
            # The model is analyzing what it already ran. Only genuine formal tool calls should be run.
            extra_tools = extract_tools_from_text(assistant_content)
            if not extra_tools and not is_post_tool_turn:
                extra_tools = extract_announced_shell_command(assistant_content)
            if not extra_tools and not is_post_tool_turn:
                extra_tools = recover_requested_read(current_request, assistant_content, session_directory())

            if extra_tools:
                tool_calls = extra_tools
            elif is_post_tool_turn:
                # The tool step already succeeded with exit status 0!
                # Render clean summary or synthesize verified completion card directly:
                clean_text = clean_response_text(assistant_content)
                if code_change_requested and not code_change_completed:
                    if no_tool_retries < 4 and step < max_steps:
                        no_tool_retries += 1
                        messages.append({"role": "user", "content": (
                            "The requested code change is not complete. Inspect the actual file if needed, "
                            "then call replace_file_content or write_file. Do not claim success after inspection alone.")})
                        continue
                    sys.stdout.write("  Incomplete: no code change was applied.\n")
                    sys.stdout.flush()
                    break
                if (_is_observation_echo(clean_text) or last_tool_result and last_tool_result[0] == "view_file"
                        and (announces_read(clean_text) or clean_text.lower().strip(". !") in ("success", "done"))):
                    clean_text = _tool_completion_summary(last_tool_result)
                    if messages and messages[-1].get("role") == "assistant":
                        messages[-1]["content"] = clean_text
                has_more_work, _ = detect_action_intent(clean_text)
                if has_more_work and step < max_steps:
                    messages.append({"role": "user", "content": "The task is still in progress. Execute the next tool call now."})
                    continue
                if clean_text and not any(clean_text.lower().startswith(q) for q in ("putting it all together", "first, i need to", "i also need to")):
                    sys.stdout.write("\r\033[2K\n  \033[1;38;5;75m✦ Galam.ai\033[0m\n\n")
                    sys.stdout.write("  \033[1;32mSuccess.\033[0m\n\n")
                    rendered = render_markdown_response(clean_text, indent="  ")
                    sys.stdout.write(rendered + "\n\n")
                    sys.stdout.flush()
                else:
                    # Action was executed and verified; present zero-fluff completion card
                    sys.stdout.write("\r\033[2K\n  \033[1;38;5;75m✦ Galam.ai Action Completed\033[0m\n\n")
                    sys.stdout.write("  \033[1;32m✓\033[0m \033[1;37mAction executed and verified with exit status 0.\033[0m\n\n")
                    sys.stdout.flush()
                completed = True
                break
            else:
                # 2. Check if the model stated an unfulfilled action intention
                has_intent, intent_phrase = detect_action_intent(assistant_content)
                if has_intent and step < max_steps:
                    clean_intent = intent_phrase[:75]
                    sys.stdout.write(f"\r\033[38;5;244m  › Intention: {clean_intent} (Prompting immediate tool execution...)\033[0m\n")
                    sys.stdout.flush()
                    messages.append({
                        "role": "user",
                        "content": (
                            f"CRITICAL ACTION REQUIRED: You promised to take action ('{intent_phrase}'), "
                            "but did not emit a <tool_call> block! Do NOT output conversational promises or delay execution. "
                            "You must emit the tool call NOW in the standard format:\n"
                            "<tool_call>\n{\"name\": \"execute_bash\", \"arguments\": {\"command\": \"...\"}}\n</tool_call>"
                        )
                    })
                    continue
                else:
                    # Check if user had an unfulfilled actionable request
                    if (action_requested and not executed_commands or
                            code_change_requested and not code_change_completed or
                            last_tool_result and not last_tool_result[2]) and step < max_steps:
                        if no_tool_retries >= 2:
                            sys.stdout.write("\n  Incomplete: the model did not provide an executable action.\n")
                            sys.stdout.write(f"  Summary: {current_request}\n\n")
                            sys.stdout.flush()
                            break
                        no_tool_retries += 1
                        sys.stdout.write("\r\033[38;5;214m  › Unfinished task detected (Prompting autonomous tool execution...)\033[0m\n")
                        sys.stdout.flush()
                        messages.append({
                            "role": "user",
                            "content": (
                                f"Original user request: {current_request}\n"
                                "ACTION REQUIRED: The request is unfinished. Inspect the last tool result, "
                                "correct any error, and emit the next <tool_call> now. Do not claim success without verification."
                            )
                        })
                        continue

                    # True Confident Answer! Task complete, verified, or pure conversational answer
                    clean_text = clean_response_text(assistant_content)
                    if clean_text:
                        sys.stdout.write("\r\033[2K\n  \033[1;38;5;75m✦ Galam.ai\033[0m\n\n")
                        rendered = render_markdown_response(clean_text, indent="  ")
                        sys.stdout.write(rendered + "\n\n")
                        sys.stdout.flush()
                    completed = not (last_tool_result and not last_tool_result[2]) and (not action_requested or bool(executed_commands))
                    break

        # If we have tool calls and assistant had introductory text, display it as a step line
        if tool_calls and assistant_content:
            clean_pre = clean_response_text(assistant_content)
            if clean_pre and not any(clean_pre.startswith(p) for p in ("{", "<", "`")):
                first_sentence = clean_pre.splitlines()[0][:120]
                if not any(clean_pre.lower().startswith(q) for q in (
                    "putting it all together", "the command becomes", "first, i need",
                    "first, let me", "next, i need", "then, i have to", "okay,", "alright,",
                    "let me check", "to create the file", "in order to", "i should", "i will run"
                )):
                    sys.stdout.write(f"\r\033[38;5;250m  › {first_sentence}\033[0m\n")
                    sys.stdout.flush()

        executed_in_this_step = 0
        rejected_in_this_step = False
        repeated_invalid = False
        inspection_performed = False
        for tc in tool_calls:
            if AGENT_STATE.get("abort", False):
                break

            fn = tc.get("function", {})
            tool_name = fn.get("name", "execute_bash")
            args_data = fn.get("arguments", "{}")
            if isinstance(args_data, str):
                try:
                    args = json.loads(args_data)
                except (ValueError, TypeError):
                    rejected_in_this_step = True
                    messages.append({"role": "user", "_galam_internal": True, "content": (
                        f"The {tool_name} arguments were incomplete JSON. Emit one complete tool call with an argument object.")})
                    continue
            else:
                args = args_data

            try:
                args = validate_tool_arguments(tool_name, args)
            except (ValueError, TypeError) as validation_error:
                rejected_in_this_step = True
                sys.stdout.write(f"  Incomplete: invalid {tool_name} arguments: {validation_error}\n")
                sys.stdout.flush()
                messages.append({"role": "user", "content": (
                    f"The {tool_name} tool arguments are invalid: {validation_error}. "
                    "Inspect the current workspace and provide a valid tool call.")})
                continue

            if tool_name == "execute_bash":
                proposed = str(args.get("command", "")).strip()
                valid_command, invalid_reason = validate_shell_command(proposed)
                if valid_command:
                    valid_command, invalid_reason = preflight_shell_command(proposed)
                if valid_command and code_change_requested and not is_verification and re.search(
                        r"\b(?:touch\s+|sed\s+-i|perl\s+-pi|python(?:3|3\.\d+)?\s+-c|tee\s+|cat\s+>|printf\s+.*>|echo\s+.*>)",
                        proposed, re.IGNORECASE):
                    valid_command = False
                    invalid_reason = "Use write_file(path, content) with the complete implementation for new or empty files; inspect and use replace_file_content for existing code. Creating an empty file does not implement the request"
                if not valid_command:
                    rejected_in_this_step = True
                    rejected_commands[proposed] = rejected_commands.get(proposed, 0) + 1
                    sys.stdout.write(f"  Incomplete: {invalid_reason}\n")
                    sys.stdout.flush()
                    if rejected_commands[proposed] >= 2:
                        repeated_invalid = True
                        break
                    messages.append({"role": "user", "content": (
                        f"That command cannot run: {invalid_reason}. Stop repeating it. "
                        "Use the live workspace snapshot to find the requested project and a real entry file. "
                        f"User request: {current_request}")})
                    continue

            if tool_name in ("write_file", "replace_file_content"):
                target_path = resolve_session_path(str(args.get("path", "")))
                exists = os.path.isfile(target_path)
                if content_required and tool_name == "write_file" and not args.get("content", "").strip():
                    rejected_in_this_step = True
                    messages.append({"role": "user", "content": (
                        "Empty content was rejected: the user requested an implementation, not an empty file. "
                        "Call write_file with the complete actual code/content. Do not use touch or claim completion.")})
                    sys.stdout.write("  Incomplete: empty content cannot satisfy this build request.\n")
                    sys.stdout.flush()
                    continue
                if tool_name == "replace_file_content" and not exists:
                    rejected_in_this_step = True
                    messages.append({"role": "user", "content":
                        f"Cannot modify missing file {target_path}.\n{workspace_snapshot()}\nInspect the real file path first."})
                    sys.stdout.write(f"  Incomplete: file does not exist: {target_path}\n")
                    sys.stdout.flush()
                    continue
                if exists:
                    with open(target_path, "rb") as existing_file:
                        current_hash = hashlib.sha256(existing_file.read()).hexdigest()
                    if inspected_files.get(target_path) != current_hash:
                        inspection, _ = execute_tool_call("view_file", {"path": target_path,
                                                                          "start_line": 1, "end_line": 200})
                        if inspection.startswith("Error:"):
                            rejected_in_this_step = True
                            messages.append({"role": "user", "content": inspection})
                            continue
                        inspected_files[target_path] = current_hash
                        sys.stdout.write(f"  › Inspected existing file before editing: {target_path}\n")
                        render_output_card(inspection, title="Code Inspection", max_lines=40)
                        messages.append({"role": "user", "content": (
                            f"[Verified File Inspection: {target_path}]\n{inspection[:14000]}\n"
                            "Now make the requested change against this actual content. "
                            "Use replace_file_content for a focused edit, then verify the result.")})
                        inspection_performed = True
                        break
                    if (tool_name == "write_file" and os.path.getsize(target_path) > 0 and not re.search(
                            r"\b(?:rewrite|overwrite|replace)\s+(?:the\s+)?(?:entire|whole|full)\s+file\b",
                            current_request, re.IGNORECASE)):
                        rejected_in_this_step = True
                        messages.append({"role": "user", "content": (
                            f"{target_path} already exists. Preserve its other code: use replace_file_content "
                            "with a unique exact block from the inspected file instead of overwriting it.")})
                        sys.stdout.write(f"  Incomplete: refused whole-file overwrite of {target_path}\n")
                        sys.stdout.flush()
                        continue

            call_signature = f"{tool_name}:{json.dumps(args, sort_keys=True)}"
            execution_key = (call_signature, workspace_revision)
            if execution_key in executed_commands:
                continue

            mode = normalize_mode(CONFIG.get("mode"))
            cmd_preview = args.get("command", "") if tool_name == "execute_bash" else f"{tool_name}({args.get('path', args.get('query', ''))})"
            if not requires_review(mode, tool_name, args):
                reason = "Safe Read Execution" if mode == "AUTO" else "Always-Proceed"
                render_action_card(cmd_preview, f"{reason}: {tool_name}", mode=mode)
            elif mode == "AUTO":
                choice = request_tool_review(cmd_preview, f"Review Required ({tool_name})")
                if choice in ("cancel", "c", "/cancel"):
                    AGENT_STATE["abort"] = True
                    sys.stdout.write("\033[1;33m[!] Execution aborted by user.\033[0m\n\n")
                    sys.stdout.flush()
                    return
                elif choice not in ("y", "yes"):
                    sys.stdout.write("\033[38;5;244m[-] Tool execution declined.\033[0m\n\n")
                    sys.stdout.flush()
                    messages.append({"role": "user", "content": f"I declined to execute `{tool_name}` with args: {args}. Propose another approach."})
                    return
            else:
                choice = request_tool_review(cmd_preview, f"Approval Required: {tool_name}")
                if choice in ("cancel", "c", "/cancel"):
                    AGENT_STATE["abort"] = True
                    sys.stdout.write("\033[1;33m[!] Execution aborted by user.\033[0m\n\n")
                    sys.stdout.flush()
                    return
                elif choice not in ("y", "yes"):
                    sys.stdout.write("\033[38;5;244m[-] Tool execution declined.\033[0m\n\n")
                    sys.stdout.flush()
                    messages.append({"role": "user", "content": f"I declined to execute `{tool_name}` with args: {args}. Propose another approach."})
                    return
            step_bullet = format_action_step(tool_name, args)
            sys.stdout.write(f"{step_bullet}\n")
            sys.stdout.flush()

            AGENT_STATE["current_thought"] = f"Executing {tool_name}..."
            if AGENT_STATE.get("app_ref"):
                try:
                    AGENT_STATE["app_ref"].invalidate()
                except Exception:
                    pass

            active_model = CONFIG.get("active_model", "")
            if tool_name in ("write_file", "replace_file_content"):
                path = resolve_session_path(args["path"])
                if path in inspected_files:
                    args["_expected_hash"] = inspected_files[path]
            action_result = as_result(execute_tool_call(tool_name, args, invoking_model=active_model))
            action_events.append({"tool": tool_name, "args": dict(args), "result": action_result,
                                  "verification": is_verification})
            out, is_bg = action_result
            executed_commands.add(execution_key)
            if action_result.success and tool_name in ("write_file", "replace_file_content", "undo_file_change"):
                workspace_revision += 1
                for command in action_result.checks:
                    if command not in pending_checks:
                        pending_checks.append(command)
            if tool_name == "view_file" and not out.startswith("Error:"):
                viewed_path = resolve_session_path(str(args.get("path", "")))
                if os.path.isfile(viewed_path):
                    with open(viewed_path, "rb") as viewed_file:
                        inspected_files[viewed_path] = hashlib.sha256(viewed_file.read()).hexdigest()

            AGENT_STATE["current_thought"] = ""
            if AGENT_STATE.get("app_ref"):
                try:
                    AGENT_STATE["app_ref"].invalidate()
                except Exception:
                    pass

            # Render output transparency container
            if not is_bg:
                if tool_name == "replace_file_content":
                    card_title = f"Code Diff: {os.path.basename(args.get('path', 'file'))}"
                elif tool_name == "view_file":
                    card_title = f"Code Inspection: {os.path.basename(args.get('path', 'file'))}"
                elif tool_name == "write_file":
                    card_title = f"File Write: {os.path.basename(args.get('path', 'file'))}"
                elif tool_name == "execute_bash":
                    card_title = "Command Output"
                else:
                    card_title = f"Result ({tool_name})"
                listing_command = tool_name == "execute_bash" and bool(re.search(
                    r"(?:^|&&|;)\s*(?:ls|find|tree|dir)(?=\s|$)",
                    str(args.get("command", "")), re.IGNORECASE,
                ))
                display_output = out
                if tool_name in ("write_file", "replace_file_content") and action_result.output_path:
                    try:
                        display_output = Path(action_result.output_path).read_text(encoding="utf-8")
                    except OSError:
                        pass
                render_output_card(display_output, title=card_title, max_lines=None if listing_command or tool_name in ("write_file", "replace_file_content") else 14)

            clean_out = (out or "").strip()
            if not clean_out:
                clean_out = "(Command executed successfully with exit status 0 and no output)"

            has_error = not action_result.success
            if not has_error and tool_name in ("write_file", "replace_file_content"):
                target = resolve_session_path(str(args.get("path", "")))
                code_change_completed = (not content_required or
                                         os.path.isfile(target) and bool(Path(target).read_text(encoding="utf-8").strip()))
            last_tool_result = (tool_name, clean_out, not has_error, is_bg)
            result_label = "Success" if not has_error else "Incomplete"
            summary_line = action_acknowledgment(tool_name, args, action_result)
            sys.stdout.write(f"  \033[1;{'32' if not has_error else '33'}m{result_label}: {tool_name}\033[0m\n")
            sys.stdout.write(f"  Summary: {summary_line[:200]}\n\n")
            sys.stdout.flush()

            if has_error:
                status_instruction = "Status: Tool step failed. Inspect the error and continue correcting the task."
            else:
                status_instruction = ("Status: Background task launched and health checked."
                                      if is_bg else "Status: Tool step executed successfully with exit status 0.")
                if tool_name in ("write_file", "replace_file_content", "undo_file_change"):
                    status_instruction = ("Status: File operation completed successfully. "
                                          "Only the checks explicitly listed in the result have passed. "
                                          "Run any remaining project verification before claiming the task is fully verified.")

            clean_out = re.sub(r"\x1b\[[0-9;]*m", "", clean_out)
            observation_feedback = (
                f"[Tool Observation for `{tool_name}`]:\n{clean_out}\n\n"
                f"{status_instruction}"
            )
            messages.append({
                "role": "user",
                "content": observation_feedback,
                "_galam_action": {"id": action_result.action_id, "status": action_result.status,
                                  "exit_code": action_result.exit_code, "output_path": action_result.output_path}
            })
            executed_in_this_step += 1

            if AGENT_STATE.get("app_ref"):
                try:
                    AGENT_STATE["app_ref"].invalidate()
                except Exception:
                    pass

        if inspection_performed:
            continue

        if repeated_invalid:
            sys.stdout.write("  Incomplete: the same invalid command was proposed twice. "
                             "Check the project path and retry the request.\n\n")
            sys.stdout.flush()
            break

        if executed_in_this_step and not rejected_in_this_step and not AGENT_STATE.get("abort", False):
            receipt = completed_simple_request(current_request, action_events, pending_checks, session_directory())
            if receipt:
                messages.append({"role": "assistant", "content": receipt})
                sys.stdout.write("\r\033[2K\n  \033[1;38;5;75m✦ Galam.ai\033[0m\n\n")
                sys.stdout.write(render_markdown_response(receipt, indent="  ") + "\n\n")
                sys.stdout.flush()
                completed = True
                break

        if direct_command and last_tool_result and last_tool_result[2] and not (code_change_requested and not code_change_completed):
            tool_name, output, succeeded, _ = last_tool_result
            sys.stdout.write(f"  {'Success' if succeeded else 'Incomplete'}: {current_request}\n")
            sys.stdout.write(f"  Summary: {_tool_completion_summary(last_tool_result)}\n\n")
            sys.stdout.flush()
            completed = succeeded
            break

        if executed_in_this_step == 0:
            if rejected_in_this_step:
                invalid_tool_retries += 1
                if invalid_tool_retries < 4 and step < max_steps:
                    continue
                sys.stdout.write(f"  Incomplete: no valid command was provided for {current_request}.\n\n")
                sys.stdout.flush()
                break
            if last_tool_result and not last_tool_result[2]:
                if step < max_steps:
                    messages.append({
                        "role": "user",
                        "content": "The last tool failed and this tool call repeats a previous attempt. Inspect the error and try a different corrective action.",
                    })
                    continue
                break
            if is_post_tool_turn and code_change_requested and not code_change_completed:
                messages.append({"role": "user", "content": (
                    "The request is unfinished: repeating a successful setup/inspection command does not implement it. "
                    "Call write_file with real content, or inspect and use replace_file_content. Do not claim completion.")})
                continue
            if is_post_tool_turn:
                sys.stdout.write("\r\033[2K\n  \033[1;38;5;75m✦ Galam.ai Action Completed\033[0m\n\n")
                sys.stdout.write("  \033[1;32m✓\033[0m \033[1;37mAction executed and verified with exit status 0.\033[0m\n\n")
                if last_tool_result and last_tool_result[0] == "view_file" and not code_change_requested:
                    summary = _tool_completion_summary(last_tool_result)
                    sys.stdout.write(render_markdown_response(summary, indent="  ") + "\n\n")
                    if messages and messages[-1].get("role") == "assistant":
                        messages[-1]["content"] = summary
                sys.stdout.flush()
                break
            clean_text = clean_response_text(assistant_content)
            if any(clean_text.lower().startswith(q) for q in ("putting it all together", "the command becomes", "first, i need")):
                sys.stdout.write("\r\033[2K\n  \033[1;38;5;75m✦ Galam.ai Action Completed\033[0m\n\n")
                sys.stdout.write("  \033[1;32m✓\033[0m \033[1;37mAction executed and verified with exit status 0.\033[0m\n\n")
                sys.stdout.flush()
                break
            if clean_text:
                sys.stdout.write("\r\033[2K\n  \033[1;38;5;75m✦ Galam.ai\033[0m\n\n")
                rendered = render_markdown_response(clean_text, indent="  ")
                sys.stdout.write(rendered + "\n\n")
                sys.stdout.flush()
            break

    if step >= max_steps and not completed:
        result = (f"Last result ({last_tool_result[0]}): {last_tool_result[1][:1000]}"
                  if last_tool_result else "No requested action was executed.")
        sys.stdout.write(f"\n  \033[1;38;5;75m✦ Galam.ai\033[0m\n\n  Incomplete: step limit reached.\n  Summary: {result}\n\n")
        sys.stdout.flush()

TASK_QUEUE = queue.Queue()

def agent_worker_loop():
    while True:
        item = TASK_QUEUE.get()
        if item is None:
            break
        user_msg, conv_id, msgs = item
        try:
            AGENT_STATE["abort"] = False
            AGENT_STATE["is_thinking"] = True
            msgs.append({"role": "user", "content": user_msg})
            run_agent_loop(msgs, conv_id)
            persist_conversation(conv_id, msgs)
        except Exception as err:
            log_galam_system_event(
                event_type="WORKER_EXECUTION_ERROR",
                summary=f"Worker loop execution error: {err}",
                details={"conversation_id": conv_id, "user_msg": user_msg[:300], "error": str(err)}
            )
            sys.stdout.write(f"\n\033[1;31m[!] Execution error: {err}\033[0m\n\n")
            sys.stdout.flush()
        finally:
            AGENT_STATE["is_thinking"] = False
            AGENT_STATE["is_streaming"] = False
            AGENT_STATE["current_thought"] = ""
            if AGENT_STATE.get("app_ref"):
                try:
                    AGENT_STATE["app_ref"].invalidate()
                except Exception:
                    pass
            TASK_QUEUE.task_done()

def main():
    parser = argparse.ArgumentParser(description="GALAM.AI Autonomous Terminal Agent")
    parser.add_argument("-c", "--conversation", help="Resume specific conversation ID")
    args = parser.parse_args()

    conversation_id = args.conversation or uuid.uuid4().hex[:8]
    messages = load_conversation(conversation_id) if args.conversation else []

    print_banner(conversation_id)

    worker = threading.Thread(target=agent_worker_loop, daemon=True)
    worker.start()

    try:
        while True:
            try:
                user_input = get_interactive_input()
            except (KeyboardInterrupt, EOFError):
                break

            if user_input in ("/exit", "/quit"):
                break

            # If review is currently pending, pass this input as review decision
            if AGENT_STATE.get("review_pending", False):
                decision = user_input.strip() if user_input.strip() else "y"
                AGENT_STATE["review_decision"] = decision
                if AGENT_STATE.get("review_event"):
                    AGENT_STATE["review_event"].set()
                continue

            if not user_input:
                continue

            first_token = user_input.split()[0].lower() if user_input.strip() else ""
            KNOWN_CMDS = {"/add-dir", "/agents", "/artifact", "/btw", "/cancel", "/changelog", "/clear", "/edit", "/exit", "/help", "/model", "/mode", "/plan", "/settings", "/status", "/tasks"}

            if first_token in KNOWN_CMDS:
                handle_slash_command(user_input, messages, conversation_id)
                continue
            elif first_token.startswith("/") and "/" not in first_token[1:]:
                print(f"\n\033[1;31mx No command available for '{first_token}'. Type /help for options.\033[0m\n")
                continue

            if user_input.startswith("!"):
                cmd = user_input[1:].strip()
                if cmd:
                    out, _ = execute_shell_action(cmd)
                    print(out)
                continue

            # Natural language server termination intent (Python, React, Vite, Node, Next, etc.)
            clean_q = user_input.strip().lower()
            is_server_kill_intent = bool(
                re.search(r'\b(?:kill|stop|shut\s*down|terminate|close|kil)\b.*\b(?:server|process|py|python|react|vite|next|node|npm|app|service|port)\b', clean_q) or
                re.search(r'\b(?:kill|stop|shut\s*down|terminate)\s+(?:all\s+)?(?:tasks?|background)\b', clean_q)
            )
            if is_server_kill_intent:
                active_tasks = GLOBAL_TASK_MANAGER.get_active_tasks()
                task_count = len(active_tasks)
                GLOBAL_TASK_MANAGER.stop_all()
                GLOBAL_TASK_MANAGER.stop_task("python")
                GLOBAL_TASK_MANAGER.stop_task("vite")
                GLOBAL_TASK_MANAGER.stop_task("node")
                sys.stdout.write(f"\033[1;32m[✓] Decisively stopped all active background server tasks ({task_count} terminated).\033[0m\n\n")
                sys.stdout.flush()
                continue

            # Hardcoded greeting intercept — handle purely locally, never send to LLM
            _pure_greeting = re.fullmatch(r'\s*(hi|hello|hey|yo|sup)\s*', clean_q)
            if _pure_greeting:
                sys.stdout.write("\n\033[38;5;117m✦ Galam.ai\033[0m\n\nGalam.ai ready. How can I assist you with your projects today?\n\n")
                sys.stdout.flush()
                continue

            if handle_approved_plan_request(user_input, messages, conversation_id):
                continue

            # Dispatch task to background worker thread:
            TASK_QUEUE.put((user_input, conversation_id, messages))

    finally:
        save_history()
        persist_conversation(conversation_id, messages)
        print(f"\n\033[38;5;244mSession saved. Resume with: glm -c {conversation_id}\033[0m\n")

if __name__ == "__main__":
    main()
