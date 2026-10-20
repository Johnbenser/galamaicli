"""Regression gates for context overflow and real keyboard-driven panel redraws."""
import asyncio
import copy
import json
import os
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

from config import AGENT_STATE
from engine.context_manager import assemble_context
from engine.result_store import read_action_output


class ContextOverflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {'GALAM_STATE_DIR': self.temp.name})
        self.env.start()

    def tearDown(self):
        self.env.stop();self.temp.cleanup()

    def assemble(self, messages):
        return assemble_context(messages, 'CUSTOM_RULE: keep user UI.', 'Groq', 'fixture',
            {'context_window': 4096, 'max_tokens': 512, '_tool_schema_tokens': 700,
             '_simple_filesystem': True}, self.temp.name)

    def test_large_inspection_is_recoverable_instead_of_stopping_task(self):
        exact = '[Verified File Inspection: app.py]\n' + '\n'.join(f'{n}: source_{n}' for n in range(4000))
        messages = [{'role': 'user', 'content': 'Fix app.py. Never change the settings UI.'},
                    {'role': 'user', '_galam_internal': True, 'content': exact}]
        original = copy.deepcopy(messages)
        selected, metrics = self.assemble(messages)
        text = json.dumps(selected)
        self.assertLessEqual(metrics['input_tokens'], metrics['input_budget'])
        self.assertGreater(metrics['compacted_messages'], 0)
        self.assertIn('Never change the settings UI.', text)
        self.assertIn('CUSTOM_RULE', text)
        reference = re.search(r'action_id=\\?"([a-f0-9]{32})', text)[1]
        self.assertIn('source_3998', read_action_output(reference, start_line=3999, end_line=4001))
        self.assertEqual(messages, original)

    def test_long_active_task_preserves_latest_failure_and_native_pairing(self):
        messages = [{'role': 'user', 'content': 'Build an application. Preserve existing files.'}]
        for n in range(70):
            messages.extend([
                {'role': 'assistant', 'content': '', 'tool_calls': [{'id':str(n), 'type':'function',
                  'function': {'name':'execute_bash','arguments':json.dumps({'command':f'check_step_{n}'})}}]},
                {'role':'tool', 'tool_call_id':str(n), 'content':f'[Tool Observation]: step {n} passed.'}])
        messages[-1]['content'] = '[Tool Observation]: exit status 1. ACTUAL_MISSING_PATH /project/logs/server.log'
        selected, metrics = self.assemble(messages)
        text = json.dumps(selected)
        self.assertIn('ACTUAL_MISSING_PATH', text)
        self.assertIn('Preserve existing files.', text)
        self.assertIn('archived', text)
        calls = {call['id'] for row in selected for call in row.get('tool_calls', [])}
        results = {row['tool_call_id'] for row in selected if row.get('role') == 'tool'}
        self.assertEqual(calls, results)
        self.assertLessEqual(metrics['input_tokens'], metrics['input_budget'])

    def test_large_completed_write_arguments_are_compacted_without_mutation(self):
        messages = [{'role':'user','content':'Create app.html and test it'},
          {'role':'assistant','content':'','tool_calls':[{'id':'write','type':'function',
           'function':{'name':'write_file','arguments':json.dumps({'path':'app.html','content':'abc '*20000})}}]},
          {'role':'tool','tool_call_id':'write','content':'Saved app.html. Tests still pending.'}]
        original = copy.deepcopy(messages)
        selected, metrics = self.assemble(messages)
        self.assertIn('Tests still pending.', json.dumps(selected))
        self.assertIn('Historical tool arguments abbreviated', json.dumps(selected))
        self.assertEqual(messages, original)
        self.assertLessEqual(metrics['input_tokens'], metrics['input_budget'])

    def test_custom_model_rules_survive_boilerplate_fallback(self):
        from engine.prompt_context import identity
        base = identity('Groq', 'fixture', 'Never edit private.cfg.', True, '/tmp', snapshot='entry\n'*4000)
        selected, _ = assemble_context([{'role':'user','content':'Run pwd'}], base, 'Groq', 'fixture',
            {'context_window':4096, 'max_tokens':512, '_simple_filesystem':True}, self.temp.name)
        self.assertIn('Never edit private.cfg.', selected[0]['content'])

    def test_actual_server_log_path_is_passed_to_the_model(self):
        from engine.prompt_context import identity
        prompt = identity('Groq','fixture','',True,'/some/other/project',tasks=[
            {'id':1,'pid':123,'cmd':'python -m http.server 5500',
             'log_path':'/galam/install/logs/tasks/server_5500.log'}])
        self.assertIn('log: /galam/install/logs/tasks/server_5500.log', prompt)


class PanelKeyboardTests(unittest.TestCase):
    def test_ctrl_o_redraw_hides_panel_and_keeps_input(self):
        from prompt_toolkit import PromptSession
        from prompt_toolkit.input.defaults import create_pipe_input
        from prompt_toolkit.output import DummyOutput
        from ui import dock_layout as dock

        async def exercise():
            with create_pipe_input() as pipe:
                session = PromptSession(input=pipe, output=DummyOutput(), key_bindings=dock.kb)
                session.layout.container.children.insert(0, dock.thought_panel)
                final_visibility = []
                session.app.after_render += lambda sender: final_visibility.append(
                    (sender.is_done, bool(dock.thought_panel.filter())))
                def visible():
                    screen = session.app.renderer.last_rendered_screen
                    return ''.join(c.char for row in screen.data_buffer.values() for c in row.values())
                with patch.object(dock, 'prompt_session', session), patch.dict(AGENT_STATE,
                    {'show_thoughts':False,'app_ref':session.app,'is_thinking':False,
                     'reasoning_turns':[{'model':'fixture','reasoning':'VISIBLE_REASONING','phase':''}],
                     'task_progress':[]}):
                    task = asyncio.create_task(session.prompt_async(dock.get_prompt_text))
                    try:
                        await asyncio.sleep(.05)
                        pipe.send_text('draft input')
                        pipe.send_bytes(b'\x0f')
                        await asyncio.sleep(.1)
                        self.assertIn('VISIBLE_REASONING', visible())
                        pipe.send_bytes(b'\x0f')
                        await asyncio.sleep(.1)
                        self.assertNotIn('VISIBLE_REASONING', visible())
                        self.assertEqual(session.default_buffer.text, 'draft input')
                        pipe.send_bytes(b'\x0f')
                        await asyncio.sleep(.1)
                        self.assertIn('VISIBLE_REASONING', visible())
                        pipe.send_text('\r')
                        self.assertEqual(await task, 'draft input')
                        self.assertTrue(AGENT_STATE['show_thoughts'])
                        # The final accepted prompt must not commit the live panel
                        # into scrollback. The next prompt restores the chosen mode.
                        self.assertIn((True, False), final_visibility)
                        task = asyncio.create_task(session.prompt_async(dock.get_prompt_text))
                        await asyncio.sleep(.1)
                        self.assertIn('VISIBLE_REASONING', visible())
                        pipe.send_bytes(b'\x0f')
                        await asyncio.sleep(.1)
                        self.assertNotIn('VISIBLE_REASONING', visible())
                        pipe.send_text('\r')
                        await task
                        task = asyncio.create_task(session.prompt_async(dock.get_prompt_text))
                        await asyncio.sleep(.1)
                        self.assertNotIn('VISIBLE_REASONING', visible())
                        pipe.send_text('\r')
                        await task
                    finally:
                        if not task.done():
                            session.app.exit(result='');await task
        asyncio.run(exercise())
