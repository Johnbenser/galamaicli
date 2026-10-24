"""Inline settings, faithful history presentation and observed JSON framing."""
import contextlib
import copy
import io
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from config import AGENT_STATE
from engine import api_protocols as protocol,usage
from routers import manager
from ui import dock_layout as dock
from ui.chat_transcript import assistant_text,user_text


class InlinePresentationTests(unittest.TestCase):
    def test_settings_and_editor_stay_inline_and_clear_only_their_region(self):
        from ui.settings_menu import InlineSettingsApp
        from ui.endpoint_manager import EndpointManager
        from prompt_toolkit.application import create_app_session
        from prompt_toolkit.input.defaults import create_pipe_input
        from prompt_toolkit.output import DummyOutput
        with create_pipe_input() as pipe,create_app_session(input=pipe,output=DummyOutput()):
            menu=InlineSettingsApp();app=menu.build_application()
            self.assertFalse(app.full_screen);self.assertTrue(app.erase_when_done)
            text=''.join(part for _,part in menu.get_formatted_text())
            self.assertTrue(text.startswith('╭── Galam.AI Settings'))
            self.assertIn('│ ▶ ',text);self.assertEqual(text.count('\n'),9)
            editor=EndpointManager()
            self.assertFalse(editor.app.full_screen);self.assertTrue(editor.app.erase_when_done)
            self.assertEqual(len(editor.sections),4)

    def test_resume_uses_live_message_layout_without_changing_context_or_running_actions(self):
        import galam
        stats={'elapsed':61,'finished_at':100,'usage':{'input':10,'output':4,'total':14,'source':'provider'}}
        messages=[{'role':'user','content':'Build a page'},
            {'role':'assistant','content':'','_galam_tool_calls':[{'id':'call1'}]},
            {'role':'user','content':'PRIVATE_TOOL_OBSERVATION','_galam_action':{'status':'completed'}},
            {'role':'assistant','content':'## Done\nCreated `index.html`.','_galam_response_stats':stats}]
        before=copy.deepcopy(messages)
        with patch.object(galam,'query_with_fallback') as request,patch.object(galam,'execute_tool_call') as action,contextlib.redirect_stdout(io.StringIO()) as output:
            galam.display_resumed_history(messages)
        text=output.getvalue()
        self.assertIn(user_text('Build a page'),text)
        self.assertIn(assistant_text(messages[-1]['content']),text)
        self.assertIn('Worked for 1m 01s',text)
        self.assertNotIn('PRIVATE_TOOL_OBSERVATION',text)
        self.assertNotIn('You:\n',text);self.assertNotIn('Galam:\n',text)
        self.assertEqual(messages,before);request.assert_not_called();action.assert_not_called()

    def test_fallback_review_hides_thinking_but_keeps_review_prompt(self):
        with patch.dict(AGENT_STATE,{'task_active':True,'abort':False,'review_pending':True,
                   'review_prompt':'Switch endpoint [Y/n]> ','in_btw_mode':False}),patch.object(dock,'prompt_session',SimpleNamespace(app=SimpleNamespace(is_done=False))):
            visible=''.join(part for _,part in dock.get_prompt_text())
            self.assertIn('Switch endpoint [Y/n]> ',visible)
            self.assertNotIn('Thinking',visible);self.assertNotIn('Contacting',visible)


class ResponseFramingTests(unittest.TestCase):
    def test_json_records_handle_sse_ndjson_pretty_json_and_unfinished_input(self):
        events=[{'message':{'content':'Hello '}},{'message':{'content':'world.'},'done':True}]
        for lines in ([json.dumps(row).encode() for row in events],
                      [json.dumps(row,indent=2).encode() for row in events],
                      [b'event: message']+[('data: '+json.dumps(row)).encode() for row in events]+[b'data: [DONE]']):
            rows=[json.loads(protocol.stream_data(line)) for line in protocol.json_records(lines) if protocol.stream_data(line)!='[DONE]']
            self.assertEqual(rows,events)
        with self.assertRaises(ValueError):list(protocol.json_records([b'{"message":']))
        self.assertTrue(protocol.streaming_response('application/json','https://ollama.com/api/chat'))
        self.assertFalse(protocol.streaming_response('application/json','https://api.example/v1/chat/completions'))

    def test_nonstream_decoder_accepts_unexpected_json_streams_for_both_transports(self):
        for urllib in (False,True):
            usage.start_task('fixture')
            events=[{'message':{'content':'Hello ','thinking':'Checking.'},'done':False},
                    {'message':{'content':'world.'},'done':True,'prompt_eval_count':10,'eval_count':3}]
            raw='\n'.join(json.dumps(event) for event in events)
            response=SimpleNamespace(text=raw,json=lambda:json.loads(raw),read=lambda:raw.encode())
            usage.attach(response,usage.begin_request('fixture','fixture',{}))
            result=manager._response_json(response,urllib)
            self.assertEqual(manager._extract_response_content('fixture','fixture',result),('Hello world.','Checking.'))
            self.assertEqual(usage.totals()['total'],13)
            self.assertEqual(response._galam_finish_reason,'stop')

    def test_unexpected_openai_stream_assembles_tool_fragments_once(self):
        events=[{'choices':[{'delta':{'tool_calls':[{'index':0,'id':'call1','function':{'name':'execute_bash','arguments':'{"command":'}}]}}]},
                {'choices':[{'delta':{'tool_calls':[{'index':0,'function':{'arguments':'"pwd"}'}}]},'finish_reason':'tool_calls'}]}]
        raw='\n'.join(json.dumps(event) for event in events)
        response=SimpleNamespace(text=raw,json=lambda:json.loads(raw))
        data=manager._response_json(response)
        tools=manager._detect_and_synthesize_tool_calls('',data)
        self.assertEqual(len(tools),1);self.assertEqual(tools[0]['id'],'call1')
        self.assertEqual(json.loads(tools[0]['function']['arguments']),{'command':'pwd'})
