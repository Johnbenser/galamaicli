"""Pinned pending prompts and completion timing in the existing chat dock."""
import asyncio
import contextlib
import io
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

from config import AGENT_STATE
from engine.message_queue import MessageQueue
from engine.transparency import task_lifecycle
from ui import dock_layout as dock


class PendingDisplayTests(unittest.TestCase):
    def test_pending_prompts_leave_input_open_and_stay_pinned_until_started(self):
        from prompt_toolkit import PromptSession
        from prompt_toolkit.input.defaults import create_pipe_input
        from prompt_toolkit.output import DummyOutput
        async def exercise():
            with create_pipe_input() as pipe:
                session=PromptSession(input=pipe,output=DummyOutput(),key_bindings=dock.kb)
                queue=MessageQueue();history=[]
                def screen_text():
                    screen=session.app.renderer.last_rendered_screen
                    return ''.join(c.char for row in screen.data_buffer.values() for c in row.values())
                with patch.object(dock,'prompt_session',session),patch.dict(AGENT_STATE,
                    {'task_active':True,'is_thinking':True,'abort':False,'app_ref':session.app,
                     'queued_messages':[],'active_message':'current','review_pending':False,
                     'enqueue_prompt':lambda text:queue.enqueue(text,'chat',history,staged=True)}), \
                     contextlib.redirect_stdout(io.StringIO()) as output:
                    task=asyncio.create_task(session.prompt_async(dock.get_prompt_text))
                    try:
                        await asyncio.sleep(.05)
                        pipe.send_text('PINNED_REQUEST_ONE\r')
                        await asyncio.sleep(.08)
                        self.assertFalse(task.done())
                        self.assertEqual(session.default_buffer.text,'')
                        self.assertIn('PINNED_REQUEST_ONE',screen_text())
                        self.assertEqual(history,[])
                        pipe.send_text('PINNED_REQUEST_TWO\r')
                        await asyncio.sleep(.08)
                        self.assertFalse(task.done())
                        self.assertIn('PINNED_REQUEST_TWO',screen_text())
                        self.assertEqual(len(AGENT_STATE['queued_messages']),2)
                        item=queue.get()
                        self.assertEqual(item.text,'PINNED_REQUEST_ONE');self.assertTrue(item.staged)
                        await asyncio.sleep(.08)
                        self.assertNotIn('PINNED_REQUEST_ONE',screen_text())
                        self.assertIn('PINNED_REQUEST_TWO',screen_text())
                        queue.finish();queue.clear()
                        await asyncio.sleep(.08)
                        self.assertNotIn('PINNED_REQUEST_TWO',screen_text())
                        self.assertEqual(output.getvalue(),'')
                    finally:
                        if not task.done():session.app.exit(result='');await task
        asyncio.run(exercise())

    def test_busy_commands_and_review_answers_still_use_existing_input_path(self):
        for text,review in [('/cancel',False),('/queue clear',False),('yes',True)]:
            buffer=Mock(text=text)
            event=SimpleNamespace(current_buffer=buffer,app=Mock())
            enqueue=Mock()
            with patch.dict(AGENT_STATE,{'task_active':True,'active_message':'current',
                'enqueue_prompt':enqueue,'review_pending':review}):
                dock.handle_enter(event)
            enqueue.assert_not_called();buffer.validate_and_handle.assert_called_once()

    def test_pending_preview_is_bounded_and_omits_ids_and_terminal_controls(self):
        with patch.dict(AGENT_STATE,{'queued_messages':[{'id':'secret-id','text':'one\x1b[31m\nmore'},
             {'id':'two','text':'x'*200},{'id':'three','text':'third'}]}):
            text=''.join(value for _,value in dock.get_pending_prompt_tokens(40))
        self.assertIn('one more',text);self.assertIn('+1 more pending',text)
        self.assertNotIn('secret-id',text);self.assertNotIn('\x1b',text)
        self.assertLess(len(text.splitlines()[1]),40)

    def test_completed_task_duration_and_current_clock(self):
        with patch.dict(AGENT_STATE,{'last_task_timing':{'elapsed':125.9,'cancelled':False}}), \
             patch.object(dock.time,'strftime',return_value='02:22:08 PM'):
            self.assertEqual(dock.completed_task_text(),'Worked for 2m 05s • 02:22:08 PM • Total Token unavailable')
        with patch.dict(AGENT_STATE,{'last_task_timing':{'elapsed':3,'cancelled':True}}), \
             patch.object(dock.time,'strftime',return_value='02:22:09 PM'):
            self.assertEqual(dock.completed_task_text(),'Cancelled after 0m 03s • 02:22:09 PM • Total Token unavailable')

    def test_task_timing_measures_execution_once_instead_of_queue_wait(self):
        @task_lifecycle
        def run():pass
        with patch.dict(AGENT_STATE,{'app_ref':None}),patch('engine.transparency.time.monotonic',side_effect=[10,135.4]), \
             patch('engine.transparency.time.time',return_value=12345):
            run()
            timing=AGENT_STATE['last_task_timing']
        self.assertAlmostEqual(timing['elapsed'],125.4)
        self.assertEqual(timing['finished_at'],12345)

    def test_transient_dock_text_does_not_commit_on_prompt_accept(self):
        fake=SimpleNamespace(app=SimpleNamespace(is_done=True))
        with patch.object(dock,'prompt_session',fake),patch.dict(AGENT_STATE,
            {'task_active':True,'is_thinking':True,'abort':False,
             'queued_messages':[{'id':'one','text':'pending text'}]}):
            text=''.join(value for _,value in dock.get_prompt_text())
        self.assertNotIn('Brainstorming',text);self.assertNotIn('pending text',text)
