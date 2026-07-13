---
title: 字幕时间线驱动的视频剪辑 MVP 调研
date: 2026-05-27
status: active
audience: both
---

# 字幕时间线驱动的视频剪辑 MVP 调研

## 1. 调研问题

本次调研围绕一个 Mac 本地 MVP：导入口播或演讲视频后，先生成带时间戳字幕，再让 AI 默认勾选建议保留片段，用户基于字幕复核，最后按字幕时间线导出剪辑视频。长期目标是迁移到 iOS 和 Android，所以需要避免把业务逻辑和 Mac 桌面 UI 绑死。

核心问题：

1. 是否有成熟的开源工具可以复用字幕时间线驱动剪辑。
2. Mac MVP 应该如何处理视频预览、音频提取和视频导出。
3. AI 应该如何参与字幕清洗、片段筛选和长演讲主题切片。
4. 哪些模块未来能在 iOS/Android 复用。

## 2. 核心结论

1. “编辑字幕/转写文本来编辑视频”已经是成熟产品范式。Premiere、Descript、CapCut 都采用类似思路：文本带时间码，用户改文本，系统同步改媒体时间线。
2. 没有发现一个可直接嵌入 Swift Mac App、同时满足 AI 筛选、字幕勾选、视频导出、未来移动端复用的完整开源组件。
3. MVP 不应把 SRT 当内部唯一数据源。推荐内部使用 `TranscriptSegment JSON`，SRT/VTT 只作为导入导出格式。
4. AI 不应该生成或修改时间戳。AI 只选择已有 `segment_id`、给分、标注主题、生成改写建议；真实剪辑时间线由程序从 ASR 原始索引计算。
5. Mac MVP 推荐 `SwiftUI + AVFoundation 主视频引擎 + FFmpeg CLI 辅助`。FFmpeg 先用于音频提取、ffprobe 和备用导出；视频预览与精确导出优先走 AVFoundation，为 iOS 迁移铺路。
6. 移动端不建议把 FFmpegKit 作为长期核心依赖。FFmpegKit 已退休；iOS 优先 AVFoundation，Android 优先 Media3 Transformer。

## 3. 产品范式参考

| 产品/方案 | 可借鉴点 | 来源 |
|-----------|----------|------|
| Adobe Premiere Text-Based Editing | 转写文本包含 timecode metadata，选择/重排文本会同步修剪 timeline | https://helpx.adobe.com/premiere/desktop/edit-projects/edit-video-using-text-based-editing/overview-of-text-based-editing.html |
| Descript | 像编辑文档一样编辑音视频；删除文字会隐藏对应媒体；区分删除、忽略、仅从转写中移除 | https://help.descript.com/hc/en-us/articles/15726742913933-Edit-like-a-doc |
| CapCut transcript editing | 从视频生成 transcript，基于文本编辑、识别 filler words 和 speech gaps | https://www.capcut.com/tools/video-transcript-editing |
| AVScript / ScriptBlade 类工具 | 面向长采访和纪录片的 paper edit：从 SRT/FCPXML 选择文本，导出 FCPXML rough cut | https://www.avscript.tv/ |

对本项目的启发：第一版不需要做完整 NLE，只要把“字幕作为视频索引”这件事做稳。交互上默认展示完整字幕，AI 预选，用户改勾选，导出时再生成剪辑计划。

## 4. 开源与可复用组件

| 项目/方案 | 用途 | 许可证/活跃度 | 适用性 | 风险 |
|-----------|------|---------------|--------|------|
| Auto-Editor | 自动剪静音，支持 `--cut-out` / `--add-in`，可导出 Premiere、Resolve、Final Cut Pro、ShotCut、Kdenlive | Unlicense；约 4.3k stars；2026-05 仍活跃 | 可作为命令行导出引擎备选，也适合借鉴 timeline JSON 和按时间段导出 | 不是字幕勾选 UI；Nim 生态；半句剪辑仍依赖外部时间戳 |
| LosslessCut | 跨平台 FFmpeg GUI，无损切段、合并、segments 管理、项目文件、命令日志 | GPL-2.0；约 40.7k stars；2026-05 仍活跃 | 借鉴 segments 模型、无损/智能切、FFmpeg 命令构造 | GPL 不适合直接嵌入未来商业产品；无损切点受关键帧限制 |
| OpenTimelineIO | 编辑时间线中间格式，支持 FCPXML、AAF、CMX 3600 EDL 等适配器 | Apache-2.0；约 1.9k stars；影视行业项目 | 适合作为未来专业剪辑软件互通层 | MVP 导出 MP4 不依赖它；Swift 直接集成成本较高 |
| Subtitle Edit | 字幕编辑、波形、视频预览、格式转换、Whisper STT | MIT；约 13k stars；2026-05 活跃 | 借鉴字幕编辑体验和格式处理 | C#/.NET 生态，直接集成成本高 |
| ButterCut | Claude Code + WhisperX + Ruby 生成 FCPXML/xmeml rough cut | PolyForm Noncommercial + 商业输出例外；约 500 stars | 借鉴 AI 先选片、YAML roughcut、FCPXML 输出 | 许可证不适合直接商品化复用；依赖 Claude Code 工作流 |

推荐策略：

1. MVP 直接使用自己的 `TranscriptSegment` / `ClipPlan` 数据模型。
2. 视频处理先用 AVFoundation 和 FFmpeg CLI，而不是嵌入大型视频编辑器。
3. Auto-Editor、LosslessCut、ButterCut、Subtitle Edit 主要用于借鉴，不作为直接依赖。
4. 后续需要导出到 Final Cut Pro / Premiere 时，再引入 FCPXML 或 OpenTimelineIO。

## 5. 推荐内部数据模型

### 5.1 TranscriptSegment

```json
{
  "id": "seg_000123",
  "start_ms": 12340,
  "end_ms": 15620,
  "raw_text": "原始 ASR 文本",
  "clean_text": "清洗后文本",
  "rewrite": "适合口播传播的改写",
  "score": 0.86,
  "keep_suggestion": true,
  "topic_id": "topic_03",
  "labels": ["hook", "insight", "golden_quote"],
  "reason": "信息密度高，适合作为短视频片段",
  "source_locked": true
}
```

关键约束：

- `id/start_ms/end_ms/raw_text` 来自 ASR，生成后冻结。
- AI 可以写 `clean_text/rewrite/score/labels/reason`。
- AI 不能创建新时间戳，也不能把时间戳改漂。
- UI 勾选的是 `segment_id`，导出时再汇总成连续保留区间。

### 5.2 TopicSlice

```json
{
  "topic_id": "topic_03",
  "title": "为什么字幕时间线比视频时间线更适合口播粗剪",
  "start_segment_id": "seg_000120",
  "end_segment_id": "seg_000168",
  "summary": "这一段说明文本编辑比传统时间轴更符合口播内容判断方式。",
  "highlight_segment_ids": ["seg_000123", "seg_000141"]
}
```

### 5.3 EditDecision

```json
{
  "segment_id": "seg_000123",
  "action": "keep",
  "source": "ai",
  "user_modified_at": null
}
```

### 5.4 ClipPlan

```json
{
  "source_video": "/path/to/source.mov",
  "ranges": [
    {
      "start_ms": 12340,
      "end_ms": 23890,
      "source_segment_ids": ["seg_000123", "seg_000124"]
    }
  ]
}
```

`ClipPlan` 是导出模块的唯一输入。Mac、iOS、Android 的导出器都可以实现同一份 `ClipPlan`。

## 6. AI Pipeline 建议

### 6.1 普通口播模式

1. 视频导入后，用 `ffmpeg` 提取音频。
2. 调用 `audio-asr`，得到句级时间戳。
3. 生成稳定的 `TranscriptSegment`。
4. Gemini 做文本清洗和片段评分。
5. Gemini 返回结构化 JSON：每个 segment 的保留建议、理由、标签、改写。
6. UI 默认勾选 `keep_suggestion=true` 的片段。
7. 用户复核后生成 `EditDecision`。
8. 程序合并连续保留片段，生成 `ClipPlan`。
9. 视频导出器根据 `ClipPlan` 输出视频。

### 6.2 长演讲模式

触发条件可以先设为视频时长超过 10 分钟。

1. 先按 2-5 分钟窗口或自然段落分块。
2. 对每块做简短摘要、主题标签和高价值片段评分。
3. 汇总成 `TopicSlice` 列表。
4. 每个主题内提取金句和可传播片段。
5. UI 按主题分组展示字幕片段，用户可以按主题批量保留或删除。

长演讲模式不建议第一版直接做“全自动剪完”。更稳的做法是 AI 先做结构化筛选，人再确认。

## 7. 视频导出策略

| 策略 | 优点 | 缺点 | MVP 建议 |
|------|------|------|----------|
| AVFoundation 精确导出 | Apple 平台原生；有利于 iOS 迁移；可精确组合时间段 | 通常需要重新编码；需要实测不同素材兼容性 | 默认路径 |
| FFmpeg stream copy / concat | 快、近似无损、适合粗剪 | 非关键帧切点不一定精确，可能有音画同步或额外帧问题 | 作为快速导出或备用路径 |
| FFmpeg 重新编码 | 切点更准确，跨平台 CLI 能力强 | 慢；分发时涉及二进制、签名、许可证 | MVP 可作为 fallback |
| FCPXML / OTIO 导出 | 方便进 Final Cut / Premiere 继续精剪 | 不直接生成最终 MP4；格式细节多 | v2 预留 |

推荐：MVP 默认用 AVFoundation 生成最终视频；FFmpeg 先负责音频提取和媒体探测。如果 AVFoundation 导出遇到格式问题，再加入 FFmpeg fallback。

## 8. 跨端复用边界

| 模块 | Mac 实现 | iOS 实现 | Android 实现 | 复用程度 |
|------|----------|----------|--------------|----------|
| 字幕/ASR 数据模型 | Swift struct | Swift struct | Kotlin data class | 高，靠 JSON Schema 复用 |
| AI 筛选协议 | Gemini structured output | 同协议 | 同协议 | 高 |
| 勾选/剪辑计划 | EditDecision / ClipPlan | 同结构 | 同结构 | 高 |
| 主题切片结果 | TopicSlice / Highlight | 同结构 | 同结构 | 高 |
| 视频预览 | AVPlayer / AVKit | AVPlayer | ExoPlayer / Media3 | 低 |
| 视频导出 | AVFoundation + FFmpeg fallback | AVFoundation | Media3 Transformer | 中，接口复用，实现分平台 |
| 文件权限 | macOS sandbox/bookmark | iOS sandbox / Photos | SAF / MediaStore | 低 |
| UI | SwiftUI macOS | SwiftUI iOS | Jetpack Compose | 低 |

## 9. 推荐 MVP 架构

建议使用 Swift Package 拆出纯业务核心，再由 Mac App 引用：

1. `DomainCore`：字幕段、AI 建议、勾选状态、ClipPlan、时间线合并与校验。
2. `AIClient`：Gemini 请求、structured output schema、响应校验。
3. `ASRAdapter`：封装现有 `audio-asr`。
4. `MediaAdapter`：定义 `VideoExporter` 协议。
5. `AVFoundationExporter`：默认导出实现。
6. `FFmpegCLI`：封装 ffmpeg/ffprobe 路径、命令、日志、错误。
7. `MacApp`：SwiftUI 文件选择、视频预览、字幕列表、勾选交互。

## 10. 风险与待验证

1. `audio-asr` 的输出格式需要确认。句级时间戳可以跑通 MVP；半句话级剪辑需要词级时间戳或 forced alignment。
2. Gemini 输出必须做 schema 校验。`segment_id` 必须存在，时间戳只能从源数据复制。
3. 长演讲主题切片需要中文样本实测。通用 topic API 对中文内容未必稳定，使用 Gemini 自建流程更现实。
4. AVFoundation 对实际拍摄素材的兼容性需要用样片验证。
5. 如果未来公开分发，FFmpeg 的二进制打包、App Sandbox、notarization、LGPL/GPL 合规需要单独决策。

## 11. 参考来源

- Adobe Premiere Text-Based Editing: https://helpx.adobe.com/premiere/desktop/edit-projects/edit-video-using-text-based-editing/overview-of-text-based-editing.html
- Descript Edit like a doc: https://help.descript.com/hc/en-us/articles/15726742913933-Edit-like-a-doc
- CapCut transcript editing: https://www.capcut.com/tools/video-transcript-editing
- Auto-Editor docs: https://auto-editor.com/docs/v3
- Auto-Editor PyPI: https://pypi.org/project/auto-editor/24.24.1/
- LosslessCut GitHub: https://github.com/mifi/lossless-cut
- OpenTimelineIO GitHub: https://github.com/AcademySoftwareFoundation/OpenTimelineIO
- OpenTimelineIO docs: https://opentimelineio.readthedocs.io/en/v0.15/
- Apple AVFoundation editing guide: https://developer.apple.com/library/archive/documentation/AudioVideo/Conceptual/AVFoundationPG/Articles/03_Editing.html
- Apple AVFoundation export/trim guide: https://developer.apple.com/library/archive/documentation/AudioVideo/Conceptual/AVFoundationPG/Articles/01_UsingAssets.html
- Android Media3 Transformer: https://developer.android.com/media/media3/transformer
- FFmpeg documentation: https://ffmpeg.org/ffmpeg.html
- FFmpeg FAQ concat demuxer: https://ffmpeg.org/faq.html
- FFmpegKit retirement notice: https://github.com/arthenica/ffmpeg-kit
- Gemini structured outputs: https://ai.google.dev/gemini-api/docs/structured-output
- Gemini 3.5 Flash model page: https://ai.google.dev/gemini-api/docs/models/gemini-3.5-flash
- AssemblyAI timestamped transcripts guide: https://www.assemblyai.com/docs/guides/timestamped-transcripts

