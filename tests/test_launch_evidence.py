"""Regression cases for decision evidence lost despite successful tool calls."""
import json
import contextlib
import io
import os
from pathlib import Path
import socket
import sys
import threading
import time
import unittest
from unittest.mock import patch
from config import CONFIG, AGENT_STATE
from engine import usage
from engine import task_runner, cancellation
from engine.action_results import ActionResult
from engine.task_progress import TaskProgress, checkpoint_context
import test_pipelines as fixtures


class LaunchEvidenceTests(fixtures.Workspace):
    def setUp(self):
        super().setUp()
        usage.start_task('launch-evidence')
        self.messages = [{'role': 'user', 'content': 'Start the project server'}]
        self.flow = TaskProgress(self.messages, self.messages[0]['content'], str(self.root), {}, 8)
        self.command = 'PORT=${PORT:-7819} python3 -u custom_web_worker.py'
        self.scripts = {'build': 'node build.js', 'check': ' && '.join('node --check module%d.js' % n for n in range(8)),
                        'dev': 'npm run build && npm start', 'preview': 'npm start', 'start': self.command}

    def package(self):
        self.flow.observe('execute_bash', {'command': 'cat package.json'},
                          ActionResult(json.dumps({'scripts': self.scripts})), str(self.root))

    def test_complete_start_command_survives_long_scripts_and_recent_receipts(self):
        self.package()
        for n in range(3):
            self.flow.observe('view_file', {'path': f'note{n}.txt'}, ActionResult(f'new detail {n} ' * 100), str(self.root))
        self.assertIn(self.command, checkpoint_context(self.messages, 900))

    def test_unrelated_novel_output_cannot_renew_a_known_launch_recipe(self):
        self.package()
        self.flow.begin_round()
        for n in range(5):
            reason = self.flow.begin_round()
            if reason:
                break
            self.flow.observe('view_file', {'path': f'notes{n}.txt'}, ActionResult(f'unrelated new output {n}'), str(self.root))
        self.assertTrue(reason, 'Novel but unrelated reads must not indefinitely renew a startup task')

    def test_recovery_uses_the_retained_command_and_next_milestone(self):
        self.package()
        self.flow.pending_recovery = True
        text = self.flow.recovery()['content']
        self.assertIn(self.command, text)
        self.assertIn('launch', text.lower())

    def test_numbered_manifest_also_produces_the_same_complete_recipe(self):
        text = json.dumps({'scripts': self.scripts}, indent=2)
        numbered = '\n'.join(f'{n} | {line}' for n,line in enumerate(text.splitlines(),1))
        self.flow.observe('view_file', {'path': 'package.json'}, ActionResult(numbered), str(self.root))
        self.assertIn(self.command, checkpoint_context(self.messages, 900))


class GenericProcessTests(fixtures.Workspace):
    def setUp(self):
        super().setUp()
        self.manager = task_runner.TaskManager()
        self.manager_patch = patch.object(task_runner, 'GLOBAL_TASK_MANAGER', self.manager)
        self.manager_patch.start()

    def tearDown(self):
        for task in list(self.manager.tasks):
            process = task.get('process')
            if process and process.poll() is None:
                self.manager.stop_task(str(process.pid), pid_only=True)
                process.wait(timeout=3)
        cancellation.end_task()
        self.manager_patch.stop()
        super().tearDown()

    def server(self, delay=0, status=200):
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
        source = (f'import time\ntime.sleep({delay})\nfrom http.server import HTTPServer, BaseHTTPRequestHandler\n'
                  f'class Handler(BaseHTTPRequestHandler):\n def do_GET(self):\n  self.send_response({status}); self.end_headers(); self.wfile.write(b"ready")\n'
                  f'server=HTTPServer(("127.0.0.1",{port}), Handler)\n'
                  f'print("http://127.0.0.1:{port}/health",flush=True)\nserver.serve_forever()\n')
        (self.root/'custom_web_worker.py').write_text(source)
        return f'{sys.executable} -u custom_web_worker.py', f'http://127.0.0.1:{port}/health'

    def test_custom_launcher_is_owned_and_ready_without_a_filename_whitelist(self):
        command, url = self.server()
        result = task_runner.execute_tool_call('execute_bash', {'command': command, 'background': True})
        self.assertTrue(result.success, result.output)
        self.assertTrue(result.verified)
        self.assertEqual(result.metadata['url'], url)
        self.assertEqual(len(self.manager.tasks), 1)

    def test_pending_readiness_does_not_spawn_duplicate_or_lose_tracking(self):
        from engine.process_runner import launch_process
        command, url = self.server()
        script = self.root/'custom_web_worker.py'
        script.write_text('import time\nfrom pathlib import Path\n'
                          'while not Path("release-startup").exists(): time.sleep(.05)\n' + script.read_text())
        first = launch_process(command, str(self.root), task_runner.get_subprocess_env(), self.manager, url, timeout=1)
        self.assertFalse(first.success)
        self.assertEqual(len(self.manager.tasks), 1)
        (self.root/'release-startup').touch()
        second = launch_process(command, str(self.root), task_runner.get_subprocess_env(), self.manager, url, timeout=8)
        self.assertTrue(second.success, second.output)
        self.assertEqual(first.metadata['pid'], second.metadata['pid'])

    def test_unowned_healthy_service_cannot_prove_another_launch_succeeded(self):
        command, url = self.server()
        first = task_runner.execute_tool_call('execute_bash', {'command': command, 'background': True, 'readiness_url': url})
        self.assertTrue(first.success, first.output)
        with patch.object(task_runner, 'GLOBAL_TASK_MANAGER', task_runner.TaskManager()):
            other = task_runner.execute_tool_call('execute_bash', {'command': command, 'background': True, 'readiness_url': url})
        self.assertFalse(other.success)
        self.assertIn('unowned', other.output)

    def test_cancellation_during_startup_stops_only_the_owned_child(self):
        command, url = self.server(delay=5)
        cancellation.begin_task()
        stopper = threading.Thread(target=lambda: (time.sleep(.4), cancellation.cancel_current()))
        stopper.start()
        try:
            result = task_runner.execute_tool_call('execute_bash', {'command': command, 'background': True, 'readiness_url': url})
            self.assertFalse(result.success)
            self.assertFalse(self.manager.get_active_tasks())
        finally:
            stopper.join()
            AGENT_STATE['abort'] = False

    def test_authentication_response_is_distinct_from_http_health(self):
        command, url = self.server(status=401)
        result = task_runner.execute_tool_call('execute_bash', {'command': command, 'background': True, 'readiness_url': url})
        self.assertTrue(result.success, result.output)
        self.assertFalse(result.verified)
        self.assertEqual(result.metadata['readiness'], 'auth_required')

    def test_manage_tasks_cannot_fall_back_to_killing_an_external_pid(self):
        with patch.object(task_runner.os, 'kill') as kill:
            result = task_runner.execute_tool_call('manage_tasks', {'action': 'stop', 'target': '999999'})
        self.assertFalse(result.success)
        kill.assert_not_called()

    def test_adaptive_model_sees_complete_evidence_in_actual_small_context(self):
        import galam
        from routers.manager import AgentResponse, build_enforced_messages
        command, url = self.server()
        scripts = {'build':'node build.js','check':' && '.join(f'node --check part{n}.js' for n in range(8)),
                   'dev':'npm run build && npm start', 'start':command}
        (self.root/'package.json').write_text(json.dumps({'scripts':scripts},indent=2))
        (self.root/'large.js').write_text('\n'.join(f'// unrelated source line {n}' for n in range(200)))
        messages=[{'role':'user','content':'Start the project server'}]
        count=0; payloads=[]
        def reply(history):
            nonlocal count
            count+=1
            payloads.append(build_enforced_messages(history,'Groq','fixture'))
            if count==1:
                proposed=[('view_file',{'path':'package.json'}),('view_file',{'path':'large.js'})]
            elif not self.manager.get_active_tasks():
                if command in json.dumps(payloads[-1]):
                    proposed=[('execute_bash',{'command':command,'background':True,'readiness_url':url})]
                else:
                    proposed=[('view_file',{'path':'package.json'})]
            else:
                return AgentResponse(content='Started and HTTP health verified.'),'fixture'
            calls=[{'id':f'fixture_{count}_{n}','type':'function','function':{'name':name,'arguments':json.dumps(args)}}
                   for n,(name,args) in enumerate(proposed)]
            return AgentResponse(content='',tool_calls=calls),'fixture'
        with patch.dict(CONFIG,{'mode':'ALWAYS-PROCEED','context_window':4096,'max_tokens':1024}), \
             patch.object(galam,'query_with_fallback',side_effect=reply), \
             patch.object(galam,'lang_workflow_available',return_value=False), \
             patch.object(galam,'direct_command_for_request',return_value=None), \
             patch.object(galam,'render_output_card'),patch.object(galam,'render_action_card'), \
             contextlib.redirect_stdout(io.StringIO()):
            galam.run_agent_loop(messages,'adaptive-launch',max_steps=2)
        self.assertLessEqual(count,3)
        self.assertEqual(AGENT_STATE['task_outcome'],'completed')
        self.assertTrue(self.manager.tasks[0]['process'].poll() is None)
        self.assertIn(command,json.dumps(payloads[1]))


class ServiceIntentTests(unittest.TestCase):
    def test_adjacent_verb_correction_preserves_questions_and_negations(self):
        from engine.server_commands import server_start_requested
        self.assertTrue(server_start_requested('Go to the project then ruj my server'))
        self.assertTrue(server_start_requested('Please strt the server'))
        self.assertFalse(server_start_requested('Explain how to run my server'))
        self.assertFalse(server_start_requested('Never ruj my server; inspect files only'))
        self.assertFalse(server_start_requested('Read server.py'))

    def test_text_protocol_preserves_explicit_background_and_readiness(self):
        from engine.tool_parser import extract_tools_from_text
        args={'command':'make web','background':True,'readiness_url':'http://127.0.0.1:8193/health'}
        calls=extract_tools_from_text('<tool_call>'+json.dumps({'name':'execute_bash','arguments':args})+'</tool_call>')
        self.assertEqual(json.loads(calls[0]['function']['arguments']), args)
