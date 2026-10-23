"""Behavior tests for Galam's existing entry points and upgraded internals."""

import contextlib
import copy
import io
import json
import os
from pathlib import Path
import shlex
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from config import AGENT_STATE, CONFIG
from engine.action_results import ActionResult, request_intent
from engine.context_manager import assemble_context, estimate_tokens, ContextLimitError
from engine.edit_journal import digest, undo_edit
from engine.execution_policy import requires_review
from engine.lang_workflow import validate_tool_arguments
from engine.repo_context import repository_context
from engine.result_store import read_action_output
from engine.tool_parser import clean_response_text, extract_tools_from_text
from engine import task_runner
from tools import filesystem


class Workspace(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "project"
        self.root.mkdir()
        self.env = patch.dict(os.environ, {"GALAM_STATE_DIR": str(Path(self.temp.name) / "runtime")})
        self.env.start()
        self.state = patch.dict(AGENT_STATE, {"working_directory": str(self.root), "abort": False, "app_ref": None})
        self.state.start()
        self.logger = patch.object(task_runner, "log_tool_execution")
        self.logger.start()

    def tearDown(self):
        self.logger.stop()
        self.state.stop()
        self.env.stop()
        self.temp.cleanup()

    def command(self, code):
        return f"{shlex.quote(sys.executable)} -c {shlex.quote(code)}"


class ActionTests(Workspace):
    def test_real_exit_status_overrides_error_words(self):
        result = task_runner.execute_tool_call("execute_bash", {"command": self.command("print('permission denied is an example')")})
        self.assertTrue(result.success)
        self.assertEqual(result.exit_code, 0)
        failed = task_runner.execute_tool_call("execute_bash", {"command": self.command("raise SystemExit(7)")})
        self.assertFalse(failed.success)
        self.assertEqual(failed.exit_code, 7)
        self.assertIn("[Exit status 7]", failed.output)

    def test_legacy_unpacking_is_preserved(self):
        result = ActionResult("example", is_background=True, status="running")
        text, background = result
        self.assertEqual((text, background), ("example", True))
        self.assertEqual(result[0], "example")

    def test_http_failure_is_not_converted_to_success(self):
        response = SimpleNamespace(returncode=22, stdout="curl: (22) HTTP 404")
        with patch.object(task_runner.subprocess, "run", return_value=response), patch.object(
                task_runner, "parse_and_run_curl_command", return_value=("HTTP 404: missing", True)):
            result = task_runner.execute_shell_action("curl --fail http://example.invalid")
        self.assertFalse(result.success)
        self.assertEqual(result.exit_code, 22)

    def test_output_tail_and_full_output_remain_available(self):
        code = "print('x'*16000); print('FINAL IMPORTANT ERROR'); raise SystemExit(4)"
        result = task_runner.execute_tool_call("execute_bash", {"command": self.command(code)})
        self.assertFalse(result.success)
        self.assertIn("FINAL IMPORTANT ERROR", result.output)
        self.assertLess(len(result.output), 12000)
        self.assertIn("FINAL IMPORTANT ERROR", read_action_output(result.action_id, 1, 10))
        self.assertGreater(Path(result.output_path).stat().st_size, 16000)

    def test_output_lookup_rejects_path_traversal(self):
        with self.assertRaises(ValueError):
            read_action_output("../../something")

    def test_output_search_finds_errors_beyond_the_first_page(self):
        result = task_runner.execute_tool_call("execute_bash", {"command": self.command(
            "print('ordinary line\\n'*400); print('FINAL ERROR 987')")})
        self.assertIn("FINAL ERROR 987", read_action_output(result.action_id, 1, 5, query="ERROR"))

    def test_directory_navigation_is_preserved(self):
        child = self.root / "space folder"
        child.mkdir()
        result = task_runner.execute_shell_action(f"cd -- {shlex.quote(str(child))} && pwd")
        self.assertTrue(result.success)
        self.assertEqual(AGENT_STATE["working_directory"], str(child))
        self.assertIn(str(child), result.output)

    def test_schema_validation_does_not_need_optional_graph(self):
        self.assertEqual(validate_tool_arguments("view_file", {"path": "x", "start_line": "2"})["start_line"], 2)
        for name, args in [("write_file", {"path": "x"}), ("execute_bash", {"command": 3}), ("unknown", {})]:
            with self.assertRaises(ValueError):
                validate_tool_arguments(name, args)

    def test_review_modes_keep_their_meaning(self):
        self.assertTrue(requires_review("REVIEW", "view_file", {"path": "a.py"}))
        self.assertFalse(requires_review("AUTO", "view_file", {"path": "a.py"}))
        self.assertTrue(requires_review("AUTO", "write_file", {"path": "a.py"}))
        self.assertFalse(requires_review("ALWAYS-PROCEED", "write_file", {"path": "a.py"}))

    def test_constraints_do_not_turn_inspection_into_a_code_change(self):
        self.assertEqual(request_intent("Read a.py. Do not modify any files."), (True, False))
        self.assertEqual(request_intent("Fix a.py but do not change the UI."), (True, True))
        self.assertEqual(request_intent("Explain how to run this code."), (False, False))
        self.assertEqual(request_intent("Change the model in settings."), (True, False))


class ParserTests(unittest.TestCase):
    def test_explicit_existing_formats_work(self):
        examples = [
            '<tool_call>{"name":"execute_bash","arguments":{"command":"pwd"}}</tool_call>',
            '{"name":"execute_bash","arguments":{"command":"pwd"}}',
            '```json\n{"name":"view_file","arguments":{"path":"a.py"}}\n```',
            '<|tool_call_start|>{"name":"execute_bash","arguments":{"command":"pwd"}}<|tool_call_end|>',
            'Running the command now:\n```bash\npwd\n```',
        ]
        for example in examples:
            with self.subTest(example=example):
                self.assertEqual(len(extract_tools_from_text(example)), 1)

    def test_shell_explanations_remain_visible_and_do_not_execute(self):
        text = 'For reference, this is an example; do not run it:\n```bash\npwd\n```'
        self.assertEqual(extract_tools_from_text(text), [])
        self.assertIn("```bash\npwd", clean_response_text(text))

    def test_incomplete_calls_do_not_execute(self):
        self.assertEqual(extract_tools_from_text('<tool_call>{"name":"execute_bash","arguments":{"command":"pwd"}}'), [])

    def test_multiline_shell_semantics_are_preserved(self):
        block = "cat <<'END'\n    first\n\nsecond\nEND"
        calls = extract_tools_from_text("```bash\n" + block + "\n```")
        self.assertEqual(json.loads(calls[0]["function"]["arguments"])["command"], block)

    def test_recovery_tools_use_the_existing_envelope(self):
        calls = extract_tools_from_text('<tool_call>{"name":"undo_file_change","arguments":{"edit_id":"last"}}</tool_call>')
        self.assertEqual(calls[0]["function"]["name"], "undo_file_change")


class ContextTests(Workspace):
    def test_constraints_survive_long_history_at_different_limits(self):
        messages = [{"role": "user", "content": "PROJECT CONSTRAINT: preserve the existing API and keep the UI unchanged."}]
        for n in range(30):
            messages.extend([{"role": "assistant", "content": f"Previous response {n}: " + "noise " * 90},
                             {"role": "user", "content": f"Question {n}."}])
        messages.append({"role": "user", "content": "List project files."})
        original = copy.deepcopy(messages)
        for window in (4096, 8192, 32768, 131072):
            selected, metrics = assemble_context(messages, "You are Galam.ai.", "generic", "unknown",
                                                {"context_window": window, "max_tokens": 1024}, str(self.root))
            serialized = json.dumps(selected)
            self.assertIn("preserve the existing API", serialized)
            self.assertIn("List project files.", serialized)
            self.assertLessEqual(estimate_tokens(selected), metrics["input_budget"])
            self.assertTrue(metrics["estimated"])
        self.assertEqual(messages, original)

    def test_large_required_request_is_reported(self):
        with self.assertRaises(ContextLimitError):
            assemble_context([{"role": "user", "content": "x" * 20000}], "Galam", "x", "x",
                             {"context_window": 4096}, str(self.root))

    def test_large_observation_has_tail_and_result_reference(self):
        messages = [{"role": "user", "content": "Inspect this output."},
                    {"role": "user", "content": "[Tool Observation for `execute_bash`]:\n" + "x" * 16000 + "FINAL ERROR",
                     "_galam_action": {"id": "a" * 32}}]
        selected, _ = assemble_context(messages, "Galam", "x", "x", {"context_window": 8192}, str(self.root))
        self.assertIn("FINAL ERROR", selected[-1]["content"])
        self.assertIn("a" * 32, selected[-1]["content"])

    def test_model_outputs_are_reserved_for_provider_payloads(self):
        from engine.payload_builder import build_request_data
        for endpoint, model in [("Local Endpoint", "generic"), ("OpenRouter", "generic"), ("NVIDIA", "generic"),
                                ("Cloudflare", "@cf/test"), ("Groq", "generic"), ("ModelScope", "generic"),
                                ("Direct", "gemini-test"), ("Direct", "command-test")]:
            with self.subTest(endpoint=endpoint, model=model), patch.dict(CONFIG, {"context_window": 4096, "max_tokens": 256}):
                _, _, payload = build_request_data(endpoint, model, [{"role": "user", "content": "Hi"}])
                value = payload.get("max_tokens", payload.get("generationConfig", {}).get("maxOutputTokens"))
                self.assertLessEqual(value, 256)

    def test_actual_local_context_metadata_is_used(self):
        from engine import local_models
        from storage.endpoints import ENDPOINT_REGISTRY
        previous = copy.deepcopy(ENDPOINT_REGISTRY["Local Endpoint"]["models"])
        response = {"data": [{"id": "fixture-model", "meta": {"n_ctx": 32768, "n_ctx_train": 131072}}]}
        try:
            with patch.object(local_models, "urlopen", return_value=io.StringIO(json.dumps(response))):
                ids = local_models.refresh_local_models({})
            self.assertEqual(ids, ["fixture-model"])
            self.assertEqual(ENDPOINT_REGISTRY["Local Endpoint"]["models"]["fixture-model"]["context_window"], 32768)
        finally:
            ENDPOINT_REGISTRY["Local Endpoint"]["models"] = previous

    def test_code_outline_respects_scope_ignores_and_changes(self):
        (self.root / "a.py").write_text("def useful_function():\n    return 1\n")
        (self.root / "secret.py").write_text("def private_secret(): pass\n")
        (self.root / ".galamignore").write_text("secret.py\n")
        dep = self.root / "node_modules"
        dep.mkdir()
        (dep / "dependency.py").write_text("def hidden_dependency(): pass\n")
        outline = repository_context(self.root, "fix useful_function in a.py")
        self.assertIn("useful_function", outline)
        self.assertNotIn("private_secret", outline)
        self.assertNotIn("hidden_dependency", outline)
        self.assertEqual(repository_context(self.root, "List project files."), "")
        (self.root / "a.py").write_text("def renamed_function():\n    return 222\n")
        self.assertIn("renamed_function", repository_context(self.root, "fix a.py"))


class EditingTests(Workspace):
    def test_invalid_python_never_replaces_original(self):
        path = self.root / "a.py"
        path.write_text("x = 1\n")
        result = task_runner.execute_tool_call("replace_file_content", {"path": "a.py", "target_content": "x = 1", "replacement_content": "x = ("})
        self.assertFalse(result.success)
        self.assertEqual(path.read_text(), "x = 1\n")

    def test_inspection_conflicts_are_rejected(self):
        path = self.root / "a.py"
        path.write_text("x = 1\n")
        expected = digest(path.read_bytes())
        path.write_text("x = 2\n")
        output = filesystem.replace_file_content(str(path), "x = 2", "x = 3", expected_hash=expected)
        self.assertTrue(output.startswith("Error:"))
        self.assertEqual(path.read_text(), "x = 2\n")

    def test_undo_recovers_original_without_git(self):
        path = self.root / "a.py"
        path.write_text("x = 1\n")
        result = task_runner.execute_tool_call("replace_file_content", {"path": "a.py", "target_content": "x = 1", "replacement_content": "x = 2"})
        self.assertTrue(result.success)
        self.assertTrue(result.verified)
        undo_edit(result.metadata["edit_id"], str(self.root))
        self.assertEqual(path.read_text(), "x = 1\n")

    def test_undo_does_not_discard_later_user_edits(self):
        path = self.root / "a.py"
        path.write_text("x = 1\n")
        result = task_runner.execute_tool_call("replace_file_content", {"path": "a.py", "target_content": "x = 1", "replacement_content": "x = 2"})
        path.write_text("x = 3\n")
        with self.assertRaises(ValueError):
            undo_edit(result.metadata["edit_id"], str(self.root))
        self.assertEqual(path.read_text(), "x = 3\n")

    def test_new_file_undo_and_workspace_scope(self):
        result = task_runner.execute_tool_call("write_file", {"path": "new.json", "content": '{"ok": true}'})
        other = self.root / "other"
        other.mkdir()
        with self.assertRaises(ValueError):
            undo_edit(result.metadata["edit_id"], str(other))
        undo_edit(result.metadata["edit_id"], str(self.root))
        self.assertFalse((self.root / "new.json").exists())

    def test_crlf_and_untouched_lines_are_preserved(self):
        path = self.root / "a.py"
        path.write_bytes(b"x = 1\r\n\r\ny = 2\r\n")
        output = filesystem.replace_file_content(str(path), "x = 1", "x = 3")
        self.assertTrue(output.startswith("Successfully"))
        self.assertEqual(path.read_bytes(), b"x = 3\r\n\r\ny = 2\r\n")

    def test_ambiguous_and_empty_targets_do_not_change_files(self):
        path = self.root / "a.txt"
        path.write_text("same\nsame\n")
        for target in ("same", ""):
            self.assertTrue(filesystem.replace_file_content(str(path), target, "other").startswith("Error:"))
        self.assertEqual(path.read_text(), "same\nsame\n")


class ProviderResponseTests(Workspace):
    def query(self, payload=None, chunks=None, stream=True):
        from routers import manager
        from storage.endpoints import ENDPOINT_REGISTRY
        response = SimpleNamespace(ok=True, status_code=200, headers={
            "Content-Type": "text/event-stream" if chunks is not None else "application/json"},
            json=lambda: payload,
            iter_lines=lambda: iter(chunks or []))
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.dict(ENDPOINT_REGISTRY, {"Fixture": {"base_url": "http://fixture.invalid/v1", "models": {"generic": {"context_window": 8192}}}}))
            stack.enter_context(patch.dict(CONFIG, {"active_endpoint": "Fixture", "active_model": "generic", "stream": stream, "max_tokens": 128}))
            stack.enter_context(patch.object(manager, "_post_with_transport_retry", return_value=(response, False)))
            stack.enter_context(patch.object(manager, "StructuredThoughtStreamHandler"))
            stack.enter_context(patch.object(manager, "save_settings"))
            stack.enter_context(patch.object(manager, "log_model_interaction"))
            stack.enter_context(patch.object(task_runner.GLOBAL_TASK_MANAGER, "get_active_tasks", return_value=[]))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            return manager.query_with_fallback(messages=[{"role": "user", "content": "Inspect the current directory."}])[0]

    def test_json_native_call_is_retained_even_when_stream_was_requested(self):
        call = {"id": "call_fixture", "type": "function", "function": {"name": "execute_bash", "arguments": '{"command":"pwd"}'}}
        payload = {"choices": [{"message": {"content": None, "tool_calls": [call]}}]}
        for stream in (True, False):
            result = self.query(payload=payload, stream=stream)
            self.assertEqual(result.tool_calls[0]["function"]["name"], "execute_bash")

    def test_streamed_native_arguments_are_assembled(self):
        def chunk(delta, finish=None):
            return ("data: " + json.dumps({"choices": [{"delta": delta, "finish_reason": finish}]})).encode()
        chunks = [chunk({"tool_calls": [{"index": 0, "id": "c", "function": {"name": "execute_bash", "arguments": '{"command":'}}]}),
                  chunk({"tool_calls": [{"index": 0, "function": {"arguments": '"pwd"}'}}]}, "tool_calls"), b"data: [DONE]"]
        result = self.query(chunks=chunks)
        self.assertEqual(json.loads(result.tool_calls[0]["function"]["arguments"]), {"command": "pwd"})

    def test_reasoning_is_not_promoted_to_an_action(self):
        payload = {"choices": [{"message": {"content": "Answer: ready.", "reasoning_content": "I will run: `pwd`"}}]}
        result = self.query(payload=payload)
        self.assertEqual(result.tool_calls, [])

    def test_provider_formatting_fits_reserved_context(self):
        from engine.payload_builder import build_request_data
        for endpoint, model in [("Local Endpoint", "generic"), ("OpenRouter", "generic"), ("Direct", "gemini-test")]:
            with patch.dict(CONFIG, {"context_window": 8192, "max_tokens": 2048}):
                selected, metrics = assemble_context(
                    [{"role": "user", "content": "Explain a function."}], "Galam", endpoint, model, CONFIG, str(self.root))
                _, _, payload = build_request_data(endpoint, model, selected)
                generation = payload.get("max_tokens", payload.get("generationConfig", {}).get("maxOutputTokens", 0))
                self.assertLess(estimate_tokens(payload) + generation, 8192)


class LoopTests(Workspace):
    def response(self, tool=None, args=None, text=""):
        from routers.manager import AgentResponse
        calls = [] if not tool else [{"id": "fixture", "type": "function", "function": {
            "name": tool, "arguments": json.dumps(args)}}]
        return (AgentResponse(content=text, tool_calls=calls), "fixture-model")

    def run_loop(self, responses, request, graph=False):
        import galam
        messages = [{"role": "user", "content": request}]
        real_execute = task_runner.execute_tool_call
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.dict(CONFIG, {"mode": "ALWAYS-PROCEED"}))
            stack.enter_context(patch.object(galam, "lang_workflow_available", return_value=graph))
            stack.enter_context(patch.object(galam, "direct_command_for_request", return_value=None))
            model = stack.enter_context(patch.object(galam, "query_planning_graph" if graph else "query_with_fallback", side_effect=responses))
            spy = stack.enter_context(patch.object(galam, "execute_tool_call", wraps=real_execute))
            for name in ("render_action_card", "render_output_card", "format_action_step"):
                stack.enter_context(patch.object(galam, name, return_value=""))
            display = io.StringIO()
            stack.enter_context(contextlib.redirect_stdout(display))
            galam.run_agent_loop(messages, "fixture", max_steps=10)
            self.model_calls = model.call_count
            self.last_display = display.getvalue()
        return messages, spy

    def test_html_build_rejects_touch_and_empty_write_then_finishes_content(self):
        html = "<!doctype html>\n<title>Radio Technology</title>\n<h1>Radio technology</h1>\n"
        responses = [self.response("execute_bash", {"command": "mkdir -p radio && touch radio/radio.html"}),
                     self.response("write_file", {"path": "radio/radio.html", "content": ""}),
                     self.response("write_file", {"path": "radio/radio.html", "content": html}),
                     self.response(text="Created the radio technology page with its content.")]
        messages, spy = self.run_loop(responses, "Create a html file about radio technology and create its folder")
        self.assertEqual((self.root / "radio/radio.html").read_text(), html)
        self.assertEqual([call.args[0] for call in spy.call_args_list], ["write_file"])
        self.assertIn("empty content", self.last_display)
        self.assertNotIn("Query error", self.last_display)
        self.assertEqual(self.model_calls, 4)

    def test_build_cannot_finish_after_only_mkdir(self):
        responses = [self.response("execute_bash", {"command": "mkdir -p site"}),
                     self.response(text="Done! The website was created."),
                     self.response("write_file", {"path": "site/index.html", "content": "<h1>Hello</h1>\n"}),
                     self.response(text="Created site/index.html with a heading.")]
        messages, _ = self.run_loop(responses, "Build a website")
        self.assertEqual(self.model_calls, 4)
        self.assertTrue((self.root / "site/index.html").is_file())
        self.assertTrue(any("not complete" in m.get("content", "") for m in messages))

    def test_existing_zero_byte_file_can_be_filled(self):
        (self.root / "page.html").touch()
        call = self.response("write_file", {"path": "page.html", "content": "<h1>Radio</h1>\n"})
        responses = [call, call, self.response(text="Created the page content.")]
        self.run_loop(responses, "Create a HTML page about radio in page.html")
        self.assertEqual((self.root / "page.html").read_text(), "<h1>Radio</h1>\n")
        self.assertNotIn("refused whole-file overwrite", self.last_display)


    def test_created_file_is_acknowledged_without_a_second_model_request(self):
        for name, request in (("hello.txt", "Create an empty file named hello.txt"),
                              ("test.txt", "Kindly create a new file called test.txt for me")):
            with self.subTest(name=name):
                messages, spy = self.run_loop([self.response("write_file", {"path": name, "content": ""})],
                                              request)
                self.assertEqual(self.model_calls, 1)
                self.assertEqual((self.root / name).read_text(), "")
                self.assertIn("Created", messages[-1]["content"])
                self.assertNotIn("Query error", self.last_display)

    def test_literal_content_is_checked_before_immediate_completion(self):
        request = 'Create hello.txt containing "hello and goodbye"'
        messages, _ = self.run_loop([self.response("write_file", {"path": "hello.txt", "content": "hello and goodbye\n"})], request, graph=True)
        self.assertEqual(self.model_calls, 1)
        self.assertIn("Created", messages[-1]["content"])

    def test_exact_edit_is_acknowledged_without_a_summary_request(self):
        (self.root / "a.txt").write_text("before\n")
        responses = [self.response("view_file", {"path": "a.txt"}),
                     self.response("replace_file_content", {"path": "a.txt", "target_content": "before", "replacement_content": "after"})]
        messages, _ = self.run_loop(responses, 'Replace "before" with "after" in a.txt')
        self.assertEqual(self.model_calls, 2)  # Inspection and edit decisions; no summary decision.
        self.assertIn("Updated", messages[-1]["content"])
        self.assertEqual((self.root / "a.txt").read_text(), "after\n")

    def test_successful_explicit_command_needs_no_acknowledgment_inference(self):
        command = self.command("print(123)")
        messages, _ = self.run_loop([self.response("execute_bash", {"command": command})], f"Run `{command}`")
        self.assertEqual(self.model_calls, 1)
        self.assertIn("exit status 0", messages[-1]["content"])

    def test_simple_folder_listing_finishes_from_verified_output_without_ack_request(self):
        (self.root / "alpha folder").mkdir()
        (self.root / "beta").mkdir()
        (self.root / "readme.txt").write_text("ordinary file")
        command = f"ls -la {shlex.quote(str(self.root))}"
        request = f"Go to my {self.root} then list my folders here"
        messages, _ = self.run_loop([self.response("execute_bash", {"command": command})], request)
        self.assertEqual(self.model_calls, 1)
        self.assertIn("Folders in", messages[-1]["content"])
        self.assertIn("alpha folder", messages[-1]["content"])
        self.assertIn("beta", messages[-1]["content"])
        self.assertNotIn("readme.txt", messages[-1]["content"])
        self.assertEqual(AGENT_STATE['task_outcome'], 'completed')

    def test_listing_receipt_does_not_finish_a_compound_mutation_request(self):
        from engine.completion import completed_simple_request
        result = ActionResult("drwxr-xr-x 2 user group 4096 Jan 1 12:00 site")
        event = {"tool": "execute_bash", "args": {"command": "ls -la ."}, "result": result}
        self.assertIsNone(completed_simple_request("List folders then delete the old one", [event], [], str(self.root)))

    def test_listing_receipt_requires_the_command_to_list_the_requested_path(self):
        from engine.completion import completed_simple_request
        other = self.root / 'other'; other.mkdir()
        command = f'ls -la {shlex.quote(str(other))}'
        result = ActionResult("drwxr-xr-x 2 user group 4096 Jan 1 12:00 hidden-from-request")
        event = {"tool": "execute_bash", "args": {"command": command}, "result": result}
        self.assertIsNone(completed_simple_request(f"List folders in {self.root}", [event], [], str(self.root)))

    def test_file_receipt_waits_for_configured_checks(self):
        command = self.command("print('checked')")
        (self.root / ".galam-checks.json").write_text(json.dumps({"checks": {".txt": [command]}}))
        messages, spy = self.run_loop([self.response("write_file", {"path": "hello.txt", "content": ""})],
                                      "Create an empty file named hello.txt")
        self.assertEqual(self.model_calls, 1)
        self.assertEqual([call.args[0] for call in spy.call_args_list], ["write_file", "execute_bash"])
        self.assertIn("1 configured check(s) passed", messages[-1]["content"])

    def test_fast_acknowledgment_waits_for_both_requested_files(self):
        responses = [self.response("write_file", {"path": "a.txt", "content": ""}),
                     self.response("write_file", {"path": "b.txt", "content": ""}), self.response(text="Created both files.")]
        messages, spy = self.run_loop(responses, "Create a.txt then create b.txt")
        self.assertEqual(self.model_calls, 2)
        self.assertTrue((self.root / "a.txt").is_file())
        self.assertTrue((self.root / "b.txt").is_file())

    def test_failed_command_retains_the_correction_path(self):
        command = self.command("raise SystemExit(7)")
        responses = [self.response("execute_bash", {"command": command}),
                     self.response("execute_bash", {"command": self.command("print('recovered')")}),
                     self.response(text="The original command failed with exit 7; the corrective command completed.")]
        messages, _ = self.run_loop(responses, f"Run `{command}`")
        self.assertGreaterEqual(self.model_calls, 2)
        self.assertIn("Command failed (exit status 7)", self.last_display)

    def test_wrong_file_contents_do_not_get_a_fast_final_receipt(self):
        from engine.completion import completed_simple_request
        result = task_runner.execute_tool_call("write_file", {"path": "hello.txt", "content": "wrong"})
        events = [{"tool": "write_file", "args": {"path": "hello.txt", "content": "wrong"}, "result": result}]
        self.assertIsNone(completed_simple_request('Create hello.txt containing "right"', events, [], self.root))

    def test_running_tests_is_still_one_explicit_command(self):
        from engine.completion import completed_simple_request
        for command in ("npm test", "pip install example", "npm run deploy"):
            events = [{"tool": "execute_bash", "args": {"command": command}, "result": ActionResult("completed")}]
            self.assertIn("exit status 0", completed_simple_request("Run " + command, events, [], self.root))
        self.assertIsNone(completed_simple_request("Run npm test then fix errors", events, [], self.root))

    def test_receipt_does_not_ignore_changes_after_the_edit(self):
        from engine.completion import completed_simple_request
        path = self.root / "a.txt"
        path.write_text("before\n")
        args = {"path": "a.txt", "target_content": "before", "replacement_content": "after"}
        result = task_runner.execute_tool_call("replace_file_content", args)
        path.write_text("external change\n")
        events = [{"tool": "replace_file_content", "args": args, "result": result}]
        self.assertIsNone(completed_simple_request('Replace "before" with "after" in a.txt', events, [], self.root))

    def test_failing_check_can_be_rerun_after_correction(self):
        (self.root / "a.py").write_text("x = 1\n")
        (self.root / "check.py").write_text("from a import x\nraise SystemExit(0 if x == 2 else 3)\n")
        command = f"{shlex.quote(sys.executable)} -B check.py"
        responses = [self.response("execute_bash", {"command": command}),
                     self.response("view_file", {"path": "a.py"}),
                     self.response("replace_file_content", {"path": "a.py", "target_content": "x = 1", "replacement_content": "x = 2"}),
                     self.response("execute_bash", {"command": command}),
                     self.response(text="Changed x and the check passed.")]
        messages, spy = self.run_loop(responses, "Fix the code in a.py and verify it.")
        checks = [m["_galam_action"] for m in messages if m.get("_galam_action") and "[Tool Observation for `execute_bash`]" in m["content"]]
        self.assertEqual([m["exit_code"] for m in checks], [3, 0])
        self.assertEqual((self.root / "a.py").read_text(), "x = 2\n")

    def test_configured_check_is_scheduled_through_existing_runner(self):
        (self.root / "a.py").write_text("x = 1\n")
        command = f"{shlex.quote(sys.executable)} -c 'print(123)'"
        (self.root / ".galam-checks.json").write_text(json.dumps({"checks": {".py": [command]}}))
        responses = [self.response("view_file", {"path": "a.py"}),
                     self.response("replace_file_content", {"path": "a.py", "target_content": "x = 1", "replacement_content": "x = 2"}),
                     self.response(text="Changed x; the configured check passed.")]
        messages, spy = self.run_loop(responses, "Fix the code in a.py.", graph=True)
        commands = [call.args[1].get("command") for call in spy.call_args_list if call.args[0] == "execute_bash"]
        self.assertEqual(commands, [command])

    def test_examples_do_not_reach_action_execution(self):
        text = "For reference, do not run this:\n```bash\npwd\n```"
        messages, spy = self.run_loop([self.response(text=text)], "Explain the pwd command.")
        spy.assert_not_called()

    def test_read_only_request_finishes_without_demanding_an_edit(self):
        (self.root / "a.py").write_text("VALUE = 79463\n")
        responses = [self.response("view_file", {"path": "a.py"}), self.response(text="VALUE is 79463.")]
        messages, spy = self.run_loop(responses, "Read a.py. Do not modify any files.")
        self.assertEqual([call.args[0] for call in spy.call_args_list], ["view_file"])
        self.assertNotIn("The requested code change is not complete", json.dumps(messages))

    def test_announced_read_is_recovered_and_answer_is_grounded_in_output(self):
        path = self.root / "a.py"
        path.write_text("VALUE = 79463\n")
        responses = [self.response(text=f"I am inspecting {path}..."),
                     self.response(text=f"I am inspecting {path}...")]
        messages, spy = self.run_loop(responses, f"Use view_file to inspect {path}. Do not modify any files.")
        self.assertEqual([call.args[0] for call in spy.call_args_list], ["view_file"])
        self.assertIn("VALUE = 79463", messages[-1]["content"])
        self.assertEqual(path.read_text(), "VALUE = 79463\n")

    def test_read_recovery_does_not_guess_or_mutate(self):
        from engine.action_recovery import recover_requested_read
        path = self.root / "a.py"
        path.write_text("x = 1\n")
        self.assertEqual(recover_requested_read(f"Edit {path}", "I am inspecting the file", self.root), [])
        self.assertEqual(recover_requested_read("Inspect missing.py", "I will inspect the file", self.root), [])
        self.assertEqual(recover_requested_read(f"Explain how to read {path}", "I will read it", self.root), [])

    def test_repeated_read_reports_existing_evidence_without_executing_twice(self):
        (self.root / "a.py").write_text("VALUE = 79463\n")
        responses = [self.response("view_file", {"path": "a.py"}), self.response("view_file", {"path": "a.py"})]
        messages, spy = self.run_loop(responses, "Read a.py. Do not modify files.")
        self.assertEqual(spy.call_count, 1)
        self.assertIn("79463", messages[-1]["content"])


class ChangeDisplayTests(Workspace):
    def test_created_file_has_green_numbered_lines(self):
        result = task_runner.execute_tool_call("write_file", {"path": "new.txt", "content": "first\nsecond\n"})
        self.assertTrue(result.success)
        self.assertIn("\033[38;5;120m+         1 | first", result.output)
        self.assertIn("+         2 | second", result.output)

    def test_replacement_has_old_new_numbers_and_context(self):
        path = self.root / "a.txt"
        path.write_text("first\nold\nlast\n")
        result = filesystem.replace_file_content(str(path), "old", "new\nextra")
        self.assertIn("\033[38;5;203m-    2      | old", result)
        self.assertIn("\033[38;5;120m+         2 | new", result)
        self.assertIn("+         3 | extra", result)
        self.assertIn("     3    4 | last", result)

    def test_whole_file_write_also_shows_deletions(self):
        path = self.root / "a.txt"
        path.write_text("old\n")
        result = filesystem.write_file(str(path), "new\n")
        self.assertIn("-    1      | old", result)
        self.assertIn("+         1 | new", result)

    def test_narrow_terminal_keeps_valid_color_sequences_and_indentation(self):
        from ui.dock_layout import render_output_card
        output = filesystem.code_diff("", "    " + "long_line_" * 20 + "\n")
        display = io.StringIO()
        with patch("ui.dock_layout.shutil.get_terminal_size", return_value=os.terminal_size((38, 24))), contextlib.redirect_stdout(display):
            render_output_card(output, max_lines=None)
        shown = display.getvalue()
        plain = __import__("re").sub(r"\x1b\[[0-9;]*m", "", shown)
        self.assertNotIn("\x1b", plain)
        self.assertIn("1 |     long_line", plain)
        self.assertGreater(shown.count("\033[38;5;120m"), 3)

    def test_positional_function_calls_keep_command_and_reason_separate(self):
        calls = extract_tools_from_text('execute_bash("mkdir -p /tmp/radio", "Create the folder.")')
        self.assertEqual(len(calls), 1)
        self.assertEqual(json.loads(calls[0]["function"]["arguments"])["command"], "mkdir -p /tmp/radio")

    def test_function_parser_preserves_parentheses_quotes_and_multiple_calls(self):
        command = 'printf "hello (world)\\n"'
        text = "execute_bash(" + repr(command) + ', "reason")\nwrite_file(path="a.py", content="print(1)\\n")'
        calls = extract_tools_from_text(text)
        self.assertEqual(len(calls), 2)
        self.assertEqual(json.loads(calls[1]["function"]["arguments"])["content"], "print(1)\n")

    def test_function_parser_rejects_executable_argument_expressions(self):
        self.assertEqual(extract_tools_from_text('execute_bash(command=__import__("os").getcwd())'), [])


if __name__ == "__main__":
    unittest.main()
