"""Shared action protocol and model-output trace behavior across response formats."""
import contextlib
import io
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import test_pipelines as fixtures
from config import CONFIG, AGENT_STATE
from engine import tool_protocol as protocol
from engine.context_manager import assemble_context, estimate_tokens
from engine.transparency import ReasoningStream, trace_text
from engine.tool_registry import validate_arguments


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.cache = patch.object(protocol, "_UNSUPPORTED", set())
        self.cache.start()

    def tearDown(self):
        self.cache.stop()

    def test_native_schema_is_derived_from_the_existing_argument_contract(self):
        tools = protocol.tool_schemas()
        self.assertEqual({t['function']['name'] for t in tools}, set(protocol.TOOLS))
        write = next(t['function'] for t in tools if t['function']['name'] == 'write_file')
        self.assertEqual(write['parameters']['required'], ['path', 'content'])
        self.assertFalse(write['parameters']['additionalProperties'])
        with self.assertRaises(ValueError):
            validate_arguments('write_file', {'path': 'a.txt'})

    def test_action_requests_get_tools_and_ordinary_chat_does_not(self):
        for endpoint in ('Local Endpoint', 'OpenRouter', 'NVIDIA', 'Groq', 'ModelScope'):
            self.assertIn('tools', protocol.options(endpoint, 'arbitrary-model', [{'role': 'user', 'content': 'Create a file called notes.txt'}], {}))
            self.assertEqual(protocol.options(endpoint, 'arbitrary-model', [{'role': 'user', 'content': 'Explain how to create a file'}], {}), {})
        self.assertEqual(protocol.options('Direct', 'model', [{'role': 'user', 'content': 'Create a file'}], {}), {})

    def test_required_repair_resets_after_tool_observation(self):
        messages = [{'role': 'user', 'content': 'Create a file', '_galam_tool_mode': 'required'}]
        self.assertEqual(protocol.options('Local Endpoint', 'model', messages, {})['tool_choice'], 'required')
        messages.append({'role': 'user', 'content': '[Tool Observation]', '_galam_tool_mode': 'auto'})
        self.assertEqual(protocol.options('Local Endpoint', 'model', messages, {})['tool_choice'], 'auto')

    def test_unsupported_tools_fallback_is_scoped_and_does_not_hide_auth_errors(self):
        response = Mock(status_code=400, text='tools not supported')
        response.json.return_value = {'error': {'message': 'tools not supported'}}
        cfg = {'local_endpoint_url': 'http://127.0.0.1:8080/v1'}
        self.assertTrue(protocol.reject_unsupported(response, 'Local Endpoint', 'model', cfg, {'tools': []}))
        messages = [{'role': 'user', 'content': 'Read file.py'}]
        self.assertEqual(protocol.options('Local Endpoint', 'model', messages, cfg), {})
        self.assertIn('tools', protocol.options('Local Endpoint', 'another-model', messages, cfg))
        response.status_code = 401
        self.assertFalse(protocol.reject_unsupported(response, 'Local Endpoint', 'another-model', cfg, {'tools': []}))

    def test_http_tool_rejection_retries_the_text_protocol_once(self):
        from routers import manager
        from storage.endpoints import ENDPOINT_REGISTRY
        error = SimpleNamespace(ok=False, status_code=400, text="tools unsupported", headers={},
                                json=lambda: {"error": {"message": "tools not supported"}})
        success = SimpleNamespace(ok=True, status_code=200, headers={"Content-Type": "application/json"},
                    json=lambda: {"choices": [{"message": {"content": '<tool_call>{"name":"write_file","arguments":{"path":"a.txt","content":"hello"}}</tool_call>'}}]})
        seen = []
        def transport(url, headers, payload, *args, **kwargs):
            seen.append(dict(payload))
            return (error if len(seen) == 1 else success), False
        with patch.dict(ENDPOINT_REGISTRY, {"Local Endpoint": {"base_url": "http://fixture.invalid/v1", "models": {"generic": {"context_window": 8192}}}}), patch.dict(CONFIG, {"active_endpoint": "Local Endpoint", "active_model": "generic", "stream": False}), patch.dict(AGENT_STATE, {"app_ref": None, "abort": False, "show_thoughts": False}), patch("engine.local_models.refresh_local_models", return_value=["generic"]), patch.object(manager, "_post_with_transport_retry", side_effect=transport), patch.object(manager, "save_settings"), patch.object(manager, "log_model_interaction"):
            result, _ = manager.query_with_fallback([{"role": "user", "content": "Create a.txt containing hello"}])
        self.assertEqual(len(seen), 2)
        self.assertIn("tools", seen[0])
        self.assertNotIn("tools", seen[1])
        self.assertEqual(result.tool_calls[0]["function"]["name"], "write_file")


    def test_native_history_pairs_calls_results_and_unexecuted_feedback(self):
        calls = [{"id": "one", "type": "function", "function": {"name": "write_file", "arguments": '{"path":"a.txt","content":"alpha"}'}},
                 {"id": "two", "type": "function", "function": {"name": "execute_bash", "arguments": '{"command":"mkdir output"}'}}]
        messages = [{"role": "user", "content": "Create a.txt and output"},
                    {"role": "assistant", "content": "", "_galam_tool_calls": calls},
                    {"role": "user", "content": "Saved alpha", "_galam_call_id": "one", "_galam_action": {"status": "completed"}},
                    {"role": "user", "content": "Second action declined", "_galam_internal": True}]
        wire = protocol.wire_messages(messages)
        self.assertEqual([m['role'] for m in wire], ['user', 'assistant', 'tool', 'tool', 'user'])
        self.assertEqual(wire[1]['tool_calls'], calls)
        self.assertEqual(wire[2]['tool_call_id'], 'one')
        self.assertEqual(wire[2]['content'], 'Saved alpha')
        self.assertIn('Not executed', wire[3]['content'])
        self.assertEqual(wire[3]['tool_call_id'], 'two')

    def test_context_keeps_native_call_and_all_its_results_together(self):
        calls = [{"id": "one", "type": "function", "function": {"name": "view_file", "arguments": '{"path":"a.py"}'}}]
        raw = [{"role": "user", "content": "Read a.py"}, {"role": "assistant", "content": "", "_galam_tool_calls": calls},
               {"role": "user", "content": "[Tool Observation]: actual code", "_galam_call_id": "one", "_galam_action": {"status": "completed"}}]
        chosen, _ = assemble_context(protocol.wire_messages(raw), "Assistant protocol", 'Local Endpoint', 'fixture',
                                      {"max_tokens": 256, "context_window": 2048}, '/tmp')
        assistant = next(m for m in chosen if m.get('tool_calls'))
        result = next(m for m in chosen if m['role'] == 'tool')
        self.assertEqual(assistant['tool_calls'][0]['id'], result['tool_call_id'])

    def test_old_native_groups_are_evicted_without_orphan_results(self):
        calls = [{"id": "old", "type": "function", "function": {"name": "view_file", "arguments": '{"path":"a.py"}'}}]
        messages = [{"role": "user", "content": "Read a.py"}, {"role": "assistant", "content": "", "_galam_tool_calls": calls},
                    {"role": "user", "content": "x" * 4500, "_galam_call_id": "old", "_galam_action": {"status": "completed"}},
                    {"role": "assistant", "content": "Read done."}, {"role": "user", "content": "Hello"}]
        chosen, _ = assemble_context(protocol.wire_messages(messages), "Assistant protocol", 'Local Endpoint', 'fixture',
                                      {"max_tokens": 512, "context_window": 2048}, '/tmp')
        self.assertEqual(any(m.get('tool_calls') for m in chosen), any(m['role'] == 'tool' for m in chosen))


    def test_native_schema_cost_is_reserved_even_in_small_contexts(self):
        messages = [{'role': 'user', 'content': 'Create an empty notes.txt'}]
        options = protocol.options('Local Endpoint', 'model', messages, {})
        cost = estimate_tokens(options) + 16
        result, usage = assemble_context(messages, 'long identity ' * 500, 'Local Endpoint', 'model',
                                         {'max_tokens': 512, 'context_window': 2048, '_tool_schema_tokens': cost}, '/tmp')
        self.assertLess(estimate_tokens(result) + cost + usage['reserved_output'], 2048)
        self.assertEqual(usage['tool_schema_tokens'], cost)

    def test_identical_response_gets_changed_guidance_without_ending_the_task(self):
        repair = protocol.ActionRepair()
        self.assertIsNotNone(repair.next_message('Create arbitrary.txt', 'Creating the file.', 0))
        self.assertIn('Change the action format',repair.next_message('Create arbitrary.txt', 'Creating  the file.', 0)['content'])
        self.assertIsNotNone(repair.next_message('Create arbitrary.txt', 'Creating the file.', 1))


class ActionLoopTests(fixtures.Workspace):
    response = fixtures.LoopTests.response
    run_loop = fixtures.LoopTests.run_loop

    def test_repeated_action_prose_recovers_to_real_outputs_without_a_retry_cap(self):
        responses = [self.response(text='Creating the requested file and folder.') for _ in range(4)]
        responses += [self.response('write_file',{'path':'fresh.txt','content':''}),
                      self.response('make_directory',{'path':'output'})]
        messages, spy = self.run_loop(responses, 'Create a file named fresh.txt then make a folder named output')
        self.assertEqual(self.model_calls, 6)
        self.assertEqual(spy.call_count,2)
        self.assertTrue((self.root/'fresh.txt').is_file())
        self.assertTrue((self.root/'output').is_dir())
        self.assertEqual(AGENT_STATE['task_outcome'],'completed')

    def test_native_repair_runs_actual_generic_file_and_directory_tools(self):
        responses = [self.response(text='Creating the requested outputs.'),
                     self.response('write_file', {'path': 'notes.txt', 'content': 'alpha\n'}),
                     self.response('execute_bash', {'command': 'mkdir -p output'}),
                     self.response(text='Created notes.txt and the output folder.')]
        messages, spy = self.run_loop(responses, 'Create notes.txt containing "alpha" and create a folder named output')
        self.assertEqual((self.root / 'notes.txt').read_text(), 'alpha\n')
        self.assertTrue((self.root / 'output').is_dir())
        self.assertEqual(self.model_calls, 3)
        self.assertNotIn('Query error', self.last_display)
        self.assertEqual(messages[-2].get('_galam_tool_mode'), 'auto')

    def test_clarification_does_not_force_guessed_mutations(self):
        messages, spy = self.run_loop([self.response(text='Which file should I modify?')], 'Modify the file')
        self.assertEqual(self.model_calls, 1)
        spy.assert_not_called()
        self.assertIn('Input needed', self.last_display)

    def test_verified_outputs_stop_before_speculative_extra_calls_in_same_batch(self):
        from routers.manager import AgentResponse
        calls = []
        for i, (tool, args) in enumerate([('make_directory', {'path': 'output'}),
                    ('write_file', {'path': 'notes.txt', 'content': 'alpha'}),
                    ('write_file', {'path': 'notes.txt', 'content': 'delta'})]):
            call = self.response(tool, args)[0].tool_calls[0]
            call['id'] = f'batch-{i}';calls.append(call)
        messages, spy = self.run_loop([(AgentResponse(tool_calls=calls), 'fixture')],
                                     'Create notes.txt containing "alpha" and create a folder named output')
        self.assertEqual(self.model_calls, 1)
        self.assertEqual(spy.call_count, 2)
        self.assertEqual((self.root / 'notes.txt').read_text(), 'alpha')
        self.assertIn('Verified directory:', messages[-1]['content'])

    def test_rejected_extra_proposal_does_not_delay_verified_completion(self):
        from routers.manager import AgentResponse
        calls = []
        for i, (tool, args) in enumerate([('make_directory', {'path': 'output'}),
                    ('write_file', {'path': 'notes.txt', 'content': 'delta'}),
                    ('write_file', {'path': 'notes.txt', 'content': 'alpha'})]):
            call = self.response(tool, args)[0].tool_calls[0]
            call['id'] = f'batch-{i}';calls.append(call)
        messages, spy = self.run_loop([(AgentResponse(tool_calls=calls), 'fixture')],
                                     'Create notes.txt containing "alpha" and create a folder named output')
        self.assertEqual(self.model_calls, 1)
        self.assertEqual(spy.call_count, 2)
        self.assertEqual((self.root / 'notes.txt').read_text(), 'alpha')
        self.assertIn('Verified file:', messages[-1]['content'])


    def test_directory_target_cannot_be_written_as_a_file(self):
        responses = [self.response('write_file', {'path': 'notes.txt', 'content': 'alpha'}),
                     self.response('write_file', {'path': 'output', 'content': 'not a directory'}),
                     self.response('make_directory', {'path': 'output'})]
        messages, spy = self.run_loop(responses, 'Create notes.txt containing "alpha" and create a folder named output')
        self.assertTrue((self.root / 'output').is_dir())
        self.assertEqual((self.root / 'notes.txt').read_text(), 'alpha')
        self.assertEqual([call.args[0] for call in spy.call_args_list], ['write_file', 'make_directory'])
        self.assertEqual(self.model_calls, 3)
        self.assertIn('Verified directory:', messages[-1]['content'])

    def test_false_completion_cannot_hide_a_missing_directory(self):
        responses = [self.response('write_file', {'path': 'notes.txt', 'content': 'alpha'}),
                     self.response(text='Task completed successfully.'),
                     self.response('make_directory', {'path': 'output'})]
        messages, _ = self.run_loop(responses, 'Create notes.txt containing "alpha" and create a folder named output')
        self.assertTrue((self.root / 'output').is_dir())
        self.assertTrue(any('Verified requirements still missing' in m.get('content', '') for m in messages))
        self.assertEqual(self.model_calls, 3)

    def test_completed_literal_output_is_protected_during_remaining_steps(self):
        responses = [self.response('write_file', {'path': 'notes.txt', 'content': 'alpha'}),
                     self.response('replace_file_content', {'path': 'notes.txt', 'target_content': 'alpha', 'replacement_content': 'delta'}),
                     self.response('make_directory', {'path': 'output'})]
        self.run_loop(responses, 'Create notes.txt containing "alpha" and create a folder named output')
        self.assertEqual((self.root / 'notes.txt').read_text(), 'alpha')
        self.assertIn('violates', self.last_display)

    def test_multi_step_receipt_does_not_skip_an_extra_requested_check(self):
        responses = [self.response('write_file', {'path': 'notes.txt', 'content': 'alpha'}),
                     self.response('make_directory', {'path': 'output'}),
                     self.response('execute_bash', {'command': 'true'}), self.response(text='The outputs exist and the check passed.')]
        self.run_loop(responses, 'Create notes.txt containing "alpha" and create a folder named output then run the check')
        self.assertEqual(self.model_calls, 4)

    def test_directory_creation_is_typed_and_preserves_file_collisions(self):
        from engine.task_runner import execute_tool_call
        from engine.execution_policy import requires_review
        self.assertTrue(requires_review('AUTO', 'make_directory', {'path': 'nested/output'}))
        result = execute_tool_call('make_directory', {'path': 'nested/output'})
        self.assertTrue(result.success)
        self.assertTrue(result.verified)
        self.assertTrue((self.root / 'nested/output').is_dir())
        (self.root / 'collision').write_text('preserve')
        result = execute_tool_call('make_directory', {'path': 'collision'})
        self.assertFalse(result.success)
        self.assertEqual((self.root / 'collision').read_text(), 'preserve')

    def test_contract_preserves_literal_punctuation_and_does_not_invent_about_path(self):
        from engine.task_contract import TaskContract
        contract = TaskContract('Create notes.txt containing "alpha." and create a folder named output', str(self.root))
        self.assertEqual(contract.requirements[0]['literal'], 'alpha.')
        contract = TaskContract('Create a HTML file about radio and create its folder', str(self.root))
        self.assertEqual(contract.missing(), [])
        self.assertFalse(contract.complete_scope)



class TraceTests(unittest.TestCase):
    def setUp(self):
        self.state = patch.dict(AGENT_STATE, {'app_ref': None, 'show_thoughts': False, 'reasoning_turns': [],
                                           'task_progress': [], 'last_reasoning': '', 'task_active': False}, clear=False)
        self.state.start()

    def tearDown(self):
        self.state.stop()

    def test_reasoning_tags_survive_every_token_boundary(self):
        for opening, closing in zip(ReasoningStream.OPEN, ReasoningStream.CLOSE):
            source = opening + 'Checking the requested file.' + closing + 'Done.'
            stream = ReasoningStream()
            answer, reasoning = '', ''
            for character in source:
                c, r = stream.feed(character)
                answer += c;reasoning += r
            c, r = stream.feed(final=True)
            self.assertEqual(answer + c, 'Done.')
            self.assertEqual(reasoning + r, 'Checking the requested file.')

    def test_think_tags_in_code_or_tool_arguments_are_not_removed(self):
        from engine.tool_parser import parse_reasoning_and_content
        text = '<tool_call>' + json.dumps({'name': 'write_file', 'arguments': {'path': 'a.html', 'content': '<think>literal markup</think>'}}) + '</tool_call>'
        reason, content = parse_reasoning_and_content(text)
        self.assertEqual(reason, '')
        self.assertEqual(content, text)
        stream = ReasoningStream()
        content, reason = stream.feed('```html\n<think>literal</think>\n```', final=True)
        self.assertIn('<think>literal</think>', content)
        self.assertEqual(reason, '')

    def test_trace_keeps_all_requests_and_ctrl_o_does_not_drop_the_prefix(self):
        from ui.dock_layout import StructuredThoughtStreamHandler, handle_ctrl_o, get_thought_panel_tokens, thought_panel
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            first = StructuredThoughtStreamHandler('model-one')
            first.update_thought('Checking the first file.')
            first.clear()
            second = StructuredThoughtStreamHandler('model-two')
            second.update_thought('Verifying the next result.')
            event = SimpleNamespace(app=Mock())
            handle_ctrl_o(event)
            panel = ''.join(text for _, text in get_thought_panel_tokens())
            self.assertIn('Verifying the next result.', panel)
            self.assertTrue(thought_panel.filter())
            self.assertIn('Checking the first file.', trace_text())
            handle_ctrl_o(event)
            self.assertFalse(thought_panel.filter())
            length = len(output.getvalue())
            second.update_thought(' Additional detail remains stored.')
            self.assertEqual(len(output.getvalue()), length)
            self.assertIn('Additional detail remains stored.', trace_text())
            self.assertEqual(output.getvalue(), '')
        self.assertIn('Checking the first file.', AGENT_STATE['last_reasoning'])

    def test_view_preference_survives_new_tasks_and_handlers(self):
        from ui.dock_layout import StructuredThoughtStreamHandler, handle_ctrl_o, thought_panel
        from engine.transparency import task_lifecycle
        event = SimpleNamespace(app=Mock())
        @task_lifecycle
        def next_query():
            handler = StructuredThoughtStreamHandler('next-model')
            handler.update_thought('A new response.')
        handle_ctrl_o(event)
        for _ in range(2):
            next_query()
            self.assertTrue(thought_panel.filter())
        handle_ctrl_o(event)
        next_query()
        self.assertFalse(thought_panel.filter())

    def test_compact_summary_uses_model_text_without_trigger_word_requirements(self):
        from ui.dock_layout import StructuredThoughtStreamHandler, get_prompt_text
        handler = StructuredThoughtStreamHandler('model')
        handler.update_thought('Compare the available options before choosing a command.')
        prompt = ''.join(text for _, text in get_prompt_text())
        self.assertIn('Thinking', prompt)
        self.assertIn('Compare the available options', prompt)
        handler.clear()

    def test_no_reasoning_is_reported_honestly_and_progress_is_retained(self):
        from ui.dock_layout import StructuredThoughtStreamHandler
        from engine.transparency import progress
        handler = StructuredThoughtStreamHandler('non-reasoning-model')
        progress('Waiting for approval: write_file')
        handler.complete_reasoning('')
        handler.clear()
        self.assertEqual(AGENT_STATE['last_reasoning'], '')
        self.assertIn('No reasoning supplied', trace_text())
        self.assertIn('Waiting for approval: write_file', trace_text())

    def test_json_reasoning_and_provider_details_are_normalized(self):
        from routers.manager import _extract_response_content, _extract_stream_chunk
        body = {'choices': [{'message': {'content': [{'type': 'text', 'text': 'Done.'}],
                  'reasoning_details': [{'type': 'reasoning.text', 'text': 'Checking the result.'}]}}]}
        content, reason = _extract_response_content('Fixture', 'model', body)
        self.assertEqual((content, reason), ('Done.', 'Checking the result.'))
        chunk = {'choices': [{'delta': {'reasoning_details': [{'text': 'Comparing options.'}]}}]}
        self.assertEqual(_extract_stream_chunk(chunk)[1], 'Comparing options.')

    def test_nonstream_reasoning_reaches_actual_trace_handler(self):
        from routers import manager
        from storage.endpoints import ENDPOINT_REGISTRY
        response = SimpleNamespace(ok=True, status_code=200, headers={'Content-Type': 'application/json'},
                                   json=lambda: {'choices': [{'message': {'content': 'Done.', 'reasoning_content': 'Checking the requested result.'}}]})
        with patch.dict(ENDPOINT_REGISTRY, {'Fixture': {'base_url': 'http://fixture.invalid/v1', 'models': {'generic': {'context_window': 8192}}}}), patch.dict(CONFIG, {'active_endpoint': 'Fixture', 'active_model': 'generic', 'stream': False}), patch.object(manager, '_post_with_transport_retry', return_value=(response, False)), patch.object(manager, 'save_settings'), patch.object(manager, 'log_model_interaction'):
            result, _ = manager.query_with_fallback([{'role': 'user', 'content': 'Hello'}])
        self.assertEqual(result.reasoning, 'Checking the requested result.')
        self.assertIn('Checking the requested result.', AGENT_STATE['last_reasoning'])

    def test_side_requests_preserve_reasoning_without_changing_main_progress(self):
        from routers import manager
        from storage.endpoints import ENDPOINT_REGISTRY
        AGENT_STATE["current_thought"] = "Waiting for approval: write_file"
        response = SimpleNamespace(ok=True, status_code=200, headers={"Content-Type": "application/json"},
                  json=lambda: {"choices": [{"message": {"content": "Answer.", "reasoning_content": "Comparing the side question options."}}]})
        with patch.dict(ENDPOINT_REGISTRY, {"Fixture": {"base_url": "http://fixture.invalid/v1", "models": {"generic": {"context_window": 8192}}}}), patch.dict(CONFIG, {"active_endpoint": "Fixture", "active_model": "generic", "stream": False}), patch.object(manager.SESSION, "post", return_value=response):
            answer = manager.query_subagent([{"role": "user", "content": "Explain the available options."}])
        self.assertEqual(answer, "Answer.")
        self.assertIn("Comparing the side question options.", trace_text())
        self.assertEqual(AGENT_STATE["current_thought"], "Waiting for approval: write_file")


    def test_streamed_tags_and_native_fields_reach_same_trace(self):
        from routers import manager
        from storage.endpoints import ENDPOINT_REGISTRY
        fragments = ['<th', 'ink>First', ' thought.</thi', 'nk>Done.']
        chunks = [('data: ' + json.dumps({'choices': [{'delta': {'content': text}}]})).encode() for text in fragments]
        chunks.append(b'data: [DONE]')
        response = SimpleNamespace(ok=True, status_code=200, headers={'Content-Type': 'text/event-stream'}, iter_lines=lambda: iter(chunks))
        with patch.dict(ENDPOINT_REGISTRY, {'Fixture': {'base_url': 'http://fixture.invalid/v1', 'models': {'generic': {'context_window': 8192}}}}), patch.dict(CONFIG, {'active_endpoint': 'Fixture', 'active_model': 'generic', 'stream': True}), patch.object(manager, '_post_with_transport_retry', return_value=(response, False)), patch.object(manager, 'save_settings'), patch.object(manager, 'log_model_interaction'):
            result, _ = manager.query_with_fallback([{'role': 'user', 'content': 'Hello'}])
        self.assertEqual(result.content, 'Done.')
        self.assertEqual(result.reasoning, 'First thought.')
        self.assertIn('First thought.', AGENT_STATE['last_reasoning'])


if __name__ == '__main__':
    unittest.main()
