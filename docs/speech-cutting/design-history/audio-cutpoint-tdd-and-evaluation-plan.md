---
title: 音频切点校准 TDD 与效果对比方案
date: 2026-06-07
status: archived
audience: both
---

# 音频切点校准 TDD 与效果对比方案

> 历史测试设计；当前测试和资产位置见 [测试与资产索引](../experiments/test-and-artifact-inventory.md)。

## 1. 目的

本方案把两类测试分开：

1. **TDD 跑通测试**：证明每个方案在工程上可用、稳定、可复现，不会静默剪错。
2. **效果对比测试**：证明哪个方案在真实口播视频里更自然，是否值得进入正式 MVP。

三套候选方案：

- 方案 A：`token_padding`，只用 token/word boundary + padding。
- 方案 B：`rms_snap`，在 token 边界附近找 RMS 低能量/静音 gap。
- 方案 C：`vad_snap`，在 token 边界附近找 VAD non-speech gap。

## 2. TDD 跑通测试

### 2.1 公共数据模型测试

这些测试不属于某个策略，而是所有策略共享的安全底座。

| ID | 场景 | 输入 | 预期 |
|----|------|------|------|
| M1 | 正常 transcript JSON | `segments + selected_segment_ids + tokens` | 成功加载为内部模型 |
| M2 | 未知 selected ID | `selected_segment_ids` 包含不存在的片段 | 抛错，不静默忽略 |
| M3 | token 乱序 | token 时间顺序打乱 | 计算边界前按 `start_ms` 排序 |
| M4 | 无效 token | 空文本、负时间、`end <= start` | 过滤，不作为边界 |
| M5 | 缺少 tokens | segment 有时间戳但无 token | 策略可 fallback 到 segment 边界 |
| M6 | 媒体时长 clamp | `duration_ms` 存在且片段接近片尾 | 输出 `end_ms <= duration_ms` |
| M7 | 空选择 | `selected_segment_ids=[]` | 输出空 ranges，不报错 |
| M8 | JSON 字段缺失 | 缺少 `id/start_ms/end_ms` | 明确报错，不能生成伪结果 |

### 2.2 方案 A：Token Padding Baseline

目标：证明“不看音频也不会切字”，作为所有策略的 fallback。

| ID | 场景 | 输入 | 预期 |
|----|------|------|------|
| A1 | 单个已选 segment，有 tokens | 首 token 1000，末 token 2000 | `start = 1000 - pre_roll`，`end = 2000 + post_roll` |
| A2 | 单个已选 segment，无 tokens | segment 1000-2000 | 使用 segment 边界 + padding，reason=`segment_padding` |
| A3 | 相邻已选片段间隔小 | gap `< merge_gap_ms` | 合并为一个 range |
| A4 | 相邻已选片段间隔大 | gap `> merge_gap_ms` | 输出两个 ranges |
| A5 | token 乱序 | tokens 倒序 | 仍使用最早 token 作为 start、最晚 token 作为 end |
| A6 | 片头 clamp | first token 很靠近 0 | `start_ms=0` |
| A7 | 片尾 clamp | last token 靠近 duration | `end_ms=duration_ms` |
| A8 | 极短片段 | token duration 很短 | 输出不反转，`end > start` |
| A9 | selected ID 不存在 | 选中 `seg_missing` | 抛错 |
| A10 | 输出可追溯 | 多个 segment 合并 | `source_segment_ids` 完整保留 |

通过标准：

- 所有 A 组单元测试通过。
- 不依赖音频文件或 VAD 文件。
- 任何输入异常都不能静默漏剪。

### 2.3 方案 B：RMS / Silence Low-Energy Snap

目标：证明 RMS 方案只在低能量边界可靠时吸附，否则回退 A。

| ID | 场景 | 输入 | 预期 |
|----|------|------|------|
| B1 | 边界前后有明显静音 | token 附近有 `>=120ms` 低能量 gap | start/end 吸附到 gap，并保留 pre/post roll |
| B2 | 无静音 | frames 全部高能量 | 回退 A，reason 包含 `fallback` |
| B3 | 动态范围太小 | P80-P10 `<10dB` | 回退 A |
| B4 | 弱 gap | gap `80-120ms` | 可低置信接受，或按配置回退 |
| B5 | gap 在 token 内 | 低能量 run 与首/末 token 重叠 | 不接受，回退 A |
| B6 | 起点 guard | 候选 start 晚于首 token 前 60ms | 不接受 |
| B7 | 终点 guard | 候选 end 早于末 token 后 80ms | 不接受 |
| B8 | 噪声底变化 | 局部噪声变高 | 阈值动态抬高，不用固定 dB 硬切 |
| B9 | 片头/片尾 clamp | 吸附点超出媒体范围 | clamp 到合法范围 |
| B10 | RMS frames 缺失 | 无 audio frames | 回退 A |
| B11 | RMS frames 乱序 | frames 时间顺序异常 | 排序或明确报错 |
| B12 | 输出指标 | 成功/失败 mixed | metrics 里能看到 fallback_count、reason_counts |

通过标准：

- B 组单元测试通过。
- RMS 不可靠时必须保守回退。
- 不能因为局部低能量把切点放进 token 时间范围。

### 2.4 方案 C：VAD Interval Snap

目标：证明 VAD 方案能使用 speech intervals 的补集找 non-speech gap，同时保持 token 护栏。

| ID | 场景 | 输入 | 预期 |
|----|------|------|------|
| C1 | 正常 speech interval | 首 token 前、末 token 后有 non-speech gap | start/end 吸附到 gap |
| C2 | VAD 缺失 | 无 VAD JSON | 回退 A |
| C3 | VAD overlap | speech intervals 重叠 | 先合并，再推导 gaps |
| C4 | VAD 抖动 | speech intervals 间隔 `<80ms` | 合并为连续 speech |
| C5 | gap 太短 | non-speech gap `<80ms` | 不接受，回退 A |
| C6 | 起点 guard | VAD gap 会导致吃字头 | 不接受 |
| C7 | 终点 guard | VAD gap 会导致吃字尾 | 不接受 |
| C8 | 媒体首尾 gap | 片头/片尾自然静音 | 可吸附并 clamp |
| C9 | 快速口播 | speech interval 几乎连续 | 回退 A，不强行拉大窗口 |
| C10 | VAD 越界 | speech interval 超过 duration | 裁剪到 duration |
| C11 | 无 duration | VAD 无 duration_ms | 不推导尾部 gap，必要时回退 A |
| C12 | 输出指标 | 成功/失败 mixed | metrics 可统计 snap/fallback 分布 |

通过标准：

- C 组单元测试通过。
- VAD 不能覆盖 token guard。
- VAD 不可靠时必须保守回退。

### 2.5 CLI 与集成 Smoke Test

| ID | 场景 | 输入 | 预期 |
|----|------|------|------|
| CLI1 | compare 最小输入 | sample transcript + sample vad | 输出 A/B/C 三个 plan |
| CLI2 | compare 无音频 | transcript only | A 正常，B/C fallback 或缺特征 |
| CLI3 | compare 无 VAD | transcript + audio | A/B 正常，C fallback |
| CLI4 | extract-audio 正常视频 | MP4/MOV | 生成 `16kHz mono s16 wav` |
| CLI5 | extract-audio 无音轨 | video without audio | 明确失败 |
| CLI6 | extract-audio 纯静音 | silent audio/video | 明确失败或标记不可用 |
| CLI7 | 输出 JSON 可读 | compare output | 可被 `json.tool` 解析 |

通过标准：

- CLI 退出码能准确表达成功/失败。
- 音频提取必须验证“内容可用”，不能只验证文件存在。
- 所有输出都能被后续评估脚本消费。

## 3. 效果对比测试

TDD 只能说明方案“能跑”；效果对比要回答“哪个剪得更自然”。

### 3.1 样本集

第一批建议 5 类，每类至少 2 条样本：

| 类型 | 用途 |
|------|------|
| 干净中文口播，有明显停顿 | 检查 RMS/VAD 是否能明显优于 baseline |
| 语速快、连读多 | 检查 B/C 是否会误切 |
| 轻微背景噪声 | 检查 RMS 抗噪能力 |
| 中英混杂、数字/专有名词多 | 检查 token/ASR 边界稳定性 |
| 句间隔很短 | 检查 merge/fallback 是否保守 |

每个样本准备：

- 原始视频。
- 音频 wav。
- 可选 RMS frames JSON；格式为 `{"frames": [{"start_ms": 0, "end_ms": 20, "rms_db": -42.0}]}`，可替代 wav 输入用于快速复现。
- transcript JSON。
- selected_segment_ids。
- 可选 VAD JSON。
- 可选人工标注 gold boundary JSON；支持 `{"ranges": [{"source_segment_ids": ["seg_001"], "start_ms": 900, "end_ms": 1900}]}`。

### 3.2 自动指标

每个策略、每个 range 输出：

| 指标 | 含义 |
|------|------|
| `range_count` | 输出片段数 |
| `total_duration_ms` | 总保留时长 |
| `delta_start_ms` | 调整后 start 相对原始 start 的变化 |
| `delta_end_ms` | 调整后 end 相对原始 end 的变化 |
| `fallback_count` | 回退 baseline 次数 |
| `snap_hit_rate` | 成功吸附到 gap 的比例 |
| `boundary_risk_count` | `end <= start` 或边界异常次数 |
| `token_chop_risk` | 是否切进首/末 token guard |
| `unselected_token_leak_ms` | 多包含相邻未选 token 的时长 |
| `boundary_rms_db` | 切点附近 RMS，越低通常越自然 |
| `gold_error_start_ms` | 与人工 start 标注的误差 |
| `gold_error_end_ms` | 与人工 end 标注的误差 |

### 3.3 人工试听评分

每个样本导出三份 clip，文件名隐藏策略名，例如：

```text
sample_01_A.mp4
sample_01_B.mp4
sample_01_C.mp4
```

人工评分表：

| 维度 | 1 分 | 3 分 | 5 分 |
|------|-----|------|------|
| 起点自然度 | 吃字或突兀 | 可接受但略紧/略拖 | 自然 |
| 终点自然度 | 吃尾音或硬断 | 可接受 | 自然收住 |
| 气口保留 | 完全没有或太长 | 尚可 | 刚好 |
| 废话残留 | 明显多留 | 少量多留 | 干净 |
| 整体观感 | 不可用 | 可用 | 可直接用 |

最终人工分：

```text
manual_score = 起点自然度 * 0.25
             + 终点自然度 * 0.25
             + 气口保留 * 0.20
             + 废话残留 * 0.15
             + 整体观感 * 0.15
```

### 3.4 对比结论规则

不直接按单个样本下结论。至少满足：

1. 每类样本至少 2 条。
2. 每条样本三种策略都成功输出。
3. 人工评分和自动风险指标都可用。

推荐采纳条件：

- 如果 B/C 相比 A，人工平均分提升 `>=0.5`，且 `token_chop_risk` 不升高，可进入下一阶段。
- 如果 B/C 的 fallback 率 `>50%`，说明它在当前样本集上不稳定，只能作为可选增强。
- 如果 B/C 多留时长明显低于 A，但人工分没有提升，不优先采纳。
- 如果 C 在干净口播和快速口播都稳定优于 B，优先推进 C 接 Silero VAD。

### 3.5 提测交付物

每轮提测应包含：

```text
outputs/eval/YYYYMMDD-HHMM/
  compare.json
  summary.csv
  notes.md
```

当前 evaluator 只负责自动指标和人工评分模板，不导出视频片段。真实 clip 导出接入后，可在同一目录下增加：

```text
clips/
  sample_01_token_padding.mp4
  sample_01_rms_snap.mp4
  sample_01_vad_snap.mp4
```

命令示例：

```bash
python -m cutpoint_lab.cli eval \
  --transcript path/to/transcript.json \
  --audio /tmp/cutpoint_audio.wav \
  --vad path/to/vad.json \
  --gold path/to/gold_boundaries.json
```

如果已有 RMS frames JSON，可以用 `--rms-frames path/to/rms_frames.json` 替代 `--audio`。

`notes.md` 至少写：

- 样本来源。
- 使用的 transcript/VAD/参数。
- 每个策略是否失败。
- 自动指标摘要。
- 人工评分表。
- 当前推荐。

## 4. 当前任务分工建议

如果多个代理并行：

- 代理 A：只做 `token_padding` 和公共模型校验。
- 代理 B：只做 `rms_snap` 和 RMS feature/gap 逻辑。
- 代理 C：只做 `vad_snap` 和 VAD gap 逻辑。
- 代理 D：只做 evaluator，对比输出、summary、人工评分模板。

每个实现代理必须先写测试，再写实现。Evaluator 可以在三方案接口稳定后开始。
