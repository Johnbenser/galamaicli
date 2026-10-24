"""Reproducible user-style builder evaluation; never imported by Galam's runtime.

python tools/audit_builder_workflows.py --output /tmp/galam-builder-eval
Credentials use Galam's existing loader. Reports exclude headers and secret values.
Confinement checks are an experiment guard, not an OS sandbox.
"""
import argparse
import contextlib
import copy
import hashlib
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

INDEX = '<!doctype html><title>Preview</title><h1>Preview</h1><p>Version Alpha</p><div data-keep="yes">KEEP_UI</div>\n'
FIXTURE = {
    'totals.py': 'from decimal import Decimal, ROUND_HALF_UP\n\ndef total(prices, rate):\n    subtotal = sum(Decimal(str(price)) for price in prices)\n    return float((subtotal * (Decimal("1") - Decimal(str(rate)))).quantize(Decimal(".01"), rounding=ROUND_HALF_UP))\n',
    'test_totals.py': 'import unittest\nfrom totals import total\n\nclass TotalsTests(unittest.TestCase):\n    def test_tax(self): self.assertEqual(total([100], .1), 110.0)\n    def test_zero_rate(self): self.assertEqual(total([20], 0), 20.0)\n    def test_decimal(self): self.assertEqual(total([19.99, .01], .075), 21.5)\n    def test_empty(self): self.assertEqual(total([], .1), 0.0)\n',
    'index.html': INDEX,
    'build.cjs': 'const fs=require("fs");fs.mkdirSync("dist",{recursive:true});for(const name of ["index.html","about.html"]){if(fs.existsSync(name))fs.copyFileSync(name,"dist/"+name)}console.log("BUILD_OK");\n',
    'server.py': 'from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler\nfrom functools import partial\nhandler = partial(SimpleHTTPRequestHandler, directory="dist")\nserver = ThreadingHTTPServer(("127.0.0.1", 0), Handler)\nprint(f"http://127.0.0.1:{server.server_port}/", flush=True)\nserver.serve_forever()\n',
    'package.json': json.dumps({'private': True, 'scripts': {'test': 'python -m unittest -v', 'build': 'node build.cjs', 'dev': 'npm run build && python -u server.py'}}),
    'data/user.json': '{"keep":"USER_DATA_MUST_SURVIVE"}\n',
    'README.md': 'Builder fixture. Python and Node standard libraries only; do not install dependencies.\nTests: python -m unittest -v (or npm test).\nBuild: npm run build.\nPreview: npm run dev. The server prints its OS-assigned address.\nDo not edit test_totals.py, data/user.json, package.json or build.cjs.\n',
}
PROTECTED = ('test_totals.py', 'data/user.json', 'package.json', 'build.cjs')
STAGES = [
    ('repair', 'Analyze this project, run its documented tests, and fix the calculation defect using the real errors. Tax must be added to the subtotal. Preserve unrelated code, tests, package/build scripts and data/user.json. Run tests again and report only observed results.'),
    ('create', 'Create about.html as a small complete standalone HTML5 page. Its title and h1 must be Galam Builder Lab, include a paragraph with exact text Version Alpha, and a navigation link to index.html. No dependencies. Build the file, not a code example.'),
    ('edit', 'Read about.html and change only the paragraph text Version Alpha to Version Beta. Preserve every other byte; use a focused edit. Do not change any other file.'),
    ('startup', 'Run the documented build and preview with npm run dev. Fix any real startup error yourself, preserve the intended build/start chain, then leave one server running. Use its actual OS-assigned local address; do not pin a port or install dependencies. Verify it serves the built index page.'),
    ('followup', 'Do not stop, restart or modify the server or any file. Explain briefly which startup defect you fixed and give its actual running address based on observed results. If the answer is already in this conversation, use that evidence.'),
    ('handoff', 'Change only the paragraph text Version Alpha to Version Gamma in index.html. Preserve all other bytes, rebuild the served page and verify the new text over HTTP. Keep the existing server running; do not start a second copy. Preserve user data and tests.'),
]


class ExperimentStop(BaseException):
    pass


def write_fixture(root):
    root.mkdir(parents=True, exist_ok=True)
    for name, content in FIXTURE.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)


def hashes(root):
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob('*') if path.is_file() and '__pycache__' not in path.parts}


def inside(root, value):
    path = Path(value).expanduser()
    return (path if path.is_absolute() else root / path).resolve().is_relative_to(root.resolve())


def allowed_command(command, root):
    """Permit fixture operations, local probes and ordinary project inspections."""
    import urllib.parse
    # Galam removes a final shell '&' before handing a service to its owner.
    visible = re.sub(r'\s*&\s*$', '', re.sub(r'2>\s*&1', '', command)).strip()
    if any(mark in visible for mark in ('$', '`', '\n', '>', '<')):
        return False
    lexer = shlex.shlex(visible, posix=True, punctuation_chars=';&|')
    lexer.whitespace_split = True
    segments, current = [], []
    try:
        for token in list(lexer) + [';']:
            if token in (';', '&&', '||', '|'):
                if current:
                    segments.append(current)
                current = []
            elif token == '&':
                return False
            else:
                current.append(token)
    except ValueError:
        return False
    for words in segments:
        program = Path(words[0]).name
        args = words[1:]
        if program == 'cd':
            args = [arg for arg in args if arg != '--']
            if len(args) != 1 or args[0] == '-' or not inside(root, args[0]):
                return False
        elif program in ('python', 'python3', Path(sys.executable).name):
            if not (args[:2] in (['-m', 'unittest'], ['-m', 'pytest']) or args == ['-u', 'server.py'] or args == ['server.py']):
                return False
        elif program == 'npm':
            if args not in (['test'], ['run', 'test'], ['run', 'build'], ['run', 'dev']):
                return False
        elif program == 'pytest':
            if any(arg.startswith(('/', '~')) and not inside(root,arg) or '..' in Path(arg).parts for arg in args):
                return False
        elif program == 'node':
            if args not in (['build.cjs'], ['--check', 'build.cjs']):
                return False
        elif program == 'curl':
            urls = [arg for arg in args if '://' in arg]
            if not urls or any(urllib.parse.urlsplit(url).hostname not in ('127.0.0.1', 'localhost', '::1') for url in urls):
                return False
            if any(arg in ('-o', '--output', '-T', '--upload-file', '-d', '--data', '-X', '--request') for arg in args):
                return False
        elif program in ('pwd', 'ls', 'cat', 'head', 'tail', 'find', 'wc', 'echo', 'printf'):
            if any(arg in ('-exec', '-execdir', '-delete', '-ok', '-fprint') for arg in args):
                return False
            if any(arg.startswith(('/', '~')) and not inside(root, arg) for arg in args):
                return False
            if any('..' in Path(arg).parts for arg in args):
                return False
        else:
            return False
    if command.rstrip().endswith('&') and not any(
            words == ['npm', 'run', 'dev'] or words in (['python', '-u', 'server.py'], ['python3', '-u', 'server.py'])
            for words in segments):
        return False
    return bool(segments)


def fetch(url):
    import urllib.request
    try:
        with urllib.request.urlopen(url, timeout=3) as response:
            return response.status, response.read().decode('utf-8', 'replace')
    except Exception:
        return None, ''


def child(job, output, deadline, allowance):
    import galam
    from config import CONFIG, AGENT_STATE
    from storage.endpoints import ENDPOINT_REGISTRY
    from routers import manager
    from engine import usage, cancellation, task_runner
    from engine.action_results import ActionResult, as_result
    from engine.security import redact

    output.mkdir(parents=True, exist_ok=True)
    root = output / 'project'
    write_fixture(root)
    os.chdir(root)
    selected = [job['primary'], job['handoff']]
    registry = {}
    for endpoint, model in selected:
        registry.setdefault(endpoint, {**copy.deepcopy(ENDPOINT_REGISTRY[endpoint]), 'models': {}})
        registry[endpoint]['models'][model] = copy.deepcopy(ENDPOINT_REGISTRY[endpoint]['models'][model])
    profile = dict(CONFIG, mode='ALWAYS-PROCEED', stream=True, fallback_policy='ask', command_agent=True,
                   task_request_limit=None, task_token_limit=None, task_time_limit_seconds=None)
    profile.pop('context_window', None)
    events, requests, interactions, stages = [], [], [], []
    protected = {name: hashes(root)[name] for name in PROTECTED}
    tasks = task_runner.TaskManager()
    original_execute, original_begin = task_runner.execute_tool_call, usage.begin_request
    transcript = io.StringIO()
    messages = []
    active = selected[0]
    started = time.monotonic()
    report = {'primary': selected[0], 'handoff': selected[1], 'stages': stages,
              'experiment_only': {'token_allowance': allowance, 'stage_seconds': deadline}, 'confinement': 'tool-level; not an OS sandbox',
              'profile': {key:profile.get(key) for key in ('max_tokens','stream','command_agent','token_optimization')}}
    first_action = None
    current_stage = ''

    def begin(endpoint, model, payload, *args, **kwargs):
        if (endpoint, model) != tuple(active):
            raise ExperimentStop('Unexpected model fallback prevented by the experiment')
        if requests and requests[-1].get('http_status') in (401, 403, 429):
            raise ExperimentStop('Experiment stopped after provider credential/quota rejection; no additional requests sent')
        spent = sum(row.get('total_tokens', row.get('estimated_input_tokens', 0) + row.get('estimated_output_tokens', 0)) for row in requests)
        if allowance and spent + int(CONFIG.get('max_tokens', 1024)) >= allowance:
            raise ExperimentStop('Experiment token allowance reached; production settings unchanged')
        row = original_begin(endpoint, model, payload, *args, **kwargs)
        requests.append(row)
        (output/'progress.json').write_text(json.dumps({'stage':current_stage,'requests':len(requests),'actions':len(events), 'last_action':redact(events[-1]) if events else None}))
        return row

    def execute(tool, args, **kwargs):
        nonlocal first_action
        permitted = False
        if tool in ('view_file', 'write_file', 'replace_file_content', 'make_directory'):
            permitted = inside(root, args.get('path', ''))
            if tool != 'view_file' and Path(args.get('path', '')).name in ('test_totals.py', 'package.json', 'build.cjs', 'user.json'):
                permitted = False
        elif tool == 'search_files':
            permitted = inside(root, args.get('directory', '.'))
        elif tool == 'read_action_output':
            permitted = True
        elif tool == 'manage_tasks':
            permitted = args.get('action', 'list') in ('list', 'status', 'inspect')
        elif tool == 'execute_bash':
            permitted = allowed_command(args.get('command', ''), root)
        before = hashes(root)
        if permitted:
            result = as_result(original_execute(tool, args, **kwargs))
        else:
            result = ActionResult('Experiment confinement: use project file tools, documented test/build/preview commands and local HTTP probes only. Protected tests, scripts and user data must remain unchanged.', False, 1)
        elapsed = round(time.monotonic() - started, 3)
        if first_action is None and permitted:
            first_action = elapsed
        events.append({'tool': tool, 'args': redact(args), 'allowed': permitted, 'success': result.success,
                       'exit_code': result.exit_code, 'status': result.status, 'elapsed': elapsed,
                       'changed': before != hashes(root), 'output': redact(result.output[:1800])})
        return result

    def timeout(signum, frame):
        raise ExperimentStop('Experiment stage deadline reached; not a production step limit')

    def save():
        report.update(events=events, requests=[{k:v for k,v in row.items() if not k.startswith('_')} for row in requests],
                      interactions=interactions, first_action_seconds=first_action,
                      elapsed_seconds=round(time.monotonic()-started, 2))
        (output / 'result.json').write_text(json.dumps(redact(report), indent=2, default=str))
        (output / 'transcript.txt').write_text(redact(transcript.getvalue()))

    try:
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.dict(CONFIG, profile, clear=True))
            stack.enter_context(patch.dict(ENDPOINT_REGISTRY, registry, clear=True))
            stack.enter_context(patch.dict(AGENT_STATE, {'working_directory':str(root),'app_ref':None,'abort':False,'active_subagents':[]}))
            stack.enter_context(patch.dict(os.environ, {'GALAM_STATE_DIR':str(output / 'runtime')}))
            for target, name, kwargs in (
                (manager, 'save_settings', {}), (task_runner, 'log_tool_execution', {}),
                (galam, 'log_galam_system_event', {}), (manager, 'log_galam_system_event', {}),
                (manager, 'request_user_confirmation', {'return_value':'n'}),
                (galam, 'request_tool_review', {'return_value':'n'}),
                (galam, 'lang_workflow_available', {'return_value':False}),
                (galam, 'direct_command_for_request', {'return_value':None}),
                (task_runner, 'GLOBAL_TASK_MANAGER', {'new':tasks}),
                (galam, 'execute_tool_call', {'side_effect':execute}),
                (usage, 'begin_request', {'side_effect':begin}),
                (manager, 'log_model_interaction', {'side_effect':lambda **kw: interactions.append(redact({key:kw.get(key) for key in ('endpoint','model_name','response_content','tool_calls','error')}))}),
            ):
                stack.enter_context(patch.object(target, name, **kwargs))
            stack.enter_context(contextlib.redirect_stdout(transcript))
            stack.enter_context(contextlib.redirect_stderr(transcript))
            signal.signal(signal.SIGALRM, timeout)
            server_url, server_pid = '', None
            for name, prompt in STAGES:
                current_stage = name
                if name == 'handoff':
                    active = selected[1]
                CONFIG.update(active_endpoint=active[0], endpoint=active[0], active_model=active[1], model=active[1])
                if name == 'edit' and not (root / 'about.html').exists():
                    stages.append({'stage':name,'passed':False,'status':'blocked_by_missing_created_file','requests':0})
                    continue
                if name in ('followup', 'handoff') and not server_url:
                    stages.append({'stage':name,'passed':False,'status':'blocked_by_missing_running_service','requests':0})
                    continue
                before = hashes(root)
                before_text = (root / ('about.html' if name == 'edit' else 'index.html')).read_text() if name != 'edit' or (root / 'about.html').exists() else ''
                first_event, first_interaction = len(events), len(interactions)
                began = time.monotonic()
                messages.append({'role':'user','content':prompt})
                error = None
                signal.alarm(deadline)
                try:
                    galam.run_agent_loop(messages, 'builder-stress')
                except ExperimentStop as exc:
                    error = str(exc)
                    cancellation.cancel_current()
                except Exception as exc:
                    error = type(exc).__name__ + ': ' + str(exc)
                finally:
                    signal.alarm(0)
                stats = usage.totals()
                evidence = {}
                if name == 'repair':
                    checked = subprocess.run([sys.executable,'-m','unittest','-v'], cwd=root, capture_output=True, text=True, timeout=30)
                    evidence = {'independent_test_exit':checked.returncode, 'model_executed_tests':any(e['tool']=='execute_bash' and 'test' in str(e['args']) for e in events[first_event:])}
                    valid = checked.returncode == 0 and evidence['model_executed_tests']
                elif name == 'create':
                    text = (root/'about.html').read_text() if (root/'about.html').exists() else ''
                    valid = all(re.search(pattern,text,re.I) for pattern in (r'<!doctype\s+html',r'<title>\s*Galam Builder Lab\s*</title>',r'<h1[^>]*>\s*Galam Builder Lab\s*</h1>',r'<p[^>]*>\s*Version Alpha\s*</p>',r'href=[\"\'](?:\./)?index.html[\"\']'))
                    evidence = {'bytes':len(text.encode())}
                elif name == 'edit':
                    after_text = (root/'about.html').read_text()
                    valid = after_text == before_text.replace('Version Alpha','Version Beta') and before_text != after_text
                    evidence = {'exact_replacement':valid, 'other_files_unchanged':all(value==hashes(root).get(path) for path,value in before.items() if path != 'about.html')}
                    valid = valid and evidence['other_files_unchanged']
                elif name == 'startup':
                    owned = tasks.get_active_tasks()
                    server_url = next((task.get('readiness_url','') for task in owned if task.get('state')=='ready'), '')
                    server_pid = owned[0]['pid'] if len(owned)==1 else None
                    code, body = fetch(server_url) if server_url else (None,'')
                    valid = code==200 and body==INDEX and len(owned)==1 and (root/'dist/index.html').exists()
                    evidence = {'http_status':code,'url':server_url,'owned_processes':len(owned),'owner_pid':server_pid,'served_expected_content':body==INDEX}
                elif name == 'followup':
                    code, body = fetch(server_url)
                    final = next((m.get('content','') for m in reversed(messages) if m.get('role')=='assistant'),'')
                    valid = code==200 and before==hashes(root) and any(task['pid']==server_pid for task in tasks.get_active_tasks()) and server_url.rstrip('/') in final and bool(re.search(r'handler|nameerror|case|capital|undefined',final,re.I))
                    evidence = {'http_status':code,'source_unchanged':before==hashes(root),'answer_has_address':server_url.rstrip('/') in final}
                else:
                    after_text = (root/'index.html').read_text()
                    code, body = fetch(server_url)
                    valid = after_text==INDEX.replace('Version Alpha','Version Gamma') and body==after_text and code==200 and len(tasks.get_active_tasks())==1 and tasks.tasks[0]['pid']==server_pid
                    evidence = {'http_status':code,'served_exact_edit':body==after_text,'same_owner':any(task['pid']==server_pid for task in tasks.get_active_tasks())}
                untouched = all(hashes(root).get(path)==value for path,value in protected.items())
                valid = valid and untouched
                final = next((m.get('content','') for m in reversed(messages) if m.get('role')=='assistant'),'')
                stages.append({'stage':name,'endpoint':active[0],'model':active[1],'passed':bool(valid),
                               'status':'experiment_stopped' if error else AGENT_STATE.get('task_outcome'),
                               'error':error,'seconds':round(time.monotonic()-began,2),'usage':stats,
                               'actions':len(events)-first_event,'protected_unchanged':untouched,
                               'evidence':evidence,'response':final[:1600],
                               'proposals':sum(len(row.get('tool_calls') or []) for row in interactions[first_interaction:])})
                save()
                if error or stats['requests'] and not events[first_event:] and AGENT_STATE.get('task_outcome')=='failed':
                    break
            report['passed'] = len(stages)==len(STAGES) and all(stage['passed'] for stage in stages)
    finally:
        signal.alarm(0)
        owned = [task['process'] for task in tasks.get_active_tasks()]
        tasks.stop_all()
        for process in owned:
            process.wait(timeout=3)
        cancellation.end_task()
        save()
    print(json.dumps({'primary':selected[0],'passed':report.get('passed',False),'stages':[(row['stage'],row['passed']) for row in stages]}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--only', help='Regex selecting endpoint/model; default diverse online pilot')
    parser.add_argument('--limit', type=int, default=3, help='Number of experiment jobs; 0 selects every matching model')
    parser.add_argument('--deadline', type=int, default=180, help='Experiment seconds per stage, not a production setting')
    parser.add_argument('--allowance', type=int, default=80000, help='Experiment tokens per job; 0 disables this harness guard')
    parser.add_argument('--child', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.child:
        child(json.loads(args.child),args.output.resolve(),args.deadline,args.allowance)
        return
    from storage.endpoints import ENDPOINT_REGISTRY
    from engine.payload_builder import get_endpoint_api_key
    preferred = [('Groq','openai/gpt-oss-20b'),('NVIDIA','nvidia/nemotron-3.5-lightning-30b-a3b'),('Direct','codestral-latest')]
    available = []
    for endpoint, detail in ENDPOINT_REGISTRY.items():
        if endpoint=='Local Endpoint':
            continue
        for model, meta in detail.get('models',{}).items():
            connection = meta.get('custom_connection') or {}
            credential = bool(os.environ.get(connection.get('api_key_env',''))) if connection else bool(get_endpoint_api_key(endpoint,meta.get('provider')))
            if credential and (not args.only or re.search(args.only,endpoint+'/'+model,re.I)):
                available.append((endpoint,model))
    selected = ([pair for pair in preferred if pair in available] if not args.only and args.limit else available)
    if args.limit:
        selected = selected[:args.limit]
    if len(selected)<2:
        raise SystemExit('At least two configured online models are needed for the handoff evaluation')
    args.output.mkdir(parents=True,exist_ok=True)
    summary = {'jobs':[],'local_endpoint_excluded':True}
    for index, primary in enumerate(selected):
        job = {'primary':primary,'handoff':selected[(index+1)%len(selected)]}
        folder = args.output/f'{index:02d}'
        process = subprocess.run([sys.executable,str(Path(__file__).resolve()),'--child',json.dumps(job),'--output',str(folder),
                                  '--deadline',str(args.deadline),'--allowance',str(args.allowance)],
                                 capture_output=True,text=True,timeout=args.deadline*len(STAGES)+90)
        path = folder/'result.json'
        result = json.loads(path.read_text()) if path.exists() else {**job,'passed':False,'harness_exit':process.returncode}
        summary['jobs'].append(result)
        (args.output/'summary.json').write_text(json.dumps(summary,indent=2))
        print(json.dumps({'primary':primary,'passed':result.get('passed'), 'stages':[(stage['stage'],stage['passed']) for stage in result.get('stages',[])]}),flush=True)


if __name__=='__main__':
    main()
