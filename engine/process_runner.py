"""Explicit long-running commands, owned from spawn through readiness/cancellation."""
import json
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import time
import urllib.parse
import uuid

from engine.action_results import ActionResult
from engine.cancellation import check, TaskCancelled, wait
from engine.result_store import runtime_dir


def log_tail(path, limit=4000):
    with open(path, 'rb') as log:
        log.seek(0, os.SEEK_END)
        log.seek(max(0, log.tell() - limit))
        return log.read(limit).decode('utf-8', 'replace')


def local_url(url):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in ('http', 'https', 'tcp') or parsed.hostname not in ('localhost', '127.0.0.1', '::1', '0.0.0.0', '::'):
        raise ValueError('Readiness must use a local HTTP(S) or TCP address')
    if parsed.username or parsed.password:
        raise ValueError('Do not put credentials in a readiness URL')
    host = '[::1]' if ':' in parsed.hostname else '127.0.0.1'
    port = parsed.port or (443 if parsed.scheme == 'https' else 80 if parsed.scheme == 'http' else None)
    if not port:
        raise ValueError('TCP readiness needs an explicit port')
    return urllib.parse.urlunsplit((parsed.scheme, f'{host}:{port}', parsed.path or '/', parsed.query, ''))


def inferred_url(command, directory, environment):
    from engine.server_commands import configured_port
    try:
        scripts = json.loads(Path(directory, 'package.json').read_text()).get('scripts', {})
    except (OSError, ValueError, TypeError):
        scripts = {}
    port = configured_port(command, scripts, environment)
    return f'http://127.0.0.1:{port}/' if port else ''


def listening(url):
    parsed = urllib.parse.urlsplit(url)
    host = parsed.hostname
    try:
        with socket.create_connection((host, parsed.port), timeout=.15):
            return True
    except OSError:
        return False


def readiness(url):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme == 'tcp':
        return ('ready', 'TCP listener verified') if listening(url) else ('starting', '')
    from engine.task_runner import python_curl
    _, status = python_curl(url, timeout=1)
    if 200 <= status < 400:
        return 'ready', f'HTTP {status} verified'
    if status in (401, 403):
        return 'auth_required', f'HTTP {status}; service responds but this route requires authentication'
    return 'starting', f'HTTP {status}' if status > 0 else ''


def launch_process(command, directory, environment, manager, readiness_url='', timeout=30):
    """Never start a second copy of this command just because readiness is pending."""
    directory = str(Path(directory).resolve())
    url = local_url(readiness_url) if readiness_url else inferred_url(command, directory, environment)
    url = local_url(url) if url else ''
    process = None
    for task in manager.get_active_tasks():
        if task.get('full_cmd') == command and task.get('cwd') == directory:
            process, entry = task['process'], task
            url = url or task.get('readiness_url', '')
            log_path = Path(task['log_path'])
            break
    if process is None:
        # A responding unrelated server never proves the requested command started.
        if url and listening(url):
            return ActionResult(f'Error: Readiness address {url} is already occupied by an unowned service. '
                                'No new process started; choose a different port or inspect the existing service.', False, 1)
        log_path = runtime_dir('processes') / (uuid.uuid4().hex + '.log')
        descriptor = os.open(log_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, 'w') as output:
            environment = dict(environment, PYTHONUNBUFFERED='1')
            process = subprocess.Popen(command, shell=True, cwd=directory, env=environment,
                stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                start_new_session=True)
        entry = manager.register_task(command, process, command,
                                     str(urllib.parse.urlsplit(url).port or '') if url else '')
        entry.update(cwd=directory, log_path=str(log_path), readiness_url=url, state='starting')
    deadline = time.monotonic() + max(1, min(int(timeout), 120))
    detail = ''
    try:
        while time.monotonic() < deadline:
            check()
            if process.poll() is not None:
                entry['state'] = 'exited'
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                tail = log_tail(log_path, 2400)
                return ActionResult(f'Error: Process exited with status {process.returncode}.\n{tail}',
                                    False, process.returncode or 1, metadata={'pid': process.pid, 'log_path': str(log_path)})
            if not url:
                # Many custom launchers announce their actual address rather than accept --port.
                tail = log_tail(log_path)
                found = re.search(r'(?:https?|tcp)://(?:localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\]|\[::\]):\d+(?:/[\w./-]*)?', tail)
                if found:
                    url = local_url(found[0])
                    entry['readiness_url'] = url
                    entry['port'] = str(urllib.parse.urlsplit(url).port)
            if url:
                state, detail = readiness(url)
                if state in ('ready', 'auth_required'):
                    entry['state'] = state
                    return ActionResult(f'Background process PID {process.pid}: {detail} at {url}. '
                        f'Command: {command}\nDirectory: {directory}\nLog: {log_path}',
                        True, None, True, 'running', verified=state == 'ready',
                        metadata={'pid': process.pid, 'readiness': state, 'url': url, 'log_path': str(log_path)})
            wait(.2)
    except TaskCancelled:
        # Only the process owned by this launch, never a global name or port match.
        manager.stop_task(str(process.pid), pid_only=True)
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=2)
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        raise
    entry['state'] = 'running_unverified'
    return ActionResult(f'Process PID {process.pid} is alive, but readiness is unverified. '
        f'{detail}\nDo not launch another copy. Inspect its log or supply the actual readiness address. '
        f'Log: {log_path}', False, None, True, 'running',
        metadata={'pid': process.pid, 'readiness': 'unverified', 'url': url, 'log_path': str(log_path)})
