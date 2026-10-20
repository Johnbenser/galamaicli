"""Side-chat concurrency and status copied locally from the existing trace."""
import contextlib
import io
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

from config import CONFIG,AGENT_STATE
from engine import cancellation
from engine.activity import phase_activity,reasoning_activity
from engine.btw_session import BtwSession
from ui import dock_layout as dock
from routers import manager
from storage.endpoints import ENDPOINT_REGISTRY


class BtwActivityTests(unittest.TestCase):
    def setUp(self):
        self.state=patch.dict(AGENT_STATE,{'abort':False,'task_active':True,'is_thinking':True,
            'app_ref':None,'active_subagents':[],'in_btw_mode':False,
            'current_thought':'Reading main.py','activity_label':'Reading',
            'reasoning_turns':[],'task_progress':[],'context_usage':{'main':'untouched'}})
        self.state.start()

    def tearDown(self):
        cancellation.end_task();self.state.stop()

    def test_side_chat_finishes_while_main_is_busy_without_touching_its_progress(self):
        done=threading.Event();answers=[];history=[]
        def emit(answer):answers.append(answer);done.set()
        def query(messages):
            history.extend(messages)
            return 'SIDE_ANSWER'
        session=BtwSession(query,emit)
        try:
            start=time.monotonic();session.submit('side question')
            self.assertLess(time.monotonic()-start,.1)
            self.assertTrue(done.wait(1))
            self.assertIn('SIDE_ANSWER',answers[0])
            self.assertEqual(AGENT_STATE['current_thought'],'Reading main.py')
            self.assertTrue(AGENT_STATE['task_active'])
            self.assertFalse(AGENT_STATE['abort'])
            self.assertEqual(history[-1]['content'],'side question')
        finally:session.close();session.worker.join(1)

    def test_closing_side_wait_is_independent_of_main_cancellation(self):
        entered=threading.Event();release=threading.Event();output=[]
        main_event=threading.Event();AGENT_STATE['cancel_event']=main_event
        def query(messages):
            return cancellation.call(lambda:(entered.set(),release.wait(2),'late')[2])
        session=BtwSession(query,output.append)
        try:
            session.submit('question');self.assertTrue(entered.wait(1))
            session.close();session.worker.join(.5)
            self.assertFalse(session.worker.is_alive())
            self.assertFalse(main_event.is_set());self.assertFalse(AGENT_STATE['abort'])
            self.assertEqual(output,[])
        finally:release.set();session.close()

    def test_main_abort_does_not_cancel_independent_side_question(self):
        entered=threading.Event();release=threading.Event();done=threading.Event()
        def query(messages):
            return cancellation.call(lambda:(entered.set(),release.wait(2),'side continues')[2])
        session=BtwSession(query,lambda answer:done.set())
        try:
            session.submit('question');self.assertTrue(entered.wait(1))
            cancellation.cancel_current();release.set()
            self.assertTrue(done.wait(1))
        finally:release.set();session.close();session.worker.join(1)

    def test_btw_command_is_nonblocking_and_escape_leaves_main_task_running(self):
        import galam
        entered=threading.Event();release=threading.Event()
        def query(messages):
            return cancellation.call(lambda:(entered.set(),release.wait(2),'answer')[2])
        with patch.object(galam,'_BTW_SESSION',None),patch.object(galam,'query_subagent',side_effect=query), \
             contextlib.redirect_stdout(io.StringIO()):
            galam.handle_btw_command('side question')
            self.assertTrue(entered.wait(1))
            side=galam._BTW_SESSION
            event=SimpleNamespace(app=Mock(),current_buffer=Mock())
            dock.handle_escape(event)
            side.worker.join(.5)
            self.assertFalse(AGENT_STATE['in_btw_mode'])
            self.assertFalse(AGENT_STATE['abort']);self.assertTrue(AGENT_STATE['task_active'])
            release.set()

    def test_btw_input_bypasses_main_queue(self):
        enqueue=Mock();buffer=Mock(text='another side question')
        with patch.dict(AGENT_STATE,{'in_btw_mode':True,'enqueue_prompt':enqueue,'active_message':'main'}):
            dock.handle_enter(SimpleNamespace(current_buffer=buffer,app=Mock()))
        enqueue.assert_not_called();buffer.validate_and_handle.assert_called_once()

    def test_side_request_does_not_overwrite_main_context_budget_or_status(self):
        response=SimpleNamespace(ok=True,status_code=200,headers={'Content-Type':'application/json'},
            json=lambda:{'choices':[{'message':{'content':'Four.','reasoning_content':'Checking arithmetic.'}}]})
        with patch.dict(ENDPOINT_REGISTRY,{'Fixture':{'base_url':'https://fixture.invalid/v1',
            'models':{'fixture':{'context_window':8192}}}}),patch.dict(CONFIG,{'active_endpoint':'Fixture',
            'active_model':'fixture','stream':False}),patch.object(manager.SESSION,'post',return_value=response) as post:
            result=manager.query_subagent([{'role':'user','content':'What is two plus two?'}])
        self.assertEqual(result,'Four.');post.assert_called_once()
        self.assertEqual(AGENT_STATE['context_usage'],{'main':'untouched'})
        self.assertEqual(AGENT_STATE['current_thought'],'Reading main.py')
        self.assertEqual(AGENT_STATE['activity_label'],'Reading')

    def test_real_router_paths_allow_side_answer_before_blocked_main_answer(self):
        from engine.transparency import task_lifecycle
        entered,release,done=threading.Event(),threading.Event(),threading.Event()
        results=[]
        def response(text):
            return SimpleNamespace(ok=True,status_code=200,headers={'Content-Type':'application/json'},
                json=lambda:{'choices':[{'message':{'content':text}}]})
        def post(url,**kwargs):
            if any(m.get('content')=='MAIN_REQUEST' for m in kwargs['json']['messages']):
                entered.set();release.wait(2);return response('MAIN_DONE')
            return response('SIDE_DONE')
        @task_lifecycle
        def main():results.append(manager.query_with_fallback([{'role':'user','content':'MAIN_REQUEST'}]))
        with patch.dict(ENDPOINT_REGISTRY,{'Fixture':{'base_url':'https://fixture.invalid/v1',
             'models':{'fixture':{'context_window':8192}}}}),patch.dict(CONFIG,{'active_endpoint':'Fixture',
             'active_model':'fixture','stream':False}),patch.object(manager.SESSION,'post',side_effect=post) as requests, \
             patch.object(manager,'save_settings'),patch.object(manager,'log_model_interaction'):
            worker=threading.Thread(target=main);worker.start()
            side=None
            try:
                self.assertTrue(entered.wait(1))
                side=BtwSession(manager.query_subagent,lambda answer:done.set())
                side.submit('SIDE_REQUEST')
                self.assertTrue(done.wait(1))
                self.assertTrue(worker.is_alive());self.assertTrue(AGENT_STATE['task_active'])
                self.assertEqual(requests.call_count,2)
                self.assertNotIn('SIDE_REQUEST',str(results))
            finally:
                release.set();worker.join(2)
                if side:side.close();side.worker.join(1)
            self.assertEqual(results[0][0].content,'MAIN_DONE')

    def test_sections_and_tool_phases_produce_one_word_labels(self):
        for text,expected in [('## Structuring\nOutline the modules.','Structuring'),
            ('### Reading local files\nInspect the route definitions.','Reading'),
            ('**Researching**\nLook up the public API.','Researching'),
            ('## Modifying\nRefactor this section.','Modifying')]:
            activity,summary=reasoning_activity(text)
            self.assertEqual(activity,expected);self.assertNotIn('\n',summary)
        self.assertEqual(phase_activity('Executing view_file: /project/app.py')[0],'Reading')
        self.assertEqual(phase_activity('Executing replace_file_content: app.py')[0],'Editing')
        self.assertEqual(phase_activity('Waiting for approval: write_file')[0],'Reviewing')

    def test_status_keeps_latest_supplied_section_without_any_api_requests(self):
        with patch.object(manager.SESSION,'post') as post:
            handler=dock.StructuredThoughtStreamHandler('fixture')
            handler.update_thought('## Reading\nInspect the project entrypoint.')
            handler.show_spinner('Waiting for model response…')
            self.assertEqual(AGENT_STATE['activity_label'],'Reading')
            self.assertIn('project entrypoint',AGENT_STATE['current_thought'])
            handler.show_spinner('Receiving structured action arguments…')
            self.assertEqual(AGENT_STATE['activity_label'],'Structuring')
            post.assert_not_called()
