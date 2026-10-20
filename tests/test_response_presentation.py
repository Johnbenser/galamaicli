"""Visible completion, honest accounting, and lossless terminal response layouts."""
import contextlib
import copy
import io
import json
import os
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from config import AGENT_STATE
from engine import usage
from engine.response_stats import KEY,footer
from engine.transparency import task_lifecycle
from engine.tool_parser import render_markdown_response
from ui import response_renderer as renderer
from ui import dock_layout as dock


def plain(value):
    return renderer.ANSI.sub('',value)


class ResponseAccountingTests(unittest.TestCase):
    def setUp(self):
        self.state=patch.dict(AGENT_STATE,{'app_ref':None,'abort':False,'task_active':False,
                                         'active_subagents':[]})
        self.state.start();usage.start_task('test')

    def tearDown(self):
        self.state.stop()

    def response(self):
        return usage.attach(SimpleNamespace(status_code=200),usage.begin_request('fixture','fixture',{'messages':[]}))

    def test_provider_counts_aggregate_requests_without_double_counting_reasoning_or_cache(self):
        first=self.response()
        counts={'usage':{'prompt_tokens':100,'completion_tokens':30,'total_tokens':130,
                        'completion_tokens_details':{'reasoning_tokens':10},
                        'prompt_tokens_details':{'cached_tokens':50}}}
        usage.capture(first,counts);usage.capture(first,counts)
        second=self.response()
        usage.capture(second,{'x_groq':{'usage':{'prompt_tokens':25,'completion_tokens':5}}})
        self.assertEqual(usage.totals(),{'source':'provider','input':125,'output':35,'total':160,'requests':2})

    def test_streamed_partial_usage_merges_and_nested_response_formats_are_counted(self):
        response=self.response()
        usage.capture(response,{'message':{'usage':{'input_tokens':12}}})
        usage.capture(response,{'usage':{'output_tokens':8}})
        self.assertEqual(usage.totals()['total'],20)
        second=self.response()
        usage.capture(second,{'response':{'usage':{'input_tokens':3,'output_tokens':2}}})
        self.assertEqual(usage.totals()['total'],25)

    def test_missing_usage_is_estimated_and_unobserved_output_stays_unavailable(self):
        response=self.response()
        usage.record_output(response,'Observed answer.', 'Observed reasoning.')
        totals=usage.totals()
        self.assertEqual(totals['source'],'estimated')
        self.assertGreater(totals['output'],0)
        self.assertEqual(totals['total'],totals['input']+totals['output'])
        self.assertNotIn('Observed',json.dumps(usage.snapshot()))
        self.response()
        totals=usage.totals()
        self.assertIsNone(totals['output']);self.assertIsNone(totals['total'])
        text=plain(footer({'elapsed':2,'finished_at':100,'usage':totals}))
        self.assertIn('unavailable',text);self.assertIn('estimated',text)

    def test_each_reply_has_frozen_metadata_and_is_restored_from_history(self):
        import galam
        from storage.conversations import save_history,load_history
        messages=[]
        @task_lifecycle
        def answer(messages):
            response=self.response()
            usage.capture(response,{'usage':{'prompt_tokens':120,'completion_tokens':8}})
            messages.append({'role':'assistant','content':'## Result\nReply '+str(len(messages))})
        with contextlib.redirect_stdout(io.StringIO()) as out:
            for prompt in ['first','second']:
                messages.append({'role':'user','content':prompt});answer(messages)
        self.assertEqual(out.getvalue().count('Worked for'),2)
        old=copy.deepcopy(messages[1][KEY])
        self.assertEqual(messages[3][KEY]['usage']['total'],128)
        self.assertEqual(messages[1][KEY],old)
        self.assertFalse(dock._thinking_active())
        self.assertEqual(AGENT_STATE['task_progress'][-1],'Task finished')
        self.assertIsNone(AGENT_STATE['active_thought_handler'])
        with tempfile.TemporaryDirectory() as directory:
            save_history(directory,'fixture',messages)
            restored=load_history(directory,'fixture')
        with patch('engine.response_stats.time.time',return_value=old['finished_at']+5000),contextlib.redirect_stdout(io.StringIO()) as replay:
            galam.display_resumed_history(restored)
        self.assertEqual(replay.getvalue().count('Worked for'),2)
        self.assertIn(plain(footer(old)),plain(replay.getvalue()))

    def test_error_and_cancel_stop_status_and_keep_footer(self):
        from engine import cancellation
        for cancelled in (False,True):
            messages=[{'role':'user','content':'question'}]
            @task_lifecycle
            def run(messages):
                if cancelled:cancellation.cancel_current()
                else:raise ValueError('fixture failure')
            with contextlib.redirect_stdout(io.StringIO()) as out:
                if cancelled:run(messages)
                else:
                    with self.assertRaises(ValueError):run(messages)
            self.assertFalse(dock._thinking_active())
            self.assertFalse(AGENT_STATE['task_active'])
            self.assertIn('Cancelled after' if cancelled else 'Stopped after',out.getvalue())
            self.assertEqual(messages[0][KEY]['status'],'cancelled' if cancelled else 'failed')

    def test_stale_boolean_cannot_animate_finished_response(self):
        with patch.dict(AGENT_STATE,{'task_active':False,'is_thinking':True,'abort':False,
                                    'active_thought_handler':None}):
            self.assertFalse(dock._thinking_active())

    def test_accounting_metadata_never_enters_model_context(self):
        from engine.context_manager import assemble_context
        messages=[{'role':'user','content':'question'}, {'role':'assistant','content':'answer',KEY:{'private_marker':123}}]
        with tempfile.TemporaryDirectory() as directory:
            selected,_=assemble_context(messages,'Identity','Groq','fixture',{'context_window':8192,'max_tokens':512},directory)
        self.assertNotIn('private_marker',json.dumps(selected));self.assertNotIn(KEY,json.dumps(selected))

    def test_btw_footers_are_per_reply_and_do_not_change_main_ledger(self):
        from engine.btw_session import BtwSession
        main=usage.ledger();done=threading.Event();answers=[]
        def query(messages):
            response=self.response()
            usage.capture(response,{'usage':{'input_tokens':20,'output_tokens':3}})
            return '**Side reply**'
        def emit(answer):answers.append(answer);done.set()
        session=BtwSession(query,emit)
        try:
            for question in ('first','second'):
                done.clear();session.submit(question);self.assertTrue(done.wait(2))
            self.assertEqual(len(answers),2)
            for answer in answers:
                self.assertIn('Worked for',answer)
                self.assertIn('23 total',plain(answer))
            self.assertIs(usage.ledger(),main)
            self.assertEqual(len(main['requests']),0)
        finally:session.close();session.worker.join(1)


class ResponseLayoutTests(unittest.TestCase):
    def render(self,source,width=44):
        with patch.object(renderer.shutil,'get_terminal_size',return_value=os.terminal_size((width,40))):
            output=render_markdown_response(source)
        self.assertTrue(all(renderer.cells(line)<=width for line in output.splitlines()),repr(plain(output)))
        return plain(output)

    def test_screenshot_table_has_borders_aligned_counts_and_no_raw_separator(self):
        output=self.render('## Download summary\n\n| Type | Count |\n|------|------:|\n| .mp3 | 11 |\n| .pdf | 89 |')
        self.assertIn('┌',output);self.assertIn('└',output)
        self.assertIn('.mp3',output);self.assertIn('11',output);self.assertIn('89',output)
        self.assertNotIn('|------',output);self.assertNotIn('##',output)

    def test_narrow_tables_switch_to_labeled_rows_without_dropping_values(self):
        output=self.render('| File | Size | Status |\n|---|---|---|\n| app.py | 1024 | Verified |',24)
        for value in ('File:','app.py','Size:','1024','Status:','Verified'):self.assertIn(value,output)

    def test_long_code_and_unicode_are_wrapped_without_clipping(self):
        source='abcdefghijklmnopqrstuvwxyz0123456789'*4
        output=self.render('```python\n'+source+'\n    print("你好世界")\n```',32)
        body=[]
        for line in output.splitlines():
            if '│' in line:body.append(line.split('│')[1][3:].rstrip())
        self.assertIn(source,''.join(body));self.assertIn('你好世界',''.join(body))
        self.assertIn('1',output);self.assertNotIn('…',output)

    def test_filename_list_sorted_but_steps_and_general_bullets_keep_their_order(self):
        files=self.render('**Files:**\n- zebra.py\n- app.py')
        self.assertLess(files.index('app.py'),files.index('zebra.py'))
        general=self.render('## Steps\n1. zebra first\n2. app second\n- zebra third\n- app fourth')
        self.assertLess(general.index('zebra first'),general.index('app second'))
        self.assertLess(general.index('zebra third'),general.index('app fourth'))

    def test_terminal_controls_removed_and_inline_table_pipes_preserved(self):
        output=self.render('| Expression | Status |\n|---|---|\n| `a|b` | OK |\n\n\x1b[2JNormal text')
        self.assertIn('a|b',output);self.assertIn('Normal text',output)
        self.assertNotIn('\x1b',output)

    def test_inline_code_preserves_literal_filename_and_operator_characters(self):
        output=self.render('Use `__init__.py` and `a**b` or ``x*y``.')
        for value in ('__init__.py','a**b','x*y'):self.assertIn(value,output)

    def test_diff_colors_and_failed_empty_tools_remain_truthful(self):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            dock.render_output_card('- L1 old\n+ L1 new',max_lines=None,success=True)
        self.assertIn('\033[38;5;203m',out.getvalue());self.assertIn('\033[38;5;120m',out.getvalue())
        with contextlib.redirect_stdout(io.StringIO()) as out:
            dock.render_output_card('',success=False)
        self.assertIn('failed',out.getvalue());self.assertNotIn('success',out.getvalue())


if __name__=='__main__':unittest.main()
