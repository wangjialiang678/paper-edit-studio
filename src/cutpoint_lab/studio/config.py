from __future__ import annotations

import os
import re
import shutil
import tempfile
import threading
import time
from pathlib import Path
from typing import Literal

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_ENV_PATH = REPO_ROOT / ".env"
DEFAULT_API_VAULT_PATH = Path.home() / ".claude" / "api-vault.env"

EnvSource = Literal["process_env", "dotenv", "missing"]
SecretSource = Literal["process_env", "dotenv", "api_vault", "missing"]

LLM_API_KEY_NAMES = (
    "STUDIO_LLM_API_KEY",
    "DASHSCOPE_LLM_API_KEY",
    "DASHSCOPE_API_KEY",
)

_ENV_KEY_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def parse_env_file(path: Path) -> dict[str, str]:
    """解析现有项目使用的 KEY=VALUE 子集，不展开变量或行尾注释。"""
    values: dict[str, str] = {}
    if not path or not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :]
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values


class EnvStore:
    """仓库 .env 的读取、分层取值与保结构安全写回。"""

    def __init__(self, path: str | Path = DEFAULT_ENV_PATH):
        self.path = Path(path).expanduser().resolve()
        self._lock = threading.RLock()

    def read(self) -> dict[str, str]:
        with self._lock:
            return parse_env_file(self.path)

    def effective(self, key: str) -> tuple[str | None, EnvSource]:
        if key in os.environ:
            return os.environ[key], "process_env"
        value = self.read().get(key)
        if value is not None:
            return value, "dotenv"
        return None, "missing"

    def write_key(self, key: str, value: str) -> None:
        """更新所有同名赋值行；没有时追加。写入前备份并用原子替换落盘。"""
        if not _ENV_KEY_PATTERN.fullmatch(key):
            raise ValueError(f"无效的环境变量名：{key}")
        if "\n" in value or "\r" in value:
            raise ValueError("环境变量值不能包含换行")

        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            existed = self.path.exists()
            original = self.path.read_text(encoding="utf-8") if existed else ""
            if existed:
                stamp = time.strftime("%Y%m%d-%H%M%S")
                shutil.copy2(self.path, self.path.with_name(f"{self.path.name}.bak-{stamp}"))

            assignment = re.compile(rf"^(\s*(?:export\s+)?{re.escape(key)}\s*=\s*).*$")
            lines = original.splitlines(keepends=True)
            found = False
            output: list[str] = []
            for raw in lines:
                if raw.endswith("\r\n"):
                    line, ending = raw[:-2], "\r\n"
                elif raw.endswith("\n"):
                    line, ending = raw[:-1], "\n"
                else:
                    line, ending = raw, ""
                match = assignment.match(line)
                if match:
                    output.append(f"{match.group(1)}{value}{ending}")
                    found = True
                else:
                    output.append(raw)

            rendered = "".join(output)
            if not found:
                if rendered and not rendered.endswith(("\n", "\r")):
                    rendered += "\n"
                rendered += f"{key}={value}\n"
            self._atomic_write(rendered, preserve_mode=existed)

    def _atomic_write(self, content: str, *, preserve_mode: bool) -> None:
        mode = self.path.stat().st_mode if preserve_mode else None
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="",
                dir=self.path.parent,
                prefix=f".{self.path.name}.",
                delete=False,
            ) as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
                temporary = Path(handle.name)
            if mode is not None:
                os.chmod(temporary, mode)
            os.replace(temporary, self.path)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()


def resolve_secret_key(
    key: str,
    env_store: EnvStore | None = None,
    *,
    api_vault_path: str | Path = DEFAULT_API_VAULT_PATH,
) -> tuple[str | None, SecretSource]:
    """按进程环境、仓库 .env、api-vault 查找一个非空密钥。"""
    store = env_store or EnvStore()
    process_value = os.environ.get(key)
    if process_value:
        return process_value, "process_env"
    dotenv_value = store.read().get(key)
    if dotenv_value:
        return dotenv_value, "dotenv"
    vault_value = parse_env_file(Path(api_vault_path).expanduser()).get(key)
    if vault_value:
        return vault_value, "api_vault"
    return None, "missing"


def resolve_llm_api_key(
    env_store: EnvStore | None = None,
    *,
    api_vault_path: str | Path = DEFAULT_API_VAULT_PATH,
) -> tuple[str | None, str | None, SecretSource]:
    """按 key 名优先级解析 LLM 密钥，每个 key 内再按来源分层。"""
    store = env_store or EnvStore()
    for key_name in LLM_API_KEY_NAMES:
        value, source = resolve_secret_key(key_name, store, api_vault_path=api_vault_path)
        if value:
            return value, key_name, source
    return None, None, "missing"


def mask_api_key(value: str | None) -> str:
    """API key 脱敏：短值全遮，长值仅保留尾四位。"""
    if not value:
        return ""
    if len(value) < 8:
        return "•" * len(value)
    return f"•••{value[-4:]}"
