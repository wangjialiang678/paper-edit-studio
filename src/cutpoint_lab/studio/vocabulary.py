from __future__ import annotations

import json
import logging
import os
import re
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any


logger = logging.getLogger("studio.vocabulary")

DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/api/v1/services/audio/asr/customization"
DEFAULT_TIMEOUT_SECONDS = 30
_PREFIX_PATTERN = re.compile(r"^[a-z0-9]{1,10}$")

Transport = Callable[..., Any]


class VocabularyError(RuntimeError):
    """DashScope 热词表请求或响应失败。"""


class VocabularyHttpError(VocabularyError):
    """保留 HTTP 状态码但不暴露凭据的热词表异常。"""

    def __init__(self, message: str, *, status: int):
        super().__init__(message)
        self.status = status


class VocabularyClient:
    """用于 DashScope 定制热词表管理的轻量 stdlib 客户端。"""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str | None = None,
        transport: Transport | None = None,
    ):
        if not isinstance(api_key, str) or not api_key or any(char.isspace() for char in api_key):
            raise ValueError("API Key 不能为空且不能包含空白字符")

        configured_url = base_url if base_url is not None else os.environ.get("VOCAB_BASE_URL")
        resolved_url = configured_url or DEFAULT_BASE_URL
        if not isinstance(resolved_url, str) or not resolved_url.strip():
            raise ValueError("Vocabulary base URL 不能为空")

        self.api_key = api_key
        self.base_url = resolved_url.strip().rstrip("/")
        self.transport = urllib.request.urlopen if transport is None else transport

    def create(
        self,
        prefix: str,
        target_model: str,
        items: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if not isinstance(prefix, str) or _PREFIX_PATTERN.fullmatch(prefix) is None:
            raise ValueError("prefix 只能包含小写字母和数字，长度为 1-10")
        model = _required_string(target_model, "target_model")
        vocabulary = _validated_items(items)
        return self._post(
            {
                "action": "create_vocabulary",
                "target_model": model,
                "prefix": prefix,
                "vocabulary": vocabulary,
            }
        )

    def query(self, vocabulary_id: str) -> dict[str, Any]:
        return self._post(
            {
                "action": "query_vocabulary",
                "vocabulary_id": _required_string(vocabulary_id, "vocabulary_id"),
            }
        )

    def update(
        self,
        vocabulary_id: str,
        items: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return self._post(
            {
                "action": "update_vocabulary",
                "vocabulary_id": _required_string(vocabulary_id, "vocabulary_id"),
                "vocabulary": _validated_items(items),
            }
        )

    def list_page_one(self) -> dict[str, Any]:
        return self._post(
            {
                "action": "list_vocabulary",
                "page_index": 0,
                "page_size": 1,
            }
        )

    def _post(self, input_payload: dict[str, Any]) -> dict[str, Any]:
        action = str(input_payload.get("action") or "unknown")
        payload = {"model": "speech-biasing", "input": input_payload}
        request = urllib.request.Request(
            self.base_url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )

        logger.info("vocabulary request: action=%s", action)
        try:
            with self.transport(request, timeout=DEFAULT_TIMEOUT_SECONDS) as response:
                raw_body = response.read()
        except urllib.error.HTTPError as exc:
            detail = _read_http_error_detail(exc)
            logger.warning("vocabulary HTTP failure: action=%s status=%s", action, exc.code)
            raise VocabularyHttpError(
                f"Vocabulary HTTP {exc.code}: {_redact(detail, self.api_key)}",
                status=exc.code,
            ) from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            logger.warning("vocabulary network failure: action=%s type=%s", action, type(exc).__name__)
            raise VocabularyError(
                f"Vocabulary 请求失败：{_redact(str(exc), self.api_key)}"
            ) from None

        body = _decode_response(raw_body, self.api_key)
        output = body.get("output")
        if not isinstance(output, dict):
            detail = _redact(json.dumps(body, ensure_ascii=False)[:300], self.api_key)
            raise VocabularyError(f"Vocabulary 响应结构异常：{detail}")

        logger.info("vocabulary response: action=%s ok", action)
        return output


def _required_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} 不能为空")
    return value.strip()


def _validated_items(items: Any) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        raise ValueError("items 必须是列表")
    if len(items) > 500:
        raise ValueError("单个词表最多包含 500 个热词")

    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise ValueError(f"items[{index}] 必须是对象")

        text = item.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"items[{index}].text 不能为空")
        text = text.strip()
        if any(ord(char) > 127 for char in text):
            if len(text) > 15:
                raise ValueError(f"items[{index}].text 含非 ASCII 字符时不能超过 15 个字符")
        elif len(text.split()) > 7:
            raise ValueError(f"items[{index}].text 为纯 ASCII 时不能超过 7 个分词")

        weight = item.get("weight", 4)
        if isinstance(weight, bool) or not isinstance(weight, int) or not 1 <= weight <= 5:
            raise ValueError(f"items[{index}].weight 必须是 1-5 的整数")

        normalized_item: dict[str, Any] = {"text": text, "weight": weight}
        if "lang" in item:
            lang = item["lang"]
            if not isinstance(lang, str) or not lang.strip():
                raise ValueError(f"items[{index}].lang 必须是非空字符串")
            normalized_item["lang"] = lang.strip()
        normalized.append(normalized_item)
    return normalized


def _read_http_error_detail(exc: urllib.error.HTTPError) -> str:
    try:
        body = exc.read()
    except Exception:  # noqa: BLE001 - secondary diagnostic reads must not hide the HTTP status.
        return str(exc.reason or "")[:500]
    if isinstance(body, bytes):
        return body.decode("utf-8", errors="replace")[:500]
    return str(body)[:500]


def _decode_response(raw_body: Any, secret: str) -> dict[str, Any]:
    try:
        if isinstance(raw_body, bytes):
            text = raw_body.decode("utf-8")
        elif isinstance(raw_body, str):
            text = raw_body
        else:
            raise TypeError("response body must be bytes or str")
        body = json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
        safe_detail = _redact(str(exc), secret)
        raise VocabularyError(f"Vocabulary 响应不是合法 JSON：{safe_detail}") from exc
    if not isinstance(body, dict):
        raise VocabularyError("Vocabulary 响应结构异常：顶层必须是 JSON object")
    return body


def _redact(text: str, secret: str) -> str:
    return text.replace(secret, "[REDACTED]") if secret else text


__all__ = ["VocabularyClient", "VocabularyError", "VocabularyHttpError"]
