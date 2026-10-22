"""Choose actual launch evidence and accept verified external readiness honestly."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request
from unittest.mock import patch

import test_pipelines as fixtures
from engine import task_runner, usage
from engine.action_results import ActionResult
from engine.command_agent import CommandAgent
from engine.process_runner import launch_process
from engine.service_observation import observe_existing, reuse_allowed
from engine.task_evidence import package_evidence, render_evidence
from engine.task_progress import TaskProgress
from engine.tool_parser import validate_shell_command
from config import CONFIG


class StartupCorrectnessTests(fixtures.Workspace):
    response = fixtures.LoopTests.response
    run_loop = fixtures.LoopTests.run_loop

    def setUp(self):
        super().setUp()
        self.processes = []
        self.manager = task_runner.TaskManager()
        self.asset = 'window.PROJECT_ID = "correct-project-unique-marker";'
        (self.root/'app.js').write_text(self.asset)
        (self.root/'index.html').write_text('<!doctype html><title>Correct project</title><script src="/app.js"></script>')

    def tearDown(self):
        self.manager.stop_all()
        for p in self.processes:
            p.terminate()
            p.wait(timeout=4)
            p.stdout.close()
        super().tearDown()

    def external(self, directory=None):
        directory = directory or self.root
        script = ('from http.server import ThreadingHTTPServer,SimpleHTTPRequestHandler\n'
                  's=ThreadingHTTPServer(("127.0.0.1",0),SimpleHTTPRequestHandler)\n'
                  'print(f"http://127.0.0.1:{s.server_port}/",flush=True)\ns.serve_forever()\n')
        p = subprocess.Popen([sys.executable,'-u','-c',script],cwd=directory,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True)
        self.processes.append(p)
        return p, p.stdout.readline().strip()

    def test_matches_project_and_asset_without_adopting_external_process(self):
        p,url = self.external()
        result = observe_existing(url,self.root)
        self.assertEqual(result['matched_files'],['index.html','app.js'])
        self.assertFalse(result['owned'])
        with CommandAgent([{'role':'user','content':'Run my server'}],'external',CONFIG):
            result = launch_process('echo MUST_NOT_RUN',self.root,dict(os.environ),self.manager,url)
        self.assertTrue(result.success)
        self.assertTrue(result.verified)
        self.assertFalse(result.is_background)
        self.assertFalse(self.manager.tasks)
        self.assertNotIn('pid',result.metadata)
        self.assertIsNone(p.poll())
        self.assertIn('proposed command was not executed',result.output)
        self.assertFalse(self.manager.stop_task(str(p.pid),pid_only=True))
        self.assertIsNone(p.poll())

    def test_legacy_inspection_without_path_metadata_does_not_break_verification(self):
        _,url=self.external()
        self.assertIsNotNone(observe_existing(url,self.root,[None,'']))
        inspected=task_runner.execute_tool_call('view_file',{'path':'index.html'})
        self.assertEqual(inspected.metadata['path'],str(self.root/'index.html'))
        messages=[{'role':'user','content':'Run my server'}, {'role':'user','content':'Read README',
                  '_galam_action':{'tool':'view_file','status':'completed','path':None}}]
        with CommandAgent(messages,'legacy',CONFIG):
            result=launch_process('echo MUST_NOT_RUN',self.root,dict(os.environ),self.manager,url)
        self.assertTrue(result.success,result.output)

    def test_unrelated_http_200_is_not_project_readiness(self):
        other=self.root/'other';other.mkdir();(other/'index.html').write_text('OTHER_APP')
        p,url=self.external(other)
        self.assertIsNone(observe_existing(url,self.root))
        with CommandAgent([{'role':'user','content':'Run my server'}],'unrelated',CONFIG):
            result=launch_process('echo MUST_NOT_RUN',self.root,dict(os.environ),self.manager,url)
        self.assertFalse(result.success)
        self.assertIn('unowned',result.output)
        self.assertFalse(self.manager.tasks)
        self.assertIsNone(p.poll())

    def test_matching_html_with_wrong_asset_is_rejected(self):
        other=self.root/'other';other.mkdir()
        (other/'index.html').write_bytes((self.root/'index.html').read_bytes())
        (other/'app.js').write_text('WRONG_BUNDLE')
        _,url=self.external(other)
        self.assertIsNone(observe_existing(url,self.root))

    def test_later_broken_script_cannot_hide_behind_a_matching_stylesheet(self):
        other=self.root/'other';other.mkdir()
        html='<link rel="stylesheet" href="/style.css"><script src="/app.js"></script>'
        for folder in (self.root,other):
            (folder/'index.html').write_text(html)
            (folder/'style.css').write_text('body{color:green}')
        (other/'app.js').write_text('WRONG_BUNDLE')
        _,url=self.external(other)
        self.assertIsNone(observe_existing(url,self.root))

    def test_public_output_directory_can_match_without_framework_rules(self):
        output=self.root/'custom-web-output';output.mkdir()
        (output/'index.html').write_bytes((self.root/'index.html').read_bytes())
        (output/'app.js').write_text(self.asset)
        (self.root/'index.html').unlink()
        _,url=self.external(output)
        self.assertEqual(observe_existing(url,self.root,[output/'index.html'])['matched_files'],['custom-web-output/index.html','custom-web-output/app.js'])

    def test_cannot_reuse_for_restart_build_or_exact_command(self):
        for goal in ('Restart my server','Rebuild then run my server','Run `npm run dev`', 'Run npm run dev', 'Run "npm run dev"', 'Run python -u server.py', 'Execute "node app.js"', 'Start another server'):
            self.assertFalse(reuse_allowed(goal),goal)
        _,url=self.external()
        with CommandAgent([{'role':'user','content':'Restart my server'}],'restart',CONFIG):
            result=launch_process('echo MUST_NOT_RUN',self.root,dict(os.environ),self.manager,url)
        self.assertFalse(result.success)
        self.assertFalse(self.manager.tasks)

    def test_atomic_startup_finishes_in_one_model_request_when_already_verified(self):
        p,url=self.external()
        with patch.object(task_runner,'GLOBAL_TASK_MANAGER',self.manager), patch.dict(CONFIG,{'command_agent':True}):
            messages,_=self.run_loop([self.response('execute_bash',{'command':'python -u server.py','background':True,'readiness_url':url})],
                                     f'Run my server located in {self.root}')
        self.assertEqual(self.model_calls,1)
        self.assertIn('Already running',messages[-1]['content'])
        self.assertIn('external service',messages[-1]['content'])
        self.assertFalse(self.manager.tasks)
        self.assertIsNone(p.poll())

    def test_existing_readiness_does_not_skip_separately_requested_checks(self):
        p,url=self.external()
        with patch.object(task_runner,'GLOBAL_TASK_MANAGER',self.manager), patch.dict(CONFIG,{'command_agent':True}):
            messages,spy=self.run_loop([
                self.response('execute_bash',{'command':'python -u server.py','background':True,'readiness_url':url}),
                self.response('execute_bash',{'command':'printf CHECK_PASSED'}),
                self.response(text='Already running; the separately requested check printed CHECK_PASSED.')],
                f'Start the server in {self.root} then run a check that prints CHECK_PASSED.')
        self.assertEqual(self.model_calls,3)
        self.assertIn('CHECK_PASSED',messages[-1]['content'])
        self.assertEqual(spy.call_count,2)
        self.assertIsNone(p.poll())

    def test_evidence_does_not_turn_default_start_into_a_mandate(self):
        scripts={'start':'python custom.py','dev':'npm run build && npm start','build':'node build.cjs'}
        e=package_evidence('view_file',{'path':'package.json'},json.dumps({'scripts':scripts}),self.root,'Run my server','source')
        self.assertIn('candidate',e['selection'])
        self.assertEqual(e['alternatives']['dev'],scripts['dev'])
        explicit=package_evidence('view_file',{'path':'package.json'},json.dumps({'scripts':scripts}),self.root,'Run npm run dev','source')
        self.assertEqual(explicit['script'],'dev')
        self.assertEqual(explicit['selection'],'explicit user command')

    def test_actual_attempt_and_verified_readiness_survive_recovery(self):
        usage.start_task('observations')
        messages=[{'role':'user','content':'Run my server'}]
        flow=TaskProgress(messages,'Run my server',self.root,{},8)
        command='npm run build && npm start'
        flow.observe('execute_bash',{'command':command,'background':True},ActionResult('already verified',verified=True,metadata={
            'startup_satisfied':True,'existing_service':True,'url':'http://127.0.0.1:41001/','owned':False}),self.root)
        flow.pending_recovery=True
        text=flow.recovery()['content']
        self.assertIn('Readiness is already verified',text)
        self.assertNotIn('to launch it',text)
        self.assertIn(command,render_evidence(flow.state,900))
        from engine.context_manager import assemble_context
        messages.append(flow.recovery() or {'role':'user','_galam_internal':True,'_galam_guidance':'recovery','content':text})
        selected,_=assemble_context(messages,'Galam','Fixture','small',{'context_window':8192,'max_tokens':1024},str(self.root))
        self.assertIn('Readiness is already verified',str(selected))

    def test_stdin_redirection_is_not_a_placeholder(self):
        for command in ("tr '\\0' ' ' < /proc/123/cmdline; echo done",'cat < ./input.txt > ./output.txt'):
            self.assertTrue(validate_shell_command(command)[0],command)
        for command in ('cd <project> && npm run dev','cat <your project path>'):
            self.assertFalse(validate_shell_command(command)[0],command)

    def test_startup_hints_use_observed_configuration_without_fixed_port_defaults(self):
        from engine.startup_discovery import discover
        (self.root/'package.json').write_text(json.dumps({'scripts':{'dev':'npm run build && npm start',
            'start':'PORT=${PORT:-41937} python -u server.py','build':'node build.cjs'}}))
        with patch.dict(os.environ,{'PORT':'41731'}):
            data=discover('Run my server',str(self.root))
        self.assertIn('http://127.0.0.1:41731/',data['content'])
        self.assertNotIn('http://127.0.0.1:41937/',data['content'])
        with patch.dict(os.environ,{'PORT':''}):
            data=discover('Run my server',str(self.root))
        self.assertIn('http://127.0.0.1:41937/',data['content'])

    def test_runtime_temporary_path_is_supplied(self):
        import tempfile
        from engine.startup_discovery import discover
        data=discover('Run my server',str(self.root))
        self.assertIn(tempfile.gettempdir(),data['content'])
