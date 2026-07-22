import io
import json
import traceback
import unittest
import urllib.error

from cutpoint_lab.studio.vocabulary import VocabularyClient, VocabularyError


class _Response:
    def __init__(self, payload: dict):
        self.body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self.body


class _Transport:
    def __init__(self, outputs: list[dict] | None = None):
        self.outputs = list(outputs or [])
        self.calls: list[tuple[object, int]] = []

    def __call__(self, request, *, timeout: int):
        self.calls.append((request, timeout))
        return _Response(self.outputs.pop(0) if self.outputs else {"output": {}})

    def payload(self, index: int = -1) -> dict:
        return json.loads(self.calls[index][0].data.decode("utf-8"))


class VocabularyClientTests(unittest.TestCase):
    def test_create_query_update_and_list_payloads(self):
        transport = _Transport(
            [
                {"output": {"vocabulary_id": "vocab-pes-created"}},
                {
                    "output": {
                        "status": "OK",
                        "target_model": "fun-asr",
                        "vocabulary": [{"text": "Paper Edit", "weight": 4}],
                    }
                },
                {"output": {}},
                {"output": {"vocabulary_list": []}},
            ]
        )
        client = VocabularyClient(
            "sk-fake-secret",
            base_url="https://example.test/vocabulary",
            transport=transport,
        )

        created = client.create("pes", "fun-asr", [{"text": "Paper Edit"}])
        queried = client.query("vocab-pes-created")
        updated = client.update("vocab-pes-created", [{"text": "超脑", "weight": 5, "lang": "zh"}])
        listed = client.list_page_one()

        self.assertEqual(created["vocabulary_id"], "vocab-pes-created")
        self.assertEqual(queried["vocabulary"][0]["text"], "Paper Edit")
        self.assertEqual(updated, {})
        self.assertEqual(listed["vocabulary_list"], [])
        self.assertEqual(
            [transport.payload(index) for index in range(4)],
            [
                {
                    "model": "speech-biasing",
                    "input": {
                        "action": "create_vocabulary",
                        "target_model": "fun-asr",
                        "prefix": "pes",
                        "vocabulary": [{"text": "Paper Edit", "weight": 4}],
                    },
                },
                {
                    "model": "speech-biasing",
                    "input": {
                        "action": "query_vocabulary",
                        "vocabulary_id": "vocab-pes-created",
                    },
                },
                {
                    "model": "speech-biasing",
                    "input": {
                        "action": "update_vocabulary",
                        "vocabulary_id": "vocab-pes-created",
                        "vocabulary": [{"text": "超脑", "weight": 5, "lang": "zh"}],
                    },
                },
                {
                    "model": "speech-biasing",
                    "input": {"action": "list_vocabulary", "page_index": 0, "page_size": 1},
                },
            ],
        )
        for request, timeout in transport.calls:
            self.assertEqual(request.full_url, "https://example.test/vocabulary")
            self.assertEqual(request.method, "POST")
            self.assertEqual(request.get_header("Authorization"), "Bearer sk-fake-secret")
            self.assertEqual(timeout, 30)

    def test_local_validation_rejects_invalid_prefix_items_weights_and_lengths(self):
        client = VocabularyClient("sk-fake", transport=_Transport())
        invalid_calls = [
            lambda: client.create("Bad-Prefix", "fun-asr", [{"text": "ok"}]),
            lambda: client.create("pes", "fun-asr", [{}]),
            lambda: client.update("vocab-id", [{"text": "ok", "weight": 0}]),
            lambda: client.update("vocab-id", [{"text": "ok", "weight": True}]),
            lambda: client.update("vocab-id", [{"text": "一" * 16}]),
            lambda: client.update("vocab-id", [{"text": "one two three four five six seven eight"}]),
            lambda: client.update("vocab-id", [{"text": str(index)} for index in range(501)]),
        ]
        for call in invalid_calls:
            with self.subTest(call=call), self.assertRaises(ValueError):
                call()

    def test_http_and_network_errors_never_expose_api_key(self):
        secret = "sk-fake-must-not-leak"

        def _http_error(_request, *, timeout):
            raise urllib.error.HTTPError(
                "https://example.test",
                403,
                "forbidden",
                {},
                io.BytesIO(f"denied {secret}".encode("utf-8")),
            )

        def _network_error(_request, *, timeout):
            raise urllib.error.URLError(f"network failed for {secret}")

        for transport in (_http_error, _network_error):
            with self.subTest(transport=transport):
                with self.assertRaises(VocabularyError) as captured:
                    VocabularyClient(secret, transport=transport).list_page_one()
                self.assertNotIn(secret, str(captured.exception))
                rendered_traceback = "".join(
                    traceback.format_exception(captured.exception)
                )
                self.assertNotIn(secret, rendered_traceback)


if __name__ == "__main__":
    unittest.main()
