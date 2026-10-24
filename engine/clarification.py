"""Ask through the live dock while a worker performs safe local discovery."""
import threading
import uuid
import shlex
import textwrap
from engine.action_results import ActionResult


class Questions:
    def __init__(self):
        self.lock = threading.RLock()
        self.active = None

    @staticmethod
    def invalidate():
        from config import AGENT_STATE
        app = AGENT_STATE.get('app_ref')
        if app:
            try:
                app.invalidate()
            except Exception:
                pass

    def open(self, question, choices):
        from config import AGENT_STATE
        from engine.transparency import safe_display
        from engine.security import redact
        with self.lock:
            if self.active:
                raise ValueError('A clarification question is already open.')
            row = {'id': uuid.uuid4().hex[:12], 'question': redact(safe_display(question))[:1000],
                   'choices': [redact(safe_display(item)) for item in choices.splitlines() if item.strip()][:10],
                   'status': 'pending', 'answer': '', 'path': '', 'event': threading.Event()}
            self.active = row
            AGENT_STATE['clarification'] = {key: value for key, value in row.items() if key != 'event'}
        self.invalidate()
        return row

    def finish(self, row, status, answer='', path=''):
        from config import AGENT_STATE
        with self.lock:
            if self.active is not row or row['status'] != 'pending':
                return False
            row.update(status=status, answer=answer, path=path)
            self.active = None
            AGENT_STATE['clarification'] = None
            AGENT_STATE['last_clarification'] = {key: value for key, value in row.items() if key != 'event'}
            row['event'].set()
        self.invalidate()
        return True

    def submit(self, text, question_id=None):
        with self.lock:
            row = self.active
            if not row:
                return 'expired' if question_id else 'unhandled'
            if question_id and question_id != row['id']:
                return 'expired'
            answer = str(text).strip()
            if not answer:
                return 'answered'  # Blank submission leaves the question pending.
            if answer.isdecimal() and 1 <= int(answer) <= len(row['choices']):
                answer = row['choices'][int(answer) - 1]
            self.finish(row, 'answered', answer=answer)
            return 'answered'


QUESTIONS = Questions()


def ask_user(question, choices='', target='', kind='directory'):
    from config import AGENT_STATE
    from engine.cancellation import check
    from engine.path_discovery import candidates
    from engine.task_runner import session_directory, execute_shell_action
    from engine.transparency import progress
    if not question.strip():
        raise ValueError('A clarification question must not be empty.')
    if kind not in ('directory', 'file'):
        raise ValueError('kind must be directory or file.')
    if AGENT_STATE.get('in_btw_mode') and threading.current_thread().name.startswith('galam-btw'):
        return ActionResult('Ask this clarification in the main task.', False, None, status='needs_input')
    row = QUESTIONS.open(question, choices)
    try:
        progress('Exploring local paths while awaiting your answer' if target else 'Waiting for your clarification')
        # Runs in the action worker; the main input loop remains free to answer.
        paths = candidates(target, session_directory(), kind) if target else []
        check()
        if len(paths) == 1 and row['status'] == 'pending':
            # Verify with an actual quoted read command, through normal execution
            # and redaction. A discovered name alone is never an execution grant.
            command = 'ls -ld -- ' + shlex.quote(paths[0])
            probe = execute_shell_action(command)
            if probe.success:
                row['discovery_command'] = command
                row['discovery_action_id'] = probe.action_id
                QUESTIONS.finish(row, 'resolved', path=paths[0])
        if row['status'] == 'pending' and not AGENT_STATE.get('clarification_input_enabled'):
            QUESTIONS.finish(row, 'needs_input')
        progress('Waiting for your clarification' if row['status'] == 'pending' else 'Clarification resolved')
        while not row['event'].wait(.05):
            check()
        check()
        if row['status'] == 'resolved':
            return ActionResult(f'Located one exact {kind}: {row["path"]}.\nVerified with: {row["discovery_command"]}\nThe unanswered question was withdrawn.',
                                verified=True, metadata={'path': row['path'], 'question_id': row['id'], 'resolution': 'discovered',
                                                         'discovery_action_id': row['discovery_action_id']})
        if row['status'] == 'answered':
            return ActionResult('User clarification (data for the current task): ' + row['answer'],
                                metadata={'answer': row['answer'], 'question_id': row['id'], 'resolution': 'answered'})
        return ActionResult(question, True, None, status='needs_input', metadata={'question_id': row['id']})
    finally:
        QUESTIONS.finish(row, 'cancelled')


def question_tokens(columns):
    from config import AGENT_STATE
    row = AGENT_STATE.get('clarification')
    if not row or row.get('status') != 'pending':
        return []
    if AGENT_STATE.get('in_btw_mode'):
        return [('fg:#71717a', ' Main task awaits clarification; exit /btw to answer.\n')]
    width = max(12, columns - 4)
    tokens = [('fg:#38bdf8 bold', ' ◇ Galam Action · Ask\n')]
    for line in textwrap.wrap(row['question'], width=width):
        tokens.append(('fg:#f1f5f9', ' ' + line + '\n'))
    for index, choice in enumerate(row['choices'], 1):
        for line in textwrap.wrap(f'{index}. {choice}', width=width):
            tokens.append(('fg:#94a3b8', ' ' + line + '\n'))
    for line in textwrap.wrap('Reply below · local discovery may resolve it · Esc twice cancels', width=width):
        tokens.append(('fg:#71717a', ' ' + line + '\n'))
    return tokens


def track_answer_draft(buffer):
    from config import AGENT_STATE
    row = AGENT_STATE.get('clarification')
    if not buffer.text.strip():
        AGENT_STATE.pop('clarification_draft_id', None)
        return
    if row and buffer.text.strip() and not AGENT_STATE.get('in_btw_mode'):
        AGENT_STATE['clarification_draft_id'] = row['id']
