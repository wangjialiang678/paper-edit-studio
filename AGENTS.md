# AGENTS.md — 给 AI 编码代理的仓库说明

> 本文件供 Codex / Claude Code 等 AI 代理自动加载。人类用户请看 [README.md](README.md)。

## 这是什么

「用文字剪视频」的本地工具（Paper Edit Studio）：导入视频 → 自动转写**词级时间戳字幕** → AI 建议保留哪些句子（保留高光、删赘语口癖重复）→ 导出成片。两个前端，共用同一套引擎：

- **网页版**（人类交互）：`scripts/studio_web.py --open`
- **无头 CLI**（AI / 批处理）：`python -m cutpoint_lab <子命令>`

## 要用 CLI 剪视频？先读 [docs/cli-usage.md](docs/cli-usage.md)

完整用法、参数速查、给 AI 的开场白都在那份文档。四个子命令（在仓库根目录跑）：

```bash
python -m cutpoint_lab transcribe 视频.mp4        # 转字幕（transcript.json + 全文 SRT）
python -m cutpoint_lab select <项目id> --brief "保留高光，删口癖重复" --redline redline.md  # AI 选段 + 修订对照
python -m cutpoint_lab export <项目id>            # 导出成片 mp4 + 重排 SRT
python -m cutpoint_lab run 视频.mp4 --brief "..." --redline --json   # 一步到位（AI 主用入口）
```

- `--json`：stdout 输出结构化 manifest（供解析），进度走 stderr；批量逐项隔离失败，任一失败退出码非 0。
- 「修订对照」是 Markdown 划线文件：保留句正常、删除句 `~~划线~~` + 行尾 AI 删除理由——先审再导出。
- 产物写进 `workspace/<项目id>/`，与网页版**双向互通**。

## 前置要求

- Python 3.11+；`ffmpeg` 在 `PATH`。
- `DASHSCOPE_API_KEY`（转写 + AI 选段都用；阿里云百炼 bailian.console.aliyun.com 申请）：复制 `.env.example` 为 `.env` 填入，或设进程环境变量。能连 DashScope 的网络。
- 更多细节见 [docs/agent-prerequisites.md](docs/agent-prerequisites.md)。

## 代码结构（要改代码时）

- `src/cutpoint_lab/engine.py` — 引擎门面，**CLI 唯一依赖的稳定 API**。
- `src/cutpoint_lab/cli.py` / `__main__.py` — CLI 四子命令编排（只依赖 engine，不引用 `studio.*`）。
- `src/cutpoint_lab/studio/` — 网页应用层（HTTP 服务、流水线、AI 选段、LLM 客户端）。
- `src/cutpoint_lab/`（models / io / features / strategies / paper_edit / export）— 核心引擎。
- 测试：`scripts/run_tests.py`（改动后必须保持全绿）。

## 约定

- 别把真实密钥写进代码/文档；`.env` / `.env.bak*` 已 gitignore，**永不提交**。
- 改代码前先看现有风格：中文注释、`from __future__ import annotations`、**零第三方依赖**（只用标准库 + 项目内部模块）。
