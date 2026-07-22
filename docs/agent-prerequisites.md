---
title: Agent 前置要求
date: 2026-06-07
updated: 2026-07-22
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

2026-07-22 起相关行为变化：

- 设置面板（`GET/PUT /api/settings*`）可在界面修改 `DASHSCOPE_API_KEY` 与热词表；写 `.env` 前会自动生成 `.env.bak-<时间戳>` 备份（已 gitignore，同样**永不提交**）。
- 密钥取值分层为 进程环境变量 > 仓库 `.env` >（仅 LLM key）`~/.claude/api-vault.env`，统一走 `studio/config.py::EnvStore`；进程环境里 export 过的 key 会覆盖 `.env`，排查"改了没生效"先看设置面板显示的"来源"。
- AI 提示词出厂默认在 `prompts/`，用户在界面里的修改保存为 `workspace/_settings/prompts/<mode>.md` 覆盖层；恢复默认=删除覆盖层文件。

## 真实视频验证流程（Studio）

1. 先跑单元测试，确认没有回归：

```bash
scripts/run_tests.py
```

2. 启动 Studio 并导入测试视频（`samples/video/`，来源与规格见 `samples/manifests/video_samples.json`）：

```bash
scripts/studio_web.py
# 默认端口 0 = 系统自动分配（8765 已被 WorkBuddy Copilot 占用，不要手动指定 8765）；
# 实际监听地址会打印在启动日志里
# 导入可用 API：POST /api/projects/import-path {"path": "..."}
```

3. 流水线自动完成 probe → 提取分析音频 → ASR 转写 →（有 LLM key 时）自动口播精剪。阶段进度与错误都写在 `workspace/<项目id>/state.json`。

4. ASR 默认走 video2md 的 `bin/mp4-md`（DashScope fun-asr，词级时间戳，免 OSS），由 `studio/asr_runner.py::Video2mdAsrRunner` 封装调用；`video2md.py::convert_video2md_transcript` 把 `<stem>.transcript.json` 转成内部 Transcript schema（`segments[].start_ms/end_ms/text/tokens`）。旧版 `transcribe_media_recorded.sh` + 自建 OSS 仍可用 `--asr-script` 启用，对应 `ShellAsrRunner` + `dashscope.py::convert_dashscope_transcript`。

> 旧 CLI 实验流程（compare/eval/盲听）已移至 `legacy` 分支，此处不再适用。

## 无头 CLI（面向 agent 批处理）

不想起网页服务时，用同一套引擎的无头 CLI 直接跑批。产物写进同一个 `workspace/<项目id>/`，与 Studio 双向互通。入口：`python -m cutpoint_lab <子命令>`（免安装）或 `scripts/pe.py`。

```bash
python -m cutpoint_lab run 视频.mp4 --brief "保留高光，删口癖重复" --redline --json
# 或分步：transcribe（→ transcript.json + 全文 SRT）→ select（口播精剪 + 可选 --redline 修订文件）→ export（mp4 + 重排 SRT）
```

- 依赖与 Studio 完全一致：`ffmpeg` + `DASHSCOPE_API_KEY`（+ LLM key，默认同 DashScope）；缺失时子命令会返回可读错误。
- `--json`：stdout 输出结构化 manifest（供 agent 解析），人类进度走 stderr；批量逐项隔离失败，任一失败退出码非 0。
- 架构边界：CLI 只依赖 `cutpoint_lab.engine` 门面，从不引用 `studio.*`；改引擎位置只需改门面，CLI 不动。
- 无网络快验：`tests/test_cli_flow.py` 用假 ASR + 假 LLM + 真 ffmpeg 跑完整 transcribe→select→export，不触网。

## 安全边界

- 不要提交真实 API key、OSS key、签名 URL、`dashscope-task.json`、`dashscope-submit-*.json`、`summary.json` 或 ASR 运行目录。
- 不要提交真实原始音频和中间 WAV，除非用户明确确认这些素材可以公开。
- 需要展示 ASR 结果时，优先使用脱敏后的 `.md` 或最小 JSON fixture。
- 如果发现样例产物里包含 `OSSAccessKeyId`、`Signature`、`Authorization` 或真实 key，先报告风险，再继续。

## 当前已知注意事项

- `samples/audio/*.m4a` 不能直接喂给 RMS/evaluator；先用 `extract-audio` 转 WAV。
- DashScope 配额失败不一定是本地代码问题；错误里出现 quota/throttling 时，应先按配额问题处理。
- 不要默认 DashScope 已提供 token 级边界；先检查 `dashscope-transcript.json` 的字段结构。
