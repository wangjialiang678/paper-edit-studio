---
title: 中文语音边界对齐实验台设计
date: 2026-07-10
status: archived
audience: both
tags: [design, forced-alignment, benchmark, cutpoint]
---

# 中文语音边界对齐实验台设计

> 历史设计记录；实现已收录在本地 `legacy` 分支。

## 1. 目标

建立一个独立、可重复的边界对齐实验台，在同一批中文音频和人工边界上比较：

- 现有 FunASR/Paraformer 词时间戳；
- ElevenLabs Scribe v2 词时间戳；
- ElevenLabs Forced Alignment；
- MFA Mandarin；
- CTC forced aligner。

实验台回答“哪个 provider 的词首/词尾更接近人工声学边界”，不直接替换视频剪辑器，也不把任一模型输出当作人工 gold。

## 2. 已确认范围

### 本轮实现

- 统一 `BoundaryCase`、`AlignmentResult` 和 provider 协议；
- 从现有三个真实样本生成 24 个候选边界及试听片段；
- 生成人工标注模板，gold 支持“可接受区间”而非唯一毫秒点；
- 实现预计算 JSON、ElevenLabs Forced Alignment、MFA、通用命令型 CTC provider；
- 计算安全区间命中、到区间误差、P50/P95、失败率和覆盖率；
- 输出 JSON、CSV 和人可读报告；
- 所有外部依赖不可用时显式记录 `unavailable`。

### 本轮不做

- 不改现有视频剪辑策略；
- 不把 provider agreement 当作 accuracy；
- 不伪造人工标注；
- 不自动安装或持久化 API Key；
- 不在没有许可核验时把研究型 CTC 模型作为产品默认依赖；
- 不处理多人 overlap 的分离，只在 case 标签中保留该维度。

## 3. 方案比较

| 方案 | 优点 | 缺点 | 结论 |
|---|---|---|---|
| 直接把每个模型接入现有 `strategies.py` | 很快看到剪辑结果 | 模型、边界策略和渲染耦合，无法公平比较 | 不采用 |
| 单文件实验脚本 | 代码少 | 数据协议、失败状态和报告不可复用 | 不采用 |
| 独立 benchmark 包 + provider 适配器 | 可离线测试、可替换模型、结果可追溯 | 初期文件较多 | 采用 |

## 4. 数据模型

```text
BoundaryCase
- case_id
- sample_id
- audio_path
- clip_start_ms / clip_end_ms
- text
- target_text
- target_occurrence
- side: start | end
- asr_boundary_ms
- condition_tags
- acceptable_start_ms / acceptable_end_ms (可为空)
- forbidden_intervals

AlignedWord
- text
- start_ms
- end_ms
- score

AlignmentResult
- case_id
- provider
- status: ok | unavailable | failed
- words
- predicted_boundary_ms
- error
- metadata
```

`target_occurrence` 用于处理同一文本在局部 transcript 中重复出现。所有时间统一使用相对 case 音频的毫秒；报告同时保留相对源媒体的绝对时间。

## 5. Provider 边界

### Precomputed

读取现有 FunASR、Scribe 或其他 provider 的标准 JSON。用于基线、离线复现和单元测试。

### ElevenLabs Forced Alignment

向 `/v1/forced-alignment` 提交 case 音频和文字，解析 `words[]` 与 `loss`。Key 只从调用方参数或环境传入，不进入结果文件。

### MFA

调用 MFA 3.4 的单文件命令，优先使用 `align_one_hf` 和 `MontrealCorpusTools/mandarin_mfa`；解析其 JSON `tiers` 中的 word entries。MFA 不存在时返回 `unavailable`。

### CTC

采用命令适配器：实验台写出音频、文字和预期输出路径，外部命令生成 JSON `segments[]`。默认示例兼容 `ctc-forced-aligner`，但不把其 CC-BY-NC 默认权重纳入产品依赖。

## 6. 数据流

```text
现有 transcript + sample audio
        ↓
候选选择器：每个样本 8 个边界
        ↓
FFmpeg 提取局部 WAV + cases.json + review.md
        ↓
人工填写 acceptable interval
        ↓
同一 case 依次运行各 provider
        ↓
标准化 AlignmentResult
        ↓
按 target_text/occurrence 定位预测边界
        ↓
metrics.json + summary.csv + report.md
```

候选选择器覆盖短间隙、中等间隙和长间隙；现有数据无法自动证明 BGM、多人或 overlap 标签，因此默认标为 `unclassified`，由人工复核。

## 7. 错误处理

- 缺 Key：ElevenLabs 结果为 `unavailable`，不进入准确率分母；
- 缺 MFA/CTC 命令：记录 `unavailable`；
- API/命令失败：记录 `failed` 和可安全展示的错误，不记录密钥或请求正文；
- target text 无法唯一映射：结果 `failed`，不猜 token；
- case 未标 gold：只报告 provider agreement 和覆盖率，不报告 accuracy；
- 音频不存在或区间非法：构建阶段直接失败，不生成伪 case。

## 8. 指标

对有 gold 的边界：

- `safe_interval_hit`：预测点是否落在可接受区间；
- `interval_error_ms`：落在区间为 0，否则到最近端点的距离；
- `absolute_error_to_midpoint_ms`：只用于分布观察；
- P50/P95；
- provider failure/unavailable/coverage；
- 按 `condition_tags` 分组结果。

对无 gold 的边界：

- provider 两两绝对差；
- 结果覆盖率；
- 不输出“更准确”的结论。

## 9. 测试策略

- 使用项目现有 `unittest`；
- 先测试数据校验、目标词定位和区间指标；
- ElevenLabs 使用本地假 HTTP server，不消耗 API；
- MFA/CTC 使用临时假命令验证参数、输出解析和不可用状态；
- case builder 使用临时 WAV/Transcript，验证恰好生成指定数量及时间映射；
- CLI smoke 生成 JSON、CSV、Markdown；
- 最后运行完整项目测试，不能有 skip。

## 10. 验收标准

1. 24 个 draft case 和 WAV 片段可重复生成；
2. 没有 gold 时报告明确写“不能比较准确率”；
3. 任一 provider 不可用不影响其他 provider；
4. fake ElevenLabs、fake MFA、fake CTC 均有通过测试；
5. 完整测试套件全部通过且无 skip；
6. 有真实 Key、MFA/CTC 命令和人工 gold 后，一条命令即可生成最终排名报告；
7. 在外部条件缺失时，交付报告必须明确列出未运行项，不宣称实验已得出模型胜负。
