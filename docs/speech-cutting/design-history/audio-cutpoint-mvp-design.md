---
title: 音频切点校准 MVP 设计方案
date: 2026-06-07
status: archived
audience: both
---

# 音频切点校准 MVP 设计方案

> 历史设计记录；其中代码结构对应 `legacy` 分支。

## 1. 目标

先独立实现一个可对比的音频切点校准实验框架。它不依赖完整 Mac App，也不依赖 Gemini。输入是：

- 带时间戳的字幕 JSON。
- 用户确认保留的 `segment_id` 列表。
- 可选音频文件，用来提取 RMS/低能量特征。
- 可选 VAD 语音区间 JSON。

输出是三套策略各自生成的 `ClipPlan`，以及可对比的指标。等真实视频和字幕到位后，先用这个实验框架跑样片，再决定哪套策略进入正式 App。

## 2. 输入输出

### 2.1 Transcript JSON

```json
{
  "source_video": "/path/to/video.mp4",
  "selected_segment_ids": ["seg_002", "seg_003"],
  "segments": [
    {
      "id": "seg_002",
      "start_ms": 1200,
      "end_ms": 2600,
      "text": "这句话值得保留",
      "tokens": [
        {"text": "这", "start_ms": 1240, "end_ms": 1310, "confidence": 0.94}
      ]
    }
  ]
}
```

### 2.2 VAD JSON

```json
{
  "duration_ms": 10000,
  "speech_intervals": [
    {"start_ms": 1200, "end_ms": 2600}
  ]
}
```

### 2.3 输出 ClipPlan

```json
{
  "strategy": "vad_snap",
  "ranges": [
    {
      "start_ms": 1040,
      "end_ms": 2840,
      "original_start_ms": 1200,
      "original_end_ms": 2600,
      "source_segment_ids": ["seg_002"],
      "adjustment_reason": "snapped_to_vad_gap",
      "confidence": 0.82
    }
  ],
  "metrics": {
    "range_count": 1,
    "total_duration_ms": 1800
  }
}
```

## 3. 三个并行 MVP 方案

### 3.1 方案 A：Token Padding Baseline

只用字幕 token/word boundary，不看音频。

步骤：

1. 合并相邻的已选 segment。
2. 找到合并 range 的首 token 和末 token。
3. 起点取 `first_token.start_ms - pre_roll_ms`。
4. 终点取 `last_token.end_ms + post_roll_ms`。
5. 没有 token 时退回 segment 起止时间。
6. 如果两个已选片段之间 gap 小于 `merge_gap_ms`，合并为一个 range。

用途：

- 作为所有音频策略的 baseline。
- 当音频/VAD 不可用时兜底。
- 检查“只用词边界 + padding”是否已经足够。

默认参数：

- `pre_roll_ms = 150-160`
- `post_roll_ms = 240-250`
- `merge_gap_ms = 500`
- `min_duration_ms = 300`

边界处理：

- token 缺失时退回 segment 边界。
- token 置信度低时先保守使用，但输出低置信标记用于后续统计。
- padding 后相邻 range 重叠或间隔小于 `merge_gap_ms` 时再次合并。
- 起点/终点需要 clamp 到媒体时长范围内。

### 3.2 方案 B：Silence/RMS Low-Energy Snap

在 token 边界附近寻找低能量 gap。

步骤：

1. 用 FFmpeg 把视频或音频转为 `16kHz mono wav`。
2. 按 20ms frame 计算 RMS dB。
3. 用低分位数估计 noise floor。
4. 在起点窗口 `[first_token.start - 600ms, first_token.start + 200ms]` 找低能量 gap。
5. 在终点窗口 `[last_token.end - 200ms, last_token.end + 800ms]` 找低能量 gap。
6. 优先吸附到持续 `>=120ms` 的 gap；没有可靠 gap 时回退方案 A。

用途：

- 适合干净口播、停顿明显的素材。
- 能找到气口和自然停顿。
- 不需要 VAD 模型。

默认参数：

- `frame_ms = 20`
- `noise_floor_percentile = 10`
- `low_energy_margin_db = 6`
- `dynamic_range_min_db = 10`
- `min_gap_ms = 120`
- `weak_gap_ms = 80`
- `search_before_start_ms = 600`
- `search_after_start_ms = 200`
- `search_before_end_ms = 200`
- `search_after_end_ms = 800`
- `start_guard_ms = 60`
- `end_guard_ms = 80`

边界处理：

- 如果窗口内 RMS 动态范围小于 10-12dB，说明没有可靠静音/低能量边界，直接回退方案 A。
- 强 gap `>=120ms`；弱 gap `80-120ms` 只能低置信接受。
- 不在选中片段内部删除短停顿，只优化外边界。
- 不为了找更安静的位置跨过相邻未选 token。
- 起点候选必须落在首 token 前至少 `start_guard_ms`；终点候选必须落在末 token 后至少 `end_guard_ms`。

### 3.3 方案 C：VAD Interval Snap

用 VAD speech intervals 推导 non-speech gaps，再吸附边界。

步骤：

1. 输入全片 VAD speech intervals。
2. 合并重叠或相邻的 speech intervals。
3. 从 speech intervals 反推出 non-speech gaps。
4. 在起点/终点搜索窗口内找合适 gap。
5. 找到 gap 时吸附；找不到时回退方案 A。

用途：

- 适合语音/非语音边界比纯音量更可靠的素材。
- 后续可以接 Silero VAD 或 pyannote。
- 第一版可以先用外部 VAD JSON，避免立刻引入重依赖。

默认参数与方案 B 一致，gap 来源从 RMS 改为 VAD non-speech。

边界处理：

- VAD speech intervals 必须先排序、裁剪、合并 overlap。
- `merge_speech_gap_ms` 默认 80ms，用来合并 VAD 抖动。
- 缺失 VAD 或找不到可靠 non-speech gap 时回退方案 A。
- 快速口播场景不要盲目拉大窗口，宁可多留一点。
- VAD gap 也必须通过 token guard；VAD 不能覆盖词边界护栏。

## 4. 对比指标

每个策略都输出：

- `range_count`
- `total_duration_ms`
- `delta_start_ms`
- `delta_end_ms`
- `reason_counts`
- `fallback_count`
- `boundary_risk_count`
- `snap_hit_rate`
- `adjustment_ms_p50/p95`
- `word_chop_rate`
- `manual_cut_error_ms`
- `human_preference`

真实样片人工评估：

- 是否吃字头。
- 是否吃字尾。
- 是否保留自然气口。
- 是否多留太多废话。
- 导出后音画是否同步。

## 5. 实施顺序

1. 先实现纯 Python 数据模型、策略接口和测试。
2. 实现方案 A，作为兜底。
3. 实现方案 B 和 C。
4. 实现 CLI：读 transcript/audio/vad，输出三方案对比 JSON。
5. 用户提供视频后，用 FFmpeg 提取音频并跑真实样片对比。

## 6. 明确不做

- 不在这个实验框架里调用 Gemini。
- 不做完整视频导出。
- 不引入 UI。
- 不默认下载 VAD/ASR 模型。
- 不删除原始视频。
