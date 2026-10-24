"""Regression cases for listing, project resolution and unfinished diagnosis actions."""
import json
from pathlib import Path
import shlex
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import test_pipelines as fixtures
from engine.action_results import ActionResult, request_intent
from engine.completion import completed_simple_request
from engine.path_discovery import prepare_listing
from engine.startup_discovery import discover
from engine.service_diagnostics import diagnose
from tools.filesystem import search_files


class ListingRecoveryTests(fixtures.Workspace):
    response = fixtures.LoopTests.response
    run_loop = fixtures.LoopTests.run_loop

    def test_symlink_metadata_never_finishes_a_directory_listing(self):
        target = self.root / 'actual'
        target.mkdir()
        alias = self.root / 'alias'
        alias.symlink_to(target, target_is_directory=True)
        event = {'tool': 'execute_bash', 'args': {'command': f'ls -la {alias}'},
                 'result': ActionResult(f'lrwxrwxrwx 1 user group 6 Jan 1 12:00 {alias} -> {target}')}
        self.assertIsNone(completed_simple_request(f'List my folders in {alias}', [event], [], str(self.root)))
        prepared = prepare_listing(f'List my folders in {alias}', event['args']['command'], str(self.root))
        self.assertEqual(shlex.split(prepared)[-1], str(target.resolve()))

    def test_recursive_request_does_not_finish_from_top_level_ls(self):
        (self.root / 'child').mkdir()
        event = {'tool': 'execute_bash', 'args': {'command': f'ls -la {self.root}'},
                 'result': ActionResult('drwxrwxrwx 2 user group 9 Jan 1 12:00 child')}
        self.assertIsNone(completed_simple_request(f'List folders including subfolders in {self.root}', [event], [], str(self.root)))

    def test_real_recursive_listing_through_symlink_finishes_without_ack_call(self):
        root = self.root / 'actual storage'
        (root / 'child' / 'nested').mkdir(parents=True)
        (root / 'ordinary.txt').write_text('data')
        alias = self.root / 'storage-link'
        alias.symlink_to(root, target_is_directory=True)
        messages, spy = self.run_loop([self.response('execute_bash', {'command': f'ls -la {shlex.quote(str(alias))}'})],
                                     f'Please list my folders including subfolders in {alias}')
        self.assertEqual(self.model_calls, 1)
        self.assertIn('nested', messages[-1]['content'])
        self.assertNotIn('ordinary.txt', messages[-1]['content'])
        self.assertTrue(spy.call_args.args[1]['command'].startswith('find '))

    def test_exact_metadata_command_is_preserved(self):
        self.assertEqual(prepare_listing('Run ls -ld alias', 'ls -ld alias', str(self.root)), 'ls -ld alias')

    def test_named_sibling_startup_uses_the_actual_project(self):
        assistant = self.root / 'assistant'
        target = self.root / 'garden-ui'
        assistant.mkdir()
        target.mkdir()
        (target / 'package.json').write_text('{"scripts":{"dev":"custom-preview"}}')
        result = discover('open garden-ui THEN run the server locally. npm run dev', str(assistant))
        self.assertEqual(result['directory'], str(target.resolve()))
        self.assertIn('custom-preview', result['content'])
        self.assertNotIn('Project directory: ' + str(assistant), result['content'])

    def test_ambiguous_named_startup_does_not_inspect_current_project_as_target(self):
        for name in ('one', 'two'):
            (self.root / name / 'garden-ui').mkdir(parents=True)
        result = discover('open garden-ui then run my server', str(self.root))
        self.assertIsNone(result['directory'])
        self.assertIn('ambiguous', result['content'])

    def test_check_is_an_action_without_turning_explanations_into_execution(self):
        self.assertTrue(request_intent('kindly check my server why I cannot access another page')[0])
        self.assertFalse(request_intent('What does the check command mean?')[0])

    def test_unclosed_tool_call_is_repaired_before_task_can_finish(self):
        source = self.root / 'entry.py'
        source.write_text('print("observed")\n')
        partial = '<tool_call>' + json.dumps({'name': 'view_file', 'arguments': {'path': str(source)}})
        messages, spy = self.run_loop([self.response(text=partial),
            self.response('view_file', {'path': str(source)}), self.response(text='The inspected entry prints observed.')],
            'kindly check my server why I cannot access another page')
        self.assertEqual(self.model_calls, 3)
        self.assertEqual(spy.call_count, 1)
        self.assertTrue(any('closing tag is missing' in m.get('content', '') for m in messages))
        self.assertEqual(messages[-1]['_galam_response_stats']['status'], 'completed')

    def test_unrequested_broken_action_is_not_executed_or_marked_completed(self):
        messages, spy = self.run_loop([self.response(text='<tool_call>{"name":"write_file"')], 'What is a Python function?')
        spy.assert_not_called()
        self.assertEqual(messages[-1]['_galam_response_stats']['status'], 'incomplete')

    def test_diagnosis_without_fix_authorization_cannot_write_files(self):
        source = self.root / 'entry.py'
        source.write_text('print(1)\n')
        _, spy = self.run_loop([
            self.response('write_file', {'path': 'unrequested.py', 'content': 'print(2)'}),
            self.response('view_file', {'path': str(source)}), self.response(text='The inspected file prints 1.')],
            'Check my server because other pages cannot be accessed')
        self.assertFalse((self.root / 'unrequested.py').exists())
        self.assertEqual([call.args[0] for call in spy.call_args_list], ['view_file'])

    def test_search_prioritizes_authored_source_and_omits_assistant_transcripts(self):
        (self.root / 'noise.html').write_text('header noise\n' * 80)
        (self.root / '.aider.chat.history.md').write_text('PRIVATE_TRANSCRIPT_ONLY')
        (self.root / 'src').mkdir()
        (self.root / 'src' / 'Navigation.jsx').write_text('header AUTHORED_NAVIGATION\n')
        found = search_files('header', str(self.root), max_results=3)
        self.assertIn('AUTHORED_NAVIGATION', found)
        self.assertIn('No matches found', search_files('PRIVATE_TRANSCRIPT_ONLY', str(self.root)))


class ServiceDiagnosisTests(fixtures.Workspace):
    def test_http_page_and_asset_failures_are_observed_before_source_speculation(self):
        body = b'<html><a href="/other">Other</a><script src="/bundle.js"></script></html>'
        (self.root / 'index.html').write_bytes(body)
        (self.root / 'bundle.js').write_text('console.log("expected");')
        (self.root / 'src/pages').mkdir(parents=True)
        (self.root / 'src/pages/Home.jsx').write_text('const link = {href: "/other"};\n' + '// filler\n' * 30000)
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(404 if self.path == '/other' else 200)
                self.send_header('Content-Type', 'text/html')
                self.end_headers()
                self.wfile.write(body)
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            url = f'http://127.0.0.1:{server.server_port}/'
            text = diagnose(f'Check my server {url}; I cannot access other pages.', self.root)
            self.assertIn('Page GET /other: 404', text)
            self.assertIn('local bytes match=False', text)
            self.assertIn('does not verify browser clicks', text)
            self.assertIn('src/pages/Home.jsx', text)
        finally:
            server.shutdown()
            server.server_close()
            worker.join(2)

    def test_unknown_or_external_address_does_not_trigger_arbitrary_requests(self):
        self.assertIsNone(diagnose('Explain Python functions', self.root))
        with patch('engine.service_diagnostics.urllib.request.build_opener') as opener:
            text = diagnose('Check my server https://example.com; pages are broken', self.root)
        opener.assert_not_called()
        self.assertIn('outside local-service diagnostics', text)
