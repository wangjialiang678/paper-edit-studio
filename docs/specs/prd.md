---
title: AI 视频自动剪辑工具 PRD
date: 2026-05-27
status: draft
audience: both
---

# AI 视频自动剪辑工具 PRD

> **实现状态（2026-07-12）**：Mac MVP 已落地为本地网页应用 Paper Edit Studio（`src/cutpoint_lab/studio/` + `scripts/studio_web.py`），覆盖 §4.1 全流程与 §4.2 主题切片；技术栈与 §9.1 的差异：UI 采用本地 Web（Python stdlib HTTP + 原生 JS）而非 SwiftUI，LLM 采用 DashScope qwen（OpenAI 兼容接口，可切换 Gemini）而非固定 Gemini；额外实现了 PRD 之外的"金句混剪"乱序成片（金句前置/重复强调）。数据模型与"AI 只能引用已有 segment_id"约束按本 PRD 执行。移动端迁移时 DomainCore/AI 协议的分层原则不变。

## 1. 背景与动机

当前口播视频剪辑流程的核心判断不是“这一帧要不要留”，而是“这句话要不要留”。传统时间轴剪辑需要反复拖动、试听和定位，对口播、访谈、演讲这类以语言为主的视频效率不高。

本项目要做的是一个本地优先的 AI 辅助剪辑工具：视频导入后生成带时间戳字幕，AI 先做内容筛选和文案优化建议，默认勾选建议保留片段；用户再基于字幕复核；最后系统按字幕时间线导出剪辑后的视频。

第一阶段目标是 Mac 桌面 MVP。未来移动端迁移是强约束，所以第一版需要把字幕模型、AI 协议、剪辑计划和平台 UI/导出实现分开。

## 2. 目标用户

MVP 阶段目标用户是项目作者本人。未来目标用户是口播、自媒体、知识内容、访谈和长演讲内容创作者。

## 3. 产品原则

1. 字幕是视频索引，不是普通文本。
2. AI 只做建议，用户拥有最终选择权。
3. AI 不能生成或修改时间戳，只能选择已有 `segment_id`。
4. 内部数据模型优先使用结构化 JSON，SRT/VTT 只作为导入导出格式。
5. 剪辑过程非破坏性，不修改原始视频。
6. Mac MVP 的业务核心要能迁移到 iOS/Android。

## 4. MVP 用户流程

### 4.1 普通口播模式

1. 用户导入一个本地视频。
2. App 提取音频。
3. App 调用 `audio-asr` 生成带时间戳字幕。
4. App 将 ASR 输出标准化为 `TranscriptSegment`。
5. Gemini 清洗字幕文本、评分、标注建议保留片段。
6. 字幕列表中完整展示所有片段，AI 建议保留的片段默认勾选。
7. 用户播放视频，点击字幕跳转到对应时间。
8. 用户手动调整保留/删除状态。
9. App 将勾选结果转换成 `ClipPlan`。
10. App 导出剪辑后的视频。

### 4.2 长演讲模式

触发条件暂定为视频超过 10 分钟。

1. 用户导入长视频。
2. App 生成完整字幕索引。
3. Gemini 按句群或时间窗口识别主题段落。
4. App 以主题分组展示字幕。
5. Gemini 标注每个主题下的金句、重点片段和可传播短片段。
6. 用户按主题或片段确认保留内容。
7. App 导出所选主题或片段组成的视频。

## 5. MVP 功能范围

### 5.1 P0 必须有

- 导入单个本地 MP4/MOV 视频。
- 使用 `ffmpeg` 或平台能力提取音频。
- 调用已有 `audio-asr` 生成句级时间戳字幕。
- 展示完整字幕片段列表。
- Gemini 生成结构化 AI 建议。
- AI 建议保留片段默认勾选。
- 用户可手动切换每个片段的保留/删除状态。
- 点击字幕可跳转视频预览时间点。
- 根据保留片段生成 `ClipPlan`。
- 导出剪辑后的视频文件。

### 5.2 P1 很重要

- 长演讲主题切片模式。
- 每个片段展示 AI 理由、标签和可选改写文案。
- 保存项目文件，支持下次继续编辑。
- 导出字幕文件和剪辑决策 JSON。
- 片段前后自动增加少量缓冲，减少生硬切口。

### 5.3 P2 后续优化

- 半句话或词级剪辑。
- 自动识别停顿、口癖、重复表达。
- FCPXML / OTIO 导出，方便进入 Final Cut Pro / Premiere 继续精剪。
- 批量处理多个视频。
- 移动端版本。

## 6. 非目标

MVP 暂不做：

- 多轨视频编辑。
- 复杂字幕样式。
- 配乐、音效、贴纸、转场。
- 封面生成。
- 自动发布到平台。
- 多人协作。
- 完全自动剪完且不需要人工复核。

## 7. 核心数据模型

### 7.1 TranscriptSegment

`TranscriptSegment` 是内部主模型，来自 ASR 输出。

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

### 7.2 EditDecision

`EditDecision` 记录 AI 或用户对片段的选择。

```json
{
  "segment_id": "seg_000123",
  "action": "keep",
  "source": "ai",
  "user_modified_at": null
}
```

### 7.3 ClipPlan

`ClipPlan` 是导出模块的输入，表示最终保留的视频时间段。

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

## 8. AI 要求

第一版使用 Gemini API，目标模型为 `gemini-3.5-flash`。

AI 输出必须使用 structured output / JSON schema 约束。AI 可以输出：

- `segment_id`
- `clean_text`
- `rewrite`
- `score`
- `keep_suggestion`
- `topic_id`
- `labels`
- `reason`
- 长演讲模式下的 `TopicSlice`

AI 不允许输出：

- 新的时间戳。
- 修改后的 `start_ms` / `end_ms`。
- 不存在的 `segment_id`。
- 直接不可逆修改原视频的指令。

## 9. 技术约束与建议

### 9.1 Mac MVP

- UI：SwiftUI。
- 视频预览：AVPlayer / AVKit。
- 精确导出：优先 AVFoundation。
- 音频提取和媒体探测：FFmpeg / ffprobe CLI。
- FFmpeg 集成方式：MVP 可先要求本机安装 Homebrew ffmpeg；正式分发前再决策是否 bundled。

### 9.2 未来移动端

- iOS：复用 Swift 数据模型和 AI 协议，视频导出优先 AVFoundation。
- Android：复用 JSON Schema 和 AI 协议，视频导出优先 Media3 Transformer。
- 不建议把 FFmpegKit 作为长期移动端核心依赖，因为 FFmpegKit 已退休。

## 10. 模块拆分

1. `DomainCore`：字幕段、AI 建议、勾选状态、主题切片、ClipPlan、时间线合并与校验。
2. `AIClient`：Gemini 请求、structured output schema、响应解析和失败处理。
3. `ASRAdapter`：封装已有 `audio-asr`。
4. `FFmpegCLI`：封装 ffmpeg/ffprobe 路径、命令、日志和错误。
5. `MediaAdapter`：定义 `VideoExporter` 协议。
6. `AVFoundationExporter`：Mac/iOS 默认导出实现。
7. `MacApp`：SwiftUI UI、文件选择、视频预览和字幕勾选。

## 11. MVP 验收标准

1. 给定一个 3-10 分钟口播视频，可以完成导入、ASR、AI 建议、人工勾选、导出视频。
2. AI 建议默认勾选能被用户覆盖。
3. 导出的成片只包含用户保留的字幕片段对应时间段。
4. 原始视频不被修改。
5. 每个导出片段都能追溯到原始 `segment_id`。
6. Gemini 返回结构校验失败时，App 能提示错误而不是静默剪错。
7. 至少用 2-3 个真实样片验证导出时间线没有明显错位。

## 12. 待确认问题

1. `audio-asr` 的实际输出格式、调用方式，以及是否支持词级时间戳。
2. Gemini API Key 的本地配置方式：环境变量、Keychain，还是 App 设置页。
3. MVP 是否必须保存项目文件；推荐保存。
4. 第一版是否需要同时支持导出 SRT/VTT 和剪辑决策 JSON；推荐支持 JSON。
5. 第一版默认导出是否以 AVFoundation 重新编码为主；推荐先这样做，稳定优先。

## 13. 调研链接

产品整体调研见 `docs/research/2026-05-27-transcript-based-video-editing-mvp.md`；语音切分算法的当前结论、实验和后续路线见 `docs/speech-cutting/README.md`。
