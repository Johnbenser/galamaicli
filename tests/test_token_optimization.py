"""Compatibility and meaningful token-saving gates for the request pipeline."""
import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from config import CONFIG, AGENT_STATE
from engine.context_manager import assemble_context, estimate_tokens
from engine.prompt_context import observation_content
from engine.tool_protocol import options, tool_schemas
from engine.completion import explicit_report_command, completed_simple_request
from engine.action_results import ActionResult
from engine import usage
from routers import manager


class TokenOptimizationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.state=patch.dict(AGENT_STATE,{'working_directory':str(self.root),'app_ref':None,'abort':False})
        self.state.start()

    def tearDown(self):
        self.state.stop();self.temp.cleanup()

    def test_question_prompt_is_smaller_and_does_not_scan_workspace(self):
        messages=[{'role':'user','content':'What is a Python function?'}]
        with patch('engine.task_runner.workspace_snapshot') as snapshot:
            compact=manager.build_enforced_messages(messages,'Groq','openai/gpt-oss-20b')
            snapshot.assert_not_called()
        with patch.dict(CONFIG,{'token_optimization':False}):
            old=manager.build_enforced_messages(messages,'Groq','openai/gpt-oss-20b')
        self.assertLess(estimate_tokens(compact),estimate_tokens(old)*0.5)

    def test_custom_instructions_and_workspace_systems_are_retained(self):
        with patch.object(manager,'load_model_rules',return_value={'system_instruction':'CUSTOM_PREFERENCE'}):
            selected=manager.build_enforced_messages([{'role':'system','content':'WORKSPACE_RULE'},
                {'role':'user','content':'Create a complete HTML page'}],'Groq','fixture')
        text=json.dumps(selected)
        self.assertIn('CUSTOM_PREFERENCE',text);self.assertIn('WORKSPACE_RULE',text)

    def test_text_fallback_still_explains_all_tools(self):
        selected=manager.build_enforced_messages([{'role':'user','content':'Build a website'}],'Direct','codestral-latest',False)
        for name in (t['function']['name'] for t in tool_schemas()):
            self.assertIn(name,selected[0]['content'])

    def test_text_fallback_preserves_native_history_arguments_when_diffs_are_compacted(self):
        calls=[{'id':'one','type':'function','function':{'name':'write_file',
            'arguments':json.dumps({'path':'a.html','content':'UNIQUE_OLD_SOURCE'})}}]
        messages=[{'role':'user','content':'Create a.html'},
            {'role':'assistant','content':'','_galam_tool_calls':calls},
            {'role':'user','content':'[Tool Observation for `write_file`]:\nSuccessfully wrote a.html.',
             '_galam_call_id':'one','_galam_action':{'status':'completed','id':'d'*32}},
            {'role':'user','content':'What source did you write?'}]
        selected=manager.build_enforced_messages(messages,'Direct','codestral-latest',False)
        self.assertIn('UNIQUE_OLD_SOURCE',json.dumps(selected))
        self.assertFalse(any(m['role']=='tool' or m.get('tool_calls') for m in selected))

    def test_successful_write_diff_is_compacted_only_in_the_prompt(self):
        text='[Tool Observation for `write_file`]:\nSuccessfully wrote index.html.\nChanges (old → new line):\n'+'+ LONG_CODE\n'*100+'\nValidation: file saved; no syntax check.\nStatus: File operation completed successfully.'
        message={'role':'user','content':text,'_galam_action':{'status':'completed','exit_code':0,'id':'a'*32}}
        original=copy.deepcopy(message)
        compact=observation_content(message)
        self.assertNotIn('LONG_CODE',compact)
        self.assertIn('no syntax check',compact);self.assertIn('a'*32,compact)
        self.assertEqual(message,original)

    def test_exact_inspected_source_is_not_silently_truncated(self):
        text='[Tool Observation for `view_file`]:\n'+'x = 1\n'*1500+'LAST_LINE'
        message={'role':'tool','content':text,'_galam_action':{'status':'completed','id':'b'*32}}
        self.assertIn(text,observation_content(message))

    def test_failed_actions_keep_error_tail_and_retrieval_id(self):
        message={'content':'[Tool Observation for `execute_bash`]:\n'+'noise\n'*1500+'FINAL_ERROR',
                 '_galam_action':{'status':'failed','exit_code':7,'id':'c'*32}}
        result=observation_content(message)
        self.assertIn('FINAL_ERROR',result);self.assertIn('c'*32,result)

    def test_history_does_not_fill_a_large_context_window_with_old_turns(self):
        history=[]
        for n in range(30):
            history.extend([{'role':'user','content':f'Question {n}: unrelated topic'},
                            {'role':'assistant','content':'old answer '*200}])
        history.append({'role':'user','content':'Explain the latest function'})
        selected,metrics=assemble_context(history,'Identity','Groq','fixture',
            {'context_window':128000,'max_tokens':4096},str(self.root))
        self.assertLess(metrics['messages_selected'],10)
        self.assertIn('latest function',json.dumps(selected))

    def test_constraints_are_sent_once_and_survive_old_turn_eviction(self):
        history=[{'role':'user','content':'Never delete KEEP_ME files'}, {'role':'assistant','content':'understood'}]
        for n in range(8):
            history.extend([{'role':'user','content':f'Unrelated question {n}'}, {'role':'assistant','content':'answer'}])
        history.append({'role':'user','content':'Explain variables'})
        selected,_=assemble_context(history,'Identity','Groq','fixture',{'context_window':8192},str(self.root))
        self.assertEqual(json.dumps(selected).count('Never delete KEEP_ME files'),1)

    def test_continue_retains_previous_request_and_native_results(self):
        calls=[{'id':'one','type':'function','function':{'name':'view_file','arguments':'{"path":"a.py"}'}}]
        messages=[{'role':'user','content':'Modify a.py according to ORIGINAL_REQUEST'},
                  {'role':'assistant','content':'','tool_calls':calls},
                  {'role':'tool','tool_call_id':'one','content':'EXACT_SOURCE'},
                  {'role':'user','content':'continue'}]
        selected,_=assemble_context(messages,'Identity','Groq','fixture',{'context_window':8192},str(self.root))
        self.assertIn('ORIGINAL_REQUEST',json.dumps(selected));self.assertIn('EXACT_SOURCE',json.dumps(selected))
        self.assertTrue(any(m.get('tool_calls') for m in selected))

    def test_exact_command_tools_expand_after_failure_or_repair(self):
        messages=[{'role':'user','content':'Run `wc -c index.html` and report output and exit status'}]
        names=lambda opts:{t['function']['name'] for t in opts['tools']}
        self.assertEqual(names(options('Groq','fixture',messages,{})),{'execute_bash','read_action_output'})
        messages.append({'role':'user','content':'command failed','_galam_action':{'status':'failed'}})
        self.assertIn('view_file',names(options('Groq','fixture',messages,{})))
        with patch.dict(CONFIG,{'token_optimization':{'tools':False}}):
            self.assertIn('view_file',names(options('Groq','fixture',messages[:1],CONFIG)))

    def test_ambiguous_actions_keep_the_full_tool_set(self):
        names={t['function']['name'] for t in options('Groq','fixture',[{'role':'user','content':'Build an application and test it'}],{})['tools']}
        self.assertEqual(names,{t['function']['name'] for t in tool_schemas()} - {'security_audit'})

    def test_continue_keeps_action_tools_and_does_not_turn_explanations_into_actions(self):
        messages=[{'role':'user','content':'Build a complete website'},
                  {'role':'assistant','content':'Created the first file; checks remain.'},
                  {'role':'user','content':'continue','_galam_tool_mode':'none'}]
        self.assertIn('tools',options('Groq','fixture',messages,{}))
        selected=manager.build_enforced_messages(messages,'Groq','fixture')
        self.assertIn('GALAM ACTION RESPONSE CONTRACT',selected[0]['content'])
        messages[0]['content']='Explain how websites work'
        self.assertEqual(options('Groq','fixture',messages,{}),{})

    def test_repeated_continue_retains_the_original_unfinished_task(self):
        messages=[{'role':'user','content':'Build ORIGINAL_TASK'}]
        for n in range(4):
            messages.extend([{'role':'assistant','content':f'Progress {n}'},{'role':'user','content':'continue'}])
        selected,_=assemble_context(messages,'Identity','Groq','fixture',{'context_window':8192},str(self.root))
        self.assertIn('ORIGINAL_TASK',json.dumps(selected))

    def test_exact_command_receipt_includes_real_output_without_extra_generation(self):
        request='Execute this exact terminal command using the execute_bash Galam action: wc -c index.html . Report its actual byte count and exit status. Do not change any files.'
        self.assertEqual(explicit_report_command(request),'wc -c index.html')
        events=[{'tool':'execute_bash','args':{'command':'wc -c index.html'},'result':ActionResult('303 index.html\n')}]
        receipt=completed_simple_request(request,events,[],str(self.root))
        self.assertIn('303 index.html',receipt);self.assertIn('exit status 0',receipt)
        self.assertIsNone(completed_simple_request(request,events,['pending check'],str(self.root)))
        self.assertIsNone(explicit_report_command('Run pwd and build an application'))

    def test_usage_includes_reasoning_once_and_keeps_cached_input_separate(self):
        usage.start_task('one')
        response=usage.attach(SimpleNamespace(status_code=200),usage.begin_request('Groq','fixture',{}))
        counts={'usage':{'prompt_tokens':100,'completion_tokens':30,'total_tokens':130,
                       'prompt_tokens_details':{'cached_tokens':50},'completion_tokens_details':{'reasoning_tokens':20}}}
        usage.capture(response,counts);usage.capture(response,counts)
        stats=usage.snapshot()
        self.assertEqual(stats['provider_total_tokens'],130)
        self.assertEqual(stats['provider_output_tokens'],30)
        self.assertEqual(stats['requests'][0]['cached_input_tokens'],50)

    def test_gemini_and_cohere_usage_formats(self):
        usage.start_task('two')
        response=usage.attach(SimpleNamespace(status_code=200),usage.begin_request('Direct','gemini',{}))
        usage.capture(response,{'usageMetadata':{'promptTokenCount':100,'candidatesTokenCount':20,'thoughtsTokenCount':10,'totalTokenCount':130}})
        self.assertEqual(usage.snapshot()['provider_output_tokens'],30)
        other=usage.attach(SimpleNamespace(status_code=200),usage.begin_request('Direct','cohere',{}))
        usage.capture(other,{'delta':{'usage':{'tokens':{'input_tokens':10,'output_tokens':5}}}})
        self.assertEqual(usage.snapshot()['provider_total_tokens'],145)

    def test_unknown_or_partial_usage_is_not_reported_as_complete(self):
        usage.start_task('three')
        response=usage.attach(SimpleNamespace(status_code=200),usage.begin_request('Other','fixture',{'messages':[]}))
        for malformed in ({'delta':'text'},{'usage':{'tokens':None}},{'result':None}):
            usage.capture(response,malformed)
        self.assertFalse(usage.snapshot()['complete_provider_usage'])
        usage.capture(response,{'usage':{'prompt_tokens':12}})
        self.assertFalse(usage.snapshot()['complete_provider_usage'])

    def test_retry_accounting_does_not_store_headers_keys_or_payload(self):
        usage.start_task('four')
        limited=Mock(status_code=429,headers={'Retry-After':'0.1'})
        success=SimpleNamespace(status_code=200)
        with patch.object(manager.SESSION,'post',side_effect=[limited,success]), patch.object(manager.time,'sleep'):
            manager._post_with_transport_retry('https://fixture.invalid',{'Authorization':'SECRET'},
                                               {'model':'fixture','messages':[{'content':'PRIVATE_CODE'}]},True,10)
        stats=usage.snapshot()
        self.assertEqual(stats['request_count'],2)
        self.assertNotIn('SECRET',json.dumps(stats));self.assertNotIn('PRIVATE_CODE',json.dumps(stats))

    def test_agent_router_uses_the_registered_key_and_chat_route(self):
        import os
        from engine.payload_builder import build_request_data
        with patch.dict(os.environ,{'AGENT_ROUTER_API_KEY':'fixture-token'}):
            url,headers,payload=build_request_data('Agent Router','gpt-6-astra',[{'role':'user','content':'Hi'}])
        self.assertEqual(url,'https://agentrouter.org/v1/chat/completions')
        self.assertEqual(headers['Authorization'],'Bearer fixture-token')
        self.assertEqual(payload['model'],'gpt-6-astra')
        self.assertIn('tools',options('Agent Router','gpt-6-astra',[{'role':'user','content':'Run pwd'}],{}))

    def test_secret_file_loads_literals_without_overriding_exports_or_running_commands(self):
        import os
        from config import _load_secret_files
        path=self.root/'.secrets'
        marker=self.root/'must-not-exist'
        path.write_text(f'AGENT_ROUTER_API_KEY="saved-token" # comment\nGROQ_API_KEY="saved-groq"\nPATH="wrong"\nBAD_API_KEY=$(touch {marker})\n')
        with patch.dict(os.environ,{'GROQ_API_KEY':'exported-token','PATH':'keep-path'},clear=True):
            _load_secret_files([str(path)])
            self.assertEqual(os.environ['AGENT_ROUTER_API_KEY'],'saved-token')
            self.assertEqual(os.environ['GROQ_API_KEY'],'exported-token')
            self.assertEqual(os.environ['PATH'],'keep-path')
            self.assertNotIn('BAD_API_KEY',os.environ)
        self.assertFalse(marker.exists())

    def test_live_request_path_records_stream_usage_in_model_logs(self):
        from storage.endpoints import ENDPOINT_REGISTRY
        usage.start_task('meter-fixture')
        chunks=[b'data: {"choices":[{"delta":{"content":"Variables store values."},"finish_reason":"stop"}]}',
                b'data: {"choices":[],"usage":{"prompt_tokens":100,"completion_tokens":20,"total_tokens":120}}',b'data: [DONE]']
        response=SimpleNamespace(ok=True,status_code=200,headers={'Content-Type':'text/event-stream'},iter_lines=lambda:iter(chunks))
        with patch.dict(ENDPOINT_REGISTRY,{'Fixture':{'base_url':'https://fixture.invalid','models':{'generic':{'context_window':8192}}}},clear=True), \
             patch.dict(CONFIG,{'active_endpoint':'Fixture','active_model':'generic','stream':True}), \
             patch.object(manager.SESSION,'post',return_value=response),patch.object(manager,'save_settings'), \
             patch.object(manager,'log_model_interaction') as log:
            answer,_=manager.query_with_fallback([{'role':'user','content':'Explain variables'}])
        self.assertIn('Variables store values',answer.content)
        measured=log.call_args.kwargs['metadata']['usage']
        self.assertEqual(measured['provider_total_tokens'],120)
        self.assertTrue(measured['complete_provider_usage'])


if __name__=='__main__':
    unittest.main()
