"""Regression coverage for setup-only completion and task-local output authorization."""
from pathlib import Path
from unittest.mock import patch

from config import AGENT_STATE
from engine.action_results import request_intent
from engine.execution_policy import normalize_mode, requires_review
from engine.security import requested_output_scope
from tools.filesystem import search_files
import test_pipelines as pipelines


class BuildCompletionTests(pipelines.Workspace):
    response = pipelines.LoopTests.response
    run_loop = pipelines.LoopTests.run_loop

    def test_website_with_folder_keeps_building_after_setup_announcement(self):
        destination = Path(self.temp.name) / 'new site'
        request = (f'Create a website about barbershop and save it under "{destination}" '
                   'then create a folder for it, with animation and navigation links.')
        self.assertEqual(request_intent(request), (True, True))
        html = '<!doctype html><title>Barber</title><nav>Home</nav><h1>Barber</h1>\n'
        responses = [self.response('make_directory', {'path': str(destination)}),
                     self.response(text='Folder created. Now building the site — starting with the HTML structure.'),
                     self.response('write_file', {'path': str(destination / 'index.html'), 'content': html}),
                     self.response(text='Created the website in index.html.')]
        with patch('galam.request_tool_review', side_effect=AssertionError('Unexpected permission prompt')):
            messages, _ = self.run_loop(responses, request)
        self.assertEqual((destination / 'index.html').read_text(), html)
        self.assertEqual(self.model_calls, 4)
        self.assertNotIn('Query error', self.last_display)
        self.assertTrue(any('no successful nonempty file change' in m.get('content', '') for m in messages))
        # Authorization must end with this request, including exception paths.
        self.assertTrue(requires_review('ALWAYS-PROCEED', 'write_file', {'path': str(destination / 'other.html')}))

    def test_exhausted_endpoint_records_incomplete_receipt_after_setup(self):
        for status in ('EXHAUSTED', 'FAILED'):
            responses = [self.response('make_directory', {'path': 'site'}), (status, 'fixture-model')]
            messages, _ = self.run_loop(responses, 'Build a website in a folder')
            self.assertIn('Task incomplete', messages[-1]['content'])
            self.assertIn('Last observed result', messages[-1]['content'])
            self.assertFalse((self.root / 'site/index.html').exists())

    def test_truncated_action_repairs_with_smaller_complete_call(self):
        truncated = self.response(text='Incomplete action')
        truncated[0].incomplete = True
        truncated[0].action_truncated = True
        responses = [truncated, self.response('write_file', {'path': 'index.html', 'content': '<h1>Built</h1>'}),
                     self.response(text='Created index.html.')]
        messages, _ = self.run_loop(responses, 'Create a website and its folder')
        self.assertTrue((self.root / 'index.html').is_file())
        self.assertTrue(any('do not continue the broken JSON' in m.get('content', '') for m in messages))

    def test_read_commands_with_echo_and_stderr_redirection_are_not_file_writes(self):
        responses = [self.response('execute_bash', {'command': 'echo "---"; pwd 2>/dev/null'}),
                     self.response('write_file', {'path': 'index.html', 'content': '<h1>Built</h1>'}),
                     self.response(text='Created index.html.')]
        _, spy = self.run_loop(responses, 'Build a website in its folder')
        self.assertIn('execute_bash', [call.args[0] for call in spy.call_args_list])
        self.assertNotIn('That command cannot run', self.last_display)

    def test_declined_action_does_not_end_task_and_alternative_can_complete(self):
        responses = [self.response('execute_bash', {'command': 'rm old.html'}),
                     self.response('execute_bash', {'command': 'rm old.html'}),
                     self.response('write_file', {'path': 'new.html', 'content': '<h1>Safe alternative</h1>'}),
                     self.response(text='Created new.html; left old.html unchanged.')]
        with patch('galam.request_tool_review', return_value='n') as review:
            self.run_loop(responses, 'Build a website')
        self.assertEqual(review.call_count, 1)
        self.assertTrue((self.root / 'new.html').is_file())
        self.assertNotIn('Query error', self.last_display)


class ScopeAndSearchTests(pipelines.Workspace):
    def test_scope_is_explicit_resolved_and_preserves_sensitive_reviews(self):
        target = Path(self.temp.name) / 'output'
        elsewhere = Path(self.temp.name) / 'unrelated'
        with requested_output_scope(f'Build an app and save it under {target}'):
            self.assertFalse(requires_review('Always Proceed', 'make_directory', {'path': str(target)}))
            self.assertFalse(requires_review('ALWAYS-PROCEED', 'write_file', {'path': str(target / 'index.html')}))
            for path in (elsewhere / 'a.html', target / '../unrelated/a.html', target / '.git/config'):
                self.assertTrue(requires_review('ALWAYS-PROCEED', 'write_file', {'path': str(path)}))
            self.assertTrue(requires_review('ALWAYS-PROCEED', 'execute_bash', {'command': 'rm -rf output'}))
            self.assertTrue(requires_review('ALWAYS-PROCEED', 'execute_bash', {'command': 'cat ~/.secrets'}))
            self.assertTrue(requires_review('REVIEW', 'make_directory', {'path': str(target)}))
            self.assertTrue(requires_review('AUTO', 'make_directory', {'path': str(target)}))
        self.assertTrue(requires_review('ALWAYS-PROCEED', 'make_directory', {'path': str(target)}))
        for request in (f'Read the website under {target}', f'Create a website but do not save it under {target}'):
            with requested_output_scope(request):
                self.assertTrue(requires_review('ALWAYS-PROCEED', 'make_directory', {'path': str(target)}))

    def test_directory_only_and_alternative_destination_phrasings(self):
        target = Path(self.temp.name) / 'output'
        for request in (f'Create a folder under {target}', f'Build an app in {target}',
                        f'Create a website and save the result to {target}'):
            with requested_output_scope(request):
                self.assertFalse(requires_review('ALWAYS-PROCEED', 'make_directory', {'path': str(target)}))
        with requested_output_scope(f'Create a website and read examples in {target}'):
            self.assertTrue(requires_review('ALWAYS-PROCEED', 'make_directory', {'path': str(target)}))
        target.mkdir()
        link = target / 'escape'
        link.symlink_to(self.root.parent / 'elsewhere', target_is_directory=True)
        with requested_output_scope(f'Create a website in {target}'):
            self.assertTrue(requires_review('ALWAYS-PROCEED', 'write_file', {'path': str(link / 'bad.html')}))

    def test_truncated_text_and_native_actions_do_not_use_prose_continuation(self):
        import contextlib
        import io
        from types import SimpleNamespace
        from config import CONFIG
        from routers import manager
        from storage.endpoints import ENDPOINT_REGISTRY
        variants = [
            {'content': '<tool_call>{"name":"write_file","arguments":{"path":"index.html","content":"partial'},
            {'content': '', 'tool_calls': [{'id': 'pending', 'type': 'function', 'function': {
                'name': 'write_file', 'arguments': '{"path":"index.html","content":"partial'}}]},
        ]
        for message in variants:
            response = SimpleNamespace(ok=True, status_code=200, headers={'Content-Type':'application/json'},
                close=lambda:None, json=lambda: {'choices':[{'message':message,'finish_reason':'length'}]})
            with patch.dict(CONFIG, {'active_endpoint':'Fixture','active_model':'small','stream':False,'max_tokens':128}), \
                 patch.dict(ENDPOINT_REGISTRY, {'Fixture':{'models':{'small':{'context_window':8192}}}}), \
                 patch.object(manager, '_post_with_transport_retry', return_value=(response,False)), \
                 patch.object(manager, '_recorded_post', side_effect=AssertionError('Must not splice action JSON')) as continuation, \
                 patch.object(manager, 'StructuredThoughtStreamHandler'), patch.object(manager, 'save_settings'), \
                 patch.object(manager, 'log_model_interaction'), contextlib.redirect_stdout(io.StringIO()):
                result, _ = manager.query_with_fallback([{'role':'user','content':'Create a website'}])
            self.assertTrue(result.incomplete)
            self.assertTrue(result.action_truncated)
            self.assertEqual(result.tool_calls, [])
            continuation.assert_not_called()

    def test_empty_length_response_recovers_capacity_without_changing_settings(self):
        import contextlib
        import io
        from types import SimpleNamespace
        from config import CONFIG
        from routers import manager
        from storage.endpoints import ENDPOINT_REGISTRY
        from engine.context_manager import model_limits
        from engine.generation_capacity import output_override
        call = {'id':'complete','type':'function','function':{'name':'write_file',
                'arguments':'{"path":"index.html","content":"<h1>Built</h1>"}'}}
        def response(message, finish):
            return SimpleNamespace(ok=True,status_code=200,headers={'Content-Type':'application/json'},close=lambda:None,
                json=lambda:{'choices':[{'message':message,'finish_reason':finish}]})
        empty = response({'content':''}, 'length')
        complete = response({'content':'','tool_calls':[call]}, 'tool_calls')
        reservations = []
        def continuation(*args, **kwargs):
            reservations.append(model_limits('Fixture','small',CONFIG)[1])
            return complete
        with patch.dict(CONFIG, {'active_endpoint':'Fixture','active_model':'small','stream':False,'max_tokens':128}), \
             patch.dict(ENDPOINT_REGISTRY, {'Fixture':{'models':{'small':{'context_window':8192,'max_output_tokens':512}}}}), \
             patch.object(manager, '_post_with_transport_retry', return_value=(empty,False)), \
             patch.object(manager, '_recorded_post', side_effect=continuation), \
             patch.object(manager, 'StructuredThoughtStreamHandler'), patch.object(manager, 'save_settings'), \
             patch.object(manager, 'log_model_interaction'), contextlib.redirect_stdout(io.StringIO()):
            result, _ = manager.query_with_fallback([{'role':'user','content':'Create a website'}])
            self.assertEqual(CONFIG['max_tokens'], 128)
            self.assertEqual(model_limits('Fixture','small',CONFIG)[1],128)
        self.assertEqual(result.tool_calls[0]['function']['name'], 'write_file')
        self.assertEqual(reservations,[256])
        self.assertIsNone(output_override('Fixture','small'))

    def test_adaptive_capacity_honors_provider_limit_opt_out_and_scope(self):
        from engine.generation_capacity import grow_output, output_scope, output_override
        from storage.endpoints import ENDPOINT_REGISTRY
        config = {'max_tokens':128, 'context_window':8192}
        with patch.dict(ENDPOINT_REGISTRY, {'Fixture':{'models':{'small':{'max_output_tokens':300}}}}):
            with output_scope():
                self.assertTrue(grow_output('Fixture','small',config,{}))
                self.assertTrue(grow_output('Fixture','small',config,{}))
                self.assertEqual(output_override('Fixture','small'),300)
                self.assertFalse(grow_output('Fixture','small',config,{}))
                self.assertFalse(grow_output('Fixture','other',{**config,'adaptive_action_output':False},{}))
                self.assertFalse(grow_output('Local Endpoint','local',{**config,'local_tuning':{'mode':'manual'}},{}))
            self.assertIsNone(output_override('Fixture','small'))
        from routers import manager
        from config import CONFIG
        marker = {'role':'user','_galam_internal':True,'content':'Repair',
                  '_galam_generation_capacity':{'endpoint':'Fixture','model':'small','tokens':256}}
        messages = [{'role':'user','content':'Create a website'}, marker]
        with patch.object(manager, '_query_with_fallback', side_effect=lambda *a, **kw: output_override('Fixture','small')):
            self.assertEqual(manager.query_with_fallback(messages),256)
            messages.append({'role':'user','content':'New task'})
            self.assertIsNone(manager.query_with_fallback(messages))
        self.assertIsNone(output_override('Fixture','small'))

    def test_buffered_native_action_changes_protocol_after_no_visible_progress(self):
        import contextlib
        import io
        from types import SimpleNamespace
        from config import CONFIG
        from routers import manager
        from storage.endpoints import ENDPOINT_REGISTRY
        from engine.tool_protocol import tool_schemas, options
        response = SimpleNamespace(ok=True,status_code=200,headers={'Content-Type':'application/json'},close=lambda:None,
            json=lambda:{'choices':[{'message':{'content':''},'finish_reason':'length'}]})
        with patch.dict(CONFIG, {'active_endpoint':'Fixture','active_model':'small','stream':False,'max_tokens':128}), \
             patch.dict(ENDPOINT_REGISTRY, {'Fixture':{'models':{'small':{'context_window':8192}}}}), \
             patch.object(manager, '_post_with_transport_retry', return_value=(response,False)), \
             patch.object(manager, '_recorded_post', return_value=response) as continuation, \
             patch.object(manager, 'tool_options', return_value={'tools':tool_schemas()}), \
             patch.object(manager, 'StructuredThoughtStreamHandler'), patch.object(manager, 'save_settings'), \
             patch.object(manager, 'log_model_interaction'), contextlib.redirect_stdout(io.StringIO()):
            result, _ = manager.query_with_fallback([{'role':'user','content':'Create a website'}])
        self.assertTrue(result.buffered_action)
        self.assertTrue(result.incomplete)
        self.assertEqual(continuation.call_count,1)
        messages = [{'role':'user','content':'Create a website'},
                    {'role':'user','_galam_internal':True,'_galam_force_text_tools':True,'content':'Repair'}]
        self.assertEqual(options('Groq','small',messages,{}),{})
        messages.append({'role':'user','content':'Create another website'})
        self.assertIn('tools',options('Groq','small',messages,{}))

    def test_mode_spelling_and_whitespace(self):
        for value in (' Always Proceed ', 'always_proceed', 'ALWAYS-PROCEED'):
            self.assertEqual(normalize_mode(value), 'ALWAYS-PROCEED')
        self.assertEqual(normalize_mode('unknown'), 'REVIEW')

    def test_filename_glob_does_not_grep_its_own_transcript(self):
        (self.root / 'logs/models').mkdir(parents=True)
        (self.root / 'logs/models/model.jsonl').write_text('search_files *.html Recursive noise')
        (self.root / 'index.html').write_text('<h1>Real project</h1>')
        output = search_files('*.html', str(self.root))
        self.assertIn(str(self.root / 'index.html'), output)
        self.assertNotIn('model.jsonl', output)
        self.assertNotIn('Recursive noise', output)
        output = search_files('Recursive noise', str(self.root))
        self.assertIn('No matches', output)
        self.assertIn('Real project', search_files('Real project', str(self.root)))

    def test_search_skips_credentials_dependencies_and_external_symlinks(self):
        (self.root / '.secrets').write_text('sensitive-needle')
        (self.root / 'node_modules').mkdir()
        (self.root / 'node_modules/vendor.js').write_text('sensitive-needle')
        outside = Path(self.temp.name) / 'outside.txt'
        outside.write_text('sensitive-needle')
        (self.root / 'linked.txt').symlink_to(outside)
        self.assertIn('No matches', search_files('sensitive-needle', str(self.root)))
        self.assertNotIn('linked.txt', search_files('*.txt', str(self.root)))
