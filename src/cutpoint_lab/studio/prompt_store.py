from __future__ import annotations

from pathlib import Path
from typing import Any

MODE_PROMPT_FILES = {
    "koubo_tighten": "koubo-tighten.md",
    "topic_slicing": "topic-slicing.md",
    "highlight_remix": "highlight-remix.md",
}

REQUIRED_PLACEHOLDERS = ("{{USER_BRIEF}}", "{{TARGET_DURATION}}")


class PromptStore:
    """提示词默认模板与 workspace 覆盖层。"""

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
        return {
            "mode": mode,
            "content": content,
            "source": source,
            "default_content": default_content,
            "warnings": self.warnings(content),
        }

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

    @staticmethod
    def warnings(content: str) -> list[str]:
        return [f"缺少占位符：{placeholder}" for placeholder in REQUIRED_PLACEHOLDERS if placeholder not in content]

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
