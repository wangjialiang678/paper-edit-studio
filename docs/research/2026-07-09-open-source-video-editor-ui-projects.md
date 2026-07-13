---
title: 开源视频剪辑 UI 项目调研
date: 2026-07-09
status: done
scene: W1 方案发现
freshness: 🔥 当前
---

# 开源视频剪辑 UI 项目调研

调研时间：2026-07-09 21:17 CST。

调研目标：寻找开源、具备 UI、支持桌面端或 HTML/Web 端、能够导入视频、具备时间线能力，并可完成常见剪辑操作的视频剪辑工具。低 star、近期 AI 生成项目也纳入，但单独标注成熟度。

## 结论摘要

1. 如果目标是“先找可直接使用的成熟剪辑器”，优先看 Kdenlive、Shotcut、OpenShot、Flowblade、Olive、LosslessCut、Blender VSE。
2. 如果目标是“借鉴现代 Web/CapCut 风格 UI”，优先看 OpenCut、OpenReel Video、FreeCut、OpenVideo React Video Editor、FlyCut、ComeCut、Clypra。
3. 如果目标是“React/Remotion/Canvas/WebCodecs 技术栈复用”，优先看 openvideodev/react-video-editor、twick、clip-js、fabric-video-editor、free-react-video-editor、WebAV、ffmpeg.wasm、Remotion。
4. 如果目标是“AI 视频剪辑/Agent 化剪辑 UI”，优先看 Nomi、CutScript、Monet、LTX Desktop、Vanta、Velorn、timeline-studio、frame、framedeck。
5. 2026 年出现大量 Electron/Tauri/React/FFmpeg 的低 star 项目，很多描述高度匹配“导入视频 + 时间线 + 剪切/导出”，适合作为 UI/架构样例，但需要试跑确认完整度。
6. 移动端需要单独判断：Android 原生/Flutter 项目最值得优先试；多数 Web 编辑器只能算“手机浏览器可能可访问”，不能等同于成熟安卓/iOS App。

## 端支持判定

| 标记 | 含义 |
|---|---|
| 桌面原生 | 有 Windows/macOS/Linux 桌面端或明确桌面应用架构。 |
| Web 桌面优先 | 浏览器端项目，主要面向桌面浏览器。手机浏览器需实测。 |
| Android 原生 | 有 Android App、Android SDK 或 Kotlin/Java Android 工程。 |
| iOS 原生 | 有 iOS App、iOS SDK 或 Swift/Objective-C 工程。 |
| Flutter 移动 | Flutter 组件/项目，明确支持 Android/iOS。 |
| 移动路线图 | README 或路线图提到 mobile，但当前仓库未见完整 Android/iOS 工程。 |

## 优先试跑清单

| 优先级 | 项目 | 类型 | 端支持/移动端判断 | 为什么先看 |
|---|---|---|---|---|
| P0 | [OpenCut](https://github.com/OpenCut-app/OpenCut) | Web/跨端 CapCut 替代 | Web 桌面优先；README 提移动路线图；当前未见完整 Android/iOS 工程 | MIT，star 很高，定位直接命中“开源 CapCut 替代”。 |
| P0 | [Kdenlive](https://github.com/KDE/kdenlive) | 桌面 NLE | 桌面原生，Linux/Windows/macOS；无手机端 | 成熟非线编，时间线、导入、剪切、转场、导出完整。 |
| P0 | [Shotcut](https://github.com/mltframework/shotcut) | 桌面 NLE | 桌面原生，Linux/Windows/macOS；无手机端 | 跨平台 Qt + MLT，维护活跃，常见剪辑功能完整。 |
| P0 | [OpenReel Video](https://github.com/Augani/openreel-video) | 浏览器编辑器 | Web 桌面优先；README 将 mobile optimization 放在路线图 | React/TypeScript/WebCodecs/WebGPU，声称完整浏览器端剪辑。 |
| P0 | [FreeCut](https://github.com/walterlow/freecut) | 浏览器编辑器 | Web 桌面优先；现代 Chromium 依赖重，Android 需实测，iOS 风险高 | 多轨时间线、关键帧、实时预览、高质量导出，MIT。 |
| P1 | [OpenShot](https://github.com/OpenShot/openshot-qt) | 桌面 NLE | 桌面原生，Linux/Windows/macOS；无手机端 | 老牌跨平台开源视频编辑器。 |
| P1 | [openvideodev/react-video-editor](https://github.com/openvideodev/react-video-editor) | Web/React/Remotion | Web 桌面优先；手机浏览器需实测 | CapCut/Canva clone，拖拽时间线、分割、裁剪、导出。 |
| P1 | [Clypra](https://github.com/AIEraDev/Clypra) | Tauri + React 桌面/跨端 | 桌面原生；README 明确 iOS/Android via Capacitor，移动端需试跑确认 | 现代 CapCut 功能方向，MIT，2026 活跃。 |
| P1 | [FlyCut](https://github.com/x007xyz/flycut) | WebCodecs Web 编辑器 | Web 桌面优先；Android Chrome 可能可测，iOS Safari 风险高 | 中文项目，定位“类似剪映 Web 版”。 |
| P1 | [ComeCut 来剪](https://github.com/juntaosun/ComeCut) | Web/桌面轻量编辑器 | Web + 桌面；手机浏览器未明确承诺 | 中文项目，灵感源自 CapCut，支持 Web 和桌面。 |
| P1 | [LosslessCut](https://github.com/mifi/lossless-cut) | 桌面快速无损剪切 | 桌面原生，Linux/Windows/macOS；无手机端 | 非完整多轨 NLE，但导入、切段、合并、导出极成熟。 |
| P1 | [twick](https://github.com/ncounterspecialist/twick) | React 视频编辑 SDK/UI | Web/React SDK；手机端需自行适配 | 有 canvas timeline、拖拽、字幕、MP4 导出。 |

## 成熟桌面端视频编辑器

| 项目 | Stars | 技术/平台 | 许可证 | 时间线/UI | 端支持/移动端判断 | 备注 |
|---|---:|---|---|---|---|---|
| [Kdenlive](https://github.com/KDE/kdenlive) | 5276 | C++ / KDE / MLT | GPL-3.0 | 是 | 桌面原生，Linux/Windows/macOS；无手机端 | 成熟桌面非线编，适合直接使用和研究传统 NLE 结构。 |
| [Shotcut](https://github.com/mltframework/shotcut) | 14511 | C++ / Qt / MLT | GPL-3.0 | 是 | 桌面原生，Linux/Windows/macOS；无手机端 | 跨平台、维护很活跃，功能完整。 |
| [OpenShot](https://github.com/OpenShot/openshot-qt) | 6003 | Python / Qt / libopenshot | NOASSERTION | 是 | 桌面原生，Linux/Windows/macOS；无手机端 | 老牌开源桌面剪辑器，Linux/Mac/Windows。 |
| [Flowblade](https://github.com/jliljebl/flowblade) | 3074 | Python / GTK / MLT | GPL-3.0 | 是 | 桌面原生，主要 Linux；无手机端 | Linux 桌面 NLE。 |
| [Blender](https://github.com/blender/blender) | 19074 | C++ / Python | NOASSERTION | 是 | 桌面原生，Linux/Windows/macOS；无手机端 | 内置 Video Sequence Editor，不是轻量剪辑器，但能力强。 |
| [Olive](https://github.com/olive-editor/olive) | 9074 | C++ / Qt | GPL-3.0 | 是 | 桌面原生，Linux/Windows/macOS；无手机端 | 免费开源非线编；最近提交是 2024-12，仍可研究架构。 |
| [LosslessCut](https://github.com/mifi/lossless-cut) | 41960 | TypeScript / Electron / FFmpeg | GPL-2.0 | 简化时间线 | 桌面原生，Linux/Windows/macOS；无手机端 | 强项是无损剪切、分段、合并，不是多轨创作型 NLE。 |
| [Pitivi](https://github.com/GNOME/pitivi) | 127 | Python / GNOME / GStreamer | NOASSERTION | 是 | 桌面原生，主要 Linux；无手机端 | GNOME 项目，GitHub 为只读镜像。 |
| [VidCutter](https://github.com/ozmartian/vidcutter) | 1972 | Python / Qt | GPL-3.0 | 简化时间线 | 桌面原生，Linux/Windows/macOS；无手机端 | 现代简单跨平台 cutter/joiner。 |
| [Beutl](https://github.com/b-editor/beutl) | 1215 | C# | MIT | 是 | 桌面原生；无手机端 | 跨平台视频编辑/合成软件。 |
| [MediaEditor](https://github.com/opencodewin/MediaEditor) | 500 | C++ | LGPL-3.0 | 是 | 桌面原生；无手机端 | 非线性编辑软件。 |
| [GoZen](https://github.com/VoylinsGamedevJourney/gozen) | 421 | Godot / GDScript | GPL-3.0 | 是 | 桌面原生；无手机端 | 最小化视频编辑器，主项目迁移到 Codeberg。 |
| [FramePFX](https://github.com/AngryCarrot789/FramePFX) | 272 | C# / Avalonia | GPL-3.0 | 是 | 桌面原生；无手机端 | C# Avalonia 非线编。 |
| [palmier-pro](https://github.com/palmier-io/palmier-pro) | 10170 | Swift / macOS | GPL-3.0 | 是 | macOS 桌面；无手机端 | macOS AI 视频编辑器。 |

## 现代 Web / HTML / 跨端视频编辑器

| 项目 | Stars | 技术/平台 | 许可证 | 时间线/UI | 端支持/移动端判断 | 备注 |
|---|---:|---|---|---|---|---|
| [OpenCut](https://github.com/OpenCut-app/OpenCut) | 61932 | TypeScript / Web | MIT | 是 | Web 桌面优先；当前 README 提 mobile，但本仓库未见 Android/iOS 工程；classic 版本主要 Web | 开源 CapCut 替代，热度最高；架构仍在快速演进。 |
| [OpenReel Video](https://github.com/Augani/openreel-video) | 3891 | React / TS / WebCodecs / WebGPU | MIT | 是 | Web 桌面优先；mobile optimization 在路线图 | 浏览器端专业编辑器，强调本地处理、无上传、无水印。 |
| [FreeCut](https://github.com/walterlow/freecut) | 1514 | TypeScript / Web | MIT | 是 | Web 桌面优先；依赖现代 Chromium 能力，Android/iOS 需实测 | 浏览器内专业级编辑器，多轨、关键帧、预览、导出。 |
| [openvideodev/react-video-editor](https://github.com/openvideodev/react-video-editor) | 1725 | React / Remotion / WebCodecs / PixiJS | NOASSERTION | 是 | Web 桌面优先；手机浏览器未明确支持 | CapCut/Canva clone，适合研究现代 Web 时间线。 |
| [Clypra](https://github.com/AIEraDev/Clypra) | 2264 | Tauri / React / TypeScript | MIT | 是 | 桌面原生；README 明确 iOS/Android via Capacitor，移动端需试跑确认 | 目标是复刻高级 CapCut 能力，2026 活跃。 |
| [FlyCut](https://github.com/x007xyz/flycut) | 924 | Vue / WebCodecs | NOASSERTION | 是 | Web 桌面优先；Android Chrome 可能可测，iOS Safari 风险高 | Web 端剪辑工具，描述为类似剪映 Web 版。 |
| [ComeCut 来剪](https://github.com/juntaosun/ComeCut) | 561 | HTML / Web / Desktop | AGPL-3.0 | 是 | Web + 桌面；手机浏览器未明确承诺 | 轻量级视频编辑器，Web 和桌面均可使用。 |
| [clip-js](https://github.com/mohyware/clip-js) | 748 | Next.js / Remotion / ffmpeg.wasm | MIT | 是 | Web 桌面优先；手机浏览器需实测 | 在线视频编辑器，可作为 Web render/timeline 样例。 |
| [fabric-video-editor](https://github.com/AmitDigga/fabric-video-editor) | 570 | Next.js / React / Fabric.js | MIT | 是 | Web 桌面优先；手机端需自行适配 | 简单视频编辑器，适合研究 canvas 编辑器 UI。 |
| [twick](https://github.com/ncounterspecialist/twick) | 509 | React SDK | NOASSERTION | 是 | Web/React SDK；手机端需自行适配 | Canvas timeline、拖拽、AI 字幕、MP4 导出。 |
| [MasterSelects](https://github.com/Sportinger/MasterSelects) | 415 | TypeScript / Browser | MIT | 是 | Web 桌面优先；手机浏览器需实测 | 浏览器实时媒体编辑，无后端。 |
| [WebCut](https://github.com/wangrongding/WebCut) | 205 | Vue / Web | LGPL-3.0 | 是 | Web 桌面优先；手机浏览器未明确支持 | 中文 Web 音视频编辑器。 |
| [reframe](https://github.com/magic-peach/reframe) | 166 | TypeScript / Web | 未标明 | 是 | Web 桌面优先；WASM/内存限制下手机需实测 | 免费开源浏览器编辑器，无登录、无上传、无广告。 |
| [free-react-video-editor](https://github.com/reactvideoeditor/free-react-video-editor) | 123 | TypeScript / React / Remotion | MIT | 是 | Web/React 组件；手机端需自行适配 | 免费 React 视频编辑器，适合作为组件参考。 |
| [a-react-video-editor](https://github.com/sambowenhughes/a-react-video-editor) | 150 | Next.js / Remotion | MIT | 是 | Web 桌面优先；手机浏览器未明确支持 | 基础浏览器视频编辑器，可排列 clips 和加文字。 |
| [ffmpeg-webCLI](https://github.com/tejaswigowda/ffmpeg-webCLI) | 969 | JavaScript / ffmpeg.wasm | GPL-3.0 | 简化 UI | Web 工具；手机上 WASM/内存风险高 | 浏览器本地处理，无上传；更像 FFmpeg Web 工具。 |
| [wasmux](https://github.com/ero-qt/wasmux) | 2 | TypeScript / ffmpeg.wasm | MIT | 简化 UI | Web 工具；手机需实测 | 轻量 wasm 视频编辑器，低星早期。 |
| [Yug34/video-editor](https://github.com/Yug34/video-editor) | 7 | TypeScript / Next.js / WASM | 未标明 | 简化 UI | Web 工具；手机需实测 | 实时客户端侧视频编辑器。 |
| [imgly/video-editor-wasm-react](https://github.com/imgly/video-editor-wasm-react) | 82 | React / WASM | AGPL-3.0 | 简化 UI | Web/React 示例；手机需实测 | WASM + React 示例，简单 trimming。 |
| [Govind783/nextjs-video-editor](https://github.com/Govind783/nextjs-video-editor) | 66 | Next.js / shadcn | 未标明 | 是 | Web 桌面优先；手机浏览器未明确支持 | Web 视频编辑器。 |
| [RohanPoojary1107/fire-video-editor](https://github.com/RohanPoojary1107/fire-video-editor) | 60 | TypeScript / Web | 未标明 | 是 | Web 桌面优先；手机浏览器未明确支持 | Web 视频编辑软件。 |
| [aslamhus/VideoEditor](https://github.com/aslamhus/VideoEditor) | 59 | JavaScript | 未标明 | 简化 UI | Web UI；手机浏览器需实测 | JS 视频编辑 UI，支持 trim/crop。 |
| [daem-on/fwf](https://github.com/daem-on/fwf) | 261 | JavaScript / FFmpeg | MIT | 简化 UI | Web 工具；手机需实测 | HTML video editor with FFmpeg。 |

## AI / Agent / 文本驱动视频剪辑 UI

| 项目 | Stars | 技术/平台 | 许可证 | 时间线/UI | 端支持/移动端判断 | 备注 |
|---|---:|---|---|---|---|---|
| [Nomi](https://github.com/aqm857886159/Nomi) | 293 | Electron / React | Apache-2.0 | 是 | 桌面原生；无手机端 | 本地优先 AI 视频创作：脚本生成素材，再在时间线编辑导出。 |
| [CutScript](https://github.com/DataAnts-AI/CutScript) | 157 | Electron / React / FFmpeg | MIT | 文本剪辑 UI | 桌面原生；无手机端 | Descript-like，通过编辑转写文本来剪视频。 |
| [Monet](https://github.com/Monet-AI-Editor/Monet) | 90 | TypeScript | MIT | 是 | Web/桌面倾向；手机端未明确 | 面向 Claude Code/Codex 的视频与图片编辑器。 |
| [LTX Desktop](https://github.com/Lightricks/LTX-Desktop) | 1763 | Electron / React / Python backend | Apache-2.0 | 是 | 桌面原生；无手机端 | LTX 视频生成桌面 app，包含 Video Editor / Timeline gap fill。 |
| [Vanta](https://github.com/itsjwill/vanta) | 77 | TypeScript / Remotion | MIT | 是 | Web/Remotion；手机端未明确 | AI 视频引擎：timeline、100+ transitions、字幕、avatar 等。 |
| [Velorn](https://github.com/VelornLabs/velorn) | 283 | JavaScript | GPL-3.0 | 是 | Web/桌面倾向；手机端未明确 | AI-native 视频编辑，强调真实创意时间线和本地 agent 控制。 |
| [trykimu/videoeditor](https://github.com/trykimu/videoeditor) | 2131 | TypeScript | Other | 是 | Web/桌面倾向；手机端未明确 | Creative copilot for video editing；许可证需重点确认。 |
| [aregrid/frame](https://github.com/aregrid/frame) | 242 | 未标明 | MIT | 是 | Web/桌面倾向；手机端未明确 | AI-powered video editor，Cursor-like 交互。 |
| [timeline-studio](https://github.com/chatman-media/timeline-studio) | 178 | TypeScript | MIT | 是 | Web/桌面倾向；手机端未明确 | Timeline Studio - Video Editing with AI。 |
| [framedeck](https://github.com/kevinrss01/framedeck) | 56 | TypeScript | 未标明 | 是 | Web/桌面倾向；手机端未明确 | 自然语言时间线编辑、字幕、素材分析、云渲染。 |
| [carocut](https://github.com/bilibili/carocut) | 112 | TypeScript / Remotion | Other | 可能 | Web/Remotion；手机端未明确 | Bilibili 开源，多 Agent 视频制作助手。 |
| [rendiv](https://github.com/thecodacus/rendiv) | 66 | React / TypeScript | Apache-2.0 | 可能 | Web/React；手机端未明确 | 面向 AI agents/LLM pipelines 的视频编辑器。 |
| [ai-agent-video-editor](https://github.com/pifferologo/ai-agent-video-editor) | 151 | TypeScript | MIT | 可能 | Web/桌面倾向；手机端未明确 | 使用 transcript/EDL/ffmpeg 的 AI agent 视频编辑。 |
| [video-editor-ai-agent](https://github.com/Don-Uwe/video-editor-ai-agent) | 134 | TypeScript | MIT | 可能 | Web/桌面倾向；手机端未明确 | 输入素材和 brief，由多 agent 进行剪辑与质量检查。 |

## 低 star / 近期生成式项目

这些项目很可能是 AI 辅助生成或早期原型。因为你的要求允许低 star，我保留它们；后续应先看 README、安装能否跑通、导入/时间线/导出是否真可用。

| 项目 | Stars | 技术/平台 | 许可证 | 端支持/移动端判断 | 描述 |
|---|---:|---|---|---|---|
| [Refloow Video Editor](https://github.com/Refloow/Refloow-Video-Editor) | 9 | JavaScript / Desktop | AGPL-3.0 | 桌面原生；无手机端 | 桌面视频编辑器，无水印、暗色模式、切割和渲染。 |
| [perseus-video-editor](https://github.com/gufao/perseus-video-editor) | 4 | TypeScript | 未标明 | 桌面原生；无手机端 | 轻量桌面视频编辑器，quick cuts/trims/exports。 |
| [Revo](https://github.com/SegMind25/Revo) | 3 | Tauri / Vue 3 | 未标明 | 桌面原生；无手机端 | 现代桌面编辑器，拖拽、时间线、效果。 |
| [maestro-cut](https://github.com/HosamTechProf/maestro-cut) | 2 | Angular / Electron / FFmpeg | MIT | 桌面原生；无手机端 | AI copilot + 响应式拖拽时间线 + Gemini 命令。 |
| [clipforge](https://github.com/Davaakhatan/clipforge) | 0 | Electron / React / FFmpeg | 未标明 | 桌面原生；无手机端 | 导入、时间线、trim、split、MP4 export。 |
| [light-cut-vidz](https://github.com/light-cut-vidz/light-cut-vidz) | 0 | Electron / React / FFmpeg | 未标明 | 桌面原生；无手机端 | 轻量桌面剪辑器。 |
| [sindus/light-cut-vidz](https://github.com/sindus/light-cut-vidz) | 未核 | Electron / React / FFmpeg | 未核 | 桌面原生；无手机端 | 搜索结果显示含 Timeline.tsx、filmstrip、bundled FFmpeg。 |
| [etcyl/react-video-editor](https://github.com/etcyl/react-video-editor) | 0 | React / ffmpeg.wasm | 未标明 | Web 桌面优先；手机需实测 | 浏览器编辑器：timeline、titles、transitions、source resolution export。 |
| [masluny/video-editor](https://github.com/masluny/video-editor) | 1 | Tauri 2 / Rust / React / FFmpeg | 未标明 | 桌面原生；无手机端 | Revind，跨平台桌面编辑器。 |
| [ffmpeg-studio](https://github.com/Agnyy/ffmpeg-studio) | 1 | Electron / TypeScript / FFmpeg | NOASSERTION | 桌面原生；无手机端 | 自定义 preview engine、timeline、audio playback、export pipeline。 |
| [Ares Video Editor](https://github.com/Mendocan/Ares-Video-Editor) | 1 | Python / PySide6 / FFmpeg | 未标明 | 桌面原生；无手机端 | 时间线编辑、字幕工具、FFmpeg 导出。 |
| [Vied](https://github.com/Rhoahndur/Vied) | 0 | Electron / TypeScript | MIT | 桌面原生；无手机端 | 跨平台桌面编辑器，timeline、trim、screen recording、PiP。 |
| [videobuilder](https://github.com/mnavas/videobuilder) | 0 | Python / PySide6 / ffmpeg | MIT | 桌面原生；无手机端 | 时间线、照片 slideshow、titles、music、crossfades。 |
| [Clipstrike](https://github.com/Gerbzz/Clipstrike) | 1 | Rust / egui / wgpu / GStreamer | 未标明 | 桌面原生；无手机端 | Rust 桌面剪辑器，GStreamer media pipeline。 |
| [streamlit-video-editor](https://github.com/RhythrosaLabs/streamlit-video-editor) | 2 | JavaScript / Streamlit component | MIT | Web 组件；手机端需自行适配 | Streamlit 非线编组件，多轨 timeline、媒体导入、音频播放。 |
| [Aether-Edits](https://github.com/wleeaf/Aether-Edits) | 2 | TypeScript / ffmpeg.wasm | 未标明 | Web 桌面优先；手机需实测 | 全客户端视频编辑，timeline、transforms、effects、transitions、audio mixing。 |
| [ruben3732/VideoCraft](https://github.com/ruben3732/VideoCraft) | 5 | Kotlin / Android | 未标明 | Android 原生；优先试跑安卓候选 | Android 视频编辑器，timeline/keyframes/AI edit。非桌面/Web，作为 UI 参考。 |
| [katana](https://github.com/milanofthe/katana) | 20 | Tauri / SvelteKit / Rust | MIT | Windows 桌面；无手机端 | Windows 轻量桌面编辑器，多轨合成、无损导出、GPU UI。 |
| [openreelio](https://github.com/openreelio/openreelio) | 46 | TypeScript | MIT | Web/桌面倾向；手机端未明确 | Prompt-driven AI video editor，标注 Pre-Alpha。 |
| [amber](https://github.com/baptisterajaut/amber) | 46 | C++ / Qt 6 / FFmpeg | GPL-3.0 | 桌面原生；无手机端 | 非线性视频编辑器，Qt RHI，Vulkan/Metal/D3D/OpenGL。 |
| [Gahara](https://github.com/Gahara-Editor/gahara) | 41 | Svelte | Apache-2.0 | Web/桌面倾向；手机端未明确 | Vim-inspired video editor。 |
| [cutlass](https://github.com/1mrnewton/cutlass) | 38 | Rust | Apache-2.0 | 桌面/CLI 倾向；手机端未明确 | 用描述方式编辑视频的 Rust 开源编辑器。 |
| [montage-ai](https://github.com/mfahsold/montage-ai) | 37 | Python | Other | 桌面/本地工具倾向；无手机端 | 本地优先 AI 编辑器，transcript-based editing、beat-synced cuts、OTIO/EDL export。 |

## 移动端 / 手机端相关项目

这些项目更接近 Android/iOS 手机端需求。注意其中很多是 SDK 或组件，不是完整可直接使用的剪辑 App。

| 项目 | Stars | 技术/平台 | 许可证 | 端支持/移动端判断 | 时间线/UI | 备注 |
|---|---:|---|---|---|---|---|
| [VideoCraft](https://github.com/ruben3732/VideoCraft) | 5 | Kotlin / Android | 未标明 | Android 原生 App；安卓优先试跑 | 是 | README 明确 import、trim、split、reorder、timeline、keyframes、FFmpegKit、ExoPlayer。 |
| [OptiVideoEditor-for-android](https://github.com/jaiobs/OptiVideoEditor-for-android) | 511 | Kotlin / Android | 未标明 | Android 原生 App | 部分 | 原生视频编辑 App，trim、merge、快慢速、音频、文字等。 |
| [Android-Video-Editor](https://github.com/LLhon/Android-Video-Editor) | 1316 | Java / Android | 未标明 | Android 原生 App/样例 | 部分 | 视频拍摄、裁剪、滤镜、压缩；较老，完整时间线能力不明确。 |
| [Android-Video-Trimmer](https://github.com/iknow4x/Android-Video-Trimmer) | 1177 | Java / Android | Apache-2.0 | Android 原生组件 | 简化 | 长短视频片段选择、裁剪、压缩；更像 trimmer，不是完整多轨剪辑器。 |
| [LeGoffMael/video_editor](https://github.com/LeGoffMael/video_editor) | 494 | Dart / Flutter | MIT | Flutter 移动；README 明确 Android + iOS，Web 仍在进展中 | 组件 UI | trim、crop、rotate、scale、cover selection；适合嵌进自研 Flutter App。 |
| [hm21/pro_video_editor](https://github.com/hm21/pro_video_editor) | 87 | Dart / Flutter | BSD-3-Clause | Flutter 移动；pubspec 标 Android/iOS/Web | 组件 UI | trim、merge、speed、reverse、mute、crop、layers、blur 等，功能覆盖较广。 |
| [rdVideoEditSDK-for-Android](https://github.com/rdsdk/rdVideoEditSDK-for-Android) | 417 | Java / Android | 未标明 | Android SDK | SDK UI/能力 | cut、join、watermark、subtitle、rotate。 |
| [rdVideoEditSDK-for-iOS](https://github.com/rdsdk/rdVideoEditSDK-for-iOS) | 201 | Objective-C / iOS | 未标明 | iOS SDK | SDK UI/能力 | iOS 对应 SDK，cut、join、watermark、subtitle、rotate。 |
| [LanSoSdk/video-edit-sdk-android](https://github.com/LanSoSdk/video-edit-sdk-android) | 72 | Java / Android | 未标明 | Android SDK | SDK UI/能力 | cut、crop、PIP、动画、滤镜、转场、音频层、视频层等。 |
| [LanSongEditor_IOS](https://github.com/LanSoSdk/LanSongEditor_IOS) | 206 | Objective-C / iOS | 未标明 | iOS SDK | SDK UI/能力 | iOS 版本，支持 cut、crop、PIP、动画、滤镜、转场、音频/视频层。 |
| [YiVideoEditor](https://github.com/coderyi/YiVideoEditor) | 138 | Swift / iOS | MIT | iOS 库 | 无完整剪辑 UI | 旋转、裁剪、加水印/图层、加音频。 |
| [HMS Video Editor Demo](https://github.com/HMS-Core/hms-video-editor-demo) | 93 | Java / Android/Huawei | Apache-2.0 | Android/Huawei SDK 示例 | SDK 示例 | 华为 Video Editor Kit 集成示例，依赖华为生态。 |
| [Banuba Android sample](https://github.com/Banuba/ve-sdk-android-integration-sample) | 122 | Kotlin / Android | 未标明 | Android 商业 SDK 示例 | SDK 示例 | 适合参考集成方式；核心 SDK 不是普通开源项目。 |
| [Banuba iOS sample](https://github.com/Banuba/ve-sdk-ios-integration-sample) | 105 | Swift / iOS | 未标明 | iOS 商业 SDK 示例 | SDK 示例 | 适合参考集成方式；核心 SDK 不是普通开源项目。 |

## 可作为底层技术或组件的项目

这些不是完整“带 UI 的剪辑软件”，但对自研 HTML/桌面端编辑器很有参考价值。

| 项目 | Stars | 端支持/移动端判断 | 用途 |
|---|---:|---|---|
| [Remotion](https://github.com/remotion-dev/remotion) | 52628 | Web/Node 渲染技术；手机端不是重点 | React 生成视频；官方文档有 timeline-based editor 指南。许可证需按用途确认。 |
| [ffmpeg.wasm](https://github.com/ffmpegwasm/ffmpeg.wasm) | 17665 | Web 技术；手机浏览器受内存/性能限制 | 浏览器内 FFmpeg，适合本地导出/转码。 |
| [WebAV](https://github.com/WebAV-Tech/WebAV) | 2069 | WebCodecs SDK；Android Chrome 可能可测，iOS 风险高 | WebCodecs 视频编辑 SDK。 |
| [OpenTimelineIO](https://github.com/AcademySoftwareFoundation/OpenTimelineIO) | 1914 | 桌面/服务端/格式交换；无手机 UI | 时间线交换格式，适合与 Premiere/Resolve/Kdenlive 等互通。 |
| [etro](https://github.com/etro-js/etro) | 1142 | 浏览器库；手机需实测 | 浏览器 TypeScript 视频编辑库。 |
| [MoviePy](https://github.com/Zulko/moviepy) | 14773 | Python 库；桌面/服务端，非手机端 | Python 视频编辑库，无 UI，但很多桌面原型会用它。 |
| [libopenshot](https://github.com/OpenShot/libopenshot) | 1531 | C++/Python 库；桌面/服务端，非手机端 | OpenShot 的 C++/Python 视频编辑库。 |
| [MLT Framework](https://github.com/mltframework/mlt) | 未本轮核数 | 多媒体框架；桌面/服务端，非手机端 | Kdenlive/Shotcut 背后的多媒体框架。 |
| [OpenVideo engine/projects](https://github.com/openvideodev/react-video-editor) | 1725 | Web 桌面优先；手机需实测 | React/PixiJS/WebCodecs 时间线编辑参考。 |
| [VideoFlow](https://github.com/ybouane/VideoFlow) | 115 | Web/TS API；手机端需自行适配 | TS API 定义视频、JSON 交换、MP4 渲染、scrubbing。 |

## 许可证和风险提醒

1. GPL/AGPL 项目很多。若要嵌入自研商业产品，必须先评估许可证影响，尤其是 Shotcut、Kdenlive、Olive、LosslessCut、ComeCut、Refloow、imgly 示例等。
2. OpenCut、FreeCut、Clypra、OpenReel、Nomi、Katana 等 MIT/Apache 项目更适合作为集成或借鉴起点。
3. Remotion 生态强，但 Remotion 本身存在商业使用许可边界；用于公司产品前要单独确认。
4. Web 端剪辑通常在 WebCodecs、WebGPU、ffmpeg.wasm、浏览器内存和跨浏览器兼容性上有风险；Android Chrome 比 iOS Safari 更值得优先实测。
5. 低 star 2026 项目需要重点验证三件事：能否导入本地视频、时间线操作是否真实可用、导出是否稳定。
6. Android 方向优先看 VideoCraft、OptiVideoEditor-for-android、LeGoffMael/video_editor、hm21/pro_video_editor；iOS 方向优先看 Flutter 组件、YiVideoEditor、LanSongEditor_IOS。

## 下一步建议

1. P0 Web/桌面试跑：OpenCut、OpenReel、FreeCut、Clypra、ComeCut、FlyCut。
2. P0 桌面参考：Kdenlive、Shotcut、OpenShot、LosslessCut。
3. P0 Android 试跑：VideoCraft、OptiVideoEditor-for-android、LeGoffMael/video_editor、hm21/pro_video_editor。
4. 自研 Web 编辑器参考：openvideodev/react-video-editor、twick、fabric-video-editor、WebAV、ffmpeg.wasm。
5. AI 剪辑方向参考：Nomi、CutScript、Monet、Vanta、timeline-studio。
6. 建议为每个候选建立一张试跑表：安装耗时、导入是否成功、时间线是否可拖拽/分割/裁剪、导出是否成功、许可证、可复用组件位置、Android/iOS 是否可用。

## 参考来源

- GitHub metadata via `gh search repos` and `gh api repos/*`，检索时间 2026-07-09。
- [OpenCut](https://github.com/OpenCut-app/OpenCut)
- [Kdenlive](https://github.com/KDE/kdenlive)
- [Shotcut](https://github.com/mltframework/shotcut)
- [OpenShot](https://github.com/OpenShot/openshot-qt)
- [Flowblade](https://github.com/jliljebl/flowblade)
- [Blender](https://github.com/blender/blender)
- [LosslessCut](https://github.com/mifi/lossless-cut)
- [OpenReel Video](https://github.com/Augani/openreel-video)
- [FreeCut](https://github.com/walterlow/freecut)
- [OpenVideo React Video Editor](https://github.com/openvideodev/react-video-editor)
- [Clypra](https://github.com/AIEraDev/Clypra)
- [FlyCut](https://github.com/x007xyz/flycut)
- [ComeCut](https://github.com/juntaosun/ComeCut)
- [twick](https://github.com/ncounterspecialist/twick)
- [Remotion timeline guide](https://www.remotion.dev/docs/building-a-timeline)
- [WebAV](https://github.com/WebAV-Tech/WebAV)
- [ffmpeg.wasm](https://github.com/ffmpegwasm/ffmpeg.wasm)
- [OpenTimelineIO](https://github.com/AcademySoftwareFoundation/OpenTimelineIO)
