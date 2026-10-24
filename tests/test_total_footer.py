"""Provider totals and graph-worker accounting without extra model calls."""
import contextlib
from contextvars import copy_context
from concurrent.futures import ThreadPoolExecutor
import io
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from config import CONFIG,AGENT_STATE
from engine import usage
from engine.response_stats import footer,summary,KEY
from ui.response_renderer import ANSI


class TotalFooterTests(unittest.TestCase):
    def setUp(self):usage.start_task('total-footer')

    def response(self):
        return usage.attach(SimpleNamespace(status_code=200),usage.begin_request('fixture','model',{'messages':[]}))

    def test_only_duration_fixed_completion_clock_and_total_are_displayed(self):
        stats={'elapsed':125.9,'finished_at':123,'usage':{'source':'provider','input':1000,'output':234,'total':1234}}
        clock=time.struct_time((2026,10,21,14,22,8,2,294,-1))
        with patch('engine.response_stats.time.localtime',return_value=clock) as local:
            text=ANSI.sub('',footer(stats)).strip()
        self.assertEqual(text,'Worked for 2m 05s • 02:22:08 PM • Total Token 1,234')
        local.assert_called_once_with(123)
        self.assertNotIn('input',text);self.assertNotIn('output',text)

    def test_estimate_unknown_local_and_failed_status_are_not_misreported(self):
        for source,total,expected in [('estimated',100,'~100'),('estimated',None,'unavailable'),('local',0,'0')]:
            stats={'elapsed':1,'finished_at':123,'usage':{'source':source,'total':total}}
            self.assertTrue(summary(stats).endswith('Total Token '+expected))
        for status,label in [('failed','Stopped after'),('cancelled','Cancelled after'),('incomplete','Stopped after')]:
            self.assertTrue(summary({**stats,'status':status}).startswith(label))

    def test_explicit_total_is_authoritative_without_counting_reasoning_twice(self):
        response=self.response()
        data={'usage':{'input_tokens':100,'output_tokens':20,'total_tokens':150,
                       'output_tokens_details':{'reasoning_tokens':30}}}
        usage.capture(response,data);usage.capture(response,data)
        usage.capture(response,{'usage':{'input_tokens':100,'output_tokens':20}})
        self.assertEqual(usage.totals()['total'],150)
        self.assertEqual(usage.snapshot()['provider_total_tokens'],150)

    def test_total_only_and_split_usage_frames_are_preserved(self):
        usage.capture(self.response(),{'usage':{'total_tokens':91}})
        second=self.response()
        usage.capture(second,{'usage':{'input_tokens':12}})
        usage.capture(second,{'usage':{'output_tokens':8}})
        result=usage.totals()
        self.assertEqual(result['total'],111);self.assertEqual(result['source'],'provider')
        self.assertIsNone(result['output']);self.assertEqual(usage.snapshot()['provider_total_tokens'],111)

    def test_worker_context_shares_task_ledger_and_independent_threads_do_not(self):
        main=usage.ledger()
        def record():
            usage.capture(self.response(),{'usage':{'prompt_tokens':12,'completion_tokens':3}})
            return usage.ledger()
        with ThreadPoolExecutor(max_workers=1) as pool:
            worker=pool.submit(copy_context().run,record).result()
            self.assertIs(worker,main)
            self.assertEqual(usage.totals()['total'],15)
            independent=pool.submit(usage.ledger).result()
        self.assertIsNot(independent,main);self.assertEqual(len(independent['requests']),0)

    def test_real_optional_graph_counts_all_requests_in_task_footer_without_extra_calls(self):
        from engine import lang_workflow
        from engine.transparency import task_lifecycle
        from routers import manager
        from storage.endpoints import ENDPOINT_REGISTRY
        if not lang_workflow.is_available():self.skipTest('Optional planning dependencies not installed')
        response=lambda:SimpleNamespace(ok=True,status_code=200,headers={'Content-Type':'application/json'},
            json=lambda:{'choices':[{'message':{'content':'OK.'}}],
                         'usage':{'prompt_tokens':12,'completion_tokens':3,'total_tokens':15}})
        messages=[{'role':'user','content':'Hi'}]
        @task_lifecycle
        def task(messages):
            for _ in range(2):answer,_=lang_workflow.query_planning_graph(messages)
            messages.append({'role':'assistant','content':answer.content})
        with tempfile.TemporaryDirectory() as directory,patch.dict(AGENT_STATE,{'working_directory':directory,'app_ref':None,'abort':False}), \
             patch.dict(ENDPOINT_REGISTRY,{'Fixture':{'base_url':'https://fixture.invalid/v1','models':{'fixture':{}}}},clear=True), \
             patch.dict(CONFIG,{'active_endpoint':'Fixture','active_model':'fixture','stream':False}), \
             patch.object(manager.SESSION,'post',side_effect=lambda *args,**kwargs:response()) as post, \
             patch.object(manager,'_recorded_urllib',side_effect=AssertionError('No network in fixture')), \
             patch.object(manager,'save_settings'),patch.object(manager,'log_model_interaction'),contextlib.redirect_stdout(io.StringIO()) as out:
            task(messages)
        self.assertEqual(post.call_count,2)
        self.assertEqual(messages[-1][KEY]['usage']['total'],30)
        self.assertIn('Total Token 30',out.getvalue())
        for call in post.call_args_list:
            sent=call.kwargs['json']
            self.assertNotIn(KEY,str(sent));self.assertNotIn('Total Token',str(sent))
