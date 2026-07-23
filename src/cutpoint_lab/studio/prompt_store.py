from __future__ import annotations

from pathlib import Path
from typing import Any

from .prompt_protocols import MODE_PROTOCOLS

MODE_PROMPT_FILES = {
    "koubo_tighten": "koubo-tighten.md",
    "topic_slicing": "topic-slicing.md",
    "highlight_remix": "highlight-remix.md",
    "quality_review": "quality-review.md",
    "compose_align": "compose-align.md",
}

# 旧版全文格式（协议下沉前保存的覆盖层）以是否自带输出协议段来识别。
LEGACY_FULL_MARKER = "## 输出格式"

LEGACY_WARNING = (
    "此自定义仍是旧版全文格式（自带输出协议），系统不再追加协议段；"
    "建议「恢复默认」后只用自然语言重写剪辑理念。"
)


class PromptStore:
    """提示词「剪辑理念」层：默认模板 + workspace 覆盖层。

    用户可编辑的部分只有纯自然语言的剪辑理念；输出协议（JSON 格式、
    模式硬约束、占位符注入点）由 prompt_protocols.MODE_PROTOCOLS 在
    拼装时追加，与解析代码同步维护，不暴露给编辑器。
    """

    def __init__(self, prompts_dir: str | Path, override_dir: str | Path | None):
        self.prompts_dir = Path(prompts_dir)
        self.override_dir = Path(override_dir) if override_dir is not None else None

    def get(self, mode: str) -> dict[str, Any]:
        default_path = self._default_path(mode)
        default_content = default_path.read_text(encoding="utf-8")
        override_path = self._override_path(mode)
        if override_path is not None and override_path.is_file():
            content = override_path.read_text(encoding="utf-8")
            source = "override"
        else:
            content = default_content
            source = "default"
        protocol = self._protocol(mode)
        legacy = source == "override" and LEGACY_FULL_MARKER in content
        assembled = content if legacy else content + protocol
        return {
            "mode": mode,
            "content": content,
            "source": source,
            "default_content": default_content,
            "protocol": protocol,
            "legacy": legacy,
            "assembled_template": assembled,
            "warnings": [LEGACY_WARNING] if legacy else [],
        }

    def assemble(self, mode: str) -> str:
        """运行时最终模板（理念 + 协议，占位符待渲染）。"""
        return str(self.get(mode)["assembled_template"])

    def write(self, mode: str, content: str) -> dict[str, Any]:
        if not isinstance(content, str) or not content.strip():
            raise ValueError("提示词内容不能为空")
        override_path = self._override_path(mode, required=True)
        override_path.parent.mkdir(parents=True, exist_ok=True)
        override_path.write_text(content, encoding="utf-8")
        return self.get(mode)

    def reset(self, mode: str) -> dict[str, Any]:
        override_path = self._override_path(mode, required=True)
        override_path.unlink(missing_ok=True)
        return self.get(mode)

    def _protocol(self, mode: str) -> str:
        try:
            return MODE_PROTOCOLS[mode]
        except KeyError as exc:
            raise ValueError(f"未知 AI 模式：{mode}") from exc

    def _default_path(self, mode: str) -> Path:
        try:
            filename = MODE_PROMPT_FILES[mode]
        except KeyError as exc:
            raise ValueError(f"未知 AI 模式：{mode}") from exc
        return self.prompts_dir / filename

    def _override_path(self, mode: str, *, required: bool = False) -> Path | None:
        self._default_path(mode)
        if self.override_dir is None:
            if required:
                raise ValueError("未配置提示词覆盖目录")
            return None
        return self.override_dir / f"{mode}.md"
