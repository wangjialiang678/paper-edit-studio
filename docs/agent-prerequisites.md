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
- `ffmpeg` 在 `PATH` 中；流水线用它提取 `16kHz mono s16 wav` 分析音频。
- 真实 ASR 链路还需要 `curl`、`jq`、`python3`、`ffmpeg`。

快速检查：

```bash
python3 --version
ffmpeg -version
jq --version
curl --version
```

## ASR 凭据

只跑 `examples/` 和已有 transcript fixture 不需要外部 key。重新转写真实音频时，必须先确认以下环境变量已设置，但不要打印变量值：

```bash
DASHSCOPE_API_KEY
OSS_ACCESS_KEY_ID
OSS_ACCESS_KEY_SECRET
OSS_BUCKET
OSS_ENDPOINT
```

这些变量可以来自 shell 环境、`~/.Codex/api-vault.env`，或 ASR 工具目录中未提交的 `.env`。缺失时停止并让用户先配置，不要猜测、不要写占位密钥。

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

4. ASR 底层脚本是 `transcribe_media_recorded.sh`（DashScope fun-asr，词级时间戳），由 `studio/asr_runner.py` 封装调用；`dashscope.py` 负责把 `dashscope-transcript.json` 转成内部 Transcript schema（`segments[].start_ms/end_ms/text/tokens`）。

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
