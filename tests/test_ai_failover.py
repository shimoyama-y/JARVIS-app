import unittest
from unittest.mock import patch

import ai


class AIFailoverTests(unittest.TestCase):
    def make_assistant(self):
        assistant = object.__new__(ai.Assistant)
        assistant.gemini = ("node", "gemini.js")
        assistant.exe = "claude.exe"
        assistant._current_channel = "chat"
        assistant._gemini_sessions = {}
        assistant._ollama_sessions = {}
        assistant._fallback_context = ""
        return assistant

    def test_tries_gemini_first(self):
        assistant = self.make_assistant()
        expected = {"result": "ok", "provider": "gemini"}
        with patch.object(assistant, "_run_gemini_stream", return_value=expected) as gemini, \
                patch.object(assistant, "_run_claude_stream") as claude:
            self.assertEqual(assistant._run(["hello"], 10), expected)
        gemini.assert_called_once()
        claude.assert_not_called()

    def test_falls_back_to_local_before_claude_and_retries_gemini_next_turn(self):
        assistant = self.make_assistant()
        local_result = {"result": "local reply", "provider": "ollama"}
        with patch.object(assistant, "_run_gemini_stream", side_effect=[RuntimeError("quota"),
                                                                        {"result": "recovered", "provider": "gemini"}]), \
                patch.object(assistant, "_run_claude_stream", side_effect=RuntimeError("unavailable")), \
                patch.object(assistant, "_local_model_ready", return_value=True), \
                patch.object(assistant, "_run_ollama", return_value=local_result) as ollama:
            self.assertEqual(assistant._run(["hello"], 10), local_result)
            self.assertEqual(assistant._run(["hello again"], 10)["provider"], "gemini")
        self.assertEqual(ollama.call_count, 1)

    def test_uses_claude_when_local_provider_is_unavailable(self):
        assistant = self.make_assistant()
        expected = {"result": "cloud reply", "provider": "claude"}
        with patch.object(assistant, "_run_gemini_stream", side_effect=RuntimeError("quota")), \
                patch.object(assistant, "_local_model_ready", return_value=True), \
                patch.object(assistant, "_run_ollama", side_effect=RuntimeError("offline")), \
                patch.object(assistant, "_run_claude_stream", return_value=expected) as claude:
            self.assertEqual(assistant._run(["hello"], 10), expected)
        claude.assert_called_once()

    def test_streaming_fallback_uses_local_before_claude(self):
        assistant = self.make_assistant()
        expected = {"result": "local reply", "provider": "ollama"}
        with patch.object(assistant, "_run_gemini_stream", side_effect=RuntimeError("quota")), \
                patch.object(assistant, "_local_model_ready", return_value=True), \
                patch.object(assistant, "_run_ollama", return_value=expected) as ollama, \
                patch.object(assistant, "_run_claude_stream") as claude:
            self.assertEqual(assistant._run_stream(["hello"], 10), expected)
        ollama.assert_called_once()
        claude.assert_not_called()

    def test_does_not_repeat_request_after_provider_started_side_effects(self):
        assistant = self.make_assistant()
        failure = RuntimeError("partial operation")
        failure.side_effect_started = True
        with patch.object(assistant, "_run_gemini_stream", side_effect=failure) as gemini, \
                patch.object(assistant, "_run_claude_stream") as claude, \
                patch.object(assistant, "_run_ollama") as ollama:
            with self.assertRaisesRegex(RuntimeError, "partial operation"):
                assistant._run(["do something"], 10)
        gemini.assert_called_once()
        claude.assert_not_called()
        ollama.assert_not_called()

    def test_web_requests_are_not_sent_to_local_provider(self):
        assistant = self.make_assistant()
        with patch.object(assistant, "_run_gemini_stream", side_effect=RuntimeError("quota")), \
                patch.object(assistant, "_run_claude_stream", side_effect=RuntimeError("unavailable")), \
                patch.object(assistant, "_local_model_ready") as local_ready, \
                patch.object(assistant, "_run_ollama") as ollama:
            with self.assertRaisesRegex(RuntimeError, "unavailable"):
                assistant._run(["--tools", "WebSearch"], 10)
        local_ready.assert_not_called()
        ollama.assert_not_called()


if __name__ == "__main__":
    unittest.main()
