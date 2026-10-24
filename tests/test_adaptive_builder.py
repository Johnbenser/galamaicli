"""Stress-derived regressions: phantom outputs, stale orders and useful context."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from config import CONFIG
from engine.context_manager import assemble_context
from engine.conversation_constraints import constraint_clauses
from engine.task_contract import TaskContract
from engine.prompt_context import identity
from tools.audit_builder_workflows import STAGES, allowed_command, write_fixture
import test_pipelines as fixtures


class AdaptiveBuilderTests(fixtures.Workspace):
    response = fixtures.LoopTests.response
    run_loop = fixtures.LoopTests.run_loop

    def assemble(self, messages):
        return assemble_context(messages, 'Galam', 'Fixture', 'small',
                                {'context_window':32768,'max_tokens':1024,'_simple_filesystem':True},str(self.root))

    def test_readonly_followup_with_coordinated_prohibitions_requests_no_mutation(self):
        from engine.action_results import request_intent
        self.assertEqual(request_intent(STAGES[4][1]), (False,False))
        for request in ('Do not stop, restart or modify any file. Explain the error.',
                        'Do not edit app.py or create data.json. What happened?',
                        'Never create "run.py"; explain startup.',
                        'Explain this snippet: "create file then run server".'):
            self.assertEqual(request_intent(request), (False,False), request)
        self.assertEqual(request_intent('Do not modify app.py but run tests.'), (True,False))
        self.assertEqual(request_intent('Read "fix.py".'), (True,False))
        self.assertEqual(request_intent('Read fix.py.'), (True,False))
        self.assertEqual(request_intent('Do not edit app.py but explain how to run it.'), (False,False))
        self.assertEqual(request_intent('Create "page.html".'), (True,True))
        self.assertEqual(request_intent('Analyze this project.'), (True,False))

    def test_explicit_readonly_followup_rejects_mutations_even_in_always_proceed(self):
        (self.root/'index.html').write_text('KEEP_ME')
        messages, spy = self.run_loop([
            self.response('write_file',{'path':'index.html','content':'WRONG'}),
            self.response('execute_bash',{'command':'npm run build'}),
            self.response('manage_tasks',{'action':'stop','target':'all'}),
            self.response(text='The observed startup error was an undefined handler name. No changes made.')], STAGES[4][1])
        self.assertEqual((self.root/'index.html').read_text(), 'KEEP_ME')
        spy.assert_not_called()
        self.assertTrue(any('[Read-only request]' in m.get('content','') for m in messages))

    def test_descriptive_references_are_not_new_output_names(self):
        for ending in ('Build the file, not a code example.', 'Build the file; do not print examples.',
                       'Build the file not a code example.', 'Then build that file.'):
            contract = TaskContract('Create about.html as a standalone page. '+ending,str(self.root))
            self.assertNotIn(str(self.root/'not'),contract.missing())
            self.assertEqual([r['path'] for r in contract.requirements if r['path']], [self.root/'about.html'])

    def test_negated_outputs_are_not_created_but_positive_clauses_survive(self):
        for request in ("Do not create secret.html and file hidden.html; create public.html.",
                        "Never create secret.html but create public.html."):
            contract = TaskContract(request, str(self.root))
            self.assertEqual([r['path'] for r in contract.requirements], [self.root/'public.html'])
        contract = TaskContract('Create public.html with "do not delete".', str(self.root))
        self.assertEqual(contract.requirements[0]['path'], self.root/'public.html')

    def test_quoted_and_explicitly_named_grammar_words_remain_valid_names(self):
        for request in ('Create file "not"', 'Create file named not', 'Create file "report,final.html"'):
            contract = TaskContract(request,str(self.root))
            expected = 'report,final.html' if 'report' in request else 'not'
            self.assertEqual(contract.requirements[0]['path'],self.root/expected)

    def test_creation_receipt_does_not_trigger_repair_after_a_valid_file(self):
        content = '<!doctype html><title>Galam Builder Lab</title><h1>Galam Builder Lab</h1><p>Version Alpha</p><a href="index.html">Home</a>'
        messages, _ = self.run_loop([
            self.response('write_file',{'path':'about.html','content':content}),
            self.response(text='Done. about.html contains the requested title, heading, paragraph and navigation link.')],STAGES[1][1])
        self.assertEqual(self.model_calls,2)
        self.assertEqual((self.root/'about.html').read_text(),content)
        self.assertFalse(any('protocol repair' in m.get('content','') for m in messages))
        self.assertNotIn('Query error',self.last_display)

    def test_constraints_do_not_reissue_completed_actions(self):
        kept = constraint_clauses(STAGES[0][1])
        self.assertIn('Preserve unrelated code, tests, package/build scripts and data/user.json.',kept)
        self.assertFalse(any('Analyze' in clause or 'Run tests again' in clause for clause in kept))
        self.assertEqual(constraint_clauses('Run tests and preserve data.'),['preserve data.'])
        self.assertEqual(constraint_clauses('Preserve data and run tests.'),['Preserve data'])
        self.assertEqual(constraint_clauses('Change only the paragraph to blue.'),[])
        self.assertEqual(constraint_clauses('Always run tests after editing.'),['Always run tests after editing.'])

    def test_edit_scoped_restrictions_do_not_become_permanent_global_rules(self):
        self.assertEqual(constraint_clauses('Preserve every other byte; Do not change any other file.'), [])
        self.assertEqual(constraint_clauses('Do not change test_totals.py.'), ['Do not change test_totals.py.'])
        self.assertEqual(constraint_clauses('Always preserve every other byte.'), ['Always preserve every other byte.'])

    def test_constraints_preserve_quoted_code_and_negative_scope(self):
        for request in ('Keep `data; and run tests.` unchanged.',
                        'Do not change data and run migrations.',
                        'Never delete "keep;this.txt".',
                        'Preserve the literal "and run tests" in config.json.'):
            self.assertEqual(constraint_clauses(request),[request])

    def test_new_file_task_omits_old_action_transcript_but_keeps_restrictions(self):
        old = [{'role':'user','content':'Analyze totals.py and run tests. Preserve data/user.json. Run tests again.'},
               {'role':'assistant','content':'OLD_REPAIR_RECEIPT '+('calculation traceback\n'*300)},
               {'role':'user','content':STAGES[1][1]}]
        original = copy.deepcopy(old)
        selected, metrics = self.assemble(old)
        text = json.dumps(selected)
        self.assertNotIn('OLD_REPAIR_RECEIPT',text)
        self.assertNotIn('Run tests again',text)
        self.assertIn('Preserve data/user.json',text)
        self.assertIn('Create about.html',text)
        self.assertEqual(old,original)
        self.assertLess(metrics['messages_selected'],metrics['messages_available'])

    def test_prior_factual_api_configuration_is_available_to_new_file_task(self):
        messages = [{'role':'user','content':'The API base URL is https://example.invalid/project-api.'},
                    {'role':'assistant','content':'Stored the API configuration.'},
                    {'role':'user','content':'Discuss seasons'}, {'role':'assistant','content':'Seasons vary.'},
                    {'role':'user','content':'Say hello'}, {'role':'assistant','content':'Hello'},
                    {'role':'user','content':'Create client.py for the API with a request helper.'}]
        selected,_ = self.assemble(messages)
        self.assertIn('https://example.invalid/project-api',json.dumps(selected))

    def test_recall_keywords_match_sentence_punctuation_and_keep_extensions(self):
        from engine.conversation_recall import terms
        self.assertEqual(terms('design.'), terms('design'))
        self.assertIn('about.html', terms('about.html.'))

    def test_same_file_and_explicit_history_references_keep_relevant_turns(self):
        for query in ('Edit about.html to use blue.', 'Create theme.css using the previous design.'):
            messages = [{'role':'user','content':'Create about.html with the requested design.'},
                        {'role':'assistant','content':'PRIOR_DESIGN_EVIDENCE design uses blue and a compact header.'},
                        {'role':'user','content':query}]
            selected, _ = self.assemble(messages)
            self.assertIn('PRIOR_DESIGN_EVIDENCE', json.dumps(selected))

    def test_current_native_calls_and_results_stay_paired(self):
        call={'id':'current','type':'function','function':{'name':'view_file','arguments':'{"path":"index.html"}'}}
        messages=[{'role':'user','content':'Create old.html'}, {'role':'assistant','content':'OLD_FINISHED'},
                  {'role':'user','content':'Edit index.html to use blue.'},
                  {'role':'assistant','content':'','tool_calls':[call]},
                  {'role':'tool','tool_call_id':'current','content':'CURRENT_SOURCE exact contents'}]
        selected,_ = self.assemble(messages)
        self.assertEqual([c['id'] for m in selected for c in m.get('tool_calls',[])],['current'])
        self.assertEqual([m['tool_call_id'] for m in selected if m.get('role')=='tool'],['current'])
        self.assertIn('CURRENT_SOURCE',json.dumps(selected))
        self.assertNotIn('OLD_FINISHED',json.dumps(selected))

    def test_continue_keeps_unfinished_task_and_its_original_source(self):
        messages=[{'role':'user','content':'Create index.html and theme.css.'},
                  {'role':'assistant','content':'CURRENT_PARTIAL_WORK'},
                  {'role':'user','content':'continue'}]
        selected,_ = self.assemble(messages)
        self.assertIn('CURRENT_PARTIAL_WORK',json.dumps(selected))
        self.assertIn('theme.css',json.dumps(selected))

    def test_malformed_edit_gets_specific_diagnosis_without_guessed_execution(self):
        from engine.tool_protocol import ActionRepair
        from engine.tool_parser import extract_tools_from_text
        broken = '<tool_call>{"name":"replace_file_content","arguments":{"path":"app.py","target_content":"f("x")","replacement_content":"f("y")"}}</tool_call>'
        self.assertEqual(extract_tools_from_text(broken), [])
        repair = ActionRepair().next_message('Fix app.py', broken, (0,))['content']
        self.assertIn('Rejected tool JSON', repair)
        self.assertIn('NOT executed', repair)
        self.assertIn('before dependent commands', repair)
        self.assertIn('Escape embedded double quotes', repair)
        self.assertNotIn('Original request:', repair)

    def test_repeated_json_failure_switches_existing_xml_contract_for_current_task(self):
        from engine.tool_protocol import ActionRepair
        from engine.tool_parser import extract_tools_from_text
        from engine.action_instructions import response_contract
        from routers import manager
        repair = ActionRepair()
        broken = '<tool_call>{"name":"replace_file_content","arguments":{"path":"x.py","target_content":"f("old")"}}</tool_call>'
        repair.next_message('Fix x.py',broken,(0,))
        feedback = repair.next_message('Fix x.py',broken,(0,))
        self.assertEqual(feedback['_galam_text_syntax'], 'xml')
        xml = '<invoke name="replace_file_content"><parameter name="path">x.py</parameter><parameter name="target_content">f("old")</parameter><parameter name="replacement_content">f("new")</parameter></invoke>'
        calls = extract_tools_from_text(xml)
        self.assertEqual(json.loads(calls[0]['function']['arguments'])['replacement_content'], 'f("new")')
        messages = [{'role':'user','content':'Fix x.py'}, feedback]
        with patch.dict(CONFIG,{'context_window':32768,'max_tokens':1024}):
            selected = manager.build_enforced_messages(messages,'Direct','codestral-latest')
            self.assertIn('Emit one complete <invoke',selected[0]['content'])
            messages.append({'role':'user','content':'Create fresh.html'})
            selected = manager.build_enforced_messages(messages,'Direct','codestral-latest')
            self.assertNotIn('Emit one complete <invoke', selected[0]['content'])
        self.assertNotIn('JSON',response_contract(xml=True).split('Available tools:')[1])

    def test_repeated_failure_guidance_is_coalesced_without_ending_work(self):
        from engine.command_agent import CommandAgent
        from engine.action_results import ActionResult
        from engine import usage
        usage.start_task('diagnosis')
        messages = [{'role':'user','content':'Start the project server'}]
        with CommandAgent(messages,'diagnosis',CONFIG) as agent:
            failed = ActionResult('NameError: missing_name',False,1)
            for _ in range(4):
                agent.observe('execute_bash',{'command':'npm run dev'},failed)
                agent.before_query()
            self.assertIn('same startup failure persists',agent.messages[-1]['content'])
            selected, _ = self.assemble(agent.messages)
            self.assertEqual(sum('same startup failure persists' in m['content'] for m in selected),1)
            self.assertIsNone(agent.ready)

    def test_announced_address_corrects_hint_without_losing_health_path(self):
        from engine.process_runner import announced_url
        self.assertEqual(announced_url('Listening http://0.0.0.0:45123/', 'http://localhost:1/health?ready=1'),
                         'http://127.0.0.1:45123/health?ready=1')
        self.assertEqual(announced_url('Docs https://example.com:443/'), '')
        self.assertEqual(announced_url('Invalid http://127.0.0.1:65536/'), '')

    def test_real_dynamic_service_recovers_from_incorrect_readiness_port(self):
        import socket
        import os
        from engine.process_runner import launch_process
        from engine.task_runner import TaskManager
        with socket.socket() as reserved:
            reserved.bind(('127.0.0.1', 0))
            wrong = reserved.getsockname()[1]
        (self.root/'index.html').write_text('DYNAMIC_SERVICE')
        (self.root/'server.py').write_text('from http.server import HTTPServer, SimpleHTTPRequestHandler\ns=HTTPServer(("127.0.0.1",0),SimpleHTTPRequestHandler)\nprint(f"http://127.0.0.1:{s.server_port}/",flush=True)\ns.serve_forever()\n')
        manager = TaskManager()
        try:
            result = launch_process('python -u server.py',str(self.root),dict(os.environ),manager,
                                    readiness_url=f'http://127.0.0.1:{wrong}/',timeout=8)
            self.assertTrue(result.success, result.output)
            self.assertTrue(result.verified)
            self.assertNotEqual(result.metadata['url'], f'http://127.0.0.1:{wrong}/')
            self.assertEqual(len(manager.get_active_tasks()), 1)
        finally:
            processes = [task['process'] for task in manager.get_active_tasks()]
            manager.stop_all()
            for process in processes:
                process.wait(timeout=3)

    def test_live_task_context_uses_actual_url_and_unknown_port(self):
        task={'id':1,'pid':1234,'cmd':'custom preview','state':'ready','readiness_url':'http://127.0.0.1:43217/app'}
        text = identity('Fixture','small','',False,str(self.root),tasks=[task])
        self.assertIn('url: http://127.0.0.1:43217/app',text)
        self.assertIn('state: ready',text)
        self.assertIn('port unknown',text)
        self.assertNotIn('port 5500',text)

    def test_readonly_server_followup_gets_live_state_without_probing(self):
        from routers import manager
        from engine import task_runner
        task={'id':1,'pid':1234,'cmd':'custom preview','port':'43217','state':'ready','readiness_url':'http://127.0.0.1:43217/app'}
        with patch.dict(CONFIG,{'context_window':8192,'max_tokens':1024}), \
             patch.object(task_runner,'GLOBAL_TASK_MANAGER',SimpleNamespace(get_active_tasks=lambda:[task])), \
             patch.object(task_runner,'python_curl') as probe:
            selected = manager.build_enforced_messages([{'role':'user','content':'What is the running server address?'}],'Fixture','small')
        self.assertIn('http://127.0.0.1:43217/app',json.dumps(selected))
        probe.assert_not_called()

    def test_experiment_confinement_accepts_equivalent_tests_and_rejects_escape(self):
        for command in ('python -m unittest -v','python -m pytest -q || python -m unittest -v',
                        f'cd -- {self.root} && npm run dev','npm run build 2>&1', 'npm run dev 2>&1 &'):
            self.assertTrue(allowed_command(command,self.root),command)
        for command in ('cd - && npm run dev','cat /etc/passwd','cat ../outside',
                        'pip install unknown','curl https://example.com','echo x > index.html', 'cat README.md &'):
            self.assertFalse(allowed_command(command,self.root),command)
