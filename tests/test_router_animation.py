"""Agent Router isolation and network-independent animation regression tests."""
import contextlib
import io
import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from config import CONFIG, AGENT_STATE
from engine.agent_router import request_headers, client_rejection
from engine.payload_builder import build_request_data
from routers import manager
from ui import dock_layout as dock


class RouterHeaderTests(unittest.TestCase):
    def test_headers_are_scoped_to_endpoint_and_exact_https_host(self):
        original = {'Authorization':'Bearer secret','Content-Type':'application/json'}
        for endpoint, url in [('Groq','https://agentrouter.org/v1'),
            ('Agent Router','https://agentrouter.org.attacker.example/v1'),
            ('Agent Router','https://notagentrouter.org/v1'),
            ('Agent Router','http://agentrouter.org/v1')]:
            self.assertEqual(request_headers(endpoint,url,original,True,{}),original)
        for path in ('/v1/chat/completions','/v1/responses','/v1/messages'):
            streaming = request_headers('Agent Router','https://agentrouter.org'+path,original,True,{})
            self.assertEqual(streaming['Accept'],'text/event-stream')
            self.assertEqual(streaming['User-Agent'],'GalamAI')
            self.assertEqual(streaming['Authorization'],'Bearer secret')
        self.assertNotIn('User-Agent', original)

    def test_approved_header_overrides_are_case_insensitive_and_validated(self):
        original = {'User-Agent':'GalamAI','Authorization':'Bearer secret'}
        headers = request_headers('Agent Router','https://agentrouter.org/v1',original,False,
            {'agent_router_headers':{'user-agent':'GalamAI/approved'}})
        self.assertNotIn('User-Agent',headers)
        self.assertEqual(headers['user-agent'],'GalamAI/approved')
        for invalid in ({'Authorization':'wrong'}, {'Host':'wrong'}, {'User-Agent':'bad\r\nInjected: value'}):
            with self.assertRaises(ValueError):
                request_headers('Agent Router','https://agentrouter.org/v1',original,False,
                    {'agent_router_headers':invalid})

    def test_payload_builder_uses_the_same_identity_for_stream_and_json(self):
        with patch.dict(CONFIG,{'agent_router_headers':{'originator':'galamaicli-approved'}}):
            for stream in (False,True):
                url,headers,_=build_request_data('Agent Router','deepseek-v4-flash',[],stream)
                self.assertEqual(headers['originator'],'galamaicli-approved')
                self.assertEqual(headers['Accept'],'text/event-stream' if stream else 'application/json')
                self.assertEqual(url,'https://agentrouter.org/v1/chat/completions')

    def test_client_denial_stops_model_fallback_without_saving_settings(self):
        body={'type':'unauthorized_client_error','error':{'message':'unauthorized client detected'}}
        response=Mock(ok=False,status_code=401,text=json.dumps(body))
        response.json.return_value=body
        for stream in (False,True):
            with patch.dict(CONFIG,{'active_endpoint':'Agent Router','active_model':'deepseek-v4-flash',
                  'stream':stream,'fallback_policy':'auto'}), patch.dict(AGENT_STATE,{'abort':False,'app_ref':None}), \
                  patch.object(manager,'_post_with_transport_retry',return_value=(response,False)) as post, \
                  patch.object(manager,'save_settings') as save, \
                  patch.object(manager,'request_user_confirmation') as ask, contextlib.redirect_stdout(io.StringIO()) as output:
                result,model=manager.query_with_fallback([{'role':'user','content':'Reply OK'}])
                self.assertEqual(result,'FAILED');self.assertEqual(model,'deepseek-v4-flash')
                self.assertIn('API key validity is unverified',output.getvalue())
                self.assertNotIn('missing/invalid API key',output.getvalue())
                post.assert_called_once();save.assert_not_called();ask.assert_not_called()

    def test_other_authentication_errors_are_not_called_client_denials(self):
        self.assertEqual(client_rejection('Agent Router','https://agentrouter.org/v1',
            {'error':{'message':'Invalid API key'}}),'')
        self.assertEqual(client_rejection('Groq','https://api.groq.com/v1',
            {'error':{'message':'unauthorized client detected'}}),'')

    def test_sse_denial_stops_after_one_request(self):
        body={'error':{'message':'unauthorized client detected','type':'unauthorized_client_error'}}
        response=Mock(ok=True,status_code=200,headers={'Content-Type':'text/event-stream'})
        response.iter_lines.return_value=iter([('data: '+json.dumps(body)).encode()])
        with patch.dict(CONFIG,{'active_endpoint':'Agent Router','active_model':'deepseek-v4-flash',
             'stream':True,'fallback_policy':'auto'}), patch.dict(AGENT_STATE,{'abort':False,'app_ref':None}), \
             patch.object(manager,'_post_with_transport_retry',return_value=(response,False)) as post, \
             patch.object(manager,'save_settings') as save, contextlib.redirect_stdout(io.StringIO()) as output:
            result,_=manager.query_with_fallback([{'role':'user','content':'Reply OK'}])
        self.assertEqual(result,'FAILED');post.assert_called_once();save.assert_not_called()
        self.assertIn('client identity',output.getvalue())

    def test_urllib_denial_does_not_retry_without_certificate_verification(self):
        import urllib.error
        body=io.BytesIO(b'{"error":{"message":"unauthorized client detected"}}')
        error=urllib.error.HTTPError('https://agentrouter.org/v1',401,'Unauthorized',{},body)
        from engine.agent_router import ClientAdmissionError
        with patch('urllib.request.urlopen',side_effect=error) as post, \
             patch('ssl._create_unverified_context') as unverified:
            with self.assertRaises(ClientAdmissionError):
                manager.urllib_stream_request('https://agentrouter.org/v1',{}, {}, 5)
        post.assert_called_once();unverified.assert_not_called()


class AnimationTests(unittest.TestCase):
    def test_clock_refreshes_without_response_tokens_during_all_active_phases(self):
        for streaming, thinking, task in [(False,True,True),(True,True,True),(False,False,True)]:
            app=Mock()
            with patch.dict(AGENT_STATE,{'app_ref':app,'is_streaming':streaming,'is_thinking':thinking,
                                       'task_active':task,'abort':False,'activity_label':'Thinking'}):
                for _ in range(3):
                    dock._refresh_thinking_animation()
                self.assertEqual(app.invalidate.call_count,3)
                self.assertIn('Thinking',''.join(text for _,text in dock.get_prompt_text()))

    def test_idle_and_cancelled_tasks_do_not_animate(self):
        for task, abort in [(False,False),(True,True)]:
            app=Mock()
            with patch.dict(AGENT_STATE,{'app_ref':app,'is_thinking':False,'task_active':task,'abort':abort}):
                dock._refresh_thinking_animation()
                app.invalidate.assert_not_called()

    def test_closed_display_does_not_kill_the_clock(self):
        app=Mock();app.invalidate.side_effect=RuntimeError('closed')
        with patch.dict(AGENT_STATE,{'app_ref':app,'task_active':True,'abort':False}):
            dock._refresh_thinking_animation()


if __name__=='__main__':
    unittest.main()
