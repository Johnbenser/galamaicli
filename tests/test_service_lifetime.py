"""Owned services survive launcher exit, follow-up checks and response completion."""
import os
import unittest
from pathlib import Path
import shlex
import socket
import subprocess
import sys
import time
import urllib.request
from unittest.mock import patch

from config import CONFIG, AGENT_STATE
from engine import cancellation, task_runner
from engine.process_runner import launch_process
import test_pipelines as fixtures


class ServiceLifetimeTests(fixtures.Workspace):
    response = fixtures.LoopTests.response
    run_loop = fixtures.LoopTests.run_loop

    def setUp(self):
        super().setUp()
        self.manager = task_runner.TaskManager()
        self.processes = []
        self.settings = patch.dict(CONFIG, {'server_startup_timeout':15})
        self.settings.start()
        (self.root / 'index.html').write_text('SERVICE_LIFETIME_MARKER')

    def tearDown(self):
        self.manager.stop_all()
        for process in self.processes:
            try:
                process.wait(timeout=4)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, 9)
                process.wait(timeout=3)
        cancellation.end_task()
        self.settings.stop()
        super().tearDown()

    def service(self):
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        url = f'http://127.0.0.1:{port}/'
        command = f'{shlex.quote(sys.executable)} -u -m http.server {port} --bind 127.0.0.1'
        return command, url

    def launch(self, command, url, timeout=15):
        result = launch_process(command, str(self.root), task_runner.get_subprocess_env(),
                                self.manager, readiness_url=url, timeout=timeout)
        self.processes.extend(t['process'] for t in self.manager.tasks if t['process'] not in self.processes)
        return result

    @staticmethod
    def launch_log(result):
        path = result.metadata.get('log_path')
        return Path(path).read_text()[-2000:] if path and Path(path).exists() else ''

    def assert_serving(self, url):
        with urllib.request.urlopen(url, timeout=2) as response:
            self.assertEqual(response.read().decode(), 'SERVICE_LIFETIME_MARKER')

    def wait_for(self, predicate, timeout=15):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if predicate():
                return
            time.sleep(.05)
        self.fail('Expected process state did not settle')

    def test_shell_background_job_stays_tracked_after_its_checks_finish(self):
        command, url = self.service()
        result = self.launch(f'nohup {command} > service.log 2>&1 & sleep .5; echo CHECK_DONE', url)
        self.assertTrue(result.success, result.output + '\n' + self.launch_log(result))
        time.sleep(.8)
        self.assert_serving(url)
        self.assertEqual(len(self.manager.get_active_tasks()), 1)
        self.assertIn('running', ''.join(text for _, text in self.manager.get_task_pill_tokens()))
        # Reusing the identical launch keeps one owner after the shell has exited.
        again = self.launch(f'nohup {command} > service.log 2>&1 & sleep .5; echo CHECK_DONE', url)
        self.assertEqual(again.metadata['pid'], result.metadata['pid'])
        self.assertEqual(len(self.manager.tasks), 1)
        self.assertTrue(self.manager.stop_task(str(result.metadata['pid']), pid_only=True))
        self.wait_for(lambda: self.processes[0].poll() is not None)
        with self.assertRaises(OSError):
            urllib.request.urlopen(url, timeout=1)

    def test_response_completion_keeps_detached_service_visible_and_reachable(self):
        command, url = self.service()
        with patch.object(task_runner, 'GLOBAL_TASK_MANAGER', self.manager), patch.dict(CONFIG, {'command_agent':True}):
            messages, _ = self.run_loop([self.response('execute_bash', {
                'command':f'nohup {command} > service.log 2>&1 & sleep .5; echo CHECK_DONE',
                'background':True, 'readiness_url':url})], 'Start the server')
        self.processes.extend(task['process'] for task in self.manager.tasks)
        self.assertEqual(self.model_calls, 1)
        self.assertEqual(AGENT_STATE['task_outcome'], 'completed')
        self.assertIn('Running', messages[-1]['content'])
        time.sleep(.8)
        self.assert_serving(url)
        self.assertIn('running', ''.join(text for _, text in self.manager.get_task_pill_tokens()))

    def test_nested_launcher_can_exit_before_readiness_without_killing_service(self):
        command, url = self.service()
        (self.root / 'launch.sh').write_text(f'(sleep .4; exec {command}) > service.log 2>&1 &\n')
        result = self.launch('sh launch.sh', url)
        self.assertTrue(result.success, result.output + '\n' + self.launch_log(result))
        self.assert_serving(url)
        self.assertEqual(len(self.manager.get_active_tasks()), 1)

    def test_service_exit_removes_tracking_even_when_launcher_succeeded(self):
        # Shutdown is triggered after readiness, not by a scheduling-sensitive timer.
        script = """import http.server, threading
class Handler(http.server.BaseHTTPRequestHandler):
 def do_GET(self):
  self.send_response(200); self.end_headers(); self.wfile.write(b'SERVICE_LIFETIME_MARKER')
  if self.path == '/stop': threading.Thread(target=server.shutdown).start()
server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
print('http://127.0.0.1:%s/'%server.server_port,flush=True)
server.serve_forever(poll_interval=.05)
"""
        result = launch_process(f'{shlex.quote(sys.executable)} -u -c {shlex.quote(script)} &',
                                str(self.root), task_runner.get_subprocess_env(), self.manager, timeout=8)
        self.processes.extend(t['process'] for t in self.manager.tasks)
        self.assertTrue(result.success, result.output + '\n' + self.launch_log(result))
        with urllib.request.urlopen(result.metadata['url'] + 'stop', timeout=2) as response:
            self.assertEqual(response.status, 200)
        self.wait_for(lambda: not self.manager.get_active_tasks())
        self.assertEqual(self.manager.get_task_pill_tokens(), [])

    def test_launcher_with_no_live_service_exits_truthfully(self):
        result = self.launch('echo START_FAILED; exit 7', '')
        self.assertFalse(result.success)
        self.assertEqual(result.exit_code, 7)
        self.assertIn('START_FAILED', result.output)
        self.assertEqual(self.manager.get_active_tasks(), [])

    def test_stop_all_stops_background_descendants_not_an_unowned_server(self):
        command, url = self.service()
        result = self.launch(f'{command} > service.log 2>&1 &', url)
        self.assertTrue(result.success, result.output + '\n' + self.launch_log(result))
        other_command, other_url = self.service()
        other = subprocess.Popen(other_command, shell=True, cwd=self.root, start_new_session=True,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            self.wait_for(lambda: task_runner.GLOBAL_TASK_MANAGER.is_port_in_use(int(other_url.split(':')[-1].rstrip('/'))))
            self.manager.stop_all()
            self.wait_for(lambda: self.processes[0].poll() is not None)
            self.assert_serving(other_url)
        finally:
            os.killpg(other.pid, 15)
            other.wait(timeout=3)


class StopIntentTests(unittest.TestCase):
    def setUp(self):
        self.tasks = [{'id':1,'pid':10001,'port':'43101','full_cmd':'python3 -m http.server 43101'},
                      {'id':2,'pid':10002,'port':'43102','full_cmd':'npm run dev'}]

    def test_mentions_negations_questions_and_conditionals_do_not_stop(self):
        from engine.server_stop import requested_stop_targets
        for text in ('Do not stop my server', 'Why did you stop my server?',
                     'Fix the server that stopped', 'How do I stop the server?',
                     'Stop the server if the tests pass', 'The server will stop after a minute'):
            self.assertIsNone(requested_stop_targets(text,self.tasks),text)

    def test_ambiguous_single_server_does_not_stop_all(self):
        from engine.server_stop import requested_stop_targets
        self.assertEqual(requested_stop_targets('stop my server',self.tasks),[])
        self.assertEqual(requested_stop_targets('stop the server',self.tasks[:1]),['10001'])
        self.assertEqual(requested_stop_targets('Can you stop all servers?',self.tasks),['10001','10002'])

    def test_explicit_task_pid_port_and_runtime_are_scoped(self):
        from engine.server_stop import requested_stop_targets
        for text, expected in (('stop task 2',['10002']), ('kill pid 10001',['10001']),
                               ('stop the server on port 43102',['10002']),
                               ('Please stop my Python server',['10001']), ('stop React',['10002']),
                               ('stop pid 99999',[])):
            self.assertEqual(requested_stop_targets(text,self.tasks),expected,text)


class ProbeTransportTests(unittest.TestCase):
    def test_plain_http_probe_does_not_load_tls_certificates(self):
        with patch.object(task_runner.ssl,'create_default_context',side_effect=AssertionError('HTTP does not use TLS')), \
             patch.object(task_runner.urllib.request,'urlopen') as open_url:
            response = open_url.return_value.__enter__.return_value
            response.read.return_value = b'ok'
            response.status = 200
            self.assertEqual(task_runner.python_curl('http://127.0.0.1:12345/'),('ok',200))
            self.assertNotIn('context',open_url.call_args.kwargs)

    def test_https_probe_keeps_verified_tls_context(self):
        with patch.object(task_runner.ssl,'create_default_context') as context, \
             patch.object(task_runner.urllib.request,'urlopen') as open_url:
            response = open_url.return_value.__enter__.return_value
            response.read.return_value = b'ok'
            response.status = 200
            self.assertEqual(task_runner.python_curl('https://localhost:12345/'),('ok',200))
            context.assert_called_once_with()
            self.assertIs(open_url.call_args.kwargs['context'],context.return_value)
