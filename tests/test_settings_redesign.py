"""Regression coverage for exact routes, native actions and terminal selection."""
import asyncio
import contextlib
import io
import json
import threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from unittest.mock import patch
from prompt_toolkit.application import create_app_session
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput
from prompt_toolkit.data_structures import Size
from test_custom_connections import Workspace
from config import CONFIG
from storage.endpoints import ENDPOINT_REGISTRY
from storage import user_endpoints as store
from routers import manager
from engine import usage


class NativeOllamaTests(Workspace):
    def test_separate_native_tool_chunks_keep_distinct_action_indexes(self):
        from types import SimpleNamespace
        response=SimpleNamespace()
        chunk={'message':{'tool_calls':[{'function':{'name':'execute_bash','arguments':{'command':'pwd'}}}]}}
        first=manager._extract_stream_chunk(chunk,response)[2][0]
        second=manager._extract_stream_chunk(chunk,response)[2][0]
        self.assertEqual((first['index'],second['index']),(0,1))
        self.assertNotEqual(first['id'],second['id'])

    def test_exact_native_url_json_and_ndjson_actions_and_usage(self):
        from engine.task_runner import execute_tool_call
        calls=[]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                calls.append((self.path,body))
                second=body['messages'][-1]['role']=='tool'
                message={'role':'assistant','content':'Terminal returned OLLAMA_ACTION_OK.'} if second else {
                    'role':'assistant','content':'','thinking':'Checking terminal output.',
                    'tool_calls':[{'function':{'name':'execute_bash','arguments':{'command':"printf OLLAMA_ACTION_OK"}}}]}
                result={'model':body['model'],'message':message,'done':True,'done_reason':'stop',
                        'prompt_eval_count':71,'eval_count':19}
                mode=self.server.fixture_mode
                if mode in ('mislabeled','ignores_stream'):
                    prefix={'model':body['model'],'message':{'content':'Terminal returned ' if second else '',
                            'thinking':'' if second else 'Checking terminal output.'},'done':False}
                    if second:result['message']['content']='OLLAMA_ACTION_OK.'
                    else:result['message'].pop('thinking',None)
                    data=('\n'.join(json.dumps(row) for row in (prefix,result))+'\n').encode()
                else:data=(json.dumps(result,indent=2 if mode=='pretty' else None)+'\n').encode()
                self.send_response(200)
                self.send_header('Content-Type','application/x-ndjson' if mode=='normal' and body.get('stream') else 'application/json')
                self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            for mode,streaming in (('normal',False),('normal',True),('mislabeled',True),('ignores_stream',False),('pretty',True)):
                server.fixture_mode=mode
                with self.subTest(streaming=streaming,mode=mode):
                    usage.start_task('native')
                    url=f'http://127.0.0.1:{server.server_port}/api/chat'
                    profile=store.save_profile({**self.profile,'model':'native-'+mode+'-'+str(streaming),'url':url,'api_format':'default'})
                    self.assertEqual(profile['api_format'],'ollama');self.assertEqual(profile['url'],url)
                    messages=[{'role':'user','content':'Execute a bash command in the terminal then report its output.'}]
                    with patch.dict(CONFIG,{'active_endpoint':profile['endpoint'],'active_model':profile['model'],'stream':streaming}), \
                         patch.object(manager,'save_settings'),patch.object(manager,'log_model_interaction'),contextlib.redirect_stdout(io.StringIO()):
                        answer,_=manager.query_with_fallback(messages)
                        self.assertEqual(len(answer.tool_calls),1)
                        call=answer.tool_calls[0]
                        self.assertEqual(call['function']['name'],'execute_bash')
                        with patch('engine.task_runner.log_tool_execution'):
                            result=execute_tool_call('execute_bash',json.loads(call['function']['arguments']))
                        self.assertTrue(result.success);self.assertIn('OLLAMA_ACTION_OK',result.output)
                        messages.extend([{'role':'assistant','content':'','_galam_tool_calls':answer.tool_calls},
                            {'role':'user','content':result.output,'_galam_call_id':call['id'],
                             '_galam_action':{'status':'completed','tool':'execute_bash','exit_code':0}}])
                        final,_=manager.query_with_fallback(messages)
                        self.assertIn('OLLAMA_ACTION_OK',final.content)
                    self.assertEqual(calls[-1][0],'/api/chat')
                    sent=calls[-1][1]
                    self.assertEqual(sent['messages'][-1]['tool_name'],'execute_bash')
                    self.assertIsInstance(sent['messages'][-2]['tool_calls'][0]['function']['arguments'],dict)
                    self.assertIn('num_predict',sent['options']);self.assertNotIn('max_tokens',sent)
                    self.assertNotIn('_galam_',json.dumps(sent))
                    self.assertEqual(usage.totals()['total'],180)
        finally:server.shutdown();server.server_close();thread.join(2)


class SettingsKeyboardTests(Workspace):
    def test_highlight_direct_then_tab_confirm_saves_direct_and_keeps_url(self):
        from ui.endpoint_manager import EndpointManager
        ENDPOINT_REGISTRY.update({'Direct':{'base_url':'','models':{}},'Groq':{'base_url':'https://groq.invalid/v1','models':{}}})
        async def exercise():
            with patch.dict(CONFIG,{'active_endpoint':'Groq'}),create_pipe_input() as pipe,create_app_session(input=pipe,output=DummyOutput()):
                editor=EndpointManager()
                editor.fields['url'].text='https://ollama.com/api/chat'
                editor.fields['model'].text='fixture-native'
                editor.fields['api_key_env'].text='OLLAMA_API_KEY'
                task=asyncio.create_task(editor.app.run_async())
                try:
                    editor.pick_endpoint();editor.app.layout.focus(editor.picker)
                    await asyncio.sleep(.06)
                    pipe.send_text('\x1b[A');await asyncio.sleep(.06)
                    self.assertEqual(editor.picker.current_value,'Direct')
                    pipe.send_text('\t\r');await asyncio.sleep(.06)
                    self.assertEqual(editor.selected_endpoint,'Direct')
                    self.assertEqual(editor.fields['url'].text,'https://ollama.com/api/chat')
                    self.assertEqual(editor.fields['api_key_env'].text,'OLLAMA_API_KEY')
                    editor.step=3;editor.focus_step()
                    self.assertIn('Endpoint group: Direct',editor.review_text())
                    with patch('ui.endpoint_manager.save_settings') as save:
                        pipe.send_text('\x13');await asyncio.sleep(.08)
                    self.assertEqual(editor.page,'result')
                    save.assert_called_once_with(active_endpoint='Direct',endpoint='Direct',active_model='fixture-native',model='fixture-native')
                    profile=store.read_profiles()[0]
                    self.assertEqual((profile['endpoint'],profile['api_format']),('Direct','ollama'))
                finally:
                    if not task.done():editor.app.exit()
                    await task
        asyncio.run(exercise())

    def test_settings_render_phone_sizes_and_open_embedded_editor(self):
        from ui.settings_menu import InlineSettingsApp
        class Phone(DummyOutput):
            def __init__(self,columns):super().__init__();self.columns=columns
            def get_size(self):return Size(rows=24,columns=self.columns)
        async def exercise(columns):
            with create_pipe_input() as pipe,create_app_session(input=pipe,output=Phone(columns)):
                menu=InlineSettingsApp();app=menu.build_application()
                task=asyncio.create_task(app.run_async())
                try:
                    await asyncio.sleep(.08)
                    self.assertIsNotNone(app.renderer.last_rendered_screen)
                    menu.cursor_idx=next(i for i,item in enumerate(menu._get_items()) if item.get('action')=='to_connections')
                    pipe.send_text('\r');await asyncio.sleep(.08)
                    self.assertEqual(menu.screen,'connections')
                    self.assertIs(menu.editor.app,app)
                    async def rendered_editor():
                        while True:
                            screen=app.renderer.last_rendered_screen
                            visible=''.join(cell.char for row in screen.data_buffer.values() for cell in row.values())
                            if 'MODEL' in visible:return visible
                            await asyncio.sleep(.02)
                    visible=await asyncio.wait_for(rendered_editor(),3)
                    self.assertNotIn('Window too small',visible)
                    pipe.send_text('\x1b');await asyncio.sleep(.3)
                    self.assertEqual(menu.screen,'main')
                    self.assertFalse(store.profile_path().exists())
                finally:
                    if not task.done():app.exit()
                    await task
        for columns in (44,28):asyncio.run(exercise(columns))
