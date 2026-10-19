import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError

import memory_store
import memory_tiering


class MemoryTieringTests(unittest.TestCase):
    def test_embed_texts_uses_gemini_batch_api_and_checks_dimensions(self):
        vectors = [
            {"values": [0.25] * memory_tiering.EMBEDDING_DIMENSION},
            {"values": [0.5] * memory_tiering.EMBEDDING_DIMENSION},
        ]
        with patch.object(memory_tiering, "_gemini_request", return_value={"embeddings": vectors}) as request:
            result = memory_tiering._embed_texts(["Linux", "Windows"])

        self.assertEqual(len(result), 2)
        path, payload = request.call_args.args
        self.assertEqual(path, "models/gemini-embedding-001:batchEmbedContents")
        self.assertEqual(payload["requests"][0]["outputDimensionality"], 1536)

    def test_gemini_api_key_is_sent_in_header_not_url(self):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return None

            def read(self):
                return b"{}"

        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}), patch(
            "memory_tiering._DIRECT_OPENER.open", return_value=Response()
        ) as urlopen:
            memory_tiering._gemini_request("models/model:generateContent", {})

        request = urlopen.call_args.args[0]
        self.assertEqual(request.headers["X-goog-api-key"], "test-key")
        self.assertNotIn("test-key", request.full_url)

    def test_check_connections_verifies_services_without_writing_memories(self):
        with patch.object(
            memory_tiering, "_get_client"
        ) as get_client, patch.object(
            memory_tiering, "_embed_texts", return_value=[[0.0] * 1536]
        ) as embed, patch.object(memory_tiering, "summarize_episode") as summarize:
            get_client.return_value.request.return_value = []
            memory_tiering.check_connections()

        get_client.return_value.request.assert_called_once_with(
            "GET", "/rest/v1/memory_records?select=owner_id&limit=1"
        )
        embed.assert_called_once_with(["JARVIS memory connection check"])
        summarize.assert_called_once()

    def test_summary_validates_json_and_preserves_existing_tags(self):
        response = {
            "candidates": [{
                "content": {"parts": [{
                    "text": json.dumps({
                        "summary": "権限設定を確認した",
                        "problem": "設定に誤り",
                        "approach": "権限を調整",
                        "outcome": "動作確認済み",
                        "lesson": "最小権限を維持",
                        "tags": ["linux"],
                        "importance": 7,
                    }, ensure_ascii=False)
                }]}
            }]
        }
        with patch.object(memory_tiering, "_gemini_request", return_value=response):
            result = memory_tiering.summarize_episode(
                "Linux", "権限の問題", "修正して確認", ["security"], 5
            )

        self.assertEqual(result["tags"], ["security", "linux"])
        self.assertEqual(result["importance"], 7)
        self.assertEqual(result["lesson"], "最小権限を維持")

    def test_summary_redacts_common_credentials_before_sending_to_gemini(self):
        with patch.object(
            memory_tiering,
            "_gemini_request",
            return_value={"candidates": [{"content": {"parts": [{"text": '{"summary":"確認済み"}'}]}}]},
        ) as request:
            memory_tiering.summarize_episode(
                "API", "api_key=AIza" + "A" * 35, "Bearer " + "x" * 40, [], 5
            )

        prompt = request.call_args.args[1]["contents"][0]["parts"][0]["text"]
        self.assertNotIn("AIza", prompt)
        self.assertNotIn("Bearer", prompt)
        self.assertIn("[REDACTED]", prompt)

    def test_save_reuses_matching_embedding(self):
        client = Mock()
        client.request.return_value = [{
            "record_key": "0",
            "content": "SQL SQL injection",
            "embedding": "[0.1,0.2]",
        }]
        rows = memory_tiering._records("learning", [{"topic": "SQL", "summary": "SQL injection"}])
        with patch.object(memory_tiering, "_get_client", return_value=client), patch.object(
            memory_tiering, "_embed_texts"
        ) as embed:
            memory_tiering._add_embeddings("learning", rows)

        embed.assert_not_called()
        self.assertEqual(rows[0]["embedding"], "[0.1,0.2]")

    def test_local_backend_is_default_and_invalid_backend_is_rejected(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(memory_tiering.enabled())
        with patch.dict(os.environ, {"JARVIS_MEMORY_BACKEND": "remote"}):
            with self.assertRaises(ValueError):
                memory_tiering.enabled()

    def test_records_keep_payload_and_assign_tiers(self):
        warm = memory_tiering._records("learning", [{
            "topic": "認証",
            "summary": "ログイン処理を修正",
            "tags": ["security"],
            "importance": 8,
        }])
        hot = memory_tiering._records("profile", {"interests": ["AI開発"]})

        self.assertEqual(warm[0]["tier"], "warm")
        self.assertEqual(warm[0]["content"], "認証 ログイン処理を修正")
        self.assertEqual(warm[0]["data"]["importance"], 8)
        self.assertEqual(hot[0]["tier"], "hot")
        self.assertEqual(hot[0]["record_key"], "profile")

    def test_records_reject_importance_outside_database_constraint(self):
        with self.assertRaises(ValueError):
            memory_tiering._records("learning", [{"topic": "bad", "importance": 11}])

    def test_save_kind_calls_atomic_snapshot_rpc(self):
        client = Mock()
        with patch.object(memory_tiering, "_get_client", return_value=client), patch.object(
            memory_tiering, "_add_embeddings"
        ):
            memory_tiering.save_kind("thm", [{"room": "Blue", "status": "完了"}])

        _, path, payload = client.request.call_args.args
        self.assertEqual(path, "/rest/v1/rpc/replace_memory_kind")
        self.assertEqual(payload["p_kind"], "thm")
        self.assertEqual(payload["p_records"][0]["tier"], "warm")
        self.assertEqual(payload["p_records"][0]["data"]["room"], "Blue")

    def test_search_caps_results_and_skips_empty_queries(self):
        client = Mock()
        client.request.return_value = []
        vector = [0.0] * memory_tiering.EMBEDDING_DIMENSION
        with patch.object(memory_tiering, "_get_client", return_value=client), patch.object(
            memory_tiering, "_embed_texts", return_value=[vector]
        ):
            self.assertEqual(memory_tiering.search_records("  "), [])
            self.assertEqual(memory_tiering.search_records("SQL", limit=100), [])

        method, path, payload = client.request.call_args.args
        self.assertEqual((method, path), ("POST", "/rest/v1/rpc/search_memory_records"))
        self.assertEqual(payload["p_query"], "SQL")
        self.assertEqual(payload["p_limit"], 20)
        self.assertTrue(payload["p_query_embedding"].startswith("[0,0,"))

    def test_rest_client_signs_in_before_accessing_the_database(self):
        class Response:
            def __init__(self, payload):
                self.payload = payload

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return None

            def read(self):
                return json.dumps(self.payload).encode("utf-8")

        responses = [
            Response({"access_token": "short-lived", "refresh_token": "refresh", "expires_in": 3600}),
            Response([]),
        ]
        client = memory_tiering._SupabaseRest("https://example.supabase.co", "public-key", "user", "password")
        with patch("memory_tiering._DIRECT_OPENER.open", side_effect=responses) as urlopen:
            self.assertEqual(client.request("GET", "/rest/v1/memory_records"), [])

        self.assertEqual(urlopen.call_count, 2)
        request = urlopen.call_args_list[1].args[0]
        self.assertEqual(request.headers["Authorization"], "Bearer short-lived")
        self.assertEqual(request.get_header("Authorization"), "Bearer short-lived")
        self.assertEqual(request.get_header("Apikey"), "public-key")

    def test_rest_client_explains_common_supabase_sign_in_failures(self):
        cases = (
            (
                {"code": "invalid_credentials", "message": "Invalid login credentials"},
                "Auth email or password is incorrect",
            ),
            (
                {"code": "email_not_confirmed", "message": "Email not confirmed"},
                "Auth user's email is not confirmed",
            ),
        )
        for payload, expected in cases:
            with self.subTest(code=payload["code"]):
                error = HTTPError(
                    "https://example.supabase.co/auth/v1/token",
                    400,
                    "Bad Request",
                    {},
                    io.BytesIO(json.dumps(payload).encode("utf-8")),
                )
                client = memory_tiering._SupabaseRest(
                    "https://example.supabase.co", "public-key", "user", "test-password"
                )
                with patch("memory_tiering._DIRECT_OPENER.open", side_effect=error):
                    with self.assertRaisesRegex(RuntimeError, expected):
                        client._send(
                            "POST",
                            "/auth/v1/token?grant_type=password",
                            {"email": "user", "password": "test-password"},
                            authenticated=False,
                        )

    def test_legacy_import_collects_all_memory_kinds(self):
        client = Mock()
        client.request.side_effect = [[], 3]
        payloads = {
            "learning": [{"topic": "Linux"}],
            "security": [],
            "thm": [{"room": "Blue"}],
            "profile": {"goals": ["Linux"]},
        }
        with patch.object(memory_tiering, "_get_client", return_value=client), patch.object(
            memory_tiering, "_add_embeddings"
        ):
            imported = memory_tiering.migrate_local_data(payloads)

        self.assertEqual(imported, 3)
        request = client.request.call_args.args
        records = request[2]["p_records"]
        self.assertEqual([record["kind"] for record in records], ["learning", "thm", "profile"])
        self.assertEqual([record["tier"] for record in records], ["warm", "warm", "hot"])

    def test_memory_store_keeps_local_json_backend_by_default(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            with patch.object(memory_store, "MEMORY_DIR", Path(directory)):
                expected = [{"topic": "Linux", "summary": "権限を確認"}]
                memory_store._save("learning", expected)
                self.assertEqual(memory_store._load("learning"), expected)

    def test_memory_store_routes_to_supabase_when_selected(self):
        expected = [{"topic": "Linux"}]
        with patch.dict(os.environ, {"JARVIS_MEMORY_BACKEND": "supabase"}, clear=True):
            with patch.object(memory_tiering, "load_kind", return_value=expected) as load_kind:
                with patch.object(memory_tiering, "save_kind") as save_kind:
                    self.assertEqual(memory_store._load("learning"), expected)
                    memory_store._save("learning", expected)
        load_kind.assert_called_once_with("learning")
        save_kind.assert_called_once_with("learning", expected)

    def test_auto_learn_uses_gemini_summary_only_for_supabase(self):
        episode = {
            "summary": "最小権限を確認",
            "problem": "sudo設定の誤り",
            "approach": "設定を確認",
            "outcome": "権限を修正",
            "lesson": "必要な権限だけを付与",
            "tags": ["linux", "security"],
            "importance": 6,
        }
        with tempfile.TemporaryDirectory() as directory, patch.object(
            memory_store, "COLD_DIR", Path(directory)
        ), patch.object(memory_tiering, "enabled", return_value=True), patch.object(
            memory_tiering, "summarize_episode", return_value=episode
        ) as summarize, patch.object(memory_tiering, "save_cold_memory") as save_cold, patch.object(
            memory_store, "_load", return_value=[]
        ), patch.object(memory_store, "_save") as save:
            recorded = memory_store.auto_learn("Linuxを学んだ", "sudoの設定を確認しました。")

        self.assertIn("Linux", recorded)
        summarize.assert_called_once()
        save_cold.assert_called_once()  # 原文は Cold 層 (クラウド) へ
        saved = save.call_args.args[1][0]
        self.assertEqual(saved["summary"], episode["summary"])
        self.assertEqual(saved["lesson"], episode["lesson"])

    def test_auto_learn_archives_raw_conversation_locally_and_links_it(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(
            memory_store, "COLD_DIR", Path(directory)
        ), patch.object(memory_tiering, "enabled", return_value=False), patch.object(
            memory_store, "_load", return_value=[]
        ), patch.object(memory_store, "_save") as save:
            memory_store.auto_learn("Linuxを学んだ", "sudo設定を確認しました。")
            saved = save.call_args.args[1][0]
            spoken, display = memory_store.read_cold_archive(saved["source_ref"])

        self.assertTrue(saved["source_ref"].startswith("cold:"))
        self.assertIn("原文を読み出しました", spoken)
        self.assertIn("Linuxを学んだ", display)
        self.assertIn("sudo設定を確認", display)

    def test_auto_learn_failure_is_not_silently_saved(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(
            memory_store, "COLD_DIR", Path(directory)
        ), patch.object(memory_tiering, "enabled", return_value=True), patch.object(
            memory_store, "_load", return_value=[]
        ), patch.object(
            memory_tiering, "summarize_episode", side_effect=RuntimeError("Gemini unavailable")
        ):
            with self.assertRaisesRegex(RuntimeError, "Gemini unavailable"):
                memory_store.auto_learn("Linuxを学んだ", "sudo設定を確認しました。")

    def test_save_cold_memory_embeds_and_upserts_cold_tier(self):
        client = Mock()
        with patch.object(memory_tiering, "_get_client", return_value=client), patch.object(
            memory_tiering, "_embed_texts", return_value=[[0.1] * memory_tiering.EMBEDDING_DIMENSION]
        ) as embed:
            memory_tiering.save_cold_memory(
                "abc", "learning", {"topic": "Linux", "user": "u", "assistant": "a"},
                importance=6, tags=["linux"],
            )

        embed.assert_called_once()
        method, path, payload = client.request.call_args.args
        self.assertEqual((method, path), ("POST", "/rest/v1/rpc/add_cold_memory"))
        record = payload["p_record"]
        self.assertEqual(record["tier"], "cold")
        self.assertEqual(record["record_key"], "abc")
        self.assertEqual(record["importance"], 6)
        self.assertTrue(record["embedding"].startswith("["))

    def test_save_cold_memory_redacts_secrets_before_upload(self):
        client = Mock()
        with patch.object(memory_tiering, "_get_client", return_value=client), patch.object(
            memory_tiering, "_embed_texts", return_value=[[0.0] * memory_tiering.EMBEDDING_DIMENSION]
        ):
            memory_tiering.save_cold_memory(
                "id1", "learning",
                {"topic": "t", "user": "password=hunter2", "assistant": "ok"},
            )

        record = client.request.call_args.args[2]["p_record"]
        self.assertNotIn("hunter2", json.dumps(record, ensure_ascii=False))
        self.assertIn("[REDACTED]", record["data"]["user"])

    def test_search_records_passes_requested_tier(self):
        client = Mock()
        client.request.return_value = []
        with patch.object(memory_tiering, "_get_client", return_value=client), patch.object(
            memory_tiering, "_embed_texts", return_value=[[0.0] * memory_tiering.EMBEDDING_DIMENSION]
        ):
            memory_tiering.search_records("linux", 3, tier="cold")

        payload = client.request.call_args.args[2]
        self.assertEqual(payload["p_tier"], "cold")

    def test_decay_memories_calls_prune_rpc_with_thresholds(self):
        client = Mock()
        client.request.return_value = 5
        with patch.object(memory_tiering, "_get_client", return_value=client):
            self.assertEqual(memory_tiering.decay_memories(30, 2), 5)

        method, path, payload = client.request.call_args.args
        self.assertEqual((method, path), ("POST", "/rest/v1/rpc/prune_memories"))
        self.assertEqual(payload["p_max_idle_days"], 30)
        self.assertEqual(payload["p_min_importance"], 2)

    def test_load_cold_memory_returns_data_or_raises_when_gone(self):
        client = Mock()
        client.request.return_value = [{"data": {"topic": "Linux"}}]
        with patch.object(memory_tiering, "_get_client", return_value=client):
            self.assertEqual(memory_tiering.load_cold_memory("id")["topic"], "Linux")

        client.request.return_value = []
        with patch.object(memory_tiering, "_get_client", return_value=client):
            with self.assertRaises(RuntimeError):
                memory_tiering.load_cold_memory("id")

    def test_read_cold_archive_reads_from_cloud_when_enabled(self):
        archive_ref = "cold:00000000-0000-0000-0000-000000000000"
        payload = {"created_at": "2026-01-01T00:00:00", "topic": "Linux",
                   "user": "u", "assistant": "権限を確認した"}
        with patch.object(memory_tiering, "enabled", return_value=True), patch.object(
            memory_tiering, "load_cold_memory", return_value=payload
        ) as load_cold:
            spoken, display = memory_store.read_cold_archive(archive_ref)

        load_cold.assert_called_once_with("00000000-0000-0000-0000-000000000000")
        self.assertIn("原文を読み出しました", spoken)
        self.assertIn("権限を確認した", display)

    def test_context_for_prompt_falls_back_to_cold_when_warm_is_thin(self):
        cold_hit = [{
            "kind": "learning", "tier": "cold",
            "data": {"topic": "Linux", "assistant": "権限を確認した"}, "rank": 1.0,
        }]
        tiers = []

        def fake_search(_query, limit=6, tier="warm"):
            tiers.append(tier)
            return cold_hit if tier == "cold" else []

        with patch.object(memory_tiering, "enabled", return_value=True), patch.object(
            memory_store, "summary_for_prompt", return_value="PROFILE"
        ), patch.object(memory_tiering, "search_records", side_effect=fake_search):
            out = memory_store.context_for_prompt("Linuxの権限")

        self.assertEqual(tiers, ["warm", "cold"])
        self.assertIn("Linux", out)
        self.assertIn("原文", out)  # Cold ヒットは「学習・原文」と表示される


if __name__ == "__main__":
    unittest.main()
