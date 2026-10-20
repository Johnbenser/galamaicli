"""Exercise each registered remote model through Galam in confined workspaces.

Usage: python tools/audit_remote_actions.py --secrets ~/.secrets
Reports contain no credential values. Each model runs in its own process;
fallbacks, settings writes, direct-command shortcuts and unrelated tools are disabled.
"""
import argparse
import concurrent.futures
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import re
import shlex
import signal
import subprocess
import sys
import time
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))


class AuditDeadline(BaseException):
    """Do not let model transport exception handlers swallow the audit deadline."""


def load_secrets(path):
    """Read literal shell assignments without evaluating shell code."""
    for line in Path(path).expanduser().read_text().splitlines():
        match = re.match(r'^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$', line)
        if not match:
            continue
        parts = shlex.split(match[2], comments=True)
        if len(parts) == 1:
            os.environ[match[1]] = parts[0]


def child(job, directory, deadline):
    from config import CONFIG, AGENT_STATE
    import galam
    from routers import manager
    from storage.endpoints import ENDPOINT_REGISTRY
    from engine import task_runner
    from engine.action_results import ActionResult, as_result

    root = Path(directory).resolve()
    root.mkdir(parents=True, exist_ok=True)
    os.chdir(root)
    endpoint, model = job['endpoint'], job['model']
    cfg = copy.deepcopy(dict(CONFIG))
    cfg.update(active_endpoint=endpoint, endpoint=endpoint, active_model=model, model=model,
               mode='ALWAYS-PROCEED', fallback_policy='ask', stream=True)
    registry = {endpoint: copy.deepcopy(ENDPOINT_REGISTRY[endpoint])}
    registry[endpoint]['models'] = {model: registry[endpoint]['models'][model]}
    report = dict(job, stages=[], requests=[], responses=[], elapsed_seconds=0)
    transcript = io.StringIO()
    original_tool = task_runner.execute_tool_call
    original_transport = manager._post_with_transport_retry
    events = []

    def confined(tool, args, **kwargs):
        allowed = False
        if tool in ('write_file', 'view_file', 'replace_file_content', 'make_directory'):
            target = Path(args.get('path', ''))
            target = target if target.is_absolute() else root / target
            allowed = target.resolve().is_relative_to(root)
        elif tool == 'execute_bash':
            command = args.get('command', '').strip()
            # Only the requested byte-count check can reach the real shell.
            allowed = command in ('wc -c index.html', 'wc -c -- index.html')
        elif tool == 'read_action_output':
            allowed = True
        if not allowed:
            result = ActionResult('Audit confinement: use file tools in the test workspace; '
                                  'the only shell command permitted is wc -c index.html.',
                                  success=False, exit_code=1, status='failed')
        else:
            result = as_result(original_tool(tool, args, **kwargs))
        events.append({'tool': tool, 'success': result.success, 'exit_code': result.exit_code,
                       'allowed': allowed, 'output': result.output[:1200]})
        return result

    def transport(url, headers, payload, stream, timeout, **kwargs):
        started = time.monotonic()
        entry = {'model': payload.get('model', model), 'native_tools': bool(payload.get('tools'))}
        report['requests'].append(entry)
        try:
            response, urllib_mode = original_transport(url, headers, payload, stream, min(timeout, 50), **kwargs)
            entry['http_status'] = getattr(response, 'status_code', getattr(response, 'status', None))
            entry['quota_retry'] = bool(getattr(response, '_galam_rate_limit_retried', False))
            if not urllib_mode and entry['http_status'] == 200:
                iterate = response.iter_lines
                def traced_lines(*args, **kwargs):
                    chunks = entry.setdefault('stream_samples', [])
                    length = 0
                    for line in iterate(*args, **kwargs):
                        if length < 24000:
                            value = line.decode('utf-8', errors='replace') if isinstance(line, bytes) else str(line)
                            chunks.append(value)
                            length += len(value)
                        yield line
                response.iter_lines = traced_lines
            if entry['http_status'] and entry['http_status'] >= 400 and not urllib_mode:
                # Provider body only; never record URL, request headers, or credentials.
                try:
                    error = response.json()
                    error = error.get('error') or error.get('errors') or error.get('message') or error
                    entry['error'] = json.dumps(error, ensure_ascii=False)[:1000]
                except Exception:
                    entry['error'] = response.text[:300]
            return response, urllib_mode
        except Exception as exc:
            entry['transport_error'] = type(exc).__name__
            raise
        finally:
            entry['seconds_to_headers'] = round(time.monotonic() - started, 2)

    messages = []
    began = time.monotonic()
    def expired(signum, frame):
        raise AuditDeadline('Audit stage deadline reached')

    requests = [
        ('create', 'Create index.html in the current directory as a complete standalone HTML5 page. '
         'Set the title and h1 to Galam Action Audit. Include a paragraph whose exact text is '
         'Version Alpha and a button whose text is Test Action. Use real Galam file actions, '
         'not a code example. Keep the page small. No external resources or terminal commands are needed.'),
        ('modify', 'Read index.html, then modify only the paragraph text Version Alpha to Version Beta. '
         'Preserve the title, heading, button and all unrelated code. Use real Galam file actions. '
         'Do not run terminal commands for this edit.'),
        ('bash', 'Execute this exact terminal command using the execute_bash Galam action: '
         'wc -c index.html . Report its actual byte count and exit status. Do not change any files.'),
    ]
    with contextlib.ExitStack() as stack:
        for ctx in (
            patch.dict(CONFIG, cfg, clear=True),
            patch.dict(ENDPOINT_REGISTRY, registry, clear=True),
            patch.dict(AGENT_STATE, {'working_directory': str(root), 'app_ref': None, 'abort': False}),
            patch.dict(os.environ, {'GALAM_STATE_DIR': str(root / '.runtime')}),
            patch.object(manager, 'save_settings'),
            patch.object(manager, 'log_model_interaction', side_effect=lambda **kw: report['responses'].append(
                {'content': kw.get('response_content', ''), 'tool_calls': kw.get('tool_calls', []),
                 'usage': kw.get('metadata',{}).get('usage')})),
            patch.object(task_runner, 'log_tool_execution'), patch.object(galam, 'log_galam_system_event'),
            patch.object(manager, 'log_galam_system_event'),
            patch.object(galam, 'execute_tool_call', side_effect=confined),
            patch.object(galam, 'lang_workflow_available', return_value=False),
            patch.object(galam, 'direct_command_for_request', return_value=None),
            patch.object(manager, '_post_with_transport_retry', side_effect=transport),
            patch.object(manager, 'request_user_confirmation', return_value='n'),
            contextlib.redirect_stdout(transcript), contextlib.redirect_stderr(transcript),
        ):
            stack.enter_context(ctx)
        signal.signal(signal.SIGALRM, expired)
        for stage, prompt in requests:
            fixture_seeded = False
            if stage == 'modify' and not report['stages'][0]['passed']:
                (root / 'index.html').write_text('<!DOCTYPE html>\n<html><head><title>Galam Action Audit</title></head>'
                    '<body><h1>Galam Action Audit</h1><p>Version Alpha</p><button>Test Action</button></body></html>\n')
                fixture_seeded = True
                messages = []
            # Isolate each request, but retain real previous conversation/tool results.
            messages.append({'role': 'user', 'content': prompt})
            start, first, first_request = time.monotonic(), len(events), len(report['requests'])
            before = (root / 'index.html').read_text() if (root / 'index.html').is_file() else ''
            stage_result = {'stage': stage, 'fixture_seeded_by_audit': fixture_seeded}
            signal.alarm(deadline)
            try:
                galam.run_agent_loop(messages, 'remote-action-audit', max_steps=5)
            except (Exception, AuditDeadline) as exc:
                stage_result['exception'] = type(exc).__name__
            finally:
                signal.alarm(0)
            after = (root / 'index.html').read_text() if (root / 'index.html').is_file() else ''
            actions = events[first:]
            if stage == 'create':
                valid = (bool(re.search(r'<!doctype\s+html', after, re.I))
                         and bool(re.search(r'<title>\s*Galam Action Audit\s*</title>', after, re.I))
                         and bool(re.search(r'<h1[^>]*>\s*Galam Action Audit\s*</h1>', after, re.I))
                         and bool(re.search(r'<p[^>]*>\s*Version Alpha\s*</p>', after, re.I))
                         and bool(re.search(r'<button[^>]*>\s*Test Action\s*</button>', after, re.I)))
                valid = valid and any(e['success'] and e['tool'] == 'write_file' for e in actions)
                valid = valid and len(re.findall(r'<!doctype\s+html', after, re.I)) == 1 and after.count('Version Alpha') == 1
            elif stage == 'modify':
                valid = bool(before) and 'Version Alpha' in before and after == before.replace('Version Alpha', 'Version Beta')
                valid = valid and any(e['success'] and e['tool'] == 'view_file' for e in actions)
                valid = valid and any(e['success'] and e['tool'] in ('write_file', 'replace_file_content') for e in actions)
            else:
                size = (root / 'index.html').stat().st_size if (root / 'index.html').is_file() else None
                valid = any(e['success'] and e['exit_code'] == 0 and e['tool'] == 'execute_bash'
                            and re.search(r'\b' + str(size) + r'\b', e['output']) for e in actions)
                valid = valid and before == after
            stage_result.update(passed=bool(valid), seconds=round(time.monotonic()-start, 2),
                                actions=actions, model_requests=len(report['requests'])-first_request,
                                final_response=messages[-1].get('content', '')[:1200] if messages[-1]['role'] == 'assistant' else '')
            if stage == 'bash':
                stage_result['acknowledgment_contains_byte_count'] = bool(
                    re.search(r'\b' + str(size) + r'\b', stage_result['final_response']))
            report['stages'].append(stage_result)
            (root / 'progress.json').write_text(json.dumps({
                'endpoint': endpoint, 'model': model,
                'stages': [{'stage': s['stage'], 'passed': s['passed'],
                            'exception': s.get('exception'), 'seconds': s['seconds']}
                           for s in report['stages']]}, indent=2))
            if stage_result.get('exception') == 'AuditDeadline' and not valid:
                break
            # Don't repeatedly hit a provider that already rejected the model/auth/quota.
            new_requests = report['requests'][first_request:]
            if not actions and new_requests and all(r.get('http_status', 0) >= 400 or r.get('transport_error') for r in new_requests):
                break
    report['elapsed_seconds'] = round(time.monotonic()-began, 2)
    report['passed'] = len(report['stages']) == 3 and all(s['passed'] for s in report['stages'])
    # Redact even if a provider echoes a submitted token in its error body.
    secrets = [v for k, v in os.environ.items() if len(v) >= 8 and any(x in k for x in ('KEY', 'TOKEN', 'ACCOUNT_ID'))]
    def redact(text):
        for value in secrets:
            text = text.replace(value, '[REDACTED]')
        return text
    (root / 'transcript.txt').write_text(redact(transcript.getvalue()))
    (root / 'result.json').write_text(redact(json.dumps(report, indent=2)))
    print(json.dumps({'endpoint': endpoint, 'model': model, 'passed': report['passed']}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--secrets')
    parser.add_argument('--output', default=str(PROJECT / 'artifacts' / time.strftime('remote-action-audit-%Y%m%d-%H%M%S')))
    parser.add_argument('--workers', type=int, default=2)
    parser.add_argument('--deadline', type=int, default=100, help='Seconds per stage')
    parser.add_argument('--child', help=argparse.SUPPRESS)
    parser.add_argument('--only', help='Regex selecting endpoint/model names for a recheck')
    args = parser.parse_args()
    if args.secrets:
        load_secrets(args.secrets)
    if args.child:
        child(json.loads(args.child), args.output, args.deadline)
        return
    from storage.endpoints import ENDPOINT_REGISTRY
    from engine.payload_builder import get_endpoint_api_key
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    jobs, skipped = [], []
    for endpoint, details in ENDPOINT_REGISTRY.items():
        if endpoint == 'Local Endpoint':
            continue
        for model, metadata in details.get('models', {}).items():
            if args.only and not re.search(args.only, endpoint + '/' + model, re.I):
                continue
            job = {'endpoint': endpoint, 'model': model}
            provider = metadata.get('provider')
            if not get_endpoint_api_key(endpoint, provider) and not (provider == 'llm7' or model == 'kilo-auto/free'):
                skipped.append(dict(job, status='missing_credentials'))
                continue
            jobs.append(job)
    def run(pair):
        index, job = pair
        folder = output / f'{index:02d}-{job["endpoint"].lower()}'
        folder.mkdir(exist_ok=True)
        result = subprocess.run([sys.executable, str(Path(__file__).resolve()), '--child', json.dumps(job),
                                 '--output', str(folder), '--deadline', str(args.deadline)],
                                capture_output=True, text=True, timeout=args.deadline * 3 + 30)
        path = folder / 'result.json'
        if path.is_file():
            report = json.loads(path.read_text())
            report['workspace'] = str(folder)
            return report
        return dict(job, passed=False, status='harness_error', process_exit=result.returncode)
    reports = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(run, pair): pair[1] for pair in enumerate(jobs, 1)}
        for future in concurrent.futures.as_completed(futures):
            try:
                result = future.result()
            except Exception as exc:
                result = dict(futures[future], passed=False, status=type(exc).__name__)
            reports.append(result)
            (output / 'report.json').write_text(json.dumps({'results': reports, 'skipped': skipped}, indent=2))
            statuses = [r.get('http_status', r.get('transport_error')) for r in result.get('requests', [])]
            print(f'{len(reports)}/{len(jobs)} {result["endpoint"]} {result["model"]}: '
                  f'{"PASS" if result.get("passed") else "FAIL"} HTTP={statuses}', flush=True)
    print(f'Report: {output / "report.json"}', flush=True)


if __name__ == '__main__':
    main()
