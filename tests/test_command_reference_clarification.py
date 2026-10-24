"""Behavior checks with real disposable files, deterministic model proposals and live input races."""
import contextlib
import asyncio
import io
import json
from pathlib import Path
import shlex
import threading
import unittest
from unittest.mock import patch, Mock

from test_pipelines import Workspace
from config import AGENT_STATE, CONFIG
from engine import task_runner
from engine.command_reference import assess
from engine.path_discovery import initial_action, candidates
from engine.clarification import QUESTIONS, ask_user, question_tokens


class CommandReferenceTests(Workspace):
    def test_missing_script_retrieves_real_alternatives_without_running(self):
        (self.root / 'package.json').write_text(json.dumps({'scripts': {'develop': 'custom-worker', 'check': 'checker'}}))
        result = assess('npm run dev', self.root)
        self.assertFalse(result.allowed)
        self.assertIn('develop', result.reason)
        self.assertIn('npm run develop', result.suggestions)
        with patch.object(task_runner, '_execute_shell_action_legacy') as run:
            actual = task_runner.execute_shell_action('npm run dev')
        self.assertFalse(actual.success)
        self.assertTrue(actual.metadata['rejected_before_execution'])
        run.assert_not_called()

    def test_declared_custom_script_is_allowed_and_rechecked_after_edit(self):
        package = self.root / 'package.json'
        package.write_text(json.dumps({'scripts': {'serve-special': 'custom-command'}}))
        self.assertTrue(assess('pnpm run serve-special', self.root).allowed)
        package.write_text(json.dumps({'scripts': {'other': 'different-command'}}))
        self.assertFalse(assess('pnpm run serve-special', self.root).allowed)

    def test_compound_command_reads_manifest_in_its_actual_directory(self):
        child = self.root / 'space project'
        child.mkdir()
        (child / 'package.json').write_text('{"scripts":{"check":"custom"}}')
        self.assertTrue(assess("cd 'space project' && npm run check", self.root).allowed)
        self.assertFalse(assess("cd 'space project' && npm run missing", self.root).allowed)

    def test_explicit_manager_directory_is_not_checked_against_wrong_project(self):
        (self.root / 'package.json').write_text('{"scripts":{"other":"custom"}}')
        self.assertTrue(assess('npm --prefix ../other run dev', self.root).allowed)
        self.assertTrue(assess('python -c "print(123)"', self.root).allowed)

    def test_listing_cannot_become_install_or_server(self):
        for proposed in ('npm install', 'npm run dev', 'mkdir wrong', 'touch wrong'):
            self.assertFalse(assess(proposed, self.root, 'display my folder').allowed)
        self.assertTrue(assess('ls -la', self.root, 'display my folder').allowed)

    def test_missing_python_file_is_rejected_and_existing_file_allowed(self):
        self.assertFalse(assess('python worker.py', self.root).allowed)
        self.assertFalse(assess('python -u worker.py', self.root).allowed)
        (self.root / 'worker.py').write_text('print(1)')
        self.assertTrue(assess('python worker.py', self.root).allowed)

    def test_missing_node_entrypoint_is_rejected_without_running(self):
        self.assertFalse(assess('node entry.cjs', self.root).allowed)
        (self.root / 'entry.cjs').write_text('console.log(1)')
        self.assertTrue(assess('node entry.cjs', self.root).allowed)

    def test_sample_prompt_and_unrelated_names_use_filesystem_facts(self):
        sample = self.root / 'sdcard/cli/jbnsrr-poetfolio-react'
        sample.mkdir(parents=True)
        plan, action = initial_action('Go to my sdcard/cli/jbnsrr-poetfolio-react', self.root)
        self.assertEqual(plan['operation'], 'navigate')
        self.assertEqual(shlex.split(action[1]['command'])[-1], str(sample.resolve()))
        other = self.root / 'a totally different name'
        other.mkdir()
        _, action = initial_action('Go to my "a totally different name"', self.root)
        self.assertEqual(shlex.split(action[1]['command'])[-1], str(other.resolve()))

    def test_missing_root_slash_resolves_only_existing_full_path(self):
        target = self.root / 'unique-rooted-folder'
        target.mkdir()
        paths = candidates(str(target).lstrip('/'), self.root)
        self.assertEqual(paths, [str(target.resolve())])

    def test_unnamed_folder_asks_unless_current_directory_was_explicit(self):
        _, action = initial_action('display my folder', self.root)
        self.assertEqual(action[0], 'ask_user')
        self.assertEqual(action[1]['target'], '')
        _, action = initial_action('display my folder', self.root, explicit_directory=True)
        self.assertEqual(action[0], 'execute_bash')

    def test_duplicate_names_do_not_choose_one(self):
        for parent in ('one', 'two'):
            (self.root / parent / 'portfolio').mkdir(parents=True)
        matches = candidates('portfolio', self.root)
        self.assertEqual(len(matches), 2)
        _, action = initial_action('Go to portfolio', self.root)
        self.assertEqual(action[0], 'ask_user')


class ClarificationTests(Workspace):
    def tearDown(self):
        if QUESTIONS.active:
            QUESTIONS.finish(QUESTIONS.active, 'cancelled')
        super().tearDown()

    def test_answer_preserves_case_numbered_choices_and_stale_identity(self):
        row = QUESTIONS.open('Which folder?', '/KeepCase\n/Other')
        self.assertEqual(QUESTIONS.submit('1', row['id']), 'answered')
        self.assertEqual(row['answer'], '/KeepCase')
        other = QUESTIONS.open('Next question?', '')
        self.assertEqual(QUESTIONS.submit('late', row['id']), 'expired')
        self.assertEqual(other['status'], 'pending')
        QUESTIONS.submit('/My Project', other['id'])
        self.assertEqual(other['answer'], '/My Project')

    def test_verified_discovery_withdraws_question_without_user_answer(self):
        target = self.root / 'worker'
        target.mkdir()
        with patch.dict(AGENT_STATE, {'clarification_input_enabled': True}):
            result = ask_user('Where is worker?', target='worker')
        self.assertEqual(result.metadata['resolution'], 'discovered')
        self.assertEqual(result.metadata['path'], str(target.resolve()))
        self.assertIsNone(QUESTIONS.active)

    def test_reply_wins_when_discovery_finishes_later(self):
        target = self.root / 'project'
        target.mkdir()
        def discovery(*args):
            QUESTIONS.submit('Use a different folder', QUESTIONS.active['id'])
            return [str(target)]
        with patch.dict(AGENT_STATE, {'clarification_input_enabled': True}), patch('engine.path_discovery.candidates', side_effect=discovery):
            result = ask_user('Which project?', target='project')
        self.assertEqual(result.metadata['answer'], 'Use a different folder')
        self.assertEqual(result.metadata['resolution'], 'answered')

    def test_unnamed_question_never_autoselects_and_cancellation_clears_it(self):
        from engine.cancellation import begin_task, cancel_current, end_task, TaskCancelled
        (self.root / 'only-folder').mkdir()
        with patch.dict(AGENT_STATE, {'clarification_input_enabled': False}):
            result = ask_user('Which folder?')
        self.assertEqual(result.status, 'needs_input')
        begin_task()
        with patch('engine.path_discovery.candidates', side_effect=lambda *a: (cancel_current() or [])):
            with self.assertRaises(TaskCancelled):
                ask_user('Where?', target='unknown')
        end_task()
        self.assertIsNone(QUESTIONS.active)

    def test_question_renders_inside_dock_at_phone_width(self):
        row = QUESTIONS.open('Which folder should I use?', 'First\nSecond')
        shown = ''.join(text for _, text in question_tokens(36))
        self.assertIn('Galam Action · Ask', shown)
        self.assertIn('1. First', shown)
        self.assertIn('Which folder', shown)
        QUESTIONS.finish(row, 'cancelled')

    def test_busy_enter_routes_answers_and_late_drafts_without_queue_or_autocomplete(self):
        from ui import dock_layout as dock
        from types import SimpleNamespace
        row = QUESTIONS.open('Which folder?', '')
        for draft, pending in ((None, True), (row['id'], False)):
            buffer = Mock(text='/s')  # A path prefix must not become /settings.
            event = SimpleNamespace(current_buffer=buffer, app=Mock())
            enqueue = Mock()
            with patch.dict(AGENT_STATE, {'task_active': True, 'active_message': 'main',
                    'clarification': {'id': row['id']} if pending else None,
                    'clarification_draft_id': draft, 'enqueue_prompt': enqueue, 'in_btw_mode': False}):
                dock.handle_enter(event)
            enqueue.assert_not_called()
            buffer.validate_and_handle.assert_called_once()
            self.assertEqual(buffer.text, '/s')

    def test_answer_does_not_grant_command_approval(self):
        from engine.execution_policy import requires_review
        with patch.dict(AGENT_STATE, {'review_decision': None}):
            row = QUESTIONS.open('Which file?', '')
            QUESTIONS.submit('yes', row['id'])
            self.assertIsNone(AGENT_STATE.get('review_decision'))
            self.assertTrue(requires_review('REVIEW', 'execute_bash', {'command': 'ls'}))

    def test_real_prompt_delivers_answer_while_worker_is_busy(self):
        from prompt_toolkit import PromptSession
        from prompt_toolkit.input.defaults import create_pipe_input
        from prompt_toolkit.output import DummyOutput
        from ui import dock_layout as dock
        from engine.clarification import track_answer_draft
        row = QUESTIONS.open('Which folder?', '')
        async def exercise():
            with create_pipe_input() as pipe:
                session = PromptSession(input=pipe, output=DummyOutput(), key_bindings=dock.kb)
                session.default_buffer.on_text_changed += track_answer_draft
                enqueue = Mock()
                with patch.object(dock, 'prompt_session', session), patch.dict(AGENT_STATE, {
                        'task_active': True, 'active_message': 'main', 'in_btw_mode': False,
                        'enqueue_prompt': enqueue, 'app_ref': session.app}), contextlib.redirect_stdout(io.StringIO()):
                    task = asyncio.create_task(session.prompt_async(dock.get_prompt_text))
                    try:
                        await asyncio.sleep(.05)
                        pipe.send_text('/Some Project\r')
                        answer = await asyncio.wait_for(task, timeout=2)
                        self.assertEqual(answer, '/Some Project')
                        enqueue.assert_not_called()
                        submitted = AGENT_STATE.pop('clarification_submitted_id', None)
                        self.assertEqual(submitted, row['id'])
                        self.assertEqual(QUESTIONS.submit(answer, submitted), 'answered')
                    finally:
                        if not task.done():
                            session.app.exit(result='')
                            await task
        asyncio.run(exercise())
        self.assertEqual(row['answer'], '/Some Project')

    def test_real_loop_asks_and_then_lists_without_a_model_request(self):
        import galam
        target = self.root / 'User Selected'
        target.mkdir()
        (target / 'child-folder').mkdir()
        shown = threading.Event()
        opened = QUESTIONS.open
        def notify(*args):
            row = opened(*args)
            shown.set()
            return row
        messages = [{'role': 'user', 'content': 'display my folder'}]
        errors = []
        def run():
            try:
                galam.run_agent_loop(messages, 'clarification-fixture')
            except BaseException as error:
                errors.append(error)
        with patch.dict(CONFIG, {'mode': 'AUTO', 'command_agent': False}), patch.dict(AGENT_STATE, {
                'clarification_input_enabled': True, 'working_directory_explicit': False}), patch.object(
                QUESTIONS, 'open', side_effect=notify), patch.object(galam, 'query_with_fallback') as model, contextlib.redirect_stdout(io.StringIO()):
            worker = threading.Thread(target=run)
            worker.start()
            self.assertTrue(shown.wait(3))
            QUESTIONS.submit(str(target), QUESTIONS.active['id'])
            worker.join(5)
            if worker.is_alive():
                from engine.cancellation import cancel_current
                cancel_current()
                worker.join(2)
            self.assertFalse(worker.is_alive())
            self.assertEqual(errors, [])
            model.assert_not_called()
        observations = [m for m in messages if m.get('_galam_action')]
        self.assertEqual([m['_galam_action']['tool'] for m in observations], ['ask_user', 'execute_bash'])
        self.assertIn('child-folder', observations[-1]['content'])
        self.assertIsNone(QUESTIONS.active)

    def test_ask_tool_is_available_in_native_text_and_xml_protocols(self):
        from engine.tool_protocol import tool_schemas
        from engine.tool_parser import extract_tools_from_text
        from engine.execution_policy import requires_review
        self.assertIn('ask_user', [t['function']['name'] for t in tool_schemas()])
        for wire in ('<tool_call>{"name":"ask_user","arguments":{"question":"Which folder?"}}</tool_call>',
                     '<invoke name="ask_user"><parameter name="question">Which folder?</parameter></invoke>'):
            calls = extract_tools_from_text(wire)
            self.assertEqual(calls[0]['function']['name'], 'ask_user')
        self.assertFalse(requires_review('REVIEW', 'ask_user', {'question': 'Which folder?'}))

    def test_cancel_waiting_question_then_next_queued_task_runs(self):
        import galam
        from engine.message_queue import MessageQueue
        from engine.cancellation import cancel_current
        queue, messages, shown = MessageQueue(), [], threading.Event()
        opened = QUESTIONS.open
        def notify(*args):
            row = opened(*args)
            shown.set()
            return row
        queue.enqueue('display my folder', 'queue-fixture', messages)
        queue.enqueue('list files here', 'queue-fixture', messages)
        queue.queue.put(None)
        with patch.dict(CONFIG, {'mode': 'AUTO', 'command_agent': False}), patch.dict(AGENT_STATE, {
                'clarification_input_enabled': True, 'working_directory_explicit': False}), patch.object(
                QUESTIONS, 'open', side_effect=notify), patch.object(galam, 'TASK_QUEUE', queue), patch.object(
                galam, 'persist_conversation'), patch.object(galam, 'query_with_fallback') as model, contextlib.redirect_stdout(io.StringIO()):
            worker = threading.Thread(target=galam.agent_worker_loop, daemon=True)
            worker.start()
            try:
                self.assertTrue(shown.wait(3))
                cancel_current()
                worker.join(5)
                self.assertFalse(worker.is_alive())
            finally:
                if worker.is_alive():
                    cancel_current()
                    worker.join(5)
            model.assert_not_called()
        self.assertIsNone(QUESTIONS.active)
        self.assertTrue(any(m.get('_galam_action', {}).get('tool') == 'execute_bash'
                            and m['_galam_action']['status'] == 'completed' for m in messages))


if __name__ == '__main__':
    unittest.main()
