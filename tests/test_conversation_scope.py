"""Conversation history/plan isolation with legacy resume and real Bash coverage."""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from storage.conversations import (OWNER, PLAN, bind_messages, scoped_messages,
                                   load_history, save_history, conversation_path, conversation_plan)
from engine.context_manager import assemble_context
from config import AGENT_STATE, CONFIG


class ConversationScopeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_resume_loads_only_the_requested_chat(self):
        save_history(self.root, 'a', [{'role':'user','content':'Remember ALPHA_PRIVATE'}])
        save_history(self.root, 'b', [{'role':'user','content':'Remember BETA_PRIVATE'}])
        loaded = load_history(self.root, 'b')
        self.assertNotIn('ALPHA_PRIVATE', json.dumps(loaded))
        self.assertIn('BETA_PRIVATE', json.dumps(loaded))
        self.assertTrue(all(m[OWNER]=='b' for m in loaded))
        self.assertEqual(load_history(self.root, 'new'), [])

    def test_legacy_history_is_scoped_on_read_without_rewriting_the_file(self):
        path = self.root/'old.json'
        data = json.dumps([{'role':'user','content':'Keep my existing code'}]).encode()
        path.write_bytes(data)
        loaded = load_history(self.root,'old')
        self.assertEqual(loaded[0][OWNER], 'old')
        self.assertEqual(path.read_bytes(), data)

    def test_save_drops_foreign_rows_and_preserves_native_metadata(self):
        messages = [{'role':'user','content':'OTHER',OWNER:'a'},
                    {'role':'assistant','content':'','_galam_tool_calls':[{'id':'one'}],OWNER:'b'},
                    {'role':'user','content':'result','_galam_call_id':'one','_galam_action':{'id':'output'}}]
        save_history(self.root,'b',messages)
        loaded = load_history(self.root,'b')
        self.assertEqual(len(loaded),2)
        self.assertEqual(loaded[0]['_galam_tool_calls'],[{'id':'one'}])
        self.assertEqual(loaded[1]['_galam_call_id'],'one')
        self.assertTrue(all(m[OWNER]=='b' for m in loaded))

    def test_invalid_history_data_does_not_become_context(self):
        (self.root/'bad.json').write_text('{"messages":"not a legacy message list"}')
        self.assertEqual(load_history(self.root,'bad'),[])

    def test_conversation_id_cannot_load_a_different_path(self):
        for value in ('../other','a/b','a\\b','..',''):
            with self.assertRaises(ValueError):
                conversation_path(self.root,value)
        (self.root/'alias.json').symlink_to(self.root/'real.json')
        with self.assertRaises(ValueError):
            load_history(self.root,'alias')

    def mixed(self):
        call = {'id':'b-call','type':'function','function':{'name':'execute_bash','arguments':'{"command":"pwd"}'}}
        return [
            {'role':'system','content':'FOREIGN_SYSTEM',OWNER:'a'},
            {'role':'user','content':'Always keep FOREIGN_CONSTRAINT',OWNER:'a'},
            {'role':'user','content':'Run pwd and preserve BETA_CONSTRAINT',OWNER:'b','_galam_tool_mode':'auto'},
            {'role':'assistant','content':'','_galam_tool_calls':[call],OWNER:'b'},
            {'role':'user','content':'[Tool Observation for execute_bash]: BETA_OUTPUT',OWNER:'b',
             '_galam_call_id':'b-call','_galam_action':{'status':'completed'}},
            {'role':'user','content':'FOREIGN_REPAIR',OWNER:'a','_galam_internal':True,'_galam_tool_mode':'none'}]

    def test_summary_and_selected_history_exclude_other_conversations(self):
        selected,_ = assemble_context(self.mixed(),'Identity','OpenRouter','fixture',
                                      {'context_window':8192,'max_tokens':256},str(self.root))
        text = json.dumps(selected)
        self.assertNotIn('FOREIGN',text)
        self.assertIn('BETA_CONSTRAINT',text)
        self.assertIn('BETA_OUTPUT',text)
        self.assertNotIn(OWNER,text)

    def test_native_call_result_pair_and_tool_mode_remain_with_their_chat(self):
        from engine.tool_protocol import wire_messages, options
        wired = wire_messages(self.mixed())
        self.assertNotIn('FOREIGN',json.dumps(wired))
        assistant = next(m for m in wired if m.get('tool_calls'))
        result = next(m for m in wired if m['role']=='tool')
        self.assertEqual(assistant['tool_calls'][0]['id'],result['tool_call_id'])
        with patch.dict(AGENT_STATE,{'working_directory':str(self.root)}):
            protocol = options('OpenRouter','fixture',self.mixed(),{})
        self.assertEqual(protocol['tool_choice'],'auto')

    def test_router_does_not_reintroduce_foreign_system_or_history(self):
        from routers.manager import build_enforced_messages
        with patch.dict(AGENT_STATE,{'working_directory':str(self.root)}):
            messages = build_enforced_messages(self.mixed(),'OpenRouter','fixture')
        self.assertNotIn('FOREIGN',json.dumps(messages))
        self.assertIn('BETA_OUTPUT',json.dumps(messages))

    def test_side_request_without_tags_stays_independent(self):
        request = [{'role':'user','content':'Independent side question'}]
        self.assertEqual(scoped_messages(request),request)
        bind_messages(self.mixed(),'b')
        self.assertEqual(scoped_messages(request),request)

    def test_plan_lookup_uses_this_chat_not_the_newest_artifact(self):
        own = self.root/'plan_a.md';own.write_text('own plan')
        other = self.root/'plan_newest.md';other.write_text('other plan')
        messages = [{'role':'assistant','content':'own',OWNER:'a',PLAN:str(own)},
                    {'role':'assistant','content':'other',OWNER:'b',PLAN:str(other)}]
        self.assertEqual(conversation_plan(messages,'a',self.root),own)
        self.assertIsNone(conversation_plan([],'new-chat',self.root))

    def test_legacy_plan_reference_can_resume_but_missing_latest_does_not_use_another(self):
        own = self.root/'plan_legacy.md';own.write_text('legacy')
        messages = [{'role':'assistant','content':f'Created an implementation plan for old work. Saved to {own}.'}]
        self.assertEqual(conversation_plan(messages,'a',self.root),own)
        messages.append({'role':'assistant','content':'new',PLAN:str(self.root/'plan_missing.md')})
        self.assertIsNone(conversation_plan(messages,'a',self.root))

    def test_plan_approval_queues_only_the_current_conversation_plan(self):
        import galam
        artifacts = self.root/'artifacts';artifacts.mkdir()
        own = artifacts/'plan_own.md';own.write_text('Goal\nKey Files\nExecution Steps\nVerification\nOWN_PLAN')
        other = artifacts/'plan_other.md';other.write_text('Goal\nKey Files\nExecution Steps\nVerification\nFOREIGN_PLAN')
        messages = [{'role':'assistant','content':'saved',OWNER:'a',PLAN:str(own)}]
        queue = Mock()
        with patch.object(galam,'__file__',str(self.root/'galam.py')), patch.object(galam,'TASK_QUEUE',queue):
            self.assertTrue(galam.handle_approved_plan_request('approve and build',messages,'a'))
            queued = queue.put.call_args.args[0]
            self.assertIn('OWN_PLAN',queued[0])
            self.assertNotIn('FOREIGN_PLAN',queued[0])
            queue.reset_mock()
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertTrue(galam.handle_approved_plan_request('approve and build',[],'b'))
            queue.put.assert_not_called()

    def test_real_bash_execution_works_with_scoped_messages(self):
        import galam
        from routers.manager import AgentResponse
        from engine import task_runner
        call = {'id':'bash','type':'function','function':{'name':'execute_bash',
                'arguments':json.dumps({'command':'printf isolation-pass'})}}
        messages = [{'role':'user','content':'FOREIGN_TASK',OWNER:'a'},
                    {'role':'user','content':'Run a harmless terminal check and tell me its result'}]
        responses = [(AgentResponse(tool_calls=[call]),'fixture'),
                     (AgentResponse(content='The terminal check printed isolation-pass.'),'fixture')]
        with patch.dict(CONFIG,{'mode':'ALWAYS-PROCEED'}), \
             patch.dict(AGENT_STATE,{'working_directory':str(self.root),'abort':False,'app_ref':None}), \
             patch.dict(os.environ,{'GALAM_STATE_DIR':str(self.root/'runtime')}), \
             patch.object(galam,'lang_workflow_available',return_value=False), \
             patch.object(galam,'direct_command_for_request',return_value=None), \
             patch.object(galam,'query_with_fallback',side_effect=responses), \
             patch.object(task_runner,'log_tool_execution'), \
             patch.object(galam,'execute_tool_call',wraps=task_runner.execute_tool_call) as execute, \
             contextlib.redirect_stdout(io.StringIO()):
            galam.run_agent_loop(messages,'b',max_steps=3)
        execute.assert_called_once()
        self.assertNotIn('FOREIGN_TASK',json.dumps(messages))
        self.assertTrue(any('isolation-pass' in m.get('content','') and m.get('_galam_action') for m in messages))


if __name__=='__main__':
    unittest.main()
