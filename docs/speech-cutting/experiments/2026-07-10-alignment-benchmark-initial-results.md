---
title: 中文词边界对齐实验台首轮实测
date: 2026-07-10
status: active
audience: both
tags: [forced-alignment, chinese, funasr, ctc, mfa, elevenlabs]
---

# 中文词边界对齐实验台首轮实测

## 结论先行

1. 实验工具和 24 个真实中文边界已经可重复运行。FunASR 基线、CTC 和 MFA 都有真实输出；ElevenLabs 因当前环境缺 API Key 而显式记为未运行。
2. 尚无人工可接受切点区间，所以现在不能判断 ElevenLabs、MFA、CTC 或 FunASR 谁更准。
3. MFA 在 19/24 个边界上能定位目标词，与 FunASR 的分歧中位数为 50ms、P95 为 92ms；4 个受中文 OOV/分词影响，另 1 个的目标边界落在 MFA 更粗的词 token 内部，无法从词级输出证明。
4. CTC 在 24/24 个边界上都能给出字级边界，但与 FunASR 的分歧中位数为 120ms、P95 为 314ms。这表示“更常不同意”，不表示 CTC 更错或更对。
5. 一个待 gold 验证的候选工作流是：FunASR 作快速基线，MFA/CTC 作第二意见，模型分歧大的边界优先人工复核。现有数据尚未证明该流程能提高问题召回或降低人工成本；在完成 gold 和 ElevenLabs 实测前，不应替换现有切点默认方案。

## 实验对象

- 3 个现有中文真实口播 WAV。
- 每个样本 8 个边界，合计 24 个。
- 停顿分布：短 9、中 9、长 6。
- 每个 case 保存局部 WAV、局部文本、目标词、目标出现次数、词首/词尾和 FunASR 基线。
- 局部窗口会向外扩到首尾 token 的完整区间；provider 结果用 WAV 内容和对齐输入共同生成指纹，防止重建后混入旧结果。
- 未自动推断 BGM、多人或 overlap 标签，目前这些条件仍需人工复核。

数据包：[`outputs/alignment-benchmark/20260710/`](../../../outputs/alignment-benchmark/20260710/)

## Provider 实现

### FunASR

基线直接使用现有转写中的词时间戳。FunASR 官方仓库将 timestamp prediction 作为 Paraformer 工具链能力之一：[modelscope/FunASR](https://github.com/modelscope/FunASR)。

### CTC

使用 [MahmoudAshraf97/ctc-forced-aligner](https://github.com/MahmoudAshraf97/ctc-forced-aligner) 0.3.0（源码 commit `c344f5bc900323aa434a7cb200b7c629d463bd02`）及默认 `mms-300m-1130-forced-aligner` 权重（本地 snapshot `49402e9577b1158620820667c218cd494cc44486`）。中文无空格，因此实测将 `split_size` 设为 `char`；如果使用默认 `word`，整句会成为一个 segment，无法评估词间切点。默认权重为 CC BY-NC 4.0，仅用于本次研究。

### MFA

使用 MFA 3.4.0、`align_one_hf`、`MontrealCorpusTools/mandarin_mfa`（本地 snapshot `85f9e701a29d9d80f582725c6c94bf8796520b9a`）、`--use_g2p` 和 `--beam 100`。[MFA 官方文档](https://montreal-forced-aligner.readthedocs.io/en/stable/user_guide/workflows/alignment.html) 说明 `align_one_hf` 从 HF 模型包中同时获得声学模型、字典和 G2P。[Mandarin 模型页](https://mfa-models.readthedocs.io/en/latest/acoustic/Mandarin/Mandarin%20MFA%20acoustic%20model%20v3_0_0.html) 指出模型主要基于较干净的朗读语音，对偏离训练条件的素材可能需要增大 beam。

真实 JSON 含 `<eps>` 和 `<unk>`。实验台会过滤不对应文字的 `<eps>`，但保留 `<unk>` 作为不可跨越的对齐障碍；目标词落入 `<unk>` 或重复词次序无法唯一对应时，该边界不猜时间，直接记为不可定位。

### ElevenLabs

适配器按官方 [`POST /v1/forced-alignment`](https://elevenlabs.io/docs/api-reference/forced-alignment/create?explorer=true) 实现，优先解析 `characters[]` 的字级时间，缺失时才退回 `words[]`，并保留整体 loss。[Forced Alignment 能力页](https://elevenlabs.io/docs/overview/capabilities/forced-alignment) 列出中文支持，同时说明它不支持 diarization。本地没有 `ELEVENLABS_API_KEY`，所以本轮没有向外部 API 发送真实音频。

## 首轮结果

### 边界覆盖

| Provider | 命令/API 成功 | 目标边界可定位 | 覆盖率 | 说明 |
|---|---:|---:|---:|---|
| FunASR | 24/24 | 24/24 | 100% | 现有词时间戳基线 |
| CTC MMS-300M | 24/24 | 24/24 | 100% | 中文字级对齐 |
| MFA Mandarin | 24/24 | 19/24 | 79.17% | 4 个 OOV/分词不匹配，1 个边界落在粗粒度 token 内 |
| ElevenLabs Forced Alignment | 0/24 | 0/24 | 0% | 缺 Key，状态为 `unavailable` |

MFA 无法定位的目标包括“哈”、“嘉”和两个“良”相关边界，主要是原词被输出为 `<unk>` 或分词后不再含目标字。另一个 case 中，目标字的词尾位于 MFA 合并词 token 内部；实验台不用整个合并词的词尾冒充该字词尾，因此也记为不可定位。

### Provider 两两分歧

| Provider pair | 共同可比 case | 绝对分歧 P50 | 绝对分歧 P95 |
|---|---:|---:|---:|
| FunASR vs MFA | 19 | 50ms | 92ms |
| FunASR vs CTC | 24 | 120ms | 314ms |
| MFA vs CTC | 19 | 130ms | 281ms |

这些数字只能回答“模型之间差多少”。因为 `annotated_case_count=0`，它们不能回答“谁离真实声学边界更近”。机器可读报告见 [`report.json`](../../../outputs/alignment-benchmark/20260710/run/report/report.json)，阅读版见 [`report.md`](../../../outputs/alignment-benchmark/20260710/run/report/report.md)。

## 对 video-use 方案的回答

video-use 的 EDL 生成、渲染和切点自评估是很有价值的工程闭环，但它本身没有独立证明 ElevenLabs 词边界比 FunASR/MFA/CTC 更准。这个实验台正是把 video-use 的外部对齐依赖抽出来，先做可重复比较，再决定是否接入剪辑链路。

## 可执行的下一步

1. 人工填完 24 个 case 的可接受切点区间。这是从“分歧”进入“准确率”的必要条件。
2. 配置受限额度的 ElevenLabs Key，运行同一 24 case，不改文本和 clip。
3. 按 `safe_interval_hit_rate` 、区间误差 P50/P95、失败率和 API 成本做决策，而不是只看平均误差。
4. 将样本扩展到至少 100–200 个边界，单独覆盖 BGM、噪声、多人顺序说话、overlap、专有名词、快速连读和极短停顿。
5. 如果某 provider 在人工 gold 上显著更好，再将它作为“ASR 边界局部校准器”接入现有 `anchored_rms` / `hybrid_valley`，而不是直接用模型时间戳硬切。

## 事实与推测分离

**已验证事实**：24 case 已生成；FunASR/CTC/MFA 已有真实结果；MFA 边界覆盖为 19/24；ElevenLabs 因缺 Key 未运行；当前无 gold。

**待验证推测**：ElevenLabs 可能对中文口语专有名词比 MFA 稳定；CTC 与 FunASR 分歧较大的样本可能包含 FunASR 错位也可能包含 CTC 错位；MFA 与 FunASR 更一致不等于 MFA 更准。
