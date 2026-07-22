---
title: Agent 前置要求
date: 2026-06-07
updated: 2026-07-13
status: active
audience: ai
---

# Agent 前置要求

本文件给在本仓库工作的 agent 使用。跑 Studio 或真实音频前，先确认这里的环境和安全要求。

## 必备工具

- Python 3.11+。
- `ffmpeg` 在 `PATH` 中；流水线与 ASR 二进制都用它提取音频。
- 默认 ASR（video2md `bin/mp4-md`）只额外需要 `ffmpeg`。
- 旧版 ASR 脚本路径（`--asr-script`）才需要 `curl`、`jq`、`python3`。

快速检查：

```bash
python3 --version
ffmpeg -version
```

## ASR 凭据

只跑 `examples/` 和已有 transcript fixture 不需要外部 key。

**默认路径（video2md，免 OSS）**：重新转写真实音频时，只需一个变量：

```bash
DASHSCOPE_API_KEY          # 必需
ASR_BASE_VOCABULARY_ID     # 可选，热词表 ID
```

**旧版 OSS 脚本路径（仅 `--asr-script` 时）**额外需要：

```bash
OSS_ACCESS_KEY_ID
OSS_ACCESS_KEY_SECRET
OSS_BUCKET
OSS_ENDPOINT
```

这些变量可以来自 shell 环境或仓库根目录未提交的 `.env`（模板见 `.env.example`）。默认路径由 `Video2mdAsrRunner` 读取 `.env` 并注入 `mp4-md` 子进程；旧脚本路径由 `transcribe_media_recorded.sh` 自动 source。缺失时停止并让用户先配置，不要猜测、不要写占位密钥。

## 真实视频验证流程（Studio）

1. 先跑单元测试，确认没有回归：

```bash
scripts/run_tests.py
```

2. 启动 Studio 并导入测试视频（`samples/video/`，来源与规格见 `samples/manifests/video_samples.json`）：

```bash
scripts/studio_web.py --port 8765
# 导入可用 API：POST /api/projects/import-path {"path": "..."}
```

3. 流水线自动完成 probe → 提取分析音频 → ASR 转写 →（有 LLM key 时）自动口播精剪。阶段进度与错误都写在 `workspace/<项目id>/state.json`。

4. ASR 默认走 video2md 的 `bin/mp4-md`（DashScope fun-asr，词级时间戳，免 OSS），由 `studio/asr_runner.py::Video2mdAsrRunner` 封装调用；`video2md.py::convert_video2md_transcript` 把 `<stem>.transcript.json` 转成内部 Transcript schema（`segments[].start_ms/end_ms/text/tokens`）。旧版 `transcribe_media_recorded.sh` + 自建 OSS 仍可用 `--asr-script` 启用，对应 `ShellAsrRunner` + `dashscope.py::convert_dashscope_transcript`。

> 旧 CLI 实验流程（compare/eval/盲听）已移至 `legacy` 分支，此处不再适用。

## 安全边界

- 不要提交真实 API key、OSS key、签名 URL、`dashscope-task.json`、`dashscope-submit-*.json`、`summary.json` 或 ASR 运行目录。
- 不要提交真实原始音频和中间 WAV，除非用户明确确认这些素材可以公开。
- 需要展示 ASR 结果时，优先使用脱敏后的 `.md` 或最小 JSON fixture。
- 如果发现样例产物里包含 `OSSAccessKeyId`、`Signature`、`Authorization` 或真实 key，先报告风险，再继续。

## 当前已知注意事项

- `samples/audio/*.m4a` 不能直接喂给 RMS/evaluator；先用 `extract-audio` 转 WAV。
- DashScope 配额失败不一定是本地代码问题；错误里出现 quota/throttling 时，应先按配额问题处理。
- 不要默认 DashScope 已提供 token 级边界；先检查 `dashscope-transcript.json` 的字段结构。
