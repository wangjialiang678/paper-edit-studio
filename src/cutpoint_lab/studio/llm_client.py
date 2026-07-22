from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import DEFAULT_API_VAULT_PATH, EnvStore, resolve_llm_api_key

logger = logging.getLogger("studio.llm")

DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_MODEL = "qwen-plus"
API_VAULT_PATH = DEFAULT_API_VAULT_PATH


def load_env_file(path: Path = API_VAULT_PATH) -> None:
    """把 KEY=VALUE 形式的 env 文件补进进程环境（不覆盖已有值，不打印内容）。"""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.removeprefix("export ").partition("=")
        key = key.strip()
        value = value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value


@dataclass(frozen=True)
class LlmConfig:
    base_url: str
    api_key: str
    model: str
    timeout_seconds: int = 300

    @classmethod
    def from_env(
        cls,
        env_store: EnvStore | None = None,
        *,
        api_vault_path: str | Path = API_VAULT_PATH,
    ) -> "LlmConfig":
        store = env_store or EnvStore()
        api_key, _, _ = resolve_llm_api_key(store, api_vault_path=api_vault_path)
        base_url, _ = store.effective("STUDIO_LLM_BASE_URL")
        model, _ = store.effective("STUDIO_LLM_MODEL")
        return cls(
            base_url=(DEFAULT_BASE_URL if base_url is None else base_url).rstrip("/"),
            api_key=api_key or "",
            model=DEFAULT_MODEL if model is None else model,
        )


class LlmError(RuntimeError):
    pass


class LlmClient:
    """OpenAI 兼容 chat/completions 客户端（stdlib 实现，无第三方依赖）。

    默认走 DashScope 兼容模式 + qwen；通过 STUDIO_LLM_* 环境变量可切换任意兼容服务。
    """

    def __init__(
        self,
        config: LlmConfig | None = None,
        *,
        env_store: EnvStore | None = None,
        api_vault_path: str | Path = API_VAULT_PATH,
    ):
        self._config_override = config
        self.env_store = env_store or EnvStore()
        self.api_vault_path = Path(api_vault_path).expanduser()

    @property
    def config(self) -> LlmConfig:
        """兼容旧调用方；默认客户端每次访问都返回最新配置。"""
        return self._current_config()

    def _current_config(self) -> LlmConfig:
        return self._config_override or LlmConfig.from_env(
            self.env_store,
            api_vault_path=self.api_vault_path,
        )

    def available(self) -> bool:
        return bool(self._current_config().api_key)

    def chat_json(self, system: str, user: str, *, temperature: float = 0.3) -> dict[str, Any]:
        config = self._current_config()
        if not config.api_key:
            raise LlmError("缺少 LLM API Key（设置 STUDIO_LLM_API_KEY 或 DASHSCOPE_API_KEY）")
        payload: dict[str, Any] = {
            "model": config.model,
            "temperature": temperature,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {"type": "json_object"},
            # 流式接收：慢模型生成大 JSON 时非流式会撞网关响应流超时（504），
            # 流式只要 token 持续产出连接就一直活着。
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        try:
            content = self._post_chat(payload, config)
        except LlmError as exc:
            message = str(exc)
            # 部分兼容服务不支持 response_format / stream，逐项降级重试一次。
            if "response_format" in message:
                payload.pop("response_format", None)
                content = self._post_chat(payload, config)
            elif "stream" in message:
                payload.pop("stream", None)
                payload.pop("stream_options", None)
                content = self._post_chat(payload, config)
            else:
                raise
        return extract_json_object(content)

    def _post_chat(self, payload: dict[str, Any], config: LlmConfig | None = None) -> str:
        current = config or self._current_config()
        url = f"{current.base_url}/chat/completions"
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {current.api_key}",
            },
            method="POST",
        )
        streaming = bool(payload.get("stream"))
        try:
            with urllib.request.urlopen(request, timeout=current.timeout_seconds) as response:
                if streaming:
                    content, usage = _read_sse_stream(response)
                else:
                    body = json.loads(response.read().decode("utf-8"))
                    try:
                        content = body["choices"][0]["message"]["content"]
                    except (KeyError, IndexError, TypeError) as exc:
                        detail = json.dumps(body, ensure_ascii=False)[:300]
                        raise LlmError(f"LLM 响应结构异常：{_redact(detail, current.api_key)}") from exc
                    usage = body.get("usage") or {}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise LlmError(f"LLM HTTP {exc.code}：{_redact(detail, current.api_key)}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise LlmError(f"LLM 请求失败：{_redact(str(exc), current.api_key)}") from exc
        if not isinstance(content, str) or not content.strip():
            raise LlmError("LLM 返回空内容")
        logger.info("llm ok: model=%s stream=%s prompt_tokens=%s completion_tokens=%s",
                    current.model,
                    streaming,
                    usage.get("prompt_tokens"),
                    usage.get("completion_tokens"))
        return content


def _read_sse_stream(response) -> tuple[str, dict[str, Any]]:
    """按 OpenAI 兼容 SSE 协议逐行累积 delta.content；usage 取自带 usage 的收尾块。

    timeout 在流式下是"单次读的空闲超时"而非总时长——只要模型持续吐 token 就不会断。
    个别网关会无视 stream 参数直接返回整块 JSON：没读到任何 SSE 行时按普通响应回退解析。
    """
    parts: list[str] = []
    raw_lines: list[bytes] = []
    usage: dict[str, Any] = {}
    saw_sse = False
    try:
        iterator = iter(response)
    except TypeError:
        # 测试替身/个别响应对象只有 read()：整体读取后按行解析。
        import io

        iterator = iter(io.BytesIO(response.read()))
    for raw in iterator:
        raw_lines.append(raw)
        line = raw.decode("utf-8", errors="replace").strip()
        if not line.startswith("data:"):
            continue
        saw_sse = True
        data = line[len("data:"):].strip()
        if data == "[DONE]":
            break
        try:
            chunk = json.loads(data)
        except json.JSONDecodeError:
            continue
        if chunk.get("usage"):
            usage = chunk["usage"]
        choices = chunk.get("choices") or []
        if choices:
            delta = (choices[0].get("delta") or {}).get("content")
            if isinstance(delta, str):
                parts.append(delta)
    if not saw_sse:
        try:
            body = json.loads(b"".join(raw_lines).decode("utf-8"))
            return body["choices"][0]["message"]["content"], body.get("usage") or {}
        except (json.JSONDecodeError, KeyError, IndexError, TypeError):
            return "", {}
    return "".join(parts), usage


def _redact(text: str, secret: str) -> str:
    return text.replace(secret, "[REDACTED]") if secret else text


def extract_json_object(text: str) -> dict[str, Any]:
    """从模型输出中稳健提取 JSON object：裸 JSON、代码块、前后夹杂文字均可。"""
    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = candidate.strip("`")
        candidate = candidate.removeprefix("json").strip()
    try:
        parsed = json.loads(candidate)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    start = candidate.find("{")
    if start < 0:
        raise LlmError(f"LLM 输出中找不到 JSON：{text[:200]}")
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(candidate)):
        char = candidate[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                snippet = candidate[start : index + 1]
                parsed = json.loads(snippet)
                if isinstance(parsed, dict):
                    return parsed
                break
    raise LlmError(f"LLM 输出不是合法 JSON object：{text[:200]}")
