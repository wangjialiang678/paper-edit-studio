---
title: 无头 CLI 使用指南（面向 AI / 批处理）
date: 2026-07-22
status: active
audience: ai, human
---

# 无头 CLI 使用指南

用文字剪视频的**无头命令行**：同一套引擎，既能网页交互，也能给 AI / 脚本批量驱动。
把它交给 Codex / Claude Code 这类编码 agent，你只描述剪辑意图，agent 负责调命令、读结果。

产物写进同一个 `workspace/<项目id>/`，与网页版**双向互通**——CLI 粗剪完可在网页里精修，反之亦然。

## 你需要给它的信息（就这几样）

| 必给 | 说明 |
|---|---|
| **视频路径** | 本机绝对路径，可多个（批量） |
| **剪辑意图（brief）** | 保留什么、删什么、目标时长——越具体越准 |
| **要什么产物** | 要不要修订对照文件、成片导出到哪 |
| **一次性配好** | `DASHSCOPE_API_KEY`（ASR + 口播精剪都用它）、`ffmpeg` 在 `PATH`、能连 DashScope 网络 |

## 入口

免安装：`python -m cutpoint_lab <子命令>`（在仓库根目录运行）
或 `scripts/pe.py <子命令>`；`pip install -e .` 后可用 `pe <子命令>`。

## 五个子命令

```bash
# 1) 批量转写：视频 → 词级时间戳字幕（transcript.json）+ 全文 SRT
python -m cutpoint_lab transcribe a.mp4 b.mp4

# 2) AI 选段：口播精剪（保留高光、删赘语口癖重复）；--redline 生成「修订模式」Markdown
python -m cutpoint_lab select <项目id> \
    --brief "保留成长与创作过程的高光，删掉口癖和重复" \
    --target-duration "3分钟" \
    --redline redline.md

# 3) 引导式确认：网页点确认后 CLI 自动继续，无需手动回传 selection.json
python -m cutpoint_lab review <项目id> --serve --open

# 4) 批量导出：按选择的切点导出成片 mp4 + 重排 SRT
python -m cutpoint_lab export <项目id>

# 一条命令跑完整流程（AI 主用入口）：转写→选段→导出
python -m cutpoint_lab run a.mp4 --brief "..." --redline --json
```

## 导出速度

导出对每个保留区间用 ffmpeg 帧精确重编码再拼接（切点是词级毫秒精度，无法用 `-c copy` 无损快切）。已做的提速：

- **快速 seek（默认，零配置）**：每段用输入侧 `-ss` 定位，只解码该段而非从文件头解码到切点——对靠后切点是数量级提升（实测 8 分钟 HEVC 里第 7 分钟的单段：18s → 0.8s）。
- **段级并行**：软件编码按 CPU 核数并行（`PE_EXPORT_WORKERS` 可覆盖）。
- **硬件编码（opt-in）**：默认走 `libx264`。实测在常见素材上瓶颈是源解码而非编码，硬件编码（mac VideoToolbox / Win NVENC·QSV·AMF）反而更慢，故不默认开启。需要时设 `PE_EXPORT_ENCODER=auto` 探测并使用硬件编码（探测/失败自动回退 libx264），或直接指定编码器名。

## 参数速查

| 参数 | 适用 | 说明 |
|---|---|---|
| `--brief TEXT` | select / run | 剪辑意图，注入口播精剪提示词；越具体越准 |
| `--target-duration TEXT` | select / run | 目标时长，如 `"3分钟"` |
| `--redline PATH` | select | 生成 Markdown 修订对照到指定文件 |
| `--redline` | run | 生成修订对照（默认写到项目目录 `redline.md`） |
| `--redline-dir DIR` | select / run | 批量时每个项目写 `<项目id>.md` |
| `--out PATH` | review | 单项目确认页输出路径；默认 `workspace/<项目id>/review.html` |
| `--serve` | review | 单项目启动仅绑定 `127.0.0.1` 的确认服务；网页确认后自动写回 `selection.json` 并结束等待；不能与 `--all` 同用 |
| `--timeout SECONDS` | review + `--serve` | 最长等待确认的秒数，默认 `1800`；超时后命令返回错误，仍可下载文件手动回传 |
| `--open` | review | 在默认浏览器打开确认页；`--serve` 时打开本机确认服务地址 |
| `--strategy NAME` | export / run | 切点策略，默认 `hybrid_valley`；缺分析音频自动回退 `token_padding` |
| `--out DIR` | export / run | 额外把成片/SRT 复制到此目录 |
| `--all` | select / review / export | 处理工作区内全部项目；review 批量时不能使用 `--out` |
| `--json` | 所有 | stdout 输出结构化 manifest（供 agent 解析），进度走 stderr |
| `--workspace DIR` | 所有 | 项目工作区目录，默认 `workspace` |

## 输出与结果解析

- **`--json` manifest**（stdout 一行 JSON）：
  ```json
  {"ok": true, "command": "run", "results": [
    {"project_id": "2026...-xxxx", "source": ".../a.mp4",
     "outputs": {"transcript": "...", "full_srt": "...", "selection": "...",
                 "redline": "...", "clip_plan": "...", "video": "...", "srt": "..."},
     "warnings": [], "error": null}
  ]}
  ```
  批量逐项隔离失败：某项失败其 `error` 非 null、不影响其他项；**任一失败进程退出码为 1**（全成功为 0，argparse 参数错误为 2）。
- **修订对照文件**（Markdown 划线）：保留句正常显示、删除句 `~~划线~~` 并在行尾标注 AI 删除理由，文件头有保留/删除句数与时长统计。可读、可 diff、可转 Word——**先审再导出**的信任抓手。
- **交互确认页**：`review` 总会输出完全自包含的 `review.html`，无需服务即可打开；词块按句内文字连排，英文相邻词自动补空格。使用 `--serve` 时，页面还会把确认结果直接写回项目的 `selection.json`。
- **项目目录** `workspace/<项目id>/`：`transcript.json`（词级字幕）、`selection.json`（保留/删除）、`review.html`（交互确认）、`clip_plan.json`（切点）、`exports/edited-*.mp4` + `.srt`。

## 引导式确认

需要在导出前逐句、逐词确认时，推荐用本机确认服务完成闭环：

1. `select <项目id> --brief "..."` 生成 AI 初选和 `selection.json`。
2. `review <项目id> --serve --open` 启动仅本机可访问的确认页；终端会等待网页操作。
3. 在页面里勾选句子、点击词块删除或恢复，然后点「✓ 确认完成，继续剪辑」。
4. 页面确认后，CLI 自动把同一结构的选择结果写入 `workspace/<项目id>/selection.json`，终端返回包含 `selection` 与 `confirmed: true` 的结果。
5. `export <项目id>` 按确认后的逐句/逐词选择导出成片。

如果无法使用 `--serve`，可退回静态模式：`review <项目id> --open`，在页面点「导出 selection.json」后手动覆盖项目目录里的同名文件。确认服务超时时，页面上的「下载 selection.json」也可作为同一后备路径。

页面不含视频预览和切点微调；这些操作继续使用完整版网页界面。

## 迭代工作流（真正的用法）

第一版 brief 未必对味，**改 brief 重来即可，不用重新转写**（字幕已缓存，很快）：

1. `transcribe` 一次 → 得到字幕（贵的一步只做一次）。
2. `select --brief "..."` → 看 `redline.md` 删了什么。
3. 不满意 → 换 brief 再 `select`（覆盖 selection.json）→ 满意再 `export`。
4. 想做**词级/气口的外科级微调** → 用同一项目在网页版打开手调（`scripts/studio_web.py`）。

## 给 AI agent 的开场白（可直接复制）

```
这个仓库有一个无头剪辑 CLI。先读 docs/cli-usage.md 了解用法。
然后用它把 <视频绝对路径> 剪一版：
- 保留成长与创作过程的高光，删掉口癖、赘语、重复句
- 先生成 Markdown 修订对照文件，让我看删了哪些、为什么删
- 用 --json 读结果，把项目 id、修订文件路径、成片路径告诉我
我看完修订确认后，你再执行导出成片。
前置：需要 ffmpeg 和 DASHSCOPE_API_KEY，网络要能连 DashScope。
```

## 前置检查

```bash
python3 --version      # 3.11+
ffmpeg -version        # 在 PATH
# DASHSCOPE_API_KEY 放仓库根 .env（模板 .env.example）或进程环境
```

无网络快验（不触真实 ASR/LLM）：`tests/test_cli_flow.py` 用假 ASR + 假 LLM + 真 ffmpeg 跑通全流程。
更多环境与凭据细节见 [agent-prerequisites.md](agent-prerequisites.md)。
