"""Preserve model-provided output separately from factual execution progress."""
import functools
import re
import threading
import json
import time

_LOCK = threading.RLock()


def text_value(value):
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return json.dumps(value)
    if isinstance(value, list):
        return "".join(text_value(item) for item in value)
    if isinstance(value, dict):
        return text_value(value.get("text") or value.get("thinking") or value.get("reasoning") or value.get("content") or value.get("value") or "")
    return ""


def safe_display(text):
    text = re.sub(r"\x1b\][^\x07]*(?:\x07|\x1b\\)", "", str(text))
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
    return "".join(c for c in text if c in "\n\t" or c.isprintable())


def concise(text, limit=140):
    text = safe_display(text)
    lines = [line.strip() for line in re.split(r"[\r\n]+|(?<=[.!?])\s+", text) if line.strip()]
    value = lines[-1] if lines else ""
    if len(value) < 20 and len(lines) > 1:
        value = lines[-2]
    value = re.sub(r"\s+", " ", value)
    return value if len(value) <= limit else value[:limit - 1].rstrip() + "…"


def _state():
    from config import AGENT_STATE
    return AGENT_STATE


def progress(message):
    state = _state()
    from engine.activity import phase_activity
    with _LOCK:
        rows = state.setdefault("task_progress", [])
        if not rows or rows[-1] != message:
            rows.append(str(message))
        state['activity_label'],state["current_thought"] = phase_activity(message)
    app = state.get("app_ref")
    if app:
        try:
            app.invalidate()
        except Exception:
            pass  # A closed display must not interrupt model/tool processing.


def task_lifecycle(function):
    @functools.wraps(function)
    def wrapped(*args, **kwargs):
        state = _state()
        from engine.usage import start_task
        from engine.cancellation import begin_task
        cancel_event = begin_task()
        started_at = time.monotonic()
        messages=kwargs.get('messages',args[0] if args and isinstance(args[0],list) else None)
        previous_assistants={id(message) for message in messages or [] if message.get('role')=='assistant'}
        failed=False
        start_task(kwargs.get('conversation_id',args[1] if len(args)>1 else None))
        with _LOCK:
            state.update(reasoning_turns=[], task_progress=[], last_reasoning="", task_active=True,
                         active_thought_handler=None, is_thinking=True, thought_scroll=0,task_outcome='completed')
        progress("Preparing the request")
        try:
            return function(*args, **kwargs)
        except BaseException:
            failed=True
            raise
        finally:
            from engine.usage import totals
            from engine.response_stats import attach,footer
            cancelled=cancel_event.is_set() or state.get('abort',False)
            status='cancelled' if cancelled else 'failed' if failed else state.get('task_outcome','completed')
            from engine.usage import ledger
            workflow = ledger().get('_workflow')
            if workflow:
                workflow.finish(status)
            ledger().pop('_before_request', None)
            ledger().pop('_workflow', None)
            stats={'elapsed':max(0,time.monotonic()-started_at),'finished_at':time.time(),
                   'cancelled':bool(cancelled),'status':status,'usage':totals()}
            state['last_task_timing']=stats
            state.setdefault('task_progress',[]).append({'completed':'Task finished','failed':'Task stopped after an error',
                'incomplete':'Task stopped before completion','cancelled':'Task cancelled'}.get(status,'Task ended'))
            state.update(task_active=False, is_thinking=False, is_streaming=False,
                         active_thought_handler=None, current_thought="", escape_armed_at=None)
            if state.get('cancel_event') is cancel_event:
                state['cancel_event'] = None
            from engine.cancellation import end_task
            end_task()
            if messages is not None:
                attach(messages,stats,previous_assistants)
                print('\n'+footer(stats)+'\n')
            if state.get("app_ref"):
                try:
                    state["app_ref"].invalidate()
                except Exception:
                    pass
    return wrapped


def begin_generation(model):
    state = _state()
    with _LOCK:
        row = {"model": str(model), "reasoning": "", "phase": "Connecting to model"}
        state.setdefault("reasoning_turns", []).append(row)
    return row


def append_reasoning(row, value, update_summary=True):
    state = _state()
    with _LOCK:
        row["reasoning"] += value
        state["last_reasoning"] = "\n\n".join(r["reasoning"] for r in state.get("reasoning_turns", []) if r["reasoning"])
        if update_summary:
            from engine.activity import reasoning_activity
            state['activity_label'],state["current_thought"] = reasoning_activity(row['reasoning'])


def trace_text():
    state = _state()
    with _LOCK:
        parts = []
        for index, row in enumerate(state.get("reasoning_turns", []), 1):
            body = row["reasoning"] or "No reasoning supplied by this model; execution progress is shown below."
            parts.append(f"Request {index} · {row['model']}\n{body}")
        if state.get("task_progress"):
            parts.append("Execution progress\n" + "\n".join("• " + p for p in state["task_progress"]))
        return "\n\n".join(parts) or "No model reasoning or execution progress is available yet."


class ReasoningStream:
    """Split think tags across arbitrary chunks, never interpret tags inside file/tool text."""
    OPEN = ("<think>", "<thought>", "<reasoning>")
    CLOSE = ("</think>", "</thought>", "</reasoning>")

    def __init__(self):
        self.pending = ""
        self.in_reasoning = False
        self.can_open = True

    def feed(self, content="", reasoning="", final=False):
        self.pending += text_value(content)
        answer, thoughts = [], [text_value(reasoning)]
        while self.pending:
            if self.in_reasoning:
                lower = self.pending.lower()
                matches = [(lower.find(tag), tag) for tag in self.CLOSE if tag in lower]
                if matches:
                    index, tag = min(matches)
                    thoughts.append(self.pending[:index])
                    self.pending = self.pending[index + len(tag):]
                    self.in_reasoning = False
                    continue
                keep = 0 if final else max((n for tag in self.CLOSE for n in range(1, len(tag))
                                             if lower.endswith(tag[:n])), default=0)
                thoughts.append(self.pending[:-keep] if keep else self.pending)
                self.pending = self.pending[-keep:] if keep else ""
                break
            if self.can_open:
                if self.pending[0].isspace():
                    answer.append(self.pending[0]);self.pending = self.pending[1:]
                    continue
                lower = self.pending.lower()
                tag = next((tag for tag in self.OPEN if lower.startswith(tag)), None)
                if tag:
                    self.pending = self.pending[len(tag):]
                    self.in_reasoning = True
                    continue
                if not final and any(tag.startswith(lower) for tag in self.OPEN):
                    break
                self.can_open = False
            answer.append(self.pending);self.pending = ""
        return "".join(answer), "".join(thoughts)
