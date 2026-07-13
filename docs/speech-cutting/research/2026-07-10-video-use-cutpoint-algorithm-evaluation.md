---
title: video-use 切点算法与工程复用评估
date: 2026-07-10
status: active
audience: both
tags: [research, video-use, cutpoint, edl, rendering]
---

# video-use 切点算法与工程复用评估

## 1. 评估目标

评估开源项目 [browser-use/video-use](https://github.com/browser-use/video-use) 是否能解决本项目的核心问题：ASR 词时间戳漂移时，如何在保留词和删除词之间找到不吃字、不泄漏相邻内容、能处理多人/重叠/BGM 的安全切点。

本次审查使用本地仓库：

- 路径：`/Users/michael/projects/repos/tmp-20260710-video-use/video-use`
- commit：`92c2b34e44c205cbc2acae7f6ca7c1c219d5dd66`
- commit 日期：2026-07-01
- 许可证：MIT
- 官方仓库状态：截至 2026-07-10，约 1.63 万 stars，主分支与本地 commit 一致

评估范围包括 `SKILL.md`、`helpers/transcribe.py`、`pack_transcripts.py`、`timeline_view.py`、`render.py`、`grade.py`、README、官方 issue/PR，以及 ElevenLabs 官方 STT 与 Forced Alignment 文档。

## 2. 核心结论

**video-use 不能直接解决“ASR 词时间戳不准导致切字”的核心算法问题。**

它当前的边界策略本质上是：

```text
ElevenLabs Scribe 词级时间戳
        ↓
Agent 按语义选择内容并生成 EDL
        ↓
切点落在 Scribe 词边界，固定留白 30–200ms
        ↓
每个片段两端分别做 30ms 淡入/淡出
        ↓
Agent 查看渲染后 timeline 图片，必要时手工改 EDL
```

它没有独立 VAD、forced alignment、声学起止检测、speaker embedding、overlap detection、多人多标签时间线、语音增强或分离，也没有自动安全区间评分与 `no_safe_cut`。

最合理的复用方式是：

> 把 video-use 当成“Agent 选材 + EDL + 渲染 + 字幕 + 预览闭环”，把本项目的“强制对齐 + 独立 VAD + speaker/overlap + 安全区间”作为它与渲染器之间的确定性边界引擎。

## 3. 真实数据流

### 3.1 转写

`transcribe.py` 使用 FFmpeg 提取 16kHz 单声道 PCM，然后调用 ElevenLabs STT：

```text
model_id = scribe_v1
diarize = true
tag_audio_events = true
timestamps_granularity = word
```

源码见 [`helpers/transcribe.py:49-87`](https://github.com/browser-use/video-use/blob/92c2b34e44c205cbc2acae7f6ca7c1c219d5dd66/helpers/transcribe.py#L49-L87)。唯一的语音边界来源是 Scribe 返回的词级 `start/end`。

### 3.2 Phrase 压缩

`pack_transcripts.py` 遇到以下情况就结束一个 phrase：

- ASR token 间隙 ≥0.5 秒；
- `speaker_id` 发生变化。

输出 Markdown 只保留每个 phrase 的首尾时间，没有把每个词的时间写进 LLM 的主要阅读视图。[`helpers/pack_transcripts.py:38-160`](https://github.com/browser-use/video-use/blob/92c2b34e44c205cbc2acae7f6ca7c1c219d5dd66/helpers/pack_transcripts.py#L38-L160)

### 3.3 EDL 决策

Agent 阅读 phrase Markdown，根据语义、停顿和说话人标签生成 EDL。Skill 要求：

- 切点必须落在 Scribe 词边界；
- 每个切点留 30–200ms；
- 优先使用 ≥400ms 的停顿；
- 小于 150ms 的停顿视为不安全。

仓库同时明确承认 Scribe 时间戳可能漂移 50–100ms，处理方法仍是固定 padding。[`SKILL.md:27-29`](https://github.com/browser-use/video-use/blob/92c2b34e44c205cbc2acae7f6ca7c1c219d5dd66/SKILL.md#L27-L29)、[`SKILL.md:102-114`](https://github.com/browser-use/video-use/blob/92c2b34e44c205cbc2acae7f6ca7c1c219d5dd66/SKILL.md#L102-L114)

### 3.4 Timeline 可视化

`timeline_view.py` 生成：

- 均匀抽取的视频帧；
- 16kHz 混合音频的归一化 RMS 波形；
- Scribe 词标签；
- 由相邻 Scribe token 时间差计算的“静音”阴影。

RMS 只用于画图，不会自动改变切点；“静音”也不是独立 VAD 结果。[`helpers/timeline_view.py:68-148`](https://github.com/browser-use/video-use/blob/92c2b34e44c205cbc2acae7f6ca7c1c219d5dd66/helpers/timeline_view.py#L68-L148)、[`helpers/timeline_view.py:263-311`](https://github.com/browser-use/video-use/blob/92c2b34e44c205cbc2acae7f6ca7c1c219d5dd66/helpers/timeline_view.py#L263-L311)

### 3.5 渲染与自评估

`render.py` 完全信任 EDL 的浮点 `start/end`，不会再次校验是否切进词、是否越过邻句，也不会对齐到声学边界。[`helpers/render.py:214-261`](https://github.com/browser-use/video-use/blob/92c2b34e44c205cbc2acae7f6ca7c1c219d5dd66/helpers/render.py#L214-L261)

所谓 self-eval 是写在 Skill 中的 Agent 操作要求，不是一个自动评分算法：Agent 应在每个渲染后切点附近查看 timeline PNG，检查画面跳变、波形尖峰、字幕遮挡和 overlay 错位；最多三轮。[`SKILL.md:83-100`](https://github.com/browser-use/video-use/blob/92c2b34e44c205cbc2acae7f6ca7c1c219d5dd66/SKILL.md#L83-L100)

官方 issue 也确认“逐边界 self-eval”目前是 instruction-only，并且与 `timeline_view` 不应批量扫描的说明存在冲突。[Issue #64](https://github.com/browser-use/video-use/issues/64)

## 4. 能否解决本项目的问题

| 本项目需要的能力 | video-use 当前能力 | 判断 |
|---|---|---|
| 修正不准的 ASR 词边界 | 不修正；使用 Scribe 时间戳加 padding | ❌ 不能解决根因 |
| 独立判断是否有人声 | 没有 VAD；静音来自 ASR token gap | ❌ 循环证据 |
| 找到字/音素的真实起止 | 没有 forced alignment 或 CTC blank | ❌ |
| 不跨入未选相邻语音 | 依赖 Agent 遵守提示，renderer 无硬约束 | ❌ |
| 说话人交接 | Scribe 每个 token 有匿名 `speaker_id` | 🟡 可辅助粗分段 |
| 重叠说话 | 单 token 单 speaker，不能表达多标签 overlap | ❌ |
| 目标人物声纹 | 没有 enrollment、embedding 或 verification | ❌ |
| BGM/噪声中的人声边界 | 只有混合音频 RMS 图 | ❌ |
| 防止音频 click/pop | 每段两端 30ms fade | ✅ 有帮助 |
| 恢复被切掉的音素 | fade 无法恢复信息 | ❌ |
| EDL 与字幕输出时间映射 | 有完整实现 | ✅ 值得复用 |
| 预览—检查—重渲染闭环 | 有工作流约束 | ✅ 值得借鉴 |

结论：它擅长长停顿、多 take 选材、语义删减和口播粗剪；在短词间隙、连读、BGM、噪声和重叠语音下，没有比“ASR 时间戳 + 固定 padding”更强的安全边界算法。

## 5. 关键源码问题

### 5.1 高优先级

1. **边界证据仍然只有 ASR 时间戳。** 仓库承认 50–100ms 漂移，但没有局部重对齐，只用 30–200ms padding。[`SKILL.md:27-29`](https://github.com/browser-use/video-use/blob/92c2b34e44c205cbc2acae7f6ca7c1c219d5dd66/SKILL.md#L27-L29)
2. **“静音”是从相同 ASR token 计算的。** 它不能独立验证 ASR 是否切早或切晚。[`timeline_view.py:135-148`](https://github.com/browser-use/video-use/blob/92c2b34e44c205cbc2acae7f6ca7c1c219d5dd66/helpers/timeline_view.py#L135-L148)
3. **安全规则只存在于提示词。** Renderer 不校验词边界、padding、邻句、speaker 或 overlap，直接使用 EDL 时间。[`render.py:234-258`](https://github.com/browser-use/video-use/blob/92c2b34e44c205cbc2acae7f6ca7c1c219d5dd66/helpers/render.py#L234-L258)
4. **Self-eval 没有确定性检查器。** EDL timeline 模式明确未实现，输出视频的 transcript 也不会自动映射成新的输出时间线。[`timeline_view.py:333-379`](https://github.com/browser-use/video-use/blob/92c2b34e44c205cbc2acae7f6ca7c1c219d5dd66/helpers/timeline_view.py#L333-L379)
5. **Phrase 视图不足以支持 phrase 内精确删词。** LLM 主要输入只有 phrase 首尾；若要删除内部 filler，必须额外读取原始词 JSON，但主流程没有确定性工具完成该步骤。

### 5.2 中优先级

1. **30ms fade 不是 crossfade。** 每个片段先淡出到零，下一个片段再从零淡入；它能抑制爆音，但不能修复被截断的音素，还可能让连续 BGM/底噪出现能量凹陷。[`render.py:187-211`](https://github.com/browser-use/video-use/blob/92c2b34e44c205cbc2acae7f6ca7c1c219d5dd66/helpers/render.py#L187-L211)
2. **说话人信息是单标签。** 它能显示匿名 speaker change，但没有允许同帧多人活动的时间线和独立 overlap 区。
3. **缓存只检查同名 JSON 是否存在。** 同名源视频被替换后，可能继续使用旧时间戳。[`transcribe.py:90-123`](https://github.com/browser-use/video-use/blob/92c2b34e44c205cbc2acae7f6ca7c1c219d5dd66/helpers/transcribe.py#L90-L123)
4. **归一化 RMS 不能区分人声与音乐。** 每个观察窗口单独归一化，也不能跨样本比较真实噪声底。
5. **仓库没有自动测试目录或真实边界标注。** 没有吃字率、内容泄漏、BGM、多人、overlap 或渲染时序的回归测试。

### 5.3 渲染稳定性

当前主分支会把片段重编码为固定 24fps、AAC 48kHz，再进行 stream-copy concat。社区已有两个尚未合并的修复方向：

- [PR #62](https://github.com/browser-use/video-use/pull/62) 报告 37 段、103 秒成片末尾出现约 570ms 的渐进式音画漂移，建议按输出帧量化片段并使用 PCM 中间文件；
- [PR #31](https://github.com/browser-use/video-use/pull/31) 报告长视频、多切点场景下固定帧率/采样率和快速 seek 会积累漂移，建议保留源参数并做更精确 seek；
- [Issue #64](https://github.com/browser-use/video-use/issues/64) 指出固定 24fps 和 1080p 长边会导致画质、流畅度和上采样问题。

两个 PR 的修复方法并不完全相同，因此不能直接据此确定唯一实现；但它们足以说明当前 `render.py` 不应未经验证直接作为生产渲染内核。

## 6. ElevenLabs 能力与仓库实现之间的差距

### 6.1 Scribe 版本风险

仓库写死 `scribe_v1`。ElevenLabs 2026-06-08 的官方 changelog 宣布 Scribe v1 将于 2026-07-09 移除，并要求迁移到 v2；但当前 STT API reference 仍把 `scribe_v1` 和 `scribe_v2` 都列为 allowed values。该外部状态存在文档差异，因此工程上不能假设 v1 继续稳定可用，应迁移到 v2 并做真实请求验证。[ElevenLabs 2026-06-08 changelog](https://elevenlabs.io/docs/changelog/2026/6/8)、[STT API reference](https://elevenlabs.io/docs/api-reference/speech-to-text/convert)

### 6.2 仓库没有使用 Forced Alignment

ElevenLabs 另有独立 Forced Alignment API：输入音频和已有文字，输出字符/词时间及 `loss`；官方列出中文支持，但明确不支持 diarization。[Forced Alignment 概览](https://elevenlabs.io/docs/overview/capabilities/forced-alignment)、[API reference](https://elevenlabs.io/docs/api-reference/forced-alignment/create?explorer=true)

这项能力比普通 STT 词时间戳更贴近本项目问题，但 `video-use` 当前完全没有调用它。它值得进入 MFA/CTC 的 A/B 候选，不应在没有本项目人工边界测试前被假设为更准确。

## 7. 可复用与不应复用的部分

### 7.1 建议复用或借鉴

| 部分 | 复用方式 |
|---|---|
| Agent 语义选材 | 借鉴“自然语言意图 → 选择内容”的 Skill 工作流 |
| EDL `sources/ranges` | 借鉴为内容选择层和渲染层之间的协议 |
| 转写缓存 | 保留思想，但增加源文件 hash/mtime/provider/version |
| Phrase 压缩 | 用于 LLM 低 token 浏览，不作为边界真相源 |
| Timeline 可视化 | 作为人工诊断和盲听标注辅助工具 |
| 字幕输出时间映射 | 复用 `word.start - segment_start + output_offset` 思路 |
| Preview/self-eval | 借鉴“渲染后再检查”的闭环，但增加自动边界验证 |
| ElevenLabs Scribe v2 | 作为 FunASR 的可选 ASR 对照 provider |
| ElevenLabs Forced Alignment | 作为 MFA/CTC 的一个对齐挑战者 |

### 7.2 不建议直接复用

- 固定 30–200ms padding 作为精确切点算法；
- 从 ASR token gap 推导独立“静音”；
- 把 30ms fade 当作防吃字机制；
- 只看 timeline PNG 判断轻辅音、爆破音、词尾是否被截断；
- 完全信任 Agent 生成的 EDL 浮点时间；
- 未处理社区漂移问题的当前渲染实现；
- “手工边界评分函数都是过度工程”的主张：审美排序可以交给 LLM，但禁止切进语音属于确定性安全约束。

## 8. 推荐组合架构

```text
video-use 式 Agent 内容规划
          ↓
选择要保留/删除的词句
          ↓
本项目 SafeBoundaryEngine
  - ASR 粗锚点
  - Forced Alignment
  - 独立 VAD
  - speaker activity / overlap
  - 邻接内容硬约束
  - safe interval / no_safe_cut
          ↓
EDL Validator
  - 所有时间必须来自 BoundaryDecision
  - 禁止越过未选内容
  - 禁止 overlap 内切分
  - 校验媒体范围和时间轴
          ↓
经过时序修复的 Renderer
          ↓
字幕 + preview + 自动检查 + 人工盲听
```

建议把 video-use 的 EDL 扩展为：

```json
{
  "start": 2.42,
  "end": 6.85,
  "boundary_decision_ids": ["bd-start-001", "bd-end-001"],
  "boundary_status": "safe",
  "fallback": null
}
```

Renderer 只消费已经通过安全校验的 EDL，不负责重新猜测边界。

## 9. 最小对照实验

复用现有 24 个边界样本，增加两条 ElevenLabs 路线：

```text
A  当前 FunASR + 固定 padding
B  video-use 原始思路：Scribe v2 + 30–200ms padding
C  Scribe v2 文本 + ElevenLabs Forced Alignment + 独立 VAD
D  推荐主线：现有 ASR + MFA/CTC + 独立 VAD + speaker/overlap guards
```

统一比较：

- 保留语音截断；
- 未选内容泄漏；
- 下一说话人泄漏；
- overlap 内切点；
- 安全区间命中；
- 拒绝切分率；
- 盲听自然度；
- API 成本、运行时间和失败率。

只有 C 在同一批人工边界上稳定优于现有对齐器时，才考虑把 ElevenLabs Forced Alignment 作为默认或备用 provider。

## 10. 假设与复杂度审查

| 假设 | 状态 | 依据 |
|---|---|---|
| Scribe 词时间戳足以成为最终切点 | 未验证，仓库自己承认漂移 | `SKILL.md:27-29` |
| 30–200ms padding 能吸收所有漂移 | 不成立于连续语音和邻句过近场景 | 没有真实边界测试 |
| ASR token gap 等于真实静音 | 未验证且循环依赖 | `timeline_view.py:135-148` |
| 30ms fade 能避免吃字 | 不成立；只能改变已保留信号的包络 | `render.py:187-190` |
| Agent 会始终执行每个 hard rule | 未由代码强制 | `render.py` 无 EDL boundary validation |
| 单 speaker_id 能表达 overlap | 不成立 | 数据结构每 token 只有一个 speaker |

复杂度方面，video-use 的代码体量小、流程清晰，没有明显抽象膨胀。主要问题不是过度工程，而是大量“生产 hard rule”只写在 Skill 里，没有变成可测试、可拒绝的确定性约束。

## 11. 最终建议

采用策略：**🟡 借鉴并局部复用，不直接集成其切点算法或当前 renderer。**

1. 保留本项目的安全边界研究主线，不用 video-use 替换；
2. 借鉴其 Agent 内容规划、EDL、字幕映射和预览闭环；
3. 将 Scribe v2 加入 ASR 对照，将 ElevenLabs Forced Alignment 加入对齐 provider 对照；
4. 先完成 24 个边界样本的 A/B，再决定是否引入外部 API；
5. 若复用 renderer，先独立解决固定 24fps、seek、AAC 分段 concat 和渐进式音画漂移问题；
6. 最终架构保持“LLM 决定剪什么，确定性边界引擎决定在哪里切，renderer 只执行已验证 EDL”。

## 12. 未确定项

- `scribe_v1` 在真实账户上是否已经完全拒绝请求；本轮未读取密钥或消耗 API 额度；
- Scribe v2 与 FunASR 在本项目中文、口头语和 BGM 样本上的真实边界误差；
- ElevenLabs Forced Alignment 在中文连续语音、多人和强背景声下的 loss 与边界误差；
- 两个社区渲染修复 PR 中哪一种时序策略更适合本项目；
- video-use 的 Agent self-eval 在不同模型、不同图片缩放下能否稳定发现明显切字。

## 13. 参考来源

- [browser-use/video-use 官方仓库](https://github.com/browser-use/video-use)
- [video-use SKILL.md](https://github.com/browser-use/video-use/blob/92c2b34e44c205cbc2acae7f6ca7c1c219d5dd66/SKILL.md)
- [video-use transcribe.py](https://github.com/browser-use/video-use/blob/92c2b34e44c205cbc2acae7f6ca7c1c219d5dd66/helpers/transcribe.py)
- [video-use timeline_view.py](https://github.com/browser-use/video-use/blob/92c2b34e44c205cbc2acae7f6ca7c1c219d5dd66/helpers/timeline_view.py)
- [video-use render.py](https://github.com/browser-use/video-use/blob/92c2b34e44c205cbc2acae7f6ca7c1c219d5dd66/helpers/render.py)
- [Issue #64：输出规格与自动边界 QA](https://github.com/browser-use/video-use/issues/64)
- [PR #62：渐进式音画漂移修复](https://github.com/browser-use/video-use/pull/62)
- [PR #31：帧率、采样率与精确 seek 修复](https://github.com/browser-use/video-use/pull/31)
- [ElevenLabs STT API](https://elevenlabs.io/docs/api-reference/speech-to-text/convert)
- [ElevenLabs Forced Alignment](https://elevenlabs.io/docs/overview/capabilities/forced-alignment)
- [ElevenLabs Scribe v1 移除公告](https://elevenlabs.io/docs/changelog/2026/6/8)
