"""Task recovery, queue ordering, timed hints and local resume navigation."""
import asyncio
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from config import AGENT_STATE,CONFIG
from engine import cancellation
from engine.message_queue import MessageQueue
from engine.transparency import task_lifecycle
from storage.conversations import save_history,load_history,recent_conversations,delete_history
from ui import dock_layout as dock
from routers import manager


class SessionWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.state=patch.dict(AGENT_STATE,{'abort':False,'app_ref':None,'task_active':False,
            'is_thinking':False,'is_streaming':False,'review_pending':False,'escape_armed_at':None,
            'working_directory':self.temp.name,'cancel_event':None})
        self.state.start()

    def tearDown(self):
        cancellation.end_task()
        self.state.stop();self.temp.cleanup()

    def test_double_escape_cancels_review_and_preserves_draft(self):
        AGENT_STATE.update(task_active=True,is_thinking=True,review_pending=True,review_event=threading.Event())
        token=cancellation.begin_task()
        event=SimpleNamespace(app=Mock(),current_buffer=SimpleNamespace(text='draft prompt'))
        dock.handle_escape(event)
        self.assertFalse(token.is_set());self.assertEqual(event.current_buffer.text,'draft prompt')
        dock.handle_escape(event)
        self.assertTrue(token.is_set());self.assertTrue(AGENT_STATE['review_event'].is_set())
        self.assertEqual(AGENT_STATE['review_decision'],'cancel')

    def test_escape_taps_outside_time_window_do_not_cancel(self):
        AGENT_STATE['task_active']=True
        event=SimpleNamespace(app=Mock())
        with patch.object(dock.time,'monotonic',side_effect=[10,12]):
            dock.handle_escape(event);dock.handle_escape(event)
        self.assertFalse(AGENT_STATE['abort'])

    def test_hint_schedule(self):
        with patch.object(dock,'CLI_OPENED_AT',100):
            for elapsed,visible in [(0,False),(29.9,False),(30,True),(59.9,True),
                (60,False),(239.9,False),(240,True),(269.9,True),(270,False),(450,True)]:
                self.assertEqual(dock.shortcut_hint_visible(100+elapsed),visible)

    def test_real_double_escape_keys_leave_first_tap_and_draft_intact(self):
        from prompt_toolkit import PromptSession
        from prompt_toolkit.input.defaults import create_pipe_input
        from prompt_toolkit.output import DummyOutput
        async def exercise():
            with create_pipe_input() as pipe:
                session=PromptSession(input=pipe,output=DummyOutput(),key_bindings=dock.kb)
                session.app.ttimeoutlen=.05
                AGENT_STATE['task_active']=True
                task=asyncio.create_task(session.prompt_async('> '))
                try:
                    await asyncio.sleep(.05)
                    pipe.send_text('draft')
                    pipe.send_bytes(b'\x1b')
                    await asyncio.sleep(.12)
                    self.assertFalse(AGENT_STATE['abort'])
                    pipe.send_bytes(b'\x1b')
                    await asyncio.sleep(.12)
                    self.assertTrue(AGENT_STATE['abort'])
                    self.assertEqual(session.default_buffer.text,'draft')
                finally:
                    session.app.exit(result='');await task
        asyncio.run(exercise())

    def test_cancel_stalled_io_releases_caller_and_closes_late_response(self):
        release=threading.Event();closed=threading.Event();started=threading.Event()
        response=SimpleNamespace(close=closed.set)
        def blocking():
            started.set();release.wait(2);return response
        cancellation.begin_task()
        def cancel():
            started.wait(1);cancellation.cancel_current()
        killer=threading.Thread(target=cancel);killer.start()
        start=time.monotonic()
        with self.assertRaises(cancellation.TaskCancelled):
            cancellation.call(blocking,dispose=lambda r:r.close())
        self.assertLess(time.monotonic()-start,.6)
        cancellation.begin_task()
        self.assertEqual(cancellation.call(lambda:'next prompt works'),'next prompt works')
        release.set();killer.join(1)
        self.assertTrue(closed.wait(1))

    def test_cancel_stalled_stream_releases_consumer(self):
        cancellation.begin_task();release=threading.Event();started=threading.Event()
        def source():
            started.set();release.wait(2);yield b'late content'
        killer=threading.Thread(target=lambda:(started.wait(1),cancellation.cancel_current()))
        killer.start()
        try:
            with self.assertRaises(cancellation.TaskCancelled):
                next(cancellation.lines(source()))
        finally:
            release.set();killer.join(1)

    def test_cancel_foreground_action_stops_owned_process(self):
        import sys
        cancellation.begin_task()
        cancel=threading.Timer(.15,cancellation.cancel_current);cancel.start()
        start=time.monotonic()
        try:
            with self.assertRaises(cancellation.TaskCancelled):
                cancellation.run_process([sys.executable,'-c','import time;time.sleep(20)'],
                    stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=30)
        finally:
            cancel.cancel()
        self.assertLess(time.monotonic()-start,2)

    def test_failed_task_always_settles_animation_flags(self):
        @task_lifecycle
        def fail():
            AGENT_STATE['is_streaming']=True
            raise RuntimeError('definitive failure')
        with self.assertRaises(RuntimeError):fail()
        self.assertFalse(dock._thinking_active());self.assertFalse(AGENT_STATE['is_streaming'])
        self.assertIsNone(AGENT_STATE.get('cancel_event'))

    def test_hard_request_quota_stops_without_transport_or_endpoint_fallback(self):
        with patch.dict(CONFIG,{'active_endpoint':'NVIDIA','active_model':'fixture','fallback_policy':'auto'}), \
             patch.object(manager.SESSION,'post',side_effect=manager.requests.RequestException(
                 'ResourceExhausted: Worker local total request limit reached (188/32)')) as post, \
             patch.object(manager,'_recorded_urllib') as fallback, \
             patch.object(manager,'save_settings') as save,contextlib.redirect_stdout(io.StringIO()):
            result,_=manager.query_with_fallback([{'role':'user','content':'Hello again'}])
        self.assertEqual(result,'FAILED');post.assert_called_once();fallback.assert_not_called();save.assert_not_called()

    def test_visited_endpoints_stop_reentry(self):
        with patch.dict(CONFIG,{'active_endpoint':'NVIDIA','active_model':'fixture'}), \
             patch.object(manager,'_post_with_transport_retry') as post:
            result,_=manager.query_with_fallback([], _visited_endpoints={'NVIDIA'})
        self.assertEqual(result,'EXHAUSTED');post.assert_not_called()

    def test_queue_fifo_and_clear_do_not_insert_future_prompts_into_context(self):
        queue=MessageQueue();messages=[]
        one=queue.enqueue('first','chat',messages);two=queue.enqueue('second','chat',messages)
        self.assertEqual(messages,[])
        self.assertEqual(queue.get(),one);queue.finish()
        self.assertEqual(queue.get(),two);queue.finish()
        queue.enqueue('discard me','chat',messages)
        self.assertEqual(queue.clear(),1)
        queue.queue.put(None)
        self.assertIsNone(queue.get())

    def test_approved_plan_queue_interface_remains_compatible(self):
        queue=MessageQueue();messages=[]
        queue.put(('approved plan body','chat',messages))
        selected=queue.get()
        self.assertEqual(selected.text,'approved plan body')
        self.assertIs(selected.messages,messages)
        queue.finish()

    def test_worker_cancellation_then_runs_queued_prompt_without_late_mutation(self):
        import galam
        queue=MessageQueue();messages=[];started=threading.Event();release=threading.Event();finished=threading.Event()
        def generate(history):
            current=history[-1]['content']
            if current=='first prompt':
                def slow():
                    started.set();release.wait(2);return 'LATE_OLD_RESPONSE'
                cancellation.call(slow)
            return manager.AgentResponse(content='SECOND_RESPONSE'), 'fixture'
        queue.enqueue('first prompt','chat',messages);queue.enqueue('second prompt','chat',messages)
        queue.queue.put(None)
        with patch.object(galam,'TASK_QUEUE',queue),patch.object(galam,'query_with_fallback',side_effect=generate), \
             patch.object(galam,'lang_workflow_available',return_value=False),patch.object(galam,'direct_command_for_request',return_value=None), \
             patch.object(galam,'persist_conversation'),patch.object(galam,'log_galam_system_event'),contextlib.redirect_stdout(io.StringIO()):
            worker=threading.Thread(target=galam.agent_worker_loop,daemon=True);worker.start()
            self.assertTrue(started.wait(1))
            cancellation.cancel_current()
            worker.join(1)
            self.assertFalse(worker.is_alive())
            release.set()
        self.assertIn('SECOND_RESPONSE',json.dumps(messages))
        self.assertNotIn('LATE_OLD_RESPONSE',json.dumps(messages))
        self.assertEqual([m['content'] for m in messages if m['role']=='user'],['first prompt','second prompt'])
        self.assertFalse(dock._thinking_active())


class ResumeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        for n in range(3):
            save_history(self.root,f'chat{n}',[{'role':'user','content':f'Question {n}'},
                {'role':'assistant','content':f'Reply {n}'}])
            os.utime(self.root/f'chat{n}.json',(100+n,100+n))

    def tearDown(self):self.temp.cleanup()

    def test_recent_index_order_and_selected_context(self):
        rows=recent_conversations(self.root)
        self.assertEqual([r['id'] for r in rows],['chat2','chat1','chat0'])
        messages=load_history(self.root,rows[1]['id'])
        self.assertIn('Question 1',json.dumps(messages));self.assertNotIn('Question 2',json.dumps(messages))

    def test_resume_cli_replays_selected_history_without_sending_a_query(self):
        import sys
        env={**os.environ,'GALAM_CONVERSATIONS_DIR':str(self.root)}
        result=subprocess.run([sys.executable,str(Path(__file__).resolve().parents[1]/'galam.py'),'resume','chat1'],
            input='/exit\n',stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,env=env,timeout=15)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('Question 1',result.stdout)
        self.assertIn('Reply 1',result.stdout)
        self.assertNotIn('Question 2',result.stdout)
        self.assertIn('galamai resume chat1',result.stdout)

    def test_delete_is_permanent_and_rejects_symlink_and_path_traversal(self):
        self.assertTrue(delete_history(self.root,'chat1'))
        self.assertFalse((self.root/'chat1.json').exists())
        self.assertTrue((self.root/'chat2.json').exists())
        for invalid in ('../outside','/tmp/history'):
            with self.assertRaises(ValueError):delete_history(self.root,invalid)
        (self.root/'linked.json').symlink_to(self.root/'chat2.json')
        with self.assertRaises(ValueError):delete_history(self.root,'linked')
        self.assertEqual(len(recent_conversations(self.root)),2)

    def test_picker_keyboard_navigation_uppercase_d_deletion_and_resume(self):
        from prompt_toolkit.input.defaults import create_pipe_input
        from prompt_toolkit.output import DummyOutput
        from ui.conversation_picker import ConversationPicker
        async def exercise():
            with create_pipe_input() as pipe:
                picker=ConversationPicker(self.root,input=pipe,output=DummyOutput())
                task=asyncio.create_task(picker.app.run_async())
                try:
                    await asyncio.sleep(.05)
                    pipe.send_text('\x1b[B')
                    await asyncio.sleep(.05)
                    self.assertEqual(picker.rows[picker.index]['id'],'chat1')
                    pipe.send_text('D')
                    await asyncio.sleep(.05)
                    self.assertFalse((self.root/'chat1.json').exists())
                    pipe.send_text('\r')
                    self.assertEqual(await task,'chat0')
                finally:
                    if not task.done():picker.app.exit(result=None);await task
        asyncio.run(exercise())
