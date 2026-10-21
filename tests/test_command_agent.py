"""Delegated startup/repair with real local tools, shared accounting and dock state."""
import contextlib
import io
import json
import os
from pathlib import Path
import socket
import sys
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import test_pipelines as fixtures
from config import CONFIG, AGENT_STATE
from engine import task_runner, usage, cancellation
from engine.command_agent import CommandAgent, active_agent, request_profile, dock_tokens, should_delegate
from engine.action_results import ActionResult
from routers import manager


class CommandAgentTests(fixtures.Workspace):
    def setUp(self):
        super().setUp()
        self.manager = task_runner.TaskManager()
        self.manager_patch = patch.object(task_runner, 'GLOBAL_TASK_MANAGER', self.manager)
        self.manager_patch.start()
        self.agent_state = patch.dict(AGENT_STATE, {'active_subagents':[], 'waiting_for_command_agent':False})
        self.agent_state.start()
        usage.start_task('command-fixture')

    def tearDown(self):
        for task in list(self.manager.tasks):
            process = task.get('process')
            if process and process.poll() is None:
                self.manager.stop_task(str(process.pid), pid_only=True)
                process.wait(timeout=3)
        cancellation.end_task()
        self.agent_state.stop()
        self.manager_patch.stop()
        super().tearDown()

    def service(self, broken=False, build=False):
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0)); port=sock.getsockname()[1]
        source=('BROKEN =\n' if broken else '') + (
            ('import time\nprint("> fixture@1.0 build", flush=True)\ntime.sleep(1.5)\nprint("> fixture@1.0 start", flush=True)\n' if build else '') +
            'from http.server import HTTPServer, BaseHTTPRequestHandler\n'
            'class Handler(BaseHTTPRequestHandler):\n def do_GET(self):\n  self.send_response(200); self.end_headers(); self.wfile.write(b"ready")\n'
            f'server=HTTPServer(("127.0.0.1",{port}),Handler)\n'
            f'print("http://127.0.0.1:{port}/health",flush=True)\nserver.serve_forever()\n')
        (self.root/'service_task.py').write_text(source)
        command=f'{sys.executable} -u service_task.py'
        (self.root/'package.json').write_text(json.dumps({'scripts':{'start':command}}))
        return command, f'http://127.0.0.1:{port}/health'

    @staticmethod
    def response(tool, args):
        return manager.AgentResponse(content='',tool_calls=[{'id':str(time.time_ns()),'type':'function',
            'function':{'name':tool,'arguments':json.dumps(args)}}]), 'fixture-model'

    def run_job(self, query, messages=None, **settings):
        import galam
        from ui import dock_layout
        messages = messages if messages is not None else [{'role':'user','content':'Start the project server'}]
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.dict(CONFIG,{'mode':'ALWAYS-PROCEED','command_agent':True,
                'active_endpoint':'Fixture','active_model':'fixture-model', **settings}))
            stack.enter_context(patch.object(galam,'lang_workflow_available',return_value=False))
            stack.enter_context(patch.object(galam,'direct_command_for_request',return_value=None))
            model=stack.enter_context(patch.object(galam,'query_with_fallback',side_effect=query))
            stack.enter_context(patch.object(dock_layout,'GLOBAL_TASK_MANAGER',self.manager))
            for name in ('render_action_card','render_output_card','format_action_step'):
                stack.enter_context(patch.object(galam,name,return_value=''))
            display=io.StringIO()
            stack.enter_context(contextlib.redirect_stdout(display))
            galam.run_agent_loop(messages,'command-fixture',max_steps=2)
        return messages, model, display.getvalue()

    def test_same_model_worker_owns_startup_parent_waits_and_no_ack_request(self):
        command,url=self.service()
        messages=[{'role':'user','content':'Never delete KEEP_ME'},
                  {'role':'assistant','content':'UNRELATED_OLD_CHAT'},
                  {'role':'user','content':'Start the project server'}]
        def query(history):
            self.assertIsNotNone(active_agent())
            self.assertTrue(AGENT_STATE['waiting_for_command_agent'])
            self.assertEqual(request_profile(),{'endpoint':'Fixture','model':'fixture-model'})
            self.assertNotIn('UNRELATED_OLD_CHAT',str(history))
            self.assertIn('Never delete KEEP_ME',str(history))
            self.assertIn('Command agent', ''.join(text for _,text in dock_tokens(44)))
            row=usage.begin_request('Fixture','fixture-model',{'messages':history})
            row.update(input_tokens=80,output_tokens=20,total_tokens=100)
            return self.response('execute_bash',{'command':command,'background':True,'readiness_url':url})
        messages,model,output=self.run_job(query,messages)
        self.assertEqual(model.call_count,1)
        self.assertEqual(usage.totals()['total'],100)
        self.assertEqual(messages[-1]['_galam_response_stats']['usage']['requests'],1)
        self.assertEqual(output.count('Worked for'),1)
        self.assertIn('Running',messages[-1]['content'])
        self.assertFalse(AGENT_STATE['waiting_for_command_agent'])
        self.assertFalse(AGENT_STATE['active_subagents'])
        self.assertIsNone(active_agent())
        self.assertTrue(self.manager.tasks[0]['process'].poll() is None)
        self.assertIn('Command agent', ''.join(text for _,text in self.manager.get_task_pill_tokens()))
        self.assertIn('running', ''.join(text for _,text in self.manager.get_task_pill_tokens()))
        self.assertNotIn('You are Galam\'s Command Agent',str(messages))

    def test_worker_repairs_real_syntax_failure_checks_and_starts_service(self):
        command,url=self.service(broken=True)
        keep=self.root/'KEEP_ME';keep.write_text('untouched user content')
        calls=0
        def query(history):
            nonlocal calls
            calls+=1
            usage.begin_request('Fixture','fixture-model',{'messages':history}).update(
                input_tokens=80,output_tokens=20,total_tokens=100)
            if calls==1:return self.response('view_file',{'path':'package.json'})
            if calls==2:return self.response('execute_bash',{'command':command,'background':True,'readiness_url':url})
            if calls==3:
                self.assertIn('SyntaxError',str(history))
                return self.response('view_file',{'path':'service_task.py'})
            if calls==4:return self.response('replace_file_content',{'path':'service_task.py','target_content':'BROKEN =\n','replacement_content':'BROKEN = True\n'})
            if calls==5:return self.response('execute_bash',{'command':command,'background':True,'readiness_url':url})
            self.fail('No extra acknowledgment or repeated startup is needed')
        messages,model,_=self.run_job(query)
        self.assertEqual(model.call_count,5)
        self.assertEqual(AGENT_STATE['task_outcome'],'completed')
        self.assertEqual(usage.totals()['total'],500)
        self.assertEqual(keep.read_text(),'untouched user content')
        self.assertTrue((self.root/'service_task.py').read_text().startswith('BROKEN = True'))
        self.assertIn('Running',messages[-1]['content'])
        self.assertEqual(len(self.manager.get_active_tasks()),1)

    def test_build_wait_is_local_and_visible_below_input(self):
        from engine.process_runner import launch_process
        from ui import dock_layout
        command,url=self.service(build=True)
        stages=[]
        agent=CommandAgent([{'role':'user','content':'Run the server'}],'build',CONFIG)
        original=agent.update
        def update(stage,detail=''):
            original(stage,detail)
            stages.append(stage)
            if stage=='Building':
                with patch.object(dock_layout,'GLOBAL_TASK_MANAGER',self.manager):
                    text=''.join(value for _,value in dock_layout.get_bottom_toolbar_tokens())
                self.assertIn('Command agent · Building',text)
                self.assertNotIn('running\n',text)
        with agent, patch.object(agent,'update',side_effect=update),patch.dict(CONFIG,{'server_build_timeout':6}):
            result=launch_process(command,str(self.root),task_runner.get_subprocess_env(),self.manager,url,timeout=1)
        self.assertTrue(result.success,result.output)
        self.assertIn('Building',stages)
        self.assertEqual(usage.ledger()['requests'],[])

    def test_failure_clears_agent_status_and_preserves_conversation_output(self):
        def query(history):
            raise RuntimeError('fixture transport failure')
        messages,_,_=self.run_job(query)
        self.assertFalse(AGENT_STATE['waiting_for_command_agent'])
        self.assertFalse(AGENT_STATE['active_subagents'])
        self.assertFalse(AGENT_STATE['task_active'])
        self.assertEqual(AGENT_STATE['task_outcome'],'failed')
        self.assertFalse(self.manager.get_active_tasks())

    def test_profile_and_side_agent_are_preserved_without_global_mutation(self):
        other={'id':'btw-fixture','task':'btw'}
        AGENT_STATE['active_subagents'].append(other)
        messages=[{'role':'user','content':'Start a server'}]
        with patch.dict(CONFIG,{'active_endpoint':'first','active_model':'same'}):
            with CommandAgent(messages,'scope',CONFIG) as agent:
                CONFIG['active_endpoint']='different'
                CONFIG['active_model']='other'
                self.assertEqual(request_profile(),{'endpoint':'first','model':'same'})
                independent=[]
                thread=threading.Thread(target=lambda:independent.append(request_profile()))
                thread.start();thread.join()
                self.assertEqual(independent,[{}])
        self.assertEqual(AGENT_STATE['active_subagents'],[other])

    def test_unverified_process_is_not_labeled_running(self):
        proc=SimpleNamespace(poll=lambda:None)
        self.manager.tasks=[{'id':1,'pid':os.getpid(),'process':proc,'cmd':'service',
                            'state':'running_unverified','start_time':'12:00'}]
        text=''.join(value for _,value in self.manager.get_task_pill_tokens())
        self.assertIn('unverified',text)
        self.assertNotIn('running',text)
        self.manager.tasks.clear()

    def test_rollback_flag_uses_original_loop(self):
        def query(history):
            self.assertIsNone(active_agent())
            return manager.AgentResponse(content='Which project should I run?'),'fixture'
        self.run_job(query, command_agent=False)

    def test_worker_checkpoint_is_synced_into_parent_without_duplicate_rows(self):
        from storage.conversations import save_history,load_history
        messages=[{'role':'user','content':'Start a server'}]
        location=str(Path(self.temp.name)/'history')
        with CommandAgent(messages,'save',CONFIG,lambda:save_history(location,'save',messages)) as agent:
            agent.messages.append({'role':'assistant','content':'Observed a real error'})
            agent.flush();agent.flush()
            saved=load_history(location,'save')
            self.assertEqual(sum(m.get('content')=='Observed a real error' for m in saved),1)
        self.assertEqual(len(messages),2)

    def test_cancel_during_startup_reaps_owned_process_and_releases_parent(self):
        command,url=self.service(build=True)
        path=self.root/'service_task.py'
        path.write_text(path.read_text().replace('time.sleep(1.5)','time.sleep(30)'))
        timer=None
        def query(history):
            nonlocal timer
            timer=threading.Timer(.6,cancellation.cancel_current)
            timer.start()
            return self.response('execute_bash',{'command':command,'background':True,'readiness_url':url})
        try:
            self.run_job(query)
            self.assertFalse(AGENT_STATE['waiting_for_command_agent'])
            self.assertFalse(AGENT_STATE['active_subagents'])
            self.assertFalse(AGENT_STATE['task_active'])
            self.assertFalse(self.manager.get_active_tasks())
            self.assertEqual(AGENT_STATE['task_outcome'],'cancelled')
        finally:
            if timer:timer.cancel();timer.join()

    def test_router_pins_profile_without_changing_settings_or_falling_back(self):
        from storage.endpoints import ENDPOINT_REGISTRY
        from engine.payload_builder import build_request_data
        history=[{'role':'user','content':'Start the project server'}]
        registry={'Fixture':{'base_url':'http://fixture.invalid/v1','models':{
            'fixture-model':{'context_window':8192},'other-model':{'context_window':8192}}}}
        for success in (True,False):
            response=SimpleNamespace(ok=success,status_code=200 if success else 500,
                headers={'Content-Type':'application/json'},close=lambda:None,text='fixture error',
                json=lambda:{'choices':[{'message':{'content':'Startup needs an entrypoint.'}}]})
            with contextlib.ExitStack() as stack:
                stack.enter_context(patch.dict(ENDPOINT_REGISTRY,registry))
                stack.enter_context(patch.dict(CONFIG,{'active_endpoint':'Fixture','active_model':'fixture-model',
                    'stream':False,'max_tokens':128,'fallback_policy':'auto'}))
                stack.enter_context(CommandAgent(history,'router',CONFIG))
                CONFIG.update(active_endpoint='Direct',active_model='different')
                post=stack.enter_context(patch.object(manager,'_post_with_transport_retry',return_value=(response,False)))
                build=stack.enter_context(patch.object(manager,'build_request_data',wraps=build_request_data))
                save=stack.enter_context(patch.object(manager,'save_settings'))
                stack.enter_context(patch.object(manager,'StructuredThoughtStreamHandler'))
                stack.enter_context(patch.object(manager,'log_model_interaction'))
                stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
                result,model=manager.query_with_fallback(history)
                self.assertEqual(model,'fixture-model')
                self.assertEqual(post.call_count,1)
                self.assertEqual(build.call_args.kwargs['endpoint_name'],'Fixture')
                self.assertEqual(build.call_args.kwargs['model_name'],'fixture-model')
                save.assert_not_called()
                if not success:self.assertEqual(result,'FAILED')

    def test_repeated_failure_does_not_renew_workflow_progress(self):
        workflow=SimpleNamespace(new_in_round=False)
        usage.ledger()['_workflow']=workflow
        with CommandAgent([{'role':'user','content':'Start a server'}],'failure',CONFIG) as agent:
            args={'command':'npm run dev'}
            agent.observe('execute_bash',args,ActionResult('Error: PID 123 failed',False,1))
            self.assertTrue(workflow.new_in_round)
            workflow.new_in_round=False
            agent.observe('execute_bash',args,ActionResult('Error: PID 456 failed',False,1))
            self.assertFalse(workflow.new_in_round)

    def test_routing_handles_plain_commands_without_executing_questions_or_negations(self):
        for request in ('Run npm run dev', 'Please execute python -m http.server 8000',
                        'Start the project server', 'Run `pnpm dev`'):
            self.assertTrue(should_delegate(request),request)
        for request in ('Explain npm run dev', 'Never run npm run dev; inspect package.json',
                        'Do not start the server', 'Read package.json', 'Run npm run build'):
            self.assertFalse(should_delegate(request),request)
