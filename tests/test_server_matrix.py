"""Framework-independent startup, late handoff and real served application content."""
import contextlib
import io
import json
import os
from pathlib import Path
import shlex
import shutil
import socket
import sys
import urllib.request
from unittest.mock import patch
import unittest
import test_pipelines as fixtures
from config import CONFIG, AGENT_STATE
from engine import task_runner, cancellation
from engine.command_agent import active_agent
from routers.manager import AgentResponse


class ServerMatrixTests(fixtures.Workspace):
    def setUp(self):
        super().setUp()
        self.manager=task_runner.TaskManager()
        self.manager_patch=patch.object(task_runner,'GLOBAL_TASK_MANAGER',self.manager)
        self.manager_patch.start()

    def tearDown(self):
        for task in list(self.manager.tasks):
            process=task.get('process')
            if process and process.poll() is None:
                self.manager.stop_task(str(process.pid),pid_only=True)
                process.wait(timeout=3)
        cancellation.end_task()
        self.manager_patch.stop()
        super().tearDown()

    @staticmethod
    def port():
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0)); return sock.getsockname()[1]

    def run_service(self, command, url, request=None, expect_initial_worker=True, followup=False):
        import galam
        messages=[{'role':'user','content':request or f'Hi so can u run my web on localhost {self.root} ?'}]
        seen=[]
        def propose(history):
            seen.append(active_agent() is not None)
            self.assertEqual(seen[-1],expect_initial_worker)
            if expect_initial_worker:
                self.assertIn(str(self.root),str(history))
                self.assertIn('Startup metadata',str(history))
            if followup and len(seen)==2:
                check=f'import urllib.request; r=urllib.request.urlopen({url!r},timeout=3); assert r.status==200; print("FOLLOWUP_CHECK_OK")'
                return AgentResponse(tool_calls=[{'id':'check','type':'function','function':{
                    'name':'execute_bash','arguments':json.dumps({'command':f'{shlex.quote(sys.executable)} -c {shlex.quote(check)}'})}}]),'fixture'
            if followup and len(seen)==3:
                self.assertIn('FOLLOWUP_CHECK_OK',str(history))
                return AgentResponse(content='Startup and the follow-up HTTP check completed.'),'fixture'
            return AgentResponse(tool_calls=[{'id':'start','type':'function','function':{
                'name':'execute_bash','arguments':json.dumps({'command':command,'background':True,'readiness_url':url})}}]),'fixture'
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.dict(CONFIG,{'mode':'ALWAYS-PROCEED','command_agent':True,'server_startup_timeout':15}))
            stack.enter_context(patch.object(galam,'lang_workflow_available',return_value=False))
            stack.enter_context(patch.object(galam,'direct_command_for_request',return_value=None))
            query=stack.enter_context(patch.object(galam,'query_with_fallback',side_effect=propose))
            for name in ('render_action_card','render_output_card','format_action_step'):
                stack.enter_context(patch.object(galam,name,return_value=''))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            galam.run_agent_loop(messages,'matrix',max_steps=1)
        self.assertEqual(query.call_count,3 if followup else 1)
        self.assertEqual(AGENT_STATE['task_outcome'],'completed')
        self.assertFalse(AGENT_STATE.get('waiting_for_command_agent'))
        self.assertIsNone(active_agent())
        with urllib.request.urlopen(url,timeout=3) as response:
            return response.read().decode()

    def test_actual_package_build_then_python_service(self):
        if not shutil.which('npm'):self.skipTest('npm is not installed')
        port=self.port()
        (self.root/'compile.cjs').write_text('require("fs").mkdirSync("dist",{recursive:true});require("fs").writeFileSync("dist/index.html","REACT_BUILD_MATRIX");')
        (self.root/'package.json').write_text(json.dumps({'scripts':{
            'build':'node compile.cjs','dev':f'npm run build && {shlex.quote(sys.executable)} -u -m http.server {port} --bind 127.0.0.1 --directory dist'}}))
        body=self.run_service('npm run dev',f'http://127.0.0.1:{port}/')
        self.assertEqual(body,'REACT_BUILD_MATRIX')
        self.assertEqual(len(self.manager.tasks),1)

    def test_plain_python_http_serves_expected_file(self):
        port=self.port();(self.root/'index.html').write_text('PYTHON_MATRIX')
        body=self.run_service(f'{shlex.quote(sys.executable)} -u -m http.server {port} --bind 127.0.0.1',f'http://127.0.0.1:{port}/')
        self.assertEqual(body,'PYTHON_MATRIX')

    def test_ready_server_does_not_skip_separately_requested_checks(self):
        port=self.port();(self.root/'index.html').write_text('FOLLOWUP_MATRIX')
        body=self.run_service(f'{shlex.quote(sys.executable)} -u -m http.server {port} --bind 127.0.0.1',
            f'http://127.0.0.1:{port}/',request=f'Run my web on localhost {self.root} then run checks',followup=True)
        self.assertEqual(body,'FOLLOWUP_MATRIX')
        self.assertEqual(len(self.manager.tasks),1)

    def test_custom_make_command_gets_late_worker_handoff(self):
        if not shutil.which('make'):self.skipTest('make is not installed')
        port=self.port();(self.root/'index.html').write_text('CUSTOM_MATRIX')
        (self.root/'Makefile').write_text(f'preview:\n\t{shlex.quote(sys.executable)} -u -m http.server {port} --bind 127.0.0.1\n')
        body=self.run_service('make preview',f'http://127.0.0.1:{port}/',
                              'Execute the documented preview command',expect_initial_worker=False)
        self.assertEqual(body,'CUSTOM_MATRIX')
        self.assertEqual(self.manager.tasks[0]['agent_name'],'Command agent')

    def test_node_application_uses_actual_route(self):
        if not shutil.which('node'):self.skipTest('node is not installed')
        port=self.port()
        (self.root/'custom-http.cjs').write_text(f'require("http").createServer((req,res)=>{{res.end(req.url==="/app"?"NODE_APP_MATRIX":"other");}}).listen({port},"127.0.0.1");')
        (self.root/'package.json').write_text(json.dumps({'scripts':{'dev':'node custom-http.cjs'}}))
        body=self.run_service('node custom-http.cjs',f'http://127.0.0.1:{port}/app')
        self.assertEqual(body,'NODE_APP_MATRIX')

    def test_real_vite_dev_server_serves_project(self):
        vite=os.environ.get('GALAM_TEST_VITE')
        if not vite or not Path(vite).is_file():self.skipTest('set GALAM_TEST_VITE to an installed Vite entrypoint')
        port=self.port();(self.root/'index.html').write_text('<h1>VITE_MATRIX</h1>')
        modules=Path(vite).resolve().parents[2]
        react=modules/'react'
        if react.is_dir():
            (self.root/'node_modules').symlink_to(modules,target_is_directory=True)
            (self.root/'index.html').write_text('<div id="root"></div><script type="module" src="/main.jsx"></script>VITE_MATRIX')
            (self.root/'main.jsx').write_text('import React from "react"; import {createRoot} from "react-dom/client"; createRoot(document.getElementById("root")).render(<h1>REACT_VITE_MATRIX</h1>);')
        (self.root/'package.json').write_text(json.dumps({'scripts':{'dev':f'node {shlex.quote(vite)} --host 127.0.0.1 --port {port} --strictPort'}}))
        self.assertIn('VITE_MATRIX',self.run_service('npm run dev',f'http://127.0.0.1:{port}/'))
        if react.is_dir():
            with urllib.request.urlopen(f'http://127.0.0.1:{port}/main.jsx',timeout=5) as response:
                module=response.read().decode()
            self.assertIn('REACT_VITE_MATRIX',module)
            self.assertIn('createRoot',module)
            self.assertNotIn('render(<h1>',module)

    def test_flask_framework_application_serves_expected_route(self):
        folder=os.environ.get('GALAM_TEST_FLASK_PATH')
        if not folder or not Path(folder,'flask').is_dir():self.skipTest('set GALAM_TEST_FLASK_PATH to Flask installation')
        port=self.port()
        (self.root/'webapp.py').write_text('from flask import Flask\napp=Flask(__name__)\n@app.route("/app")\ndef home(): return "FLASK_MATRIX"\n')
        command=f'PYTHONPATH={shlex.quote(folder)} {shlex.quote(sys.executable)} -u -m flask --app webapp run --host 127.0.0.1 --port {port}'
        self.assertEqual(self.run_service(command,f'http://127.0.0.1:{port}/app'),'FLASK_MATRIX')
