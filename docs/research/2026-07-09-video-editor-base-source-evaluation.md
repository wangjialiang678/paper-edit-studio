---
title: 开源视频剪辑底座源码评估
date: 2026-07-09
status: current
scenario: W3 技术评估 / W1 方案发现
audience: both
---

# 开源视频剪辑底座源码评估

## 结论先行

如果目标是“基于字幕/转写自动生成预剪辑，用户按句子、段落、词语确认后再导出”，当前最值得进入下一轮试跑的是：

| 排名 | 项目 | 适合方向 | 判断 |
|------|------|----------|------|
| 1 | FreeCut | Web 端主底座 | 最接近可二开的完整 NLE：多轨时间线、转写、场景分析、AI 面板、浏览器本地导出都已在源码中出现。尤其已有 transcript ignore / commit 机制，天然适合“AI 先标记，用户确认后提交到时间线”。 |
| 2 | CutScript | 桌面端文本剪辑原型 / 可移植交互参考 | 不是完整 NLE，但它就是“编辑文字等于剪视频”的产品形态。按词选择、删除、恢复、导出 keep segments 的代码清晰，适合快速做 AI 字幕剪辑 MVP。 |
| 3 | Clypra | 桌面端主底座 / 跨端候选 | Tauri + React + Rust FFmpeg 后端，桌面导出链路比纯 Web 更稳。已有 SRT/VTT 解析和 Whisper 相关命令，但字幕驱动剪辑还需要自己接时间线。 |
| 4 | OpenReel | Web 端专业底座候选 | 时间线和 WebCodecs/MediaBunny 导出能力强，也有字幕和自动编辑服务雏形；但字幕驱动剪辑不如 FreeCut 直接，且源码里出现疑似移动端签名密码，二开前必须做安全清理。 |
| 5 | react-video-editor | Web SaaS 起步套件 | 基于 OpenVideo 包，UI/时间线/导出起步快，已有 Deepgram 字幕结构；但双许可和云服务依赖更重，不如 MIT 项目自由。 |
| 6 | flutter video_editor | 移动端单片段组件 | 适合 Android/iOS 里做单视频 trim/crop/cover，不是完整多轨剪辑底座。导出需要应用自己接 FFmpeg 或后端。 |
| 7 | VideoCraft | Android UI 参考 | 有原生 Android 时间线 View 和 AIEdit 类型，但 FFmpeg/AI 关键逻辑大量是占位或复制文件兜底，不建议作为生产核心。 |
| 暂不推荐 | OpenCut | 观察名单 | 当前 rewrite 源码主要是 Web UI 组件和路由脚手架，README 的桌面/移动/核心引擎更多是路线图，不适合作为现在的二开底座。 |

我建议的路线是：

1. **Web 优先**：用 FreeCut 做主底座，把 AI 输出设计成 source-time ranges，先进入 FreeCut 现有 transcript ignore buffer，让用户确认，再 commit 到时间线。
2. **桌面优先**：用 CutScript 快速做“字幕文本剪辑”MVP；如果需要完整时间线、多轨、稳定本地导出，再迁移到 Clypra 或把 CutScript 的文本剪辑模块移植进去。
3. **Android 优先**：不要直接押 VideoCraft。更现实的是 Flutter `video_editor` 做移动端轻量剪辑 UI，复杂 AI 分析和最终渲染放桌面端或服务端；若坚持原生 Android，需要重建导出核心。

## 下载与验证范围

源码下载位置统一在 `/Users/michael/projects/repos`。

| 项目 | 仓库 / 来源 | 本地路径 | 下载状态 | 许可证/元数据 |
|------|-------------|----------|----------|---------------|
| OpenCut | https://github.com/OpenCut-app/OpenCut | `/Users/michael/projects/repos/OpenCut` | 已 clone | MIT；GitHub 元数据：约 61.9k stars，2026-07-09 仍活跃 |
| FreeCut | https://github.com/walterlow/freecut | `/Users/michael/projects/repos/freecut` | 已通过 GitHub tarball 下载 | MIT；约 1.5k stars，2026-07-09 仍活跃 |
| Clypra | https://github.com/AIEraDev/Clypra | `/Users/michael/projects/repos/Clypra` | 已 clone | MIT；约 2.2k stars，2026-07-09 仍活跃 |
| OpenReel | https://github.com/Augani/openreel-video | `/Users/michael/projects/repos/openreel-video` | 已 clone | MIT；约 3.9k stars，2026-07-09 仍活跃 |
| react-video-editor | https://github.com/openvideodev/react-video-editor | `/Users/michael/projects/repos/react-video-editor` | 已通过 GitHub tarball 下载 | Other / 双许可；约 1.7k stars，2026-07-09 仍活跃 |
| CutScript | https://github.com/DataAnts-AI/CutScript | `/Users/michael/projects/repos/CutScript` | 已通过 GitHub tarball 下载 | MIT；约 157 stars，2026-07-09 仍活跃 |
| VideoCraft | https://github.com/ruben3732/VideoCraft | `/Users/michael/projects/repos/VideoCraft` | 已 clone | 未识别许可证；约 5 stars，2026-06-26 更新 |
| flutter video_editor | https://github.com/LeGoffMael/video_editor | `/Users/michael/projects/repos/flutter-video-editor` | 已通过 GitHub tarball 下载 | MIT；约 494 stars，2026-07-07 更新 |
| pro_video_editor | https://github.com/hm21/pro_video_editor / https://pub.dev/packages/pro_video_editor | `/Users/michael/projects/repos/pro_video_editor` | GitHub clone 超时，未完成源码审读 | 仅作资料级补充 |
| OptiVideoEditor-for-android | https://github.com/jaiobs/OptiVideoEditor-for-android | `/Users/michael/projects/repos/OptiVideoEditor-for-android` | GitHub clone 超时，未完成源码审读 | 仅作资料级补充；项目偏旧 |

> 星标和更新时间是 2026-07-09 查询值，会随 GitHub 变化。许可证判断仅作工程筛选，不等同法律意见。

## 源码观察

### FreeCut

技术栈：React 19 + TypeScript + Vite + Zustand + WebGPU/WebCodecs/MediaBunny + onnxruntime-web + Transformers。

关键源码：

- `src/features/timeline/stores/transcript-ignore-store.ts`
- `src/features/timeline/stores/actions/edit/range-removal-actions.ts`
- `src/features/timeline/utils/transcript-search.ts`
- `src/features/media-library/transcription/*`
- `src/features/editor/agent/*`

源码里已经有“非破坏性转写编辑缓冲区”：用户先把 transcript 范围标为 ignored，确认后 `commit()` 会调用 `removeTranscriptRangesFromItems()`，再把 source-native seconds 范围映射为时间线 split / ripple removal，并作为一次可撤销操作进入时间线。这非常适合接入 AI：AI 不直接改时间线，而是生成候选删除/保留范围，用户确认后提交。

优点：

- 完整 Web 视频编辑器形态，多轨时间线、导入、预览、导出、AI 分析、字幕/转写相关模块都在。
- 本地优先，README 明确项目、媒体、转写、缓存等留在本地 workspace。
- 转写类型支持 word-level：`TranscriptWord { text, start, end, confidence }`。
- 已有 silence removal、filler word removal、transcript selection removal 三套相似的范围删除入口，AI 剪辑可以复用。

风险：

- 纯浏览器路线对长视频、移动浏览器、复杂编码格式会受 WebCodecs/WebGPU 支持限制。
- 代码体量大，二开前需要先跑通开发环境和导出链路。
- Android/iOS 不是主场；移动端更像响应式访问，不是原生剪辑 App。

适合度：**Web 端第一推荐**。

### CutScript

技术栈：Electron + React/Vite + Zustand + Python FastAPI + WhisperX + FFmpeg。

关键源码：

- `frontend/src/types/project.ts`
- `frontend/src/store/editorStore.ts`
- `frontend/src/components/TranscriptEditor.tsx`
- `backend/services/video_editor.py`
- `backend/services/transcription.py`

它的数据结构与目标高度吻合：`Word`、`Segment`、`DeletedRange`、`selectedWordIndices`、`getKeepSegments()`。前端支持点击/拖拽/Shift 选择词，删除选中词，恢复删除范围；后端用 FFmpeg stream copy 或 re-encode 导出 keep segments。

优点：

- 交互模型几乎就是“按字幕改视频”。
- Electron 本地应用，FFmpeg/WhisperX 可直接用本机能力，长视频和编码格式比纯 Web 稳。
- 代码小，适合快速二开出 AI 字幕剪辑 MVP。
- MIT，低星但目标非常准确。

风险：

- 不是完整多轨 NLE，时间线能力弱于 FreeCut/Clypra/OpenReel。
- Python 后端 + Electron 打包、跨平台发布需要投入。
- 手机端不适合直接复用。

适合度：**桌面端文本剪辑 MVP 第一推荐；也是所有项目里最值得借鉴的字幕交互参考**。

### Clypra

技术栈：Tauri v2 + React 19 + Rust + FFmpeg。

关键源码：

- `src/core/timeline/items.ts`
- `src/core/timeline/adapter.ts`
- `src/features/subtitles/parser.ts`
- `src-tauri/src/commands/export.rs`
- `src-tauri/src/commands/whisper.rs`

Clypra 的优势在本地原生处理：Rust/Tauri 后端负责 FFmpeg 导出，源码里有进度、取消、音频片段、编码等处理；前端已有 timeline item model 和 SRT/VTT parser。它适合做桌面端剪辑底座，但要实现你的需求，需要新增“字幕/转写 -> source time ranges -> timeline cuts”的完整产品层。

优点：

- 桌面端基础较强，Rust FFmpeg 后端比浏览器更可控。
- README 标注目标覆盖 macOS/Windows/Linux，并通过 Capacitor 指向 Android/iOS。
- 有字幕解析、Whisper 模型下载相关代码。

风险：

- 移动端是 Capacitor 路线，源码层面不能证明移动剪辑体验已经成熟。
- 时间线 adapter 仍有迁移痕迹，部分结构可能还在重构。
- 字幕驱动剪辑需要自己做。

适合度：**桌面端完整产品第二推荐；跨端候选，但移动端要先做真机验证**。

### OpenReel

技术栈：monorepo，核心包 `@openreel/core`，WebCodecs/WebGPU/MediaBunny，Web app + Android/iOS 目录。

关键源码：

- `packages/core/src/timeline/clip-manager.ts`
- `packages/core/src/timeline/auto-edit-service.ts`
- `packages/core/src/export/export-engine.ts`
- `apps/web/src/stores/project/subtitle-helpers.ts`

OpenReel 的时间线和导出核心比普通 demo 更完整：clip manager 有吸附、非重叠、历史动作，export engine 走 WebCodecs/MediaBunny，subtitle helper 可解析/生成 SRT。`auto-edit-service` 是基于 beat analysis 生成剪辑，不是字幕剪辑，但可以作为 AI edit plan 的设计参考。

优点：

- Web 导出和 timeline core 相对完整。
- 有字幕数据入口。
- Android/iOS 目录存在，至少说明有移动端工程探索。

风险：

- 字幕驱动剪辑需要新建。
- 源码中 `Openreel Video Android/local.properties` 出现疑似发布签名密码字段；二开前必须从历史和本地配置中清理，不能直接复用移动端目录。
- 移动端成熟度未验证。

适合度：**Web 端备选；安全清理后再考虑移动端**。

### react-video-editor

技术栈：Next.js 15 + Zustand + PixiJS/OpenVideo engine + `@openvideo/core` / `@openvideo/timeline`。

关键源码：

- `src/components/editor/timeline/items/caption.ts`
- `src/lib/transcribe/types.ts`
- `src/lib/transcribe/deepgram-to-combo.ts`
- `src/app/api/transcribe/route.ts`

这个项目更像 OpenVideo 引擎的展示和 SaaS 起步套件。它已经有 word/sentence/paragraph transcript 类型，也有 caption item，但转写依赖 Deepgram，资产上传依赖 R2/S3 这类云服务配置。

优点：

- UI 起步快，结构现代，适合做 Web SaaS。
- Transcript 类型细，包括 words、sentences、paragraphs。
- 基于引擎包，视频渲染路径不用从零写。

风险：

- 不是标准 MIT，README/LICENSE 写的是双许可；大于 3 人的营利组织需要公司许可。
- 核心能力在 `@openvideo/*` 包中，二开深度受外部包约束。
- 字幕驱动确认流需要自己做。

适合度：**Web SaaS 备选；商业化前先确认许可**。

### VideoCraft

技术栈：原生 Android Kotlin，Media3/ExoPlayer，Room，部分 FFmpegKit 痕迹。

关键源码：

- `data/model/Models.kt`
- `ui/editor/timeline/TimelineView.kt`
- `ui/aiedit/AIEditViewModel.kt`
- `utils/FFmpegUtils.kt`

源码有不错的 Android 时间线 UI 参考：自定义 View 绘制视频/音频/文字/图片轨道、playhead、缩放、拖动等。但 `FFmpegUtils.kt` 明确显示 FFmpegKit 因兼容问题被移除，导出、裁剪、删除片段等大量操作是复制文件或空文件兜底；AI filler 检测也是 placeholder。

优点：

- 原生 Android UI 参考价值高。
- 数据模型里已有 `AIEditSuggestion`、`SilenceSegment`、`FillerWord` 等概念。

风险：

- 不具备可依赖的剪辑/导出核心。
- 低星、未识别许可证，生产风险高。
- iOS 不支持。

适合度：**Android UI 参考，不推荐作为主底座**。

### flutter video_editor

技术栈：Flutter/Dart package。

关键源码/文档：

- `README.md`
- `lib/src/controller.dart`
- `lib/src/export/ffmpeg_export_config.dart`

它是一个移动端单片段编辑组件，支持 trim/crop/rotate/scale/cover selection。README 明确说明库只提供导出命令和工具，不自己执行导出；应用需要接 `ffmpeg_kit_flutter`、自有服务端或别的执行方式。

优点：

- Android/iOS 支持明确：Android SDK 16+，iOS 11+。
- UI 组件成熟，适合嵌入 Flutter App。
- MIT。

风险：

- 不是多轨时间线。
- 没有字幕/转写/AI 剪辑流程。
- Web 支持仍在进行中，不应作为 Web 底座。

适合度：**移动端轻量剪辑组件；适合做移动 MVP 的局部能力**。

### OpenCut

技术栈：当前 rewrite 源码主要是 Vite + React 19 + TanStack Router + UI 组件。

关键源码：

- `apps/web/src/components/ui/*`
- `apps/web/src/routes/*`
- `apps/web/src/hooks/use-mobile.ts`

README 里写了未来的一套愿景：desktop/mobile/browser one codebase、Rust engine、plugin system、headless API、MCP server 等。但当前源码没有看到可复用的 timeline/editor/export core，主要是 UI 组件和基础路由。

优点：

- 社区热度极高，值得观察。
- 项目愿景与跨端/AI 很一致。

风险：

- 当前 rewrite 不是可用底座。
- 如果要现在二开，需要回到 classic 分支或另找底座。

适合度：**暂不推荐当前 rewrite 作为底座**。

## 平台方案

### Web 端

| 推荐 | 项目 | 方案 |
|------|------|------|
| 首选 | FreeCut | 用现有 transcript ignore buffer 承接 AI 预剪辑；AI 输出删除/保留 ranges，用户确认后 commit 到时间线。 |
| 备选 | OpenReel | 复用 timeline/export/subtitle helper，新增 subtitle-to-cut-plan 和人工确认 UI。 |
| 备选 | react-video-editor | 如果你想做 Web SaaS，并能接受双许可和 Deepgram/R2 依赖，可快速起步。 |
| 观察 | OpenCut | 等 rewrite 的 editor core 落地后再评估。 |

### 桌面端

| 推荐 | 项目 | 方案 |
|------|------|------|
| 首选 MVP | CutScript | 直接围绕文本剪辑做 AI 自动粗剪；最快验证“字幕勾选 -> 预览 -> 导出”。 |
| 首选完整产品 | Clypra | 以 Tauri/Rust FFmpeg 为主，补 transcript selection store 和 edit plan commit 逻辑。 |
| 备选 | FreeCut + 桌面壳 | 如果接受浏览器能力限制，可考虑封装为桌面壳，但不如 Clypra 原生稳定。 |

### 手机端，尤其 Android

| 推荐 | 项目 | 方案 |
|------|------|------|
| 轻量可行 | flutter video_editor | 做单视频裁剪/裁切/封面选择；AI 字幕剪辑和最终合成放后端或桌面端。 |
| 跨端实验 | Clypra | Capacitor 目标覆盖 Android/iOS，但必须先真机验证导入、预览、导出、权限和性能。 |
| UI 参考 | VideoCraft | 参考 Android 时间线 View，不建议复用导出/AI 核心。 |
| 资料级补充 | OptiVideoEditor-for-android | 网络未成功下载源码；公开资料显示偏旧，除非后续源码审读确认，否则不进入主推荐。 |
| 资料级补充 | pro_video_editor | 网络未成功下载源码；看起来是 Flutter 组件，后续可单独补审。 |

## 面向 AI 剪辑的二开设计预判

建议不要让 AI 直接改时间线。更稳的设计是：

1. 导入视频和字幕文件，字幕统一转成 `words / sentences / paragraphs` 三层结构。
2. AI 根据字幕生成 `EditPlan`，每条建议包含 `mediaId`、`sourceStart`、`sourceEnd`、`action`、`reason`、`confidence`。
3. UI 在 transcript 面板展示 AI 建议，用户可以按词、句、段勾选、取消或恢复。
4. 预览阶段只使用 staged ranges，不破坏时间线。
5. 用户确认后，再把 staged ranges 转为时间线 split/ripple/remove 操作。
6. 导出阶段读取真实时间线，而不是重新信任 AI 输出。

这套设计和 FreeCut 现有源码最贴合；CutScript 的 `DeletedRange` / `getKeepSegments()` 则适合做最小 MVP。

建议的 `EditPlan` 数据形状：

```ts
type EditPlanRange = {
  mediaId: string
  sourceStart: number
  sourceEnd: number
  level: 'word' | 'sentence' | 'paragraph'
  action: 'remove' | 'keep' | 'highlight'
  text: string
  reason?: string
  confidence?: number
}
```

如果字幕只有 SRT/VTT 的句级时间戳，先支持句子/段落勾选即可；有 word-level timestamps 时再开放词级勾选。

## 下一步建议

1. 先试跑 FreeCut：确认本机能启动、导入视频、生成/导入 transcript、导出一个短片段。
2. 同时试跑 CutScript：确认 WhisperX/FFmpeg 环境和文本剪辑导出链路。
3. 如果桌面完整产品优先，再试跑 Clypra 的导入、预览、导出。
4. Android 方向先做技术 spike：Flutter `video_editor` + 一个 `EditPlan` JSON + 服务端/桌面端渲染，不要一开始押原生 Android 完整剪辑。

## 来源

- OpenCut: https://github.com/OpenCut-app/OpenCut
- FreeCut: https://github.com/walterlow/freecut
- Clypra: https://github.com/AIEraDev/Clypra
- OpenReel: https://github.com/Augani/openreel-video
- OpenVideo react-video-editor: https://github.com/openvideodev/react-video-editor
- CutScript: https://github.com/DataAnts-AI/CutScript
- VideoCraft: https://github.com/ruben3732/VideoCraft
- Flutter video_editor: https://github.com/LeGoffMael/video_editor
- pro_video_editor: https://github.com/hm21/pro_video_editor / https://pub.dev/packages/pro_video_editor
- OptiVideoEditor-for-android: https://github.com/jaiobs/OptiVideoEditor-for-android
