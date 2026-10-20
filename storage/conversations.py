"""Conversation-owned history and plan references; no global history search."""
import json
from pathlib import Path
import re
import os
import sys

OWNER = '_galam_conversation_id'
PLAN = '_galam_plan_path'


def history_directory():
    configured = os.environ.get('GALAM_CONVERSATIONS_DIR')
    if configured:
        return str(Path(configured).expanduser())
    home = Path.home()/'.galam_conversations'
    # Termux launched through a proot shell may expose /root as HOME. Prefer
    # its existing chat store when this alternate HOME has no conversations.
    legacy = Path('/data/data/com.termux/files/home/.galam_conversations')
    if (str(sys.executable).startswith('/data/data/com.termux/') and
            not any(home.glob('*.json')) and legacy.is_dir() and any(legacy.glob('*.json'))):
        return str(legacy)
    return str(home)


def scoped_messages(messages, conversation_id=None):
    """Select this chat before summarizing, budgeting, or wiring tool results.

    Legacy untagged rows belong to their explicitly loaded conversation. Ad-hoc
    side requests with no ownership tags remain independent, as before.
    """
    rows = [m for m in messages if isinstance(m, dict)]
    if conversation_id is None:
        humans = [m for m in rows if m.get('role') == 'user'
                  and not m.get('_galam_internal') and not m.get('_galam_action') and m.get(OWNER)]
        tagged = humans or [m for m in rows if m.get(OWNER)]
        conversation_id = tagged[-1][OWNER] if tagged else None
    if conversation_id is None:
        return rows
    return [m for m in rows if m.get(OWNER) in (None, str(conversation_id))]


def bind_messages(messages, conversation_id):
    """Attach ownership at the task/file boundary; exclude foreign tagged rows."""
    messages[:] = scoped_messages(messages, str(conversation_id))
    for row in messages:
        row[OWNER] = str(conversation_id)
    return messages


def conversation_path(directory, conversation_id):
    name = str(conversation_id)
    if not name or name in ('.', '..') or any(c in name for c in ('/', '\\', '\x00')):
        raise ValueError('Conversation ID must be a single file name')
    root = Path(directory).resolve()
    path = root / (name + '.json')
    if path.is_symlink():
        raise ValueError('Conversation history must not be a symlink')
    return path


def load_history(directory, conversation_id):
    path = conversation_path(directory, conversation_id)
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return []
    return bind_messages(data, conversation_id) if isinstance(data, list) else []


def save_history(directory, conversation_id, messages):
    from engine.result_store import atomic_bytes
    path = conversation_path(directory, conversation_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    bind_messages(messages, conversation_id)
    atomic_bytes(path, json.dumps(messages, indent=2).encode('utf-8'))


def recent_conversations(directory):
    """Local picker index; never merge contents from different conversations."""
    from engine.context_manager import is_user_request
    from engine.transparency import safe_display
    root = Path(directory)
    if not root.is_dir():
        return []
    result = []
    for path in root.glob('*.json'):
        if path.is_symlink() or not path.is_file():
            continue
        try:
            messages = load_history(directory, path.stem)
            if not messages:
                continue
            users = [m for m in messages if is_user_request(m)]
            visible = [m for m in messages if is_user_request(m) or
                       (m.get('role') == 'assistant' and m.get('content') and not m.get('_galam_tool_calls'))]
            result.append({'id':path.stem, 'updated':path.stat().st_mtime,
                'title':safe_display(str(users[0].get('content','Untitled'))).replace('\n',' ') if users else 'Untitled',
                'turns':len(users), 'preview':'\n'.join(
                    ('You: ' if m.get('role')=='user' else 'Galam: ') + safe_display(str(m.get('content','')))[:600]
                    for m in visible[-4:])})
        except (OSError, ValueError, TypeError):
            continue
    return sorted(result, key=lambda row: row['updated'], reverse=True)


def delete_history(directory, conversation_id):
    """Permanently delete only the selected regular history file."""
    path = conversation_path(directory, conversation_id)
    if path.is_file():
        path.unlink()
        return True
    return False


def conversation_plan(messages, conversation_id, artifacts_directory):
    """Use only a plan referenced by this chat, including old saved chat rows."""
    root = Path(artifacts_directory).resolve()
    for row in reversed(scoped_messages(messages, conversation_id)):
        if row.get('role') != 'assistant':
            continue
        reference = row.get(PLAN)
        if not reference and str(row.get('content', '')).startswith('Created an implementation plan for '):
            match = re.search(r'Saved to (.+\.md)\.?\s*$', str(row['content']))
            reference = match.group(1) if match else None
        if not reference:
            continue
        try:
            path = Path(reference).resolve()
        except (OSError, ValueError, TypeError):
            continue
        if path.parent == root and path.name.startswith('plan_') and path.suffix == '.md':
            # Do not silently fall back to an older plan when the latest was removed.
            return path if path.is_file() else None
    return None
