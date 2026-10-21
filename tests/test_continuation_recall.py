"""Real failure shapes: startup intent, bounded request context and task continuation."""
import copy
import json
from types import SimpleNamespace
from unittest.mock import patch

import test_pipelines as fixtures
from config import DEFAULT_VALUES
from engine import usage
from engine.command_agent import should_delegate
from engine.context_manager import assemble_context, estimate_tokens
from engine.server_commands import server_start_requested
from engine.task_progress import TaskProgress
from engine.tool_parser import validate_shell_command
from engine.task_runner import parse_and_run_curl_command


class ContinuationRecallTests(fixtures.Workspace):
    def assemble(self, messages):
        return assemble_context(messages,'You are Galam. Preserve user code.','Fixture','small',
            {'context_window':4096,'max_tokens':1024,'_tool_schema_tokens':1038,'_simple_filesystem':True},str(self.root))

    def test_actual_localhost_wording_enters_startup_workflow(self):
        for request in ('Hi so can u run my web on localhost /tmp/project ?',
                        'Bring up the development preview for this app',
                        'Please start my React app', 'Run this Python project on localhost'):
            self.assertTrue(server_start_requested(request),request)
            self.assertTrue(should_delegate(request),request)
        for request in ('Explain how to run my web on localhost', 'Do not run this app on localhost',
                        'What is a localhost server?', 'Read the server instructions'):
            self.assertFalse(should_delegate(request),request)

    def test_native_batch_and_recovery_fit_small_window_without_mutating_history(self):
        messages=[{'role':'user','content':'Run this web project on localhost. Never delete user files.'}]
        for n in range(5):
            messages += [{'role':'assistant','content':'','tool_calls':[
                {'id':f'{n}-a','type':'function','function':{'name':'read_action_output','arguments':json.dumps({'action_id':'a'*32})}},
                {'id':f'{n}-b','type':'function','function':{'name':'view_file','arguments':'{"path":"package.json"}'}}]},
                {'role':'tool','tool_call_id':f'{n}-a','content':'archive listing\n'+'file.js\n'*160},
                {'role':'tool','tool_call_id':f'{n}-b','content':'package scripts: '+json.dumps({'dev':'npm run build && npm start','start':'PORT=${PORT:-5501} python3 -u server.py'})},
                {'role':'user','_galam_internal':True,'content':'[Workflow recovery] '+('Do not repeat unchanged inspections. '*30)}]
        original=copy.deepcopy(messages)
        result,metrics=self.assemble(messages)
        self.assertLessEqual(metrics['input_tokens'],metrics['input_budget'])
        self.assertIn('Never delete user files.',json.dumps(result))
        self.assertIn('PORT=${PORT:-5501}',json.dumps(result))
        calls={c['id'] for m in result for c in m.get('tool_calls',[])}
        replies={m['tool_call_id'] for m in result if m.get('role')=='tool'}
        self.assertEqual(calls,replies)
        self.assertEqual(messages,original)

    def test_worker_role_and_multi_tool_recovery_fit_four_k_with_rules_intact(self):
        from engine.command_agent import SYSTEM
        from engine.action_instructions import response_contract
        messages=[{'role':'system','content':SYSTEM,'_galam_generated_system':True},
                  {'role':'system','content':'Never change the settings UI.'},
                  {'role':'user','content':'Run this app on localhost.'}]
        for n in range(4):
            messages += [{'role':'assistant','tool_calls':[
                {'id':f'{n}a','type':'function','function':{'name':'read_action_output','arguments':json.dumps({'action_id':'a'*32})}},
                {'id':f'{n}b','type':'function','function':{'name':'view_file','arguments':'{"path":"package.json"}'}}]},
                {'role':'tool','tool_call_id':f'{n}a','content':'earlier archived transcript\n'+'listing\n'*250},
                {'role':'tool','tool_call_id':f'{n}b','content':'{"scripts":{"dev":"npm run build && npm start","start":"PORT=${PORT:-5501} python3 -u server.py"}}'},
                {'role':'user','_galam_internal':True,'content':'[Workflow recovery] '+('Use retained launch evidence. '*40)}]
        result,metrics=assemble_context(messages,'You are Galam. '+response_contract(True),
            'Fixture','small',{'context_window':4096,'max_tokens':1024,'_tool_schema_tokens':1118,'_simple_filesystem':True},str(self.root))
        self.assertLessEqual(metrics['input_tokens'],metrics['input_budget'])
        self.assertIn('Never change the settings UI.',json.dumps(result))
        self.assertIn('PORT=${PORT:-5501}',json.dumps(result))

    def test_research_followup_recalls_original_findings_without_unrelated_turn(self):
        messages=[{'role':'user','content':'Research Northbridge College'},
            {'role':'user','_galam_action':{'id':'a'*32,'status':'completed','tool':'execute_bash'},
             'content':'[Tool Observation for `execute_bash`]:\nNorthbridge College official campus: https://college.example/about\n'+'Unrelated page boilerplate\n'*800},
            {'role':'assistant','content':'Inspecting the search helper: '+('source code '*2500)},
            {'role':'user','content':'Discuss planets'}, {'role':'assistant','content':'JUPITER_UNRELATED '+('planet '*2000)},
            {'role':'user','content':'Based on your research, what is Northbridge College?'}]
        result,metrics=self.assemble(messages)
        text=json.dumps(result)
        self.assertIn('https://college.example/about',text)
        self.assertNotIn('JUPITER_UNRELATED',text)
        self.assertLessEqual(estimate_tokens(result),metrics['input_budget'])

    def test_recall_is_conditional_scoped_and_redacted(self):
        from engine.conversation_recall import recall
        rows=[{'role':'user','content':'Research Northbridge College','_galam_conversation_id':'one'},
              {'role':'tool','content':'Northbridge College official https://college.example/about','_galam_conversation_id':'one'},
              {'role':'user','content':'Research Secretland','_galam_conversation_id':'other'},
              {'role':'tool','content':'FOREIGN https://secret.example','_galam_conversation_id':'other'},
              {'role':'user','content':'Based on your research, describe Northbridge College','_galam_conversation_id':'one'}]
        text=recall(rows,rows[-1]['content'],400,estimate_tokens)
        self.assertIn('college.example',text)
        self.assertNotIn('FOREIGN',text)
        self.assertEqual(recall(rows,'Hello',400,estimate_tokens),'')
        short=[{'role':'user','content':'Do you know Northbridge College?'},
               {'role':'assistant','content':'Which details are you interested in?'},
               {'role':'user','content':'Search it on web'}]
        self.assertIn('Northbridge College',recall(short,short[-1]['content'],300,estimate_tokens))

    def test_research_evidence_follows_an_unresolved_search_reference(self):
        from engine.conversation_recall import recall
        rows=[{'role':'user','content':'Do you know Northbridge College?'},
              {'role':'assistant','content':'What is Northbridge College?'},
              {'role':'user','content':'Search it on web'},
              {'role':'user','_galam_action':{'tool':'view_file','id':'a'*32},
               'content':'File: search.py\nquery="Northbridge College"\nurl="https://search.example/"'},
              {'role':'user','_galam_action':{'tool':'execute_bash','id':'b'*32},
               'content':'QUERY: Northbridge College\n---------------------\n1. Official campus\nhttps://nb.example/about'},
              {'role':'user','content':'Based on your research what is Northbridge College?'}]
        text=recall(rows,rows[-1]['content'],220,estimate_tokens)
        self.assertIn('https://nb.example/about',text)
        self.assertNotIn('search.py',text)

    def test_recall_budget_includes_json_escaping_and_system_message_envelope(self):
        rows=[{'role':'user','content':'Research Northbridge College'},
              {'role':'user','content':'Search it on web'},
              {'role':'user','_galam_action':{'id':'a'*32,'tool':'execute_bash','status':'completed'},
               'content':'QUERY: Northbridge College\n'+ '\n'.join(f'Northbridge College finding {n}: "quoted result" https://nb.example/{n}' for n in range(200))},
              {'role':'assistant','content':'Archived source helper '+('source '*4000)},
              {'role':'user','content':'Based on your research describe Northbridge College.'}]
        selected,metrics=self.assemble(rows)
        self.assertLessEqual(estimate_tokens(selected),metrics['input_budget'])
        self.assertIn('https://nb.example/',str(selected))

    def test_command_recall_requires_a_matching_execution_observation(self):
        from engine.conversation_recall import recall
        rows=[{'role':'user','content':'Run a terminal check'},
              {'role':'assistant','_galam_tool_calls':[{'id':'ran','function':{'name':'execute_bash','arguments':'{"command":"printf verified-command"}'}}]},
              {'role':'user','content':'verified-command','_galam_call_id':'ran',
               '_galam_action':{'id':'a'*32,'tool':'execute_bash','status':'completed'}},
              {'role':'assistant','_galam_tool_calls':[{'id':'pending','function':{'name':'execute_bash','arguments':'{"command":"UNEXECUTED_COMMAND"}'}}]},
              {'role':'user','content':'What command did you run earlier?'}]
        text=recall(rows,rows[-1]['content'],300,estimate_tokens)
        self.assertIn('printf verified-command',text)
        self.assertNotIn('UNEXECUTED_COMMAND',text)

    def test_old_generated_caps_migrate_but_explicit_caps_remain(self):
        import config
        path=self.root/'settings.json'
        path.write_text(json.dumps({'active_endpoint':'Fixture','context_window':4096,
                                    'task_request_limit':128,'task_token_limit':120000}))
        with patch.object(config,'CONFIG_CANDIDATES',[str(path)]):
            migrated=config.load_config()
        self.assertIsNone(migrated.get('task_request_limit'))
        self.assertIsNone(migrated.get('context_window'))
        path.write_text(json.dumps({'active_endpoint':'Fixture','context_window':4096,
            'context_limit_explicit':True,'task_request_limit':7,'explicit_task_limits':['task_request_limit']}))
        with patch.object(config,'CONFIG_CANDIDATES',[str(path)]):
            migrated=config.load_config()
        self.assertEqual(migrated.get('task_request_limit'),7)
        self.assertEqual(migrated.get('context_window'),4096)

    def test_current_session_id_is_local_without_a_provider_call(self):
        import galam
        messages=[{'role':'user','content':'what is my current conversation id'}]
        with patch.object(galam,'query_with_fallback') as model:
            galam.run_agent_loop(messages,'session-fixture')
        model.assert_not_called()
        self.assertIn('session-fixture',messages[-1]['content'])

    def test_full_request_guard_uses_connection_capacity(self):
        from engine.context_manager import validate_request_capacity,ContextLimitError
        from storage.endpoints import ENDPOINT_REGISTRY
        with patch.dict(ENDPOINT_REGISTRY,{'Fixture':{'models':{'small':{'context_window':4096}}}}):
            with self.assertRaises(ContextLimitError):
                validate_request_capacity({'messages':[{'role':'user','content':'x'*18000}]},
                    'Fixture','small',{'max_tokens':1024,'context_window':32000})
            validate_request_capacity({'messages':[{'role':'user','content':'A small task'}],'max_tokens':1024},
                    'Fixture','small',{'max_tokens':1024})

    def test_output_continues_past_old_three_continuation_cap(self):
        import contextlib,io
        from config import CONFIG
        from routers import manager
        from storage.endpoints import ENDPOINT_REGISTRY
        def response(n):
            return SimpleNamespace(ok=True,status_code=200,headers={'Content-Type':'application/json'},close=lambda:None,
                json=lambda:{'choices':[{'message':{'content':f'piece{n}'},'finish_reason':'stop' if n==5 else 'length'}]})
        with patch.dict(CONFIG,{'active_endpoint':'Fixture','active_model':'small','stream':False,'max_tokens':128}), \
             patch.dict(ENDPOINT_REGISTRY,{'Fixture':{'base_url':'http://fixture.invalid/v1','models':{'small':{'context_window':8192}}}}), \
             patch.object(manager,'_post_with_transport_retry',return_value=(response(0),False)), \
             patch.object(manager,'_recorded_post',side_effect=[response(n) for n in range(1,6)]) as post, \
             patch.object(manager,'StructuredThoughtStreamHandler'),patch.object(manager,'save_settings'), \
             patch.object(manager,'log_model_interaction'),contextlib.redirect_stdout(io.StringIO()):
            result,_=manager.query_with_fallback([{'role':'user','content':'Describe a landscape'}])
        self.assertEqual(post.call_count,5)
        self.assertIn('piece5',result.content)

    def test_startup_discovery_preserves_custom_chain_and_uses_explicit_project(self):
        from engine.startup_discovery import discover
        (self.root/'package.json').write_text(json.dumps({'packageManager':'pnpm@9',
            'scripts':{'dev':'node compile.cjs && python3 custom_site.py','start':'python3 custom_site.py'}}))
        result=discover(f'Run my web on localhost {self.root}',str(self.root.parent))
        self.assertEqual(result['directory'],str(self.root.resolve()))
        self.assertIn('node compile.cjs && python3 custom_site.py',result['content'])
        self.assertIn('pnpm@9',result['content'])
        self.assertEqual(list(self.root.iterdir()),[self.root/'package.json'])

    def test_startup_metadata_cannot_follow_a_credential_symlink(self):
        from engine.startup_discovery import discover
        secret=self.root/'keys.json'
        secret.write_text('{"scripts":{"start":"NEVER_REVEAL_THIS"}}')
        (self.root/'package.json').symlink_to(secret)
        result=discover(f'Run my app {self.root}',str(self.root))
        self.assertNotIn('NEVER_REVEAL_THIS',str(result))

    def test_long_task_evidence_is_not_deleted_by_age(self):
        import os
        from engine.result_store import runtime_dir,store_result,read_action_output
        from engine.action_results import ActionResult
        old=runtime_dir('results')/('a'*32+'.txt')
        old.write_text('Important earlier source')
        os.utime(old,(1,1))
        store_result(ActionResult('New result'))
        self.assertIn('Important earlier source',read_action_output('a'*32))

    def test_default_step_and_spend_counts_do_not_end_useful_work(self):
        self.assertIsNone(DEFAULT_VALUES['task_request_limit'])
        usage.start_task('long')
        flow=TaskProgress([{'role':'user','content':'Solve the task'}],'Solve the task',str(self.root),{},2)
        for _ in range(140):
            self.assertEqual(flow.begin_round(),'')
        usage.ledger()['requests']=[{'total_tokens':200000}]*140
        self.assertEqual(flow.budget_reason(),'')

    def test_quoted_html_is_data_but_path_placeholders_stay_invalid(self):
        self.assertTrue(validate_shell_command("python -c 'print(\"<html>hello</html>\")'")[0])
        self.assertFalse(validate_shell_command('cd <project_path> && npm run dev')[0])

    def test_curl_option_argument_cannot_become_url_and_parse_failure_is_failure(self):
        with patch('engine.task_runner.python_curl',return_value=('ok',200)) as fetch:
            parse_and_run_curl_command('curl -sS --max-time 25 https://example.invalid',env={})
            self.assertEqual(fetch.call_args.kwargs['url'],'https://example.invalid')
        with patch('engine.task_runner.python_curl',side_effect=ValueError('bad request')):
            self.assertTrue(parse_and_run_curl_command('curl https://example.invalid',env={})[1])
