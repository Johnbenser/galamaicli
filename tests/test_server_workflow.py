"""Actual runner classification and truthful server-task completion."""
import contextlib
import io
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from config import CONFIG,AGENT_STATE
from engine import task_runner
from engine.server_commands import server_invocation,configured_port
from engine.action_results import ActionResult
import test_pipelines as fixtures


class ServerCommandTests(unittest.TestCase):
    def test_inspection_arguments_and_literals_are_not_server_launches(self):
        commands=[
            'cd /tmp && cat package.json && echo "-----VITE-----" && cat vite.config.mjs && cat server.log && ls -la scripts',
            'cat vite.config.mjs 2>/dev/null; echo "---PORT CHECK---"; ss -ltnp',
            'echo "npm start"', 'grep "python3 server.py" README.md',
            'printf "%s" ";" "vite"', "cat <<'EOF'\nnpm run dev\nnpx vite\nEOF\n",
            'cat <<-EOF\n\tnpm start\n\tEOF\n',
            "python3 -c 'print(\"npm start\")'",'npx vite build','node worker.js &']
        for command in commands:
            with self.subTest(command=command):self.assertFalse(task_runner.GLOBAL_TASK_MANAGER.is_server_command(command))

    def test_actual_executable_segments_and_options_are_recognized(self):
        for command in ('cd /tmp && npm run dev','env PORT=5001 nohup python3 -u server.py',
                        'python3 -m http.server 8023','npx --yes vite --port 5175',
                        'node server.js','echo "inspecting"; npm start',
                        "cat <<EOF\nignored\nEOF\nnpm start"):
            with self.subTest(command=command):self.assertTrue(server_invocation(command))

    def test_nested_scripts_defaults_and_explicit_ports_resolve_without_execution(self):
        scripts={'dev':'npm run build && npm start','build':'node build-react.js',
                 'start':'PORT=${PORT:-5501} python3 -u server.py','preview':'npm start'}
        self.assertEqual(configured_port('npm run dev',scripts,{}),'5501')
        self.assertEqual(configured_port('npm run preview',scripts,{}),'5501')
        self.assertEqual(configured_port('npm run dev',scripts,{'PORT':'7011'}),'7011')
        self.assertEqual(configured_port('npm run dev -- --port=8123',scripts,{}),'8123')
        self.assertIsNone(configured_port('npm run dev',{'dev':'npm start','start':'npm run dev'},{}))
        self.assertEqual(configured_port('npx vite',{},{}),'5173')
        self.assertEqual(configured_port('python3 -m http.server',{},{}),'8000')


class ServerExecutionTests(fixtures.Workspace):
    def test_real_inspection_runs_foreground_and_preserves_all_output(self):
        (self.root/'package.json').write_text('{"scripts":{"start":"npm run dev"}}')
        (self.root/'vite.config.mjs').write_text('export default {};')
        (self.root/'server.log').write_text('EXISTING_LOG')
        with patch.object(task_runner.subprocess,'Popen',wraps=task_runner.subprocess.Popen) as popen:
            result=task_runner.execute_tool_call('execute_bash',{'command':'cat package.json && cat vite.config.mjs && cat server.log'})
        self.assertTrue(result.success);self.assertFalse(result.is_background)
        self.assertIn('export default {};',result.output);self.assertIn('EXISTING_LOG',result.output)
        self.assertFalse(any(call.kwargs.get('stdout') not in (None,task_runner.subprocess.PIPE) for call in popen.call_args_list))
        self.assertEqual((self.root/'server.log').read_text(),'EXISTING_LOG')

    def test_real_http_server_is_health_checked_on_its_requested_port(self):
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        commands=task_runner.TaskManager()
        with patch.object(task_runner,'GLOBAL_TASK_MANAGER',commands),patch.object(task_runner,'print_server_card'), \
             patch.object(task_runner,'get_subprocess_env',return_value=os.environ.copy()):
            try:
                result=task_runner.execute_tool_call('execute_bash',{'command':f'{sys.executable} -m http.server {port} --bind 127.0.0.1'})
                self.assertTrue(result.success,result.output);self.assertTrue(result.is_background)
                self.assertIn(f'127.0.0.1:{port}',result.output)
                self.assertEqual(len(commands.tasks),1)
            finally:
                for task in commands.tasks:
                    process=task.get('process')
                    if process:
                        os.killpg(process.pid,15)
                        process.wait(timeout=3)


class ServerCompletionTests(fixtures.Workspace):
    response=fixtures.LoopTests.response
    run_loop=fixtures.LoopTests.run_loop

    def test_successful_inspection_is_not_completion_of_requested_server_start(self):
        placeholder=self.response(text='The model did not provide a final answer or a complete action.')
        placeholder[0].incomplete=True
        responses=[self.response('execute_bash',{'command':'pwd'}),placeholder,placeholder,
                   self.response(text='Which command should I use for the missing entrypoint?')]
        messages,_=self.run_loop(responses,'Go to my project and run the server first')
        self.assertIn('Which command',messages[-1]['content'])
        self.assertEqual(AGENT_STATE['task_outcome'],'incomplete')
        self.assertEqual(messages[-1]['_galam_response_stats']['status'],'incomplete')
        self.assertIn('HTTP-verified server startup',str(messages))


    def test_resumed_continue_retains_original_server_goal_until_user_cancels(self):
        import galam
        messages=[{'role':'user','content':'Go to my project and run the server first'},
                  {'role':'assistant','content':'Inspecting the project.'},
                  {'role':'user','content':'PRIVATE OBSERVATION','_galam_action':{'status':'completed'}},
                  {'role':'user','content':'continue'}]
        response=self.response(text='The model did not provide a final answer or a complete action.')
        response[0].incomplete=True
        calls=0
        def reply(history):
            nonlocal calls
            calls+=1
            if calls==6:
                from engine.cancellation import cancel_current
                cancel_current()
                return None
            return response
        with patch.object(galam,'lang_workflow_available',return_value=False), \
             patch.object(galam,'query_with_fallback',side_effect=reply) as query, \
             patch.object(galam,'execute_tool_call') as action, \
             patch.object(galam,'direct_command_for_request',return_value=None),contextlib.redirect_stdout(io.StringIO()):
            galam.run_agent_loop(messages,'fixture-resume')
        action.assert_not_called();self.assertEqual(query.call_count,6)
        self.assertIn('HTTP-verified server startup',str(messages))
        self.assertEqual(AGENT_STATE['last_task_timing']['status'],'cancelled')
        self.assertTrue(any(m.get('_galam_response_stats',{}).get('status')=='cancelled' for m in messages))

    def test_router_marks_reasoning_only_reply_as_incomplete_without_extra_requests(self):
        from routers import manager
        from storage.endpoints import ENDPOINT_REGISTRY
        response=SimpleNamespace(ok=True,status_code=200,headers={'Content-Type':'application/json'},
            json=lambda:{'choices':[{'message':{'content':'','reasoning_content':'Checking the next step. '*20},'finish_reason':'stop'}]})
        with patch.dict(ENDPOINT_REGISTRY,{'Fixture':{'base_url':'https://fixture.invalid/v1','models':{'fixture':{}}}},clear=True), \
             patch.dict(CONFIG,{'active_endpoint':'Fixture','active_model':'fixture','stream':False}), \
             patch.object(manager.SESSION,'post',return_value=response) as post, \
             patch.object(manager,'save_settings'),patch.object(manager,'log_model_interaction'),contextlib.redirect_stdout(io.StringIO()):
            answer,_=manager.query_with_fallback([{'role':'user','content':'Start the server'}])
        self.assertTrue(answer.incomplete);post.assert_called_once()


    def test_explicit_stop_avoids_repeated_reasoning_continuations_but_length_still_continues(self):
        from routers.manager import _is_stream_truncated
        reasoning='Inspecting the project and choosing a command. '*20
        self.assertFalse(_is_stream_truncated('stop','',reasoning,{}))
        self.assertFalse(_is_stream_truncated('STOP','',reasoning,{}))
        self.assertTrue(_is_stream_truncated('length','',reasoning,{}))
        self.assertTrue(_is_stream_truncated('','',reasoning,{}))
