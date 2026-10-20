"""Small request instructions and disposable prompt-only action previews."""
import re
from engine.result_store import compact_text


def identity(endpoint, model, custom, action, directory, snapshot='', tasks=()):
    text = (f"You are Galam.ai, created by John Benser, a coding and terminal assistant on Android/Termux. "
            f"The user is John Benser. Endpoint: {endpoint}; model: {model}. "
            "Answer questions directly using this conversation's context.\n")
    if custom:
        text += 'CUSTOM MODEL INSTRUCTIONS:\n' + custom + '\nEND CUSTOM MODEL INSTRUCTIONS\n'
    if action or snapshot:
        text += f'CURRENT WORKING DIRECTORY: {directory}\n'
    if action:
        text += ('Resolve relative paths here; cd persists. For navigation do not create missing directories. '
                 'Inspect real paths and project entrypoints before starting servers.\n')
    if snapshot:
        text += 'LIVE WORKSPACE SNAPSHOT:\n' + snapshot + '\n'
    if tasks:
        text += 'ACTIVE BACKGROUND TASKS:\n' + '\n'.join(
            f"ID {t['id']}; PID {t['pid']}; port {t.get('port','5500')}; {compact_text(t['cmd'],180)}"
            + (f"; log: {t['log_path']}" if t.get('log_path') else '')
            for t in tasks) + '\n'
    return text


def observation_content(message, current=True):
    """Never change stored history or the user's displayed tool output."""
    content = str(message.get('content', ''))
    info = message.get('_galam_action') or {}
    match = re.search(r'^\[Tool Observation for `([^`]+)`\]', content)
    tool = info.get('tool') or (match.group(1) if match else '')
    successful = info.get('status') in ('completed', 'running') and info.get('exit_code', 0) in (0, None)
    if successful and tool in ('write_file','replace_file_content','make_directory','undo_file_change'):
        lines = content.splitlines()
        kept = [line for line in lines if line.startswith(('Successfully ', 'Created directory ',
                'Verified existing directory ', 'Validation:', 'Status:', '[Edit recovery:'))]
        if kept:
            content = f'[Tool Observation for `{tool}`]:\n' + '\n'.join(kept)
            if info.get('path'):
                content += '\nPath: ' + str(info['path'])
            if info.get('content_hash'):
                content += '\nContent hash: ' + str(info['content_hash'])
    elif tool == 'view_file' or content.startswith('[Verified File Inspection'):
        pass  # Preserve exact inspected lines, even when they are large.
    else:
        content = compact_text(content, 4500 if not successful else 1800 if current else 900)
    if info.get('id'):
        content += f"\n[Stored action {info['id']}; read_action_output retrieves exact lines.]"
    return content
