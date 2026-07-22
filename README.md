# AI 视频剪辑工具（Paper Edit Studio）

用文字剪视频的本地工具：导入视频 → 自动转写字幕 → AI 建议保留哪些句子 → 人工在网页里勾选确认 → 导出成片。

## 启动

```bash
scripts/studio_web.py --open
# 默认端口 0 = 系统自动分配（8765 已被 WorkBuddy Copilot 占用，不要手动指定 8765）
# 需要固定端口时可自行指定其他值，例如：scripts/studio_web.py --port 8899 --open
```

打开页面后拖入视频（或粘贴本机路径），系统自动完成：

1. ffprobe 读取媒体信息，ffmpeg 提取 16kHz 分析音频；
2. DashScope fun-asr 录音转写，生成**词级时间戳**字幕；
3. 自动跑一次"口播精剪" AI 建议（寒暄/口癖/重复句默认取消勾选，理由显示在字幕行右侧）。

编辑界面（参考"开拍·文字快剪"）：上方视频播放器，下方字幕行列表——勾选=保留、取消=删除（划线灰显）、文本可直接编辑、句间"无声段"自动标记并在剪辑时移除。勾选与改字会自动保存。

- **成片/原片双模式播放**：播放条上切换（快捷键 M），默认成片——只播保留内容、进度条显示成片时间轴；空格播放/暂停。点击保留句从该句继续播成片；成片模式下点击已删除句会单句试听、播完自动停，方便决定要不要捞回来。
- **句内微调（✂）**：句子行展开词块面板——**点词块＝删除/恢复该词**：删句首句尾就是修边（trim），删句中间的词就是句内剪切（cuts，一句拆多段）；±10/±50ms 按钮微移切点，**波形上的蓝/黄切点竖线也可以直接鼠标拖动**；每次调整后自动做**接缝试听**（前段结尾 → 跳切 → 后段开头，听拼起来自然不自然），可在面板里关掉自动试听。微调数据（trim + cuts + 毫秒 nudge）随自动保存持久化，预览与导出一致生效。
- **剪气口**：后端自动检测句内的语气词（呃/嗯/唉）与紧邻重复（"我们要我们要"），在词块面板标红虚线并给"剪气口"按钮；工具栏"✂ 一键剪气口"对所有保留句批量应用，每处都可在词块面板单独恢复。
- **AI 选段**（右侧面板，三种模式）：
  - 口播精剪：逐句保留/删除建议（默认提示词把停顿语气词/口癖/重复列为最高优先删除）；
  - 主题切片：长视频按主题拆条，每个主题给最佳切片，可一键"仅保留此切片"；
  - 金句混剪：金句前置 HOOK → 主体 BODY → 金句重复强调 ECHO 的乱序成片方案，附标题建议。
  - **提示词可视化编辑**：面板右上"📝 提示词"可查看/修改当前模式提示词，保存即生效、重启保留；出厂默认在 [`prompts/`](prompts/)，用户修改存为 `workspace/_settings/prompts/` 覆盖层，可一键恢复默认。
- **设置面板（⚙️）**：侧栏入口。查看/修改 DashScope API Key（脱敏显示、写回 `.env` 前自动备份、显示当前生效来源、一键测试有效性）；查看/编辑云端 ASR 热词表（就地更新，或新建词表并自动回写 ID；多人协作各自建表互不影响）。
- **导出视频**：后台 ffmpeg 按工程默认切点策略 `hybrid_valley` 导出 mp4 + 重排后的 SRT。

项目数据存放在 `workspace/<项目id>/`（已 gitignore）：源文件引用/副本、字幕 JSON、AI 建议、勾选状态、导出产物，重启服务不丢失。

## 环境依赖

- **Python 3.11+**。
- **ffmpeg** 在 `PATH`（macOS：`brew install ffmpeg`；Windows：`winget install ffmpeg` 或 [ffmpeg.org](https://ffmpeg.org/download.html)）。
- **ASR（默认，mac + windows 通用）**：内置 `bin/mp4-md`（vendor 自 [video2md-cli](https://github.com/wangjialiang678/video2md-cli)），随仓库分发，**免安装、免自建 OSS**。音频临时中转走 DashScope 自带文件空间，**只需一个凭据**：复制 `.env.example` 为 `.env`，填入 `DASHSCOPE_API_KEY`（[百炼控制台](https://bailian.console.aliyun.com/)申请），也可以启动后在界面「⚙️ 设置」里粘贴并测试；可选 `ASR_BASE_VOCABULARY_ID` 指定热词表（设置面板可直接查看/编辑/新建）。
  - 仓库已内置 `mp4-md-darwin-arm64`（Apple Silicon Mac）与 `mp4-md-windows-amd64.exe`（Windows）。其他平台（Intel Mac / Linux）可自行 `go build` 或安装 video2md-cli 后用 `--asr-binary`/环境变量 `VIDEO2MD_BIN` 指向二进制。
  - **旧版 OSS 方案**仍保留：`scripts/studio_web.py --asr-script scripts/transcribe_media_recorded.sh` 走 `transcribe_media_recorded.sh`（DashScope fun-asr + 自建 OSS），此路径额外需要 `curl`、`jq` 与 `.env.example` 里注释掉的 `OSS_*` 四件套。
- **LLM**：默认 DashScope 兼容模式 qwen-plus（`~/.claude/api-vault.env` 的 `DASHSCOPE_API_KEY`），可用 `STUDIO_LLM_BASE_URL/API_KEY/MODEL` 环境变量切换任意 OpenAI 兼容服务。
- 密钥只放本机环境变量或未提交的 `.env`，不写进代码与文档。

给 agent 的前置检查清单见 `docs/agent-prerequisites.md`。

## 测试

```bash
scripts/run_tests.py
```

## 架构

```
scripts/studio_web.py           启动入口
bin/mp4-md-*                    内置 video2md ASR 二进制（DashScope fun-asr，免 OSS，mac + windows）
scripts/transcribe_media_recorded.sh  旧版 ASR 脚本（DashScope fun-asr + 自建 OSS，--asr-script 启用）
src/cutpoint_lab/
  studio/                       应用层：HTTP 服务（路由表）、流水线、工作区、AI 选段、LLM 客户端
    config.py                   .env 读写与密钥分层解析（进程环境 > .env > api-vault）
    prompt_store.py             提示词默认模板 + workspace 覆盖层
    vocabulary.py               DashScope 热词表客户端（查询/更新/创建）
    filler_detect.py            句内气口检测（语气词 + 紧邻重复，纯规则）
    static/                     前端（原生 ES modules，无构建步骤；main.js 为入口）
  dashscope.py                  DashScope 转写 → 内部 Transcript 转换
  models.py / io.py / features.py   核心数据结构、读写、音频特征
  strategies.py                 切点策略引擎（8 种策略；Studio 默认 hybrid_valley）
  paper_edit/state.py           字幕 ↔ 可编辑行 ↔ 剪辑计划
  export/ + subtitle_exporter.py + video_exporter.py   SRT/视频导出
prompts/                        AI 选段三种模式的提示词模板
docs/specs/prd.md               产品需求文档
docs/speech-cutting/            语音切分的结论、调研、实验、历史设计和资产索引
docs/research/                  其他视频剪辑与 UI 调研
```

### 切点策略引擎

`strategies.py` 保留 8 种切点策略（token_padding、rms_snap、anchored_rms、visual_waveform、hybrid_valley、voice_enhanced_rms、speaker_aware_valley、vad_snap）。Studio 以 **hybrid_valley** 作为保守的工程默认。历史整句盲听比较的混合策略是另一个 `hybrid_safe`，不是当前 `hybrid_valley`；该盲听中用户没有听出四个版本的明显区别，因此不能用它证明 Studio 当前默认策略更好。详细证据和限制见 [`docs/speech-cutting/`](docs/speech-cutting/README.md)。

## 历史实验代码

本项目早期是"音频切点校准"实验室，包含对齐基准台（alignment benchmark）、盲听对比、说话人分离脚本、旧版 CLI 自动剪辑闭环与旧版纸面剪辑 Web 工具。这些代码已于 2026-07-13 从 main 移除，归档在维护者的私有分支中，不随本仓库分发；实验方法与结论沉淀在 [`docs/speech-cutting/`](docs/speech-cutting/README.md)。
