import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from engine.task_contract import TaskContract
from engine.action_instructions import response_contract
from engine.payload_builder import _build_request_data
from routers.manager import _extract_stream_chunk, _extract_response_content, ProviderStreamError
from storage.endpoints import ENDPOINT_REGISTRY


class RemoteActionContractTests(unittest.TestCase):
    def test_context_mentions_do_not_declare_new_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            request = ('Create index.html in the current directory as a complete HTML5 page. '
                       'Set title and h1 to Demo. Use real Galam file actions.')
            contract = TaskContract(request, directory)
            self.assertEqual([(r['kind'], r['path'].name) for r in contract.requirements], [('file', 'index.html')])
            Path(directory, 'index.html').write_text('<!DOCTYPE html><h1>Demo</h1>')
            self.assertEqual(contract.missing(), [])
            self.assertFalse(contract.complete_scope)

    def test_explicit_file_wins_over_later_directory_context(self):
        with tempfile.TemporaryDirectory() as directory:
            contract = TaskContract('Create notes.txt in the current directory and a folder named output', directory)
            self.assertEqual([(r['kind'], r['path'].name) for r in contract.requirements],
                             [('file', 'notes.txt'), ('directory', 'output')])

    def test_cloudflare_v1_route_is_not_overridden_by_model_prefix(self):
        name = '@cf/test/model'
        with patch.dict(ENDPOINT_REGISTRY, {'Cloudflare': {'models': {name: {'route_type': 'v1'}}}}):
            url, _, payload = _build_request_data('Cloudflare', name, [{'role': 'user', 'content': 'hi'}], True)
        self.assertTrue(url.endswith('/ai/v1/chat/completions'))
        self.assertEqual(payload['model'], name)

    def test_cloudflare_legacy_run_remains_explicitly_available(self):
        name = '@cf/test/model'
        with patch.dict(ENDPOINT_REGISTRY, {'Cloudflare': {'models': {name: {'route_type': 'run'}}}}):
            url, _, _ = _build_request_data('Cloudflare', name, [], True)
        self.assertTrue(url.endswith('/ai/run/' + name))

    def test_error_inside_http_success_is_not_an_empty_model_answer(self):
        error = {'error': {'message': 'ResourceExhausted: request limit reached', 'code': 500}}
        for wrapper in (error, {'result': error}):
            with self.assertRaisesRegex(ProviderStreamError, 'request limit reached'):
                _extract_stream_chunk(wrapper)
            with self.assertRaises(ProviderStreamError):
                _extract_response_content('arbitrary', 'arbitrary', wrapper)

    def test_text_examples_are_complete_valid_calls_and_native_avoids_examples(self):
        import re
        examples = re.findall(r'<tool_call>(\{.*?\})</tool_call>', response_contract(False))
        self.assertEqual(len(examples), 4)
        from engine.tool_registry import validate_arguments
        for example in examples:
            call = json.loads(example)
            validate_arguments(call['name'], call['arguments'])
        self.assertNotIn('<tool_call>', response_contract(True))

    def test_action_coaching_does_not_prime_ordinary_chat_to_execute_tools(self):
        from routers import manager
        from config import AGENT_STATE
        with tempfile.TemporaryDirectory() as directory, patch.dict(AGENT_STATE, {'working_directory':directory}):
            chat = manager.build_enforced_messages([{'role':'user','content':'Hello'}], 'OpenRouter', 'fixture')
            action = manager.build_enforced_messages([{'role':'user','content':'Create a complete HTML page'}], 'OpenRouter', 'fixture')
        self.assertNotIn('GALAM ACTION RESPONSE CONTRACT', chat[0]['content'])
        self.assertIn('GALAM ACTION RESPONSE CONTRACT', action[0]['content'])

    def test_scalar_stream_tokens_preserve_digits_and_booleans(self):
        from engine.transparency import text_value
        for value, expected in ((0, '0'), (8, '8'), (False, 'false'), (True, 'true')):
            self.assertEqual(text_value(value), expected)
            self.assertEqual(_extract_stream_chunk({'response': value})[0], expected)

    def test_cohere_v2_text_reaches_the_shared_tool_parser(self):
        call = '<tool_call>{"name":"execute_bash","arguments":{"command":"pwd"}}</tool_call>'
        text, _ = _extract_response_content('Direct', 'cohere-model',
            {'message': {'role': 'assistant', 'content': [{'type': 'text', 'text': call}]}})
        from engine.tool_parser import extract_tools_from_text
        self.assertEqual(extract_tools_from_text(text)[0]['function']['name'], 'execute_bash')
        self.assertEqual(_extract_stream_chunk({'type': 'content-delta', 'delta': {
            'message': {'content': {'text': call}}}})[0], call)

    def test_missing_tool_deltas_retry_same_model_once_without_executing_reasoning(self):
        from types import SimpleNamespace
        from routers import manager
        from config import CONFIG, AGENT_STATE
        first = SimpleNamespace(ok=True, status_code=200, headers={'Content-Type': 'text/event-stream'},
            iter_lines=lambda: iter([
                b'data: {"choices":[{"delta":{"reasoning_content":"I will use a tool"}}]}',
                b'data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}]}', b'data: [DONE]']))
        call = {'id': 'actual', 'type': 'function', 'function': {'name': 'execute_bash', 'arguments': '{"command":"pwd"}'}}
        second = SimpleNamespace(ok=True, status_code=200, headers={'Content-Type':'application/json'},
            json=lambda: {'choices':[{'message':{'content':None,'tool_calls':[call]}}]})
        requests=[]
        def send(url, headers, payload, stream, timeout, **kwargs):
            requests.append((dict(payload),stream))
            return (first if len(requests)==1 else second), False
        with patch.dict(ENDPOINT_REGISTRY, {'OpenRouter': {'models':{'fixture': {'context_window':8192}}}}, clear=True), \
             patch.dict(CONFIG, {'active_endpoint':'OpenRouter','active_model':'fixture','stream':True}), \
             patch.dict(AGENT_STATE, {'working_directory':tempfile.gettempdir(),'app_ref':None,'abort':False}), \
             patch.object(manager,'_post_with_transport_retry',side_effect=send), \
             patch.object(manager,'save_settings'), patch.object(manager,'log_model_interaction'):
            response, model = manager.query_with_fallback([{'role':'user','content':'Run pwd'}])
        self.assertEqual(model,'fixture')
        self.assertEqual(response.tool_calls,[call])
        self.assertEqual(len(requests),2)
        self.assertEqual([r[0]['model'] for r in requests],['fixture','fixture'])
        self.assertEqual([r[1] for r in requests],[True,False])

    def test_quota_retry_is_bounded_and_uses_the_same_payload(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from routers import manager
        limited = Mock(status_code=429, headers={'Retry-After': '2'})
        success = SimpleNamespace(status_code=200)
        payload = {'model': 'selected-model', 'messages': []}
        with patch.object(manager.SESSION, 'post', side_effect=[limited, success]) as post, \
             patch.object(manager.time, 'sleep') as sleep, patch.dict(manager.AGENT_STATE, {'abort': False}):
            response, _ = manager._post_with_transport_retry('https://fixture.invalid', {}, payload, True, 10)
        self.assertIs(response, success)
        self.assertEqual(post.call_count, 2)
        self.assertIs(post.call_args_list[0].kwargs['json'], post.call_args_list[1].kwargs['json'])
        sleep.assert_called_once_with(2.0)
        limited.close.assert_called_once()
        self.assertIsNone(manager._rate_limit_delay(Mock(status_code=429, headers={'Retry-After': '120'})))


if __name__ == '__main__':
    unittest.main()
