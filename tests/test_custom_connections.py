"""Home-only credential editing, both wire formats, actions and real terminal navigation."""
import asyncio
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import shlex
import stat
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import patch

from config import CONFIG,AGENT_STATE
from storage.endpoints import ENDPOINT_REGISTRY
from storage import user_endpoints as store
from engine import api_protocols as protocol,usage
from routers import manager


class Workspace(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.home=Path(self.temp.name)
        self.env=patch.dict(os.environ,{'GALAM_HOME':str(self.home),'GALAM_STATE_DIR':str(self.home/'runtime'),'SHELL':'/bin/bash'})
        self.env.start()
        self.registry=patch.dict(ENDPOINT_REGISTRY,{'Builtin':{'base_url':'https://builtin.invalid/v1','models':{'original':{'name':'original'}}}},clear=True)
        self.registry.start()
        self.state=patch.dict(AGENT_STATE,{'task_active':False,'active_message':None,'abort':False,'app_ref':None,
                                         'active_subagents':[],'working_directory':str(self.home)})
        self.state.start();usage.start_task('custom-test')
        self.profile={'endpoint':'My API','model':'fixture-model','url':'https://fixture.invalid/v1',
                      'api_format':'chat_completions','context_window':8192,'api_key_env':'GALAM_TEST_API_KEY'}

    def tearDown(self):
        self.state.stop();self.registry.stop();self.env.stop();self.temp.cleanup()


class ConnectionStorageTests(Workspace):
    def test_default_format_uses_model_or_most_common_endpoint_format_and_remains_editable(self):
        ENDPOINT_REGISTRY['Mixed']={'models':{
            'one':{'custom_connection':{'api_format':'responses'}},
            'two':{'custom_connection':{'api_format':'responses'}},
            'three':{'custom_connection':{'api_format':'chat_completions'}}}}
        profile=store.validate_profile({**self.profile,'endpoint':'Mixed','api_format':'default'})
        self.assertEqual((profile['api_format_mode'],profile['api_format']),('default','responses'))
        self.assertEqual(profile['url'],'https://fixture.invalid/v1')
        self.assertEqual(store.default_api_format('Mixed','three'),'chat_completions')
        self.assertEqual(store.validate_profile({**self.profile,'endpoint':'Mixed','api_format':'chat_completions'})['api_format'],'chat_completions')
        self.assertEqual(store.validate_profile(profile),profile)

    def test_entered_urls_remain_exact_and_ollama_default_detects_native_protocol(self):
        for value in ('https://ollama.com/api','https://ollama.com/api/chat',
                      'https://custom.invalid/v3/chat/completions','https://api.invalid/v1/'):
            self.assertEqual(store.normalize_url(value,'chat_completions'),value)
        profile=store.validate_profile({**self.profile,'url':'https://ollama.com/api/chat','api_format':'default'})
        self.assertEqual(profile['url'],'https://ollama.com/api/chat')
        self.assertEqual(profile['api_format'],'ollama')

    def test_matching_provider_host_reuses_group_key_but_other_hosts_get_separate_reference(self):
        ENDPOINT_REGISTRY['Builtin']['api_key_env']='PROVIDER_API_KEY'
        profile=store.validate_profile({**self.profile,'endpoint':'Builtin','url':'https://builtin.invalid/v1','api_key_env':''})
        self.assertEqual(profile['api_key_env'],'PROVIDER_API_KEY')
        other=store.validate_profile({**self.profile,'endpoint':'Builtin','api_key_env':''})
        self.assertNotEqual(other['api_key_env'],'PROVIDER_API_KEY')

    def test_append_keeps_existing_rc_secrets_and_quotes_key_literally(self):
        existing='# user secrets\nexport OTHER_API_KEY=keep-me\n# final comment without newline'
        (self.home/'.secrets').write_text(existing)
        (self.home/'.bashrc').write_text('alias ll="ls -al"\n')
        key='fixture $HOME $(touch SHOULD_NOT_EXIST) \' quote'
        profile=store.save_profile(self.profile,key)
        saved=(self.home/'.secrets').read_text()
        self.assertIn(existing+'\n',saved)
        self.assertEqual(store._secret_value(saved,profile['api_key_env']),key)
        self.assertIn('alias ll=',(self.home/'.bashrc').read_text())
        result=subprocess.run(['bash','-c','. "$1"; printf "%s" "$GALAM_TEST_API_KEY"','bash',str(self.home/'.secrets')],cwd=self.home,capture_output=True,text=True)
        self.assertEqual(result.stdout,key);self.assertFalse((self.home/'SHOULD_NOT_EXIST').exists())
        self.assertEqual(os.environ['GALAM_TEST_API_KEY'],key)
        self.assertNotIn(key,store.profile_path().read_text())
        for path in (self.home/'.secrets',store.profile_path()):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode),0o600)

    def test_replacing_key_deduplicates_only_that_variable_and_source_line(self):
        path=self.home/'.secrets'
        path.write_text('export GALAM_TEST_API_KEY=old\nexport OTHER_API_KEY=keep\nGALAM_TEST_API_KEY=duplicate\n')
        (self.home/'.bashrc').write_text('source ~/.secrets\n# custom shell\n')
        profile=store.save_profile(self.profile,'new-key')
        store.save_profile(profile,'updated-key',original=profile)
        self.assertEqual(path.read_text().count('GALAM_TEST_API_KEY='),1)
        self.assertIn('OTHER_API_KEY=keep',path.read_text())
        self.assertEqual((self.home/'.bashrc').read_text(),'source ~/.secrets\n# custom shell\n')
        self.assertEqual(len(store.read_profiles()),1)

    def test_blank_key_keeps_existing_key_and_loading_profiles_never_copies_keys(self):
        profile=store.save_profile(self.profile,'keep-this-fixture')
        profile=store.save_profile({**profile,'context_window':16384},'',original=profile)
        self.assertEqual(os.environ[profile['api_key_env']],'keep-this-fixture')
        registry={};store.load_into(registry)
        self.assertEqual(registry['My API']['models']['fixture-model']['custom_connection'],profile)
        self.assertNotIn('keep-this-fixture',json.dumps(registry))

    def test_unauthed_endpoint_creates_no_secret_or_shell_file(self):
        os.environ.pop('GALAM_TEST_API_KEY',None)
        store.save_profile(self.profile)
        self.assertFalse((self.home/'.secrets').exists());self.assertFalse((self.home/'.bashrc').exists())

    def test_saved_definition_does_not_modify_existing_provider_or_models(self):
        builtin=copy.deepcopy(ENDPOINT_REGISTRY['Builtin'])
        store.save_profile({**self.profile,'endpoint':'Builtin'},'fixture-key')
        self.assertEqual(ENDPOINT_REGISTRY['Builtin']['base_url'],builtin['base_url'])
        self.assertEqual(ENDPOINT_REGISTRY['Builtin']['models']['original'],builtin['models']['original'])
        with self.assertRaises(ValueError):store.save_profile({**self.profile,'endpoint':'Builtin','model':'original'})

    def test_update_and_move_user_profile_preserves_other_definitions(self):
        profile=store.save_profile(self.profile,'fixture-key')
        other=store.save_profile({**self.profile,'model':'second'})
        moved=store.save_profile({**profile,'endpoint':'Another','model':'renamed'},original=profile)
        self.assertNotIn('fixture-model',ENDPOINT_REGISTRY['My API']['models'])
        self.assertIn('second',ENDPOINT_REGISTRY['My API']['models'])
        self.assertEqual(len(store.read_profiles()),2)
        self.assertIn('renamed',ENDPOINT_REGISTRY['Another']['models'])

    def test_bad_url_or_environment_name_or_multiline_key_writes_nothing(self):
        for override in ({'url':'ftp://example.com'},{'url':'https://user:password@example.com/v1'},
                         {'url':'https://example.com/v1?api_key=secret'},{'url':'https://example.com/v1#fragment'},
                         {'api_key_env':'BASH_ENV'},{'context_window':'bad'},{'url':'https://example.com/v1/embeddings'}):
            with self.subTest(override=override),self.assertRaises(ValueError):store.save_profile({**self.profile,**override},'fixture')
        with self.assertRaises(ValueError):store.save_profile(self.profile,'key\nexport BAD=value')
        self.assertFalse(store.profile_path().exists());self.assertFalse((self.home/'.secrets').exists())

    def test_corrupt_store_and_symlinked_secrets_are_not_overwritten(self):
        store.profile_path().parent.mkdir(parents=True)
        store.profile_path().write_text('{invalid')
        with self.assertRaises(ValueError):store.save_profile(self.profile,'fixture')
        self.assertEqual(store.profile_path().read_text(),'{invalid')
        store.profile_path().unlink()
        target=self.home/'real';target.write_text('original')
        (self.home/'.secrets').symlink_to(target)
        with self.assertRaises(ValueError):store.save_profile(self.profile,'fixture')
        self.assertEqual(target.read_text(),'original')

    def test_file_write_failure_rolls_back_and_does_not_apply_live_state(self):
        (self.home/'.secrets').write_text('export OTHER_API_KEY=original\n')
        original=store.atomic_bytes;calls=0
        def fail_once(path,data,mode=0o600):
            nonlocal calls
            calls+=1
            if calls==2:raise OSError('fixture failure')
            return original(path,data,mode)
        os.environ.pop('GALAM_TEST_API_KEY',None)
        with patch.object(store,'atomic_bytes',side_effect=fail_once),self.assertRaises(OSError):store.save_profile(self.profile,'fixture')
        self.assertEqual((self.home/'.secrets').read_text(),'export OTHER_API_KEY=original\n')
        self.assertFalse(store.profile_path().exists());self.assertNotIn('My API',ENDPOINT_REGISTRY)
        self.assertNotIn('GALAM_TEST_API_KEY',os.environ)

    def test_zsh_source_is_guarded_and_repeated_saves_do_not_duplicate(self):
        with patch.dict(os.environ,{'SHELL':'/bin/zsh'}):
            profile=store.save_profile(self.profile,'fixture')
            store.save_profile(profile,'fixture2',original=profile)
        text=(self.home/'.zshrc').read_text()
        self.assertEqual(text.count('# Galam model credentials'),1)
        self.assertFalse((self.home/'.bashrc').exists())

    def test_reload_uses_saved_key_even_if_parent_export_is_stale(self):
        profile=store.save_profile({**self.profile,'api_key_env':'$GALAM_TEST_API_KEY'},'new-fixture')
        os.environ['GALAM_TEST_API_KEY']='old-parent-export'
        store.load_into({})
        self.assertEqual(os.environ['GALAM_TEST_API_KEY'],'new-fixture')
        self.assertEqual(profile['api_key_env'],'GALAM_TEST_API_KEY')

    def test_echoed_or_commented_source_is_not_mistaken_for_an_active_source_command(self):
        original='echo "source ~/.secrets"\n# source ~/.secrets\n'
        (self.home/'.bashrc').write_text(original)
        store.save_profile(self.profile,'fixture')
        self.assertIn(original,(self.home/'.bashrc').read_text())
        self.assertIn('[ ! -f "$HOME/.secrets" ] || . "$HOME/.secrets"',(self.home/'.bashrc').read_text())

    def test_urls_accept_bases_and_correct_singular_response(self):
        for value in ('https://api.invalid','https://api.invalid/v1/','https://api.invalid/v1/response','https://api.invalid/v1/responses'):
            self.assertEqual(store.normalize_url(value,'responses'),value)
        self.assertEqual(store.normalize_url('https://api.invalid/api/v1/chat/completions','chat_completions'),
                         'https://api.invalid/api/v1/chat/completions')
        self.assertNotEqual(store.default_key_env('My API','https://a.invalid/v1'),store.default_key_env('My API','https://b.invalid/v1'))


class ResponsesProtocolTests(Workspace):
    def test_daily_account_limit_stops_without_retrying_every_model(self):
        ENDPOINT_REGISTRY['Builtin']['models']['second']={'name':'second'}
        response=SimpleNamespace(ok=False,status_code=429,headers={},text='limit reached',
            json=lambda:{'error':{'message':'Rate limit exceeded: free-models-per-day'}},close=lambda:None)
        with patch.dict(CONFIG,{'active_endpoint':'Builtin','active_model':'original','stream':False}), \
             patch.object(manager.SESSION,'post',return_value=response) as post,contextlib.redirect_stdout(io.StringIO()):
            answer,_=manager.query_with_fallback([{'role':'user','content':'Reply OK.'}])
        self.assertEqual(answer,'FAILED');post.assert_called_once()
        self.assertFalse(manager.terminal_quota_error('temporary per-model rate limit'))

    def test_message_tool_and_reasoning_round_trip_preserves_call_identity(self):
        encrypted={'type':'reasoning','id':'rs_1','summary':[],'encrypted_content':'fixture-encrypted'}
        payload={'model':'fixture','max_tokens':512,'stream':True,'messages':[
            {'role':'system','content':'Rules'}, {'role':'user','content':'Run pwd'},
            {'role':'assistant','content':'Checking.','_galam_response_reasoning':[encrypted],
             'tool_calls':[{'id':'call_x','type':'function','function':{'name':'execute_bash','arguments':'{"command":"pwd"}'}}]},
            {'role':'tool','tool_call_id':'call_x','content':'/project'}],
            'tools':[{'type':'function','function':{'name':'execute_bash','parameters':{'type':'object'}}}],'tool_choice':'auto'}
        original=copy.deepcopy(payload)
        converted=protocol.prepare_payload('https://api.invalid/v1/responses',payload)
        self.assertEqual(payload,original)
        self.assertNotIn('messages',converted);self.assertNotIn('max_tokens',converted)
        self.assertEqual(converted['max_output_tokens'],512);self.assertFalse(converted['store'])
        self.assertIn(encrypted,converted['input'])
        self.assertEqual(converted['input'][-1],{'type':'function_call_output','call_id':'call_x','output':'/project'})
        self.assertEqual(converted['tools'][0]['name'],'execute_bash')
        self.assertNotIn('function',converted['tools'][0])
        self.assertIs(protocol.prepare_payload('https://api.invalid/v1/chat/completions',payload),payload)

    def test_json_text_tools_refusal_and_summary_are_extracted(self):
        data={'output':[{'type':'reasoning','id':'rs','summary':[{'text':'Checking.'}]},
                        {'type':'message','content':[{'type':'output_text','text':'Answer.'},{'type':'refusal','refusal':'Refused.'}]},
                        {'type':'function_call','call_id':'call_x','name':'execute_bash','arguments':'{"command":"pwd"}'}]}
        self.assertEqual(manager._extract_response_content('fixture','fixture',data),('Answer.Refused.','Checking.'))
        self.assertEqual(manager._detect_and_synthesize_tool_calls('',data)[0]['id'],'call_x')

    def test_stream_assembles_once_and_keeps_final_encrypted_reasoning(self):
        response=SimpleNamespace()
        events=[{'type':'response.output_item.added','output_index':1,'item':{'type':'function_call','call_id':'call_x','id':'fc_x','name':'execute_bash','arguments':''}},
                {'type':'response.function_call_arguments.delta','output_index':1,'delta':'{"command":'},
                {'type':'response.function_call_arguments.delta','output_index':1,'delta':'"pwd"}'},
                {'type':'response.function_call_arguments.done','output_index':1,'arguments':'{"command":"pwd"}'},
                {'type':'response.output_item.done','output_index':1,'item':{'type':'function_call','call_id':'call_x','name':'execute_bash','arguments':'{"command":"pwd"}'}},
                {'type':'response.output_item.added','item':{'type':'reasoning','id':'rs','summary':[]}},
                {'type':'response.output_item.done','item':{'type':'reasoning','id':'rs','summary':[],'encrypted_content':'encrypted-fixture'}},
                {'type':'response.completed','response':{'output':[{'type':'function_call','call_id':'call_x','name':'execute_bash','arguments':'{"command":"pwd"}'}]}}]
        deltas=[]
        for event in events:deltas.extend(manager._extract_stream_chunk(event,response)[2])
        self.assertEqual(''.join(d['function'].get('name','') for d in deltas),'execute_bash')
        self.assertEqual(''.join(d['function'].get('arguments','') for d in deltas),'{"command":"pwd"}')
        self.assertEqual(deltas[0]['id'],'call_x')
        self.assertEqual(response._galam_response_reasoning[0]['encrypted_content'],'encrypted-fixture')

    def test_stream_terminal_snapshot_is_not_duplicated_and_errors_stop(self):
        response=SimpleNamespace()
        self.assertEqual(manager._extract_stream_chunk({'type':'response.output_text.delta','delta':'Hello.'},response)[0],'Hello.')
        final={'type':'response.completed','response':{'output':[{'type':'message','content':[{'type':'output_text','text':'Hello.'}]}]}}
        self.assertEqual(manager._extract_stream_chunk(final,response)[0],'')
        self.assertEqual(manager._extract_stream_chunk(final,SimpleNamespace())[0],'Hello.')
        for kind,result in [('response.failed',{'error':{'message':'fixture quota'}}),
                            ('response.incomplete',{'incomplete_details':{'reason':'content_filter'}})]:
            with self.assertRaises(manager.ProviderStreamError):manager._extract_stream_chunk({'type':kind,'response':result},SimpleNamespace())
        chunk=manager._extract_stream_chunk({'type':'response.incomplete','response':{'incomplete_details':{'reason':'max_output_tokens'}}},SimpleNamespace())
        self.assertEqual(chunk[3],'length')

    def test_final_snapshot_completes_missing_tool_fragments_without_duplicate_arguments(self):
        response=SimpleNamespace()
        header={'type':'response.output_item.added','output_index':2,'item':{'type':'function_call','call_id':'call_x','name':'execute_bash','arguments':''}}
        manager._extract_stream_chunk(header,response)
        manager._extract_stream_chunk({'type':'response.function_call_arguments.delta','output_index':2,'delta':'{"command":'},response)
        final={'type':'response.completed','response':{'output':[{'type':'function_call','call_id':'call_x','name':'execute_bash','arguments':'{"command":"pwd"}'}]}}
        chunk=manager._extract_stream_chunk(final,response)
        self.assertEqual(chunk[2][0]['index'],2)
        self.assertEqual(chunk[2][0]['function'],{'arguments':'"pwd"}'})
        self.assertEqual(chunk[3],'tool_calls')

    def test_json_failed_and_incomplete_are_not_reported_as_success(self):
        for status,detail in [('failed',{'error':{'message':'fixture failure'}}),
                              ('incomplete',{'incomplete_details':{'reason':'content_filter'}})]:
            response=SimpleNamespace(json=lambda:{'object':'response','status':status,'output':[],**detail})
            with self.assertRaises(manager.ProviderStreamError):manager._response_json(response)

    def test_context_preserves_continuity_only_for_responses_connections(self):
        from engine.context_manager import assemble_context
        profile=store.save_profile({**self.profile,'api_format':'responses'})
        messages=[{'role':'user','content':'question'},{'role':'assistant','content':'answer',
                   '_galam_response_reasoning':[{'type':'reasoning','id':'rs','summary':[],'encrypted_content':'fixture'}]}]
        for endpoint,model,expected in [('My API','fixture-model',True),('Builtin','original',False)]:
            selected,_=assemble_context(messages,'Rules',endpoint,model,{'max_tokens':512},str(self.home))
            self.assertEqual('_galam_response_reasoning' in selected[-1],expected)

    def test_reasoning_from_another_model_or_changed_url_is_not_forwarded(self):
        from engine.context_manager import assemble_context,model_limits
        profile=store.save_profile({**self.profile,'api_format':'responses','context_window':4096})
        origin=[profile['endpoint'],profile['model'],'https://old-service.invalid/v1/responses']
        messages=[{'role':'user','content':'question'}, {'role':'assistant','content':'answer',
            '_galam_response_origin':origin,'_galam_response_reasoning':[{'type':'reasoning','id':'rs','summary':[]}]}]
        selected,_=assemble_context(messages,'Rules',profile['endpoint'],profile['model'],{'max_tokens':512},str(self.home))
        self.assertNotIn('_galam_response_reasoning',selected[-1])
        self.assertEqual(model_limits(profile['endpoint'],profile['model'],{'context_window':64000})[0],4096)

    def test_local_discovery_keeps_custom_connection_profiles(self):
        from engine.local_models import refresh_local_models
        ENDPOINT_REGISTRY['Local Endpoint']={'models':{},'base_url':'http://127.0.0.1:8080/v1'}
        store.save_profile({**self.profile,'endpoint':'Local Endpoint'})
        with patch('engine.local_models.discover_local_models',return_value=['server-model']):refresh_local_models({})
        self.assertIn('fixture-model',ENDPOINT_REGISTRY['Local Endpoint']['models'])
        self.assertIn('server-model',ENDPOINT_REGISTRY['Local Endpoint']['models'])

    def test_streaming_router_sends_responses_payload_and_returns_complete_action_with_usage(self):
        profile=store.save_profile({**self.profile,'api_format':'responses'})
        item={'type':'function_call','call_id':'call_stream','id':'fc_stream','name':'execute_bash','arguments':''}
        encrypted={'type':'reasoning','id':'rs_stream','summary':[],'encrypted_content':'fixture-encrypted'}
        events=[{'type':'response.output_item.added','output_index':1,'item':item},
                {'type':'response.function_call_arguments.delta','output_index':1,'delta':'{"command":"pwd"}'},
                {'type':'response.completed','response':{'output':[encrypted,{**item,'arguments':'{"command":"pwd"}'}],
                                                        'usage':{'input_tokens':100,'output_tokens':20}}}]
        chunks=[('data: '+json.dumps(event)).encode() for event in events]
        response=SimpleNamespace(ok=True,status_code=200,headers={'Content-Type':'text/event-stream'},iter_lines=lambda:iter(chunks))
        with patch.dict(CONFIG,{'active_endpoint':profile['endpoint'],'active_model':profile['model'],'stream':True}), \
             patch.object(manager.SESSION,'post',return_value=response) as post,patch.object(manager,'save_settings'), \
             patch.object(manager,'log_model_interaction'),contextlib.redirect_stdout(io.StringIO()):
            answer,_=manager.query_with_fallback([{'role':'user','content':'Use a terminal action to inspect the directory.'}])
        self.assertEqual(answer.tool_calls[0]['id'],'call_stream')
        self.assertEqual(json.loads(answer.tool_calls[0]['function']['arguments']),{'command':'pwd'})
        self.assertEqual(answer.response_reasoning,[encrypted])
        self.assertEqual(usage.totals()['total'],120)
        sent=post.call_args.kwargs['json']
        self.assertIn('input',sent);self.assertNotIn('messages',sent)
        self.assertEqual(sent['tools'][0]['type'],'function');self.assertNotIn('function',sent['tools'][0])
        post.assert_called_once()

    def test_both_protocols_trigger_real_bash_and_return_results_to_the_same_call(self):
        from engine.task_runner import execute_tool_call
        from engine.tool_protocol import wire_messages
        calls=[]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                calls.append((self.path,body))
                responses=self.path.endswith('/responses')
                second=any(item.get('type')=='function_call_output' for item in body.get('input',[])) if responses else any(m.get('role')=='tool' for m in body['messages'])
                function={'name':'execute_bash','arguments':json.dumps({'command':"printf 'GALAM_ACTION_OK'"})}
                if responses:
                    output=[{'type':'message','content':[{'type':'output_text','text':'Terminal returned GALAM_ACTION_OK.'}]}] if second else [{'type':'function_call','call_id':'call_fixture','name':function['name'],'arguments':function['arguments']},
                        {'type':'reasoning','id':'rs_fixture','summary':[{'type':'summary_text','text':'Checking terminal output.'}],'encrypted_content':'fixture-encrypted'}]
                    result={'object':'response','status':'completed','output':output,'usage':{'input_tokens':100,'output_tokens':20}}
                else:
                    message={'content':'Terminal returned GALAM_ACTION_OK.'} if second else {'content':'','tool_calls':[{'id':'call_fixture','type':'function','function':function}]}
                    result={'choices':[{'message':message,'finish_reason':'stop'}],'usage':{'prompt_tokens':100,'completion_tokens':20}}
                data=json.dumps(result).encode();self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        try:
            for api_format in ('chat_completions','responses'):
                with self.subTest(api_format=api_format):
                    profile=store.save_profile({**self.profile,'endpoint':api_format,'model':'fixture-'+api_format,
                                               'api_format':api_format,'url':f'http://127.0.0.1:{server.server_port}/v1/'+store.FORMATS[api_format]})
                    messages=[{'role':'user','content':'Use the terminal to inspect output, then explain the result.'}]
                    with patch.dict(CONFIG,{'active_endpoint':profile['endpoint'],'active_model':profile['model'],'stream':False}), \
                         patch.object(manager,'save_settings'),patch.object(manager,'log_model_interaction'),contextlib.redirect_stdout(io.StringIO()):
                        answer,_=manager.query_with_fallback(messages)
                        self.assertEqual(answer.tool_calls[0]['id'],'call_fixture')
                        arguments=json.loads(answer.tool_calls[0]['function']['arguments'])
                        with patch('engine.task_runner.log_tool_execution'):
                            result=execute_tool_call('execute_bash',arguments)
                        self.assertTrue(result.success);self.assertIn('GALAM_ACTION_OK',result.output)
                        messages.extend([{'role':'assistant','content':'','_galam_tool_calls':answer.tool_calls,
                                          '_galam_response_reasoning':answer.response_reasoning},
                                         {'role':'user','content':result.output,'_galam_call_id':'call_fixture',
                                          '_galam_action':{'status':'completed','tool':'execute_bash','exit_code':0}}])
                        final,_=manager.query_with_fallback(messages)
                        self.assertIn('GALAM_ACTION_OK',final.content)
                    sent=calls[-1][1]
                    if api_format=='responses':
                        self.assertNotIn('messages',sent);self.assertEqual(sent['input'][-1]['call_id'],'call_fixture')
                        self.assertIn('function_call_output',json.dumps(sent));self.assertIn('fixture-encrypted',json.dumps(sent))
                    else:self.assertEqual(sent['messages'][-1]['tool_call_id'],'call_fixture')
        finally:server.shutdown();server.server_close();worker.join(2)


class ConnectionEditorTests(Workspace):
    def test_numbered_endpoint_picker_selects_without_an_endpoint_text_field(self):
        from ui.endpoint_manager import EndpointManager
        from prompt_toolkit.application import create_app_session
        from prompt_toolkit.input.defaults import create_pipe_input
        from prompt_toolkit.output import DummyOutput
        ENDPOINT_REGISTRY['Other']={'base_url':'https://other.invalid/v1','models':{}}
        async def exercise():
            with create_pipe_input() as pipe,create_app_session(input=pipe,output=DummyOutput()):
                editor=EndpointManager();self.assertNotIn('endpoint',editor.fields)
                task=asyncio.create_task(editor.app.run_async())
                try:
                    await asyncio.sleep(.06);pipe.send_text('\r');await asyncio.sleep(.06)
                    self.assertEqual(editor.page,'picker')
                    self.assertEqual(editor.picker.values,[('Builtin','1. Builtin'),('Other','2. Other')])
                    pipe.send_text('2\r');await asyncio.sleep(.06)
                    self.assertEqual(editor.page,'form');self.assertEqual(editor.selected_endpoint,'Other')
                    self.assertEqual(editor.fields['url'].text,'https://other.invalid/v1/chat/completions')
                    self.assertEqual(editor.protocol.current_value,'default')
                finally:
                    if not task.done():editor.app.exit()
                    await task
        asyncio.run(exercise())

    def test_multi_digit_choice_and_provider_switch_clear_old_credentials_from_form(self):
        from ui.endpoint_manager import EndpointManager
        from prompt_toolkit.application import create_app_session
        from prompt_toolkit.input.defaults import create_pipe_input
        from prompt_toolkit.output import DummyOutput
        for index in range(12):ENDPOINT_REGISTRY[f'Provider {index:02d}']={'models':{},'base_url':f'https://host{index}.invalid/v1'}
        with create_pipe_input() as pipe,create_app_session(input=pipe,output=DummyOutput()):
            editor=EndpointManager();editor.fields['key'].text='discard-old-provider-key';editor.fields['api_key_env'].text='OLD_API_KEY'
            editor.pick_endpoint()
            editor.choice_number.text='999999999999999999999999'
            editor.select_number(editor.choice_number.buffer);self.assertEqual(editor.page,'picker')
            editor.choice_number.text='12';expected=editor.picker.values[11][0]
            editor.select_number(editor.choice_number.buffer)
            self.assertEqual(editor.selected_endpoint,expected)
            self.assertEqual(editor.fields['key'].text,'');self.assertEqual(editor.fields['api_key_env'].text,'')
            editor.fields['url'].text='https://other.invalid/api/v2'
            self.assertIn('unchanged',editor.default_path())
            self.assertEqual(editor.fields['url'].text,'https://other.invalid/api/v2')

    def test_save_confirmation_stays_in_ui_and_password_is_never_rendered(self):
        from ui.endpoint_manager import EndpointManager
        from prompt_toolkit.application import create_app_session
        from prompt_toolkit.input.defaults import create_pipe_input
        from prompt_toolkit.output import DummyOutput
        from prompt_toolkit.data_structures import Size
        class PhoneOutput(DummyOutput):
            def get_size(self):return Size(rows=24,columns=44)
        async def exercise():
            with create_pipe_input() as pipe,create_app_session(input=pipe,output=PhoneOutput()):
                editor=EndpointManager()
                for name,value in self.profile.items():
                    if name in editor.fields:editor.fields[name].text=str(value)
                editor.fields['key'].text='fixture-secret-only'
                editor.step=2;editor.focus_step()
                editor.app.layout.focus(editor.fields['key'])
                with patch('ui.endpoint_manager.save_settings') as save,contextlib.redirect_stdout(io.StringIO()) as out:
                    task=asyncio.create_task(editor.app.run_async())
                    try:
                        await asyncio.sleep(.08)
                        screen=editor.app.renderer.last_rendered_screen
                        visible=''.join(cell.char for row in screen.data_buffer.values() for cell in row.values())
                        self.assertNotIn('fixture-secret-only',visible)
                        pipe.send_text('\x13');await asyncio.sleep(.08)
                        self.assertEqual(editor.step,3);self.assertFalse(store.profile_path().exists())
                        pipe.send_text('\x13');await asyncio.sleep(.08)
                        self.assertEqual(out.getvalue(),'');self.assertEqual(editor.page,'result')
                        self.assertEqual(editor.fields['key'].text,'');self.assertNotIn('fixture-secret-only',editor.result_text)
                        save.assert_called_once();self.assertIn('Saved',editor.status)
                    finally:
                        if not task.done():editor.app.exit()
                        await task
        asyncio.run(exercise())

    def test_cancel_and_busy_guard_make_no_file_changes(self):
        from ui.endpoint_manager import EndpointManager
        from prompt_toolkit.application import create_app_session
        from prompt_toolkit.input.defaults import create_pipe_input
        from prompt_toolkit.output import DummyOutput
        async def exercise():
            with create_pipe_input() as pipe,create_app_session(input=pipe,output=DummyOutput()):
                editor=EndpointManager();editor.fields['key'].text='discard-fixture'
                task=asyncio.create_task(editor.app.run_async());await asyncio.sleep(.05)
                pipe.send_text('\x1b');await asyncio.wait_for(task,2)
                self.assertEqual(editor.fields['key'].text,'')
            with patch.dict(AGENT_STATE,{'task_active':True}),create_pipe_input() as pipe,create_app_session(input=pipe,output=DummyOutput()):
                editor=EndpointManager();self.assertEqual(editor.page,'blocked');editor.save()
        asyncio.run(exercise())
        self.assertFalse(store.profile_path().exists());self.assertFalse((self.home/'.secrets').exists())

    def test_settings_adds_section_and_preserves_existing_actions(self):
        from ui.settings_menu import InlineSettingsApp
        actions=[item['action'] for item in InlineSettingsApp()._get_items()]
        for action in ('to_endpoints','to_models','toggle_mode','toggle_fallback','toggle_stream','to_connections','exit'):
            self.assertIn(action,actions)
        self.assertEqual(actions[-1],'exit')


if __name__=='__main__':unittest.main()
