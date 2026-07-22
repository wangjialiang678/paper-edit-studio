import json
import io
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

from cutpoint_lab.studio.config import EnvStore, mask_api_key, resolve_llm_api_key


class _UnusedAsrRunner:
    def transcribe(self, *_args, **_kwargs):
        raise AssertionError("本测试不应启动 ASR")


class _UnavailableSelector:
    def available(self) -> bool:
        return False


class _JsonResponse:
    def __init__(self, payload: dict):
        self.body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, *_args):
        return self.body


class _VocabularyTransport:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def __call__(self, request, *, timeout):
        self.calls.append((request, timeout))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return _JsonResponse(outcome)

    def payload(self, index: int = -1) -> dict:
        return json.loads(self.calls[index][0].data.decode("utf-8"))


def _request_json(url: str, *, method: str = "GET", payload: dict | None = None):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"} if data is not None else {},
        method=method,
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        return response.status, json.loads(response.read().decode("utf-8"))


def _request_error(url: str, *, method: str, payload: dict):
    try:
        _request_json(url, method=method, payload=payload)
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))
    raise AssertionError("请求应当失败")


class EnvStoreTests(unittest.TestCase):
    def test_read_matches_existing_dotenv_syntax(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text(
                "# 保留注释\n"
                "export FIRST = \"one\"\n"
                "INVALID\n"
                "SECOND='two'\n"
                "FIRST=last\n",
                encoding="utf-8",
            )

            self.assertEqual(EnvStore(env_path).read(), {"FIRST": "last", "SECOND": "two"})

    def test_write_key_preserves_structure_creates_backup_and_does_not_append_duplicate(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            original = "# heading\nKEEP=untouched\nexport DASHSCOPE_API_KEY='old-key'\n# tail\n"
            env_path.write_text(original, encoding="utf-8")

            EnvStore(env_path).write_key("DASHSCOPE_API_KEY", "sk-fake-new-key")

            written = env_path.read_text(encoding="utf-8")
            self.assertEqual(
                written,
                "# heading\nKEEP=untouched\nexport DASHSCOPE_API_KEY=sk-fake-new-key\n# tail\n",
            )
            self.assertEqual(written.count("DASHSCOPE_API_KEY="), 1)
            backups = list(Path(tmp).glob(".env.bak-*"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_text(encoding="utf-8"), original)

    def test_write_key_creates_missing_env_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            EnvStore(env_path).write_key("DASHSCOPE_API_KEY", "sk-fake")
            self.assertEqual(env_path.read_text(encoding="utf-8"), "DASHSCOPE_API_KEY=sk-fake\n")
            self.assertEqual(list(Path(tmp).glob(".env.bak-*")), [])

    def test_effective_prefers_process_environment(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"SAMPLE_KEY": "from-process"}, clear=True
        ):
            env_path = Path(tmp) / ".env"
            env_path.write_text("SAMPLE_KEY=from-dotenv\n", encoding="utf-8")
            self.assertEqual(EnvStore(env_path).effective("SAMPLE_KEY"), ("from-process", "process_env"))

    def test_llm_key_resolution_is_key_first_then_source(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"DASHSCOPE_API_KEY": "process-general"}, clear=True
        ):
            root = Path(tmp)
            env_path = root / ".env"
            vault_path = root / "api-vault.env"
            env_path.write_text("STUDIO_LLM_API_KEY=dotenv-studio\n", encoding="utf-8")
            vault_path.write_text("STUDIO_LLM_API_KEY=vault-studio\n", encoding="utf-8")
            self.assertEqual(
                resolve_llm_api_key(EnvStore(env_path), api_vault_path=vault_path),
                ("dotenv-studio", "STUDIO_LLM_API_KEY", "dotenv"),
            )


class MaskApiKeyTests(unittest.TestCase):
    def test_long_key_keeps_only_last_four_characters(self):
        self.assertEqual(mask_api_key("sk-fake-1234"), "•••1234")

    def test_short_key_is_fully_masked(self):
        self.assertEqual(mask_api_key("short"), "•••••")
        self.assertEqual(mask_api_key(None), "")


class LlmDynamicConfigTests(unittest.TestCase):
    def test_available_reloads_dotenv_without_recreating_client(self):
        from cutpoint_lab.studio.llm_client import LlmClient

        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=True):
            root = Path(tmp)
            store = EnvStore(root / ".env")
            client = LlmClient(env_store=store, api_vault_path=root / "vault.env")
            self.assertFalse(client.available())

            store.write_key("DASHSCOPE_API_KEY", "sk-fake-hot-reload")
            self.assertTrue(client.available())

    def test_chat_json_uses_latest_key_and_does_not_inject_vault_into_process_env(self):
        from cutpoint_lab.studio.llm_client import LlmClient

        class _Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return b'{"choices":[{"message":{"content":"{\\"ok\\": true}"}}],"usage":{}}'

        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=True):
            root = Path(tmp)
            store = EnvStore(root / ".env")
            vault_path = root / "vault.env"
            vault_path.write_text("DASHSCOPE_API_KEY=sk-fake-vault\n", encoding="utf-8")
            client = LlmClient(env_store=store, api_vault_path=vault_path)
            captured_headers: list[str | None] = []

            def _urlopen(request, **_kwargs):
                captured_headers.append(request.get_header("Authorization"))
                return _Response()

            with patch("urllib.request.urlopen", side_effect=_urlopen):
                self.assertEqual(client.chat_json("system", "user"), {"ok": True})
                store.write_key("STUDIO_LLM_API_KEY", "sk-fake-dotenv-new")
                self.assertEqual(client.chat_json("system", "user"), {"ok": True})

            self.assertEqual(
                captured_headers,
                ["Bearer sk-fake-vault", "Bearer sk-fake-dotenv-new"],
            )
            self.assertNotIn("DASHSCOPE_API_KEY", os.environ)


class SettingsHttpTests(unittest.TestCase):
    def _start(
        self,
        root: Path,
        env_path: Path,
        vault_path: Path,
        *,
        vocabulary_transport=None,
    ):
        from cutpoint_lab.studio.server import StudioApplication, bind_server
        from cutpoint_lab.studio.workspace import Workspace

        app = StudioApplication(
            Workspace(root / "workspace"),
            asr_runner=_UnusedAsrRunner(),
            selector=_UnavailableSelector(),
            auto_ai=False,
            env_store=EnvStore(env_path),
            api_vault_path=vault_path,
            vocabulary_transport=vocabulary_transport,
        )
        server, port = bind_server(app, host="127.0.0.1", port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return server, port

    def test_route_table_contains_settings_methods(self):
        from cutpoint_lab.studio.server import ROUTES

        self.assertEqual(ROUTES[("GET", "/api/settings")], "_route_settings")
        self.assertEqual(ROUTES[("PUT", "/api/settings/apikey")], "_route_save_api_key")
        self.assertEqual(ROUTES[("POST", "/api/settings/apikey/test")], "_route_test_api_key")
        self.assertEqual(ROUTES[("GET", "/api/settings/vocabulary")], "_route_vocabulary")
        self.assertEqual(ROUTES[("PUT", "/api/settings/vocabulary")], "_route_save_vocabulary")
        self.assertEqual(ROUTES[("GET", "/api/prompts/{mode}")], "_route_prompt")
        self.assertEqual(ROUTES[("PUT", "/api/prompts/{mode}")], "_route_save_prompt")
        self.assertEqual(ROUTES[("DELETE", "/api/prompts/{mode}")], "_route_reset_prompt")

    def test_settings_response_masks_and_never_leaks_full_key(self):
        fake_key = "sk-fake-never-leak-9876"
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=True):
            root = Path(tmp)
            env_path = root / ".env"
            env_path.write_text(f"DASHSCOPE_API_KEY={fake_key}\n", encoding="utf-8")
            server, port = self._start(root, env_path, root / "vault.env")
            try:
                _, payload = _request_json(f"http://127.0.0.1:{port}/api/settings")
                serialized = json.dumps(payload, ensure_ascii=False)
                self.assertNotIn(fake_key, serialized)
                self.assertEqual(payload["dashscope_key"], {"masked": "•••9876", "source": "dotenv"})
                self.assertEqual(payload["llm"]["key_name"], "DASHSCOPE_API_KEY")
                self.assertEqual(payload["llm"]["key_source"], "dotenv")
                self.assertEqual(payload["env_path"], str(env_path.resolve()))
            finally:
                server.shutdown()
                server.server_close()

    def test_process_environment_override_returns_exact_warning(self):
        warning = "进程环境变量 DASHSCOPE_API_KEY 会覆盖 .env，本次修改在当前会话可能不生效"
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"DASHSCOPE_API_KEY": "sk-fake-process-key"}, clear=True
        ):
            root = Path(tmp)
            env_path = root / ".env"
            server, port = self._start(root, env_path, root / "vault.env")
            try:
                _, payload = _request_json(
                    f"http://127.0.0.1:{port}/api/settings/apikey",
                    method="PUT",
                    payload={"key": "sk-fake-dotenv-key"},
                )
                self.assertEqual(payload, {"ok": True, "warning": warning})
            finally:
                server.shutdown()
                server.server_close()

    def test_put_api_key_rejects_whitespace(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=True):
            root = Path(tmp)
            server, port = self._start(root, root / ".env", root / "vault.env")
            try:
                status, payload = _request_error(
                    f"http://127.0.0.1:{port}/api/settings/apikey",
                    method="PUT",
                    payload={"key": "sk-fake has-space"},
                )
                self.assertEqual(status, 400)
                self.assertFalse(payload["ok"])
            finally:
                server.shutdown()
                server.server_close()

    def test_api_key_test_uses_bearer_without_returning_key(self):
        explicit_key = "sk-fake-explicit-test-6789"

        captured = {}

        def _urlopen(request, *, timeout):
            captured["url"] = request.full_url
            captured["authorization"] = request.get_header("Authorization")
            captured["timeout"] = timeout
            return _JsonResponse({"data": []})

        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=True), patch(
            "cutpoint_lab.studio.server.urlopen", side_effect=_urlopen
        ):
            root = Path(tmp)
            vocabulary_transport = _VocabularyTransport(
                {"output": {"vocabulary_list": []}}
            )
            server, port = self._start(
                root,
                root / ".env",
                root / "vault.env",
                vocabulary_transport=vocabulary_transport,
            )
            try:
                _, payload = _request_json(
                    f"http://127.0.0.1:{port}/api/settings/apikey/test",
                    method="POST",
                    payload={"key": explicit_key},
                )
                self.assertEqual(payload, {"ok": True, "detail": "API Key 验证成功", "vocab_access": True})
                self.assertNotIn(explicit_key, json.dumps(payload, ensure_ascii=False))
                self.assertEqual(captured["authorization"], f"Bearer {explicit_key}")
                self.assertEqual(captured["timeout"], 10)
                self.assertTrue(captured["url"].endswith("/models"))
                vocab_request, vocab_timeout = vocabulary_transport.calls[0]
                self.assertEqual(vocab_request.get_header("Authorization"), f"Bearer {explicit_key}")
                self.assertEqual(vocabulary_transport.payload()["input"]["page_size"], 1)
                self.assertEqual(vocab_timeout, 30)
            finally:
                server.shutdown()
                server.server_close()

    def test_api_key_test_reports_vocabulary_auth_denial_and_network_unknown(self):
        denied = urllib.error.HTTPError(
            "https://example.test/vocabulary",
            403,
            "forbidden",
            {},
            io.BytesIO(b"forbidden"),
        )
        outcomes = [(denied, False), (urllib.error.URLError("offline"), None)]
        for outcome, expected in outcomes:
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as tmp, patch.dict(
                os.environ, {}, clear=True
            ), patch(
                "cutpoint_lab.studio.server.urlopen",
                return_value=_JsonResponse({"data": []}),
            ):
                root = Path(tmp)
                server, port = self._start(
                    root,
                    root / ".env",
                    root / "vault.env",
                    vocabulary_transport=_VocabularyTransport(outcome),
                )
                try:
                    _, payload = _request_json(
                        f"http://127.0.0.1:{port}/api/settings/apikey/test",
                        method="POST",
                        payload={"key": "sk-fake-probe"},
                    )
                    self.assertTrue(payload["ok"])
                    self.assertIs(payload["vocab_access"], expected)
                finally:
                    server.shutdown()
                    server.server_close()

    def test_api_key_test_without_payload_uses_effective_dashscope_key(self):
        captured = {}

        def _urlopen(request, *, timeout):
            captured["authorization"] = request.get_header("Authorization")
            captured["url"] = request.full_url
            return _JsonResponse({"data": []})

        vocabulary_transport = _VocabularyTransport(
            {"output": {"vocabulary_list": []}}
        )
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=True), patch(
            "cutpoint_lab.studio.server.urlopen", side_effect=_urlopen
        ):
            root = Path(tmp)
            env_path = root / ".env"
            env_path.write_text(
                "STUDIO_LLM_API_KEY=sk-third-party-llm\n"
                "STUDIO_LLM_BASE_URL=https://third-party.example/v1\n"
                "DASHSCOPE_API_KEY=sk-dashscope-effective\n",
                encoding="utf-8",
            )
            server, port = self._start(
                root,
                env_path,
                root / "vault.env",
                vocabulary_transport=vocabulary_transport,
            )
            try:
                _, payload = _request_json(
                    f"http://127.0.0.1:{port}/api/settings/apikey/test",
                    method="POST",
                    payload={},
                )
                self.assertTrue(payload["ok"])
                expected = "Bearer sk-dashscope-effective"
                self.assertEqual(captured["authorization"], expected)
                self.assertTrue(captured["url"].startswith("https://dashscope.aliyuncs.com/"))
                self.assertEqual(
                    vocabulary_transport.calls[0][0].get_header("Authorization"),
                    expected,
                )
            finally:
                server.shutdown()
                server.server_close()

    def test_vocabulary_get_without_configured_id_returns_empty_state(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=True):
            root = Path(tmp)
            server, port = self._start(root, root / ".env", root / "vault.env")
            try:
                _, payload = _request_json(
                    f"http://127.0.0.1:{port}/api/settings/vocabulary"
                )
                self.assertEqual(
                    payload,
                    {"vocabulary_id": None, "items": [], "exists": False},
                )
            finally:
                server.shutdown()
                server.server_close()

    def test_vocabulary_get_queries_configured_id(self):
        transport = _VocabularyTransport(
            {
                "output": {
                    "status": "OK",
                    "target_model": "fun-asr",
                    "vocabulary": [{"text": "超脑", "weight": 4, "lang": "zh"}],
                }
            }
        )
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=True):
            root = Path(tmp)
            env_path = root / ".env"
            env_path.write_text(
                "DASHSCOPE_API_KEY=sk-fake-query\nASR_BASE_VOCABULARY_ID=vocab-pes-existing\n",
                encoding="utf-8",
            )
            server, port = self._start(
                root,
                env_path,
                root / "vault.env",
                vocabulary_transport=transport,
            )
            try:
                _, payload = _request_json(
                    f"http://127.0.0.1:{port}/api/settings/vocabulary"
                )
                self.assertEqual(
                    payload,
                    {
                        "vocabulary_id": "vocab-pes-existing",
                        "items": [{"text": "超脑", "weight": 4, "lang": "zh"}],
                        "exists": True,
                        "status": "OK",
                        "target_model": "fun-asr",
                    },
                )
                self.assertEqual(
                    transport.payload()["input"],
                    {"action": "query_vocabulary", "vocabulary_id": "vocab-pes-existing"},
                )
            finally:
                server.shutdown()
                server.server_close()

    def test_vocabulary_create_writes_id_and_polls_until_ready(self):
        transport = _VocabularyTransport(
            {"output": {"vocabulary_id": "vocab-pes-created"}},
            {"output": {"status": "UNDEPLOYED", "target_model": "fun-asr", "vocabulary": []}},
            {"output": {"status": "OK", "target_model": "fun-asr", "vocabulary": []}},
        )
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=True), patch(
            "cutpoint_lab.studio.server.time.sleep"
        ) as sleep:
            root = Path(tmp)
            env_path = root / ".env"
            env_path.write_text("DASHSCOPE_API_KEY=sk-fake-create\n", encoding="utf-8")
            server, port = self._start(
                root,
                env_path,
                root / "vault.env",
                vocabulary_transport=transport,
            )
            try:
                _, payload = _request_json(
                    f"http://127.0.0.1:{port}/api/settings/vocabulary",
                    method="PUT",
                    payload={"create": True, "items": [{"text": "Paper Edit"}]},
                )
                self.assertEqual(payload, {"ok": True, "vocabulary_id": "vocab-pes-created"})
                self.assertEqual(
                    EnvStore(env_path).read()["ASR_BASE_VOCABULARY_ID"],
                    "vocab-pes-created",
                )
                self.assertEqual(transport.payload(0)["input"]["prefix"], "pes")
                self.assertEqual(transport.payload(0)["input"]["target_model"], "fun-asr")
                sleep.assert_called_once_with(1)
            finally:
                server.shutdown()
                server.server_close()

    def test_vocabulary_create_rejects_non_boolean_flag_without_remote_call(self):
        transport = _VocabularyTransport(
            {"output": {"vocabulary_id": "must-not-be-created"}}
        )
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=True):
            root = Path(tmp)
            env_path = root / ".env"
            env_path.write_text("DASHSCOPE_API_KEY=sk-fake-create\n", encoding="utf-8")
            server, port = self._start(
                root,
                env_path,
                root / "vault.env",
                vocabulary_transport=transport,
            )
            try:
                status, payload = _request_error(
                    f"http://127.0.0.1:{port}/api/settings/vocabulary",
                    method="PUT",
                    payload={"create": "false", "items": [{"text": "Paper Edit"}]},
                )
                self.assertEqual(status, 400)
                self.assertIn("boolean", payload["error"])
                self.assertEqual(transport.calls, [])
                self.assertNotIn("ASR_BASE_VOCABULARY_ID", EnvStore(env_path).read())
            finally:
                server.shutdown()
                server.server_close()

    def test_vocabulary_update_keeps_existing_id_and_checks_status(self):
        transport = _VocabularyTransport(
            {"output": {}},
            {
                "output": {
                    "status": "OK",
                    "target_model": "fun-asr",
                    "vocabulary": [{"text": "更新", "weight": 4}],
                }
            },
        )
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=True):
            root = Path(tmp)
            env_path = root / ".env"
            original = (
                "DASHSCOPE_API_KEY=sk-fake-update\n"
                "ASR_BASE_VOCABULARY_ID=vocab-pes-existing\n"
            )
            env_path.write_text(original, encoding="utf-8")
            server, port = self._start(
                root,
                env_path,
                root / "vault.env",
                vocabulary_transport=transport,
            )
            try:
                _, payload = _request_json(
                    f"http://127.0.0.1:{port}/api/settings/vocabulary",
                    method="PUT",
                    payload={"items": [{"text": "更新"}]},
                )
                self.assertEqual(
                    payload,
                    {"ok": True, "vocabulary_id": "vocab-pes-existing", "status": "OK"},
                )
                self.assertEqual(env_path.read_text(encoding="utf-8"), original)
                self.assertNotIn("target_model", transport.payload(0)["input"])
            finally:
                server.shutdown()
                server.server_close()

    def test_vocabulary_status_error_never_echoes_api_key(self):
        secret = "sk-fake-status-must-not-leak"
        transport = _VocabularyTransport(
            {"output": {}},
            {"output": {"status": secret, "vocabulary": []}},
        )
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=True):
            root = Path(tmp)
            env_path = root / ".env"
            env_path.write_text(
                f"DASHSCOPE_API_KEY={secret}\n"
                "ASR_BASE_VOCABULARY_ID=vocab-pes-existing\n",
                encoding="utf-8",
            )
            server, port = self._start(
                root,
                env_path,
                root / "vault.env",
                vocabulary_transport=transport,
            )
            try:
                status, payload = _request_error(
                    f"http://127.0.0.1:{port}/api/settings/vocabulary",
                    method="PUT",
                    payload={"items": [{"text": "更新"}]},
                )
                self.assertEqual(status, 500)
                self.assertNotIn(secret, json.dumps(payload, ensure_ascii=False))
            finally:
                server.shutdown()
                server.server_close()

    def test_vocabulary_update_without_id_is_bad_request(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=True):
            root = Path(tmp)
            env_path = root / ".env"
            server, port = self._start(root, env_path, root / "vault.env")
            try:
                status, payload = _request_error(
                    f"http://127.0.0.1:{port}/api/settings/vocabulary",
                    method="PUT",
                    payload={"items": [{"text": "更新"}]},
                )
                self.assertEqual(status, 400)
                self.assertIn("vocabulary", payload["error"].lower())
            finally:
                server.shutdown()
                server.server_close()

    def test_prompt_endpoints_round_trip_override_and_reset(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=True):
            root = Path(tmp)
            server, port = self._start(root, root / ".env", root / "vault.env")
            base = f"http://127.0.0.1:{port}/api/prompts/koubo_tighten"
            try:
                _, initial = _request_json(base)
                self.assertEqual(initial["source"], "default")
                self.assertEqual(initial["content"], initial["default_content"])
                self.assertTrue(initial["hard_constraints"])

                override = "自定义模板 {{USER_BRIEF}}"
                _, updated = _request_json(base, method="PUT", payload={"content": override})
                self.assertEqual(updated["source"], "override")
                self.assertEqual(updated["content"], override)
                # koubo 出厂模板不含 {{TARGET_DURATION}}，保留 {{USER_BRIEF}} 时不应有任何警告。
                self.assertEqual(updated["warnings"], [])
                override_path = root / "workspace" / "_settings" / "prompts" / "koubo_tighten.md"
                self.assertEqual(override_path.read_text(encoding="utf-8"), override)

                _, deleted = _request_json(base, method="DELETE")
                self.assertEqual(deleted, {"ok": True})
                _, restored = _request_json(base)
                self.assertEqual(restored["source"], "default")
                self.assertFalse(override_path.exists())
            finally:
                server.shutdown()
                server.server_close()

    def test_prompt_endpoint_rejects_blank_content(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=True):
            root = Path(tmp)
            server, port = self._start(root, root / ".env", root / "vault.env")
            try:
                status, payload = _request_error(
                    f"http://127.0.0.1:{port}/api/prompts/koubo_tighten",
                    method="PUT",
                    payload={"content": "  \n"},
                )
                self.assertEqual(status, 400)
                self.assertIn("不能为空", payload["error"])
            finally:
                server.shutdown()
                server.server_close()

    def test_put_api_key_writes_dotenv_without_echoing_secret(self):
        fake_key = "sk-fake-write-only-4321"
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=True):
            root = Path(tmp)
            env_path = root / ".env"
            env_path.write_text("# config\nOTHER=value\n", encoding="utf-8")
            server, port = self._start(root, env_path, root / "vault.env")
            try:
                _, payload = _request_json(
                    f"http://127.0.0.1:{port}/api/settings/apikey",
                    method="PUT",
                    payload={"key": fake_key},
                )
                self.assertEqual(payload, {"ok": True})
                self.assertNotIn(fake_key, json.dumps(payload, ensure_ascii=False))
                self.assertEqual(EnvStore(env_path).read()["DASHSCOPE_API_KEY"], fake_key)
            finally:
                server.shutdown()
                server.server_close()


if __name__ == "__main__":
    unittest.main()
