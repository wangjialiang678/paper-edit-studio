---
title: 语音切分测试与资产索引
date: 2026-07-13
status: active
audience: both
tags: [testing, artifacts, legacy, reproducibility]
---

# 语音切分测试与资产索引

## 先看状态

| 资产类型 | 位置 | Git 保护 | 是否可从 main 直接复现 |
|---|---|---|---|
| 当前产品切点引擎 | `src/cutpoint_lab/` | 是 | 是 |
| 当前相关单元测试 | `tests/` | 是 | 是 |
| 对齐基准台和盲听生成器 | `legacy` 分支 | 仅本地 Git | 否，需要切换/Worktree |
| 实验样本音频 | `samples/wav/`、`samples/audio/` | 否，已 ignore | 本机可用 |
| 试听和诊断输出 | `outputs/` | 否，已 ignore | 本机可用 |
| 结论与数字摘要 | `docs/speech-cutting/` | 可由 Git 跟踪，当前待提交 | 是 |
| 关键小型证据快照 | `docs/speech-cutting/experiments/snapshots/` | 可由 Git 跟踪，当前待提交 | 可直接阅读 |

## main 分支仍在使用的代码

- `src/cutpoint_lab/strategies.py`：8 种切点策略，Studio 默认 `hybrid_valley`。
- `src/cutpoint_lab/features.py`：音频特征。
- `src/cutpoint_lab/models.py`：时间戳、片段和剪辑计划数据结构。
- `src/cutpoint_lab/dashscope.py`：FunASR/DashScope 输出转换。
- `src/cutpoint_lab/paper_edit/state.py` 和 `src/cutpoint_lab/studio/plans.py`：将文字选择转成实际剪辑区间。

这些文件属于产品运行路径，不移入文档目录。

## main 分支直接相关测试

| 文件 | 主要覆盖 |
|---|---|
| `tests/test_cutpoint_strategies.py` | token、RMS、VAD、hybrid 等切点策略 |
| `tests/test_cutpoint_token_padding.py` | token padding 和边界护栏 |
| `tests/test_voice_aware_strategies.py` | 增强音轨、说话人和 overlap 约束 |
| `tests/test_dashscope_conversion.py` | ASR 时间戳转换 |

当前还有 Studio、编辑状态和导出测试间接覆盖语音切分链路。

## legacy 分支的历史实验代码

### 对齐基准台

- `src/cutpoint_lab/alignment_benchmark/`
- `scripts/run_ctc_batch_alignment.py`
- `scripts/run_ctc_forced_alignment.py`
- `scripts/run_ctc_tool_bridge.py`
- `samples/manifests/alignment_benchmark_samples.json`
- `samples/manifests/ctc_alignment_command.json`
- `tests/test_alignment_benchmark_*.py`

### 盲听与切口放大镜

- `src/cutpoint_lab/blind_comparison.py`
- `src/cutpoint_lab/blind_zoom.py`
- `scripts/build_blind_cutpoint_comparison.py`
- `samples/manifests/blind_cutpoint_sample03.json`
- `samples/manifests/blind_sentence_cut_sample03.json`
- `tests/test_blind_comparison.py`
- `tests/test_blind_zoom.py`

### 早期并行实现交接

- `design-history/agent-handoffs/agent-a-token-padding.json`
- `design-history/agent-handoffs/agent-b-rms-snap.json`
- `design-history/agent-handoffs/agent-c-vad-snap.json`
- `design-history/agent-handoffs/agent-d-evaluator.json`

这四份 JSON 记录了当时每个策略的改动文件、测试覆盖、通过状态和已知限制。

### 声纹、分说话人和增强入口

- `scripts/run_pyannote_diarization.py`
- `scripts/run_funasr_diarization.py`
- `scripts/run_speechbrain_enhancement.py`

这三个脚本只能证明入口和失败路径曾实现，当前没有保存 pyannote、CAM++ 或 SpeechBrain 在本项目真实样本上的推理结果，不得写成“效果已验证”。

## 样本

| 资产 | 位置 | 规模 | 备注 |
|---|---|---:|---|
| 真实 WAV | `samples/wav/` | 约 47MB | 3 个中文口播样本，已 ignore |
| 真实 M4A | `samples/audio/` | 约 13MB | WAV 的源音频版本，已 ignore |
| ASR 转写和运行记录 | `samples/asr/` | 约 20MB | 包含 DashScope 请求与转写，已 ignore |
| 轻量示例 | `examples/sample_*.json` | 很小 | Git 跟踪，仍为当前 fixture |

关键样本 SHA256：

| 样本 | WAV | M4A |
|---|---|---|
| sample 01 | `cbe27d…e113` | `ba8252…6ce3` |
| sample 02 | `5416da…683c` | `a7e434…8b63` |
| sample 03 | `83a1b5…4473` | `a9240e…b67` |

## outputs 实验产物

`outputs/` 合计约 210MB，全部是本地产物。不直接移动这些文件，因为多个 JSON 包含当前绝对路径，盲测 manifest 也依赖现有相对位置。

| 目录 | 约大小 | 状态 |
|---|---:|---|
| `outputs/real-audio-comparison/` | 136MB | 早期三策略对比，无 gold，评分未完成 |
| `outputs/early-cut-preview/` | 7.1MB | 早期局部试听，无 gold |
| `outputs/alignment-benchmark/` | 2.4MB | 24 边界对齐结果，无 gold |
| `outputs/blind-cutpoint-comparison/` | 20MB | 第一轮合并式盲听包，已被公开/私有分离版取代 |
| `outputs/blind-cutpoint-diagnostics/` | 17MB | 第一轮私有诊断包 |
| `outputs/blind-cutpoint-listening/` | 3.2MB | 第一轮公开试听包 |
| `outputs/sentence-cutpoint-diagnostics/` | 20MB | 最终整句实验诊断包，当前最有价值 |
| `outputs/sentence-cutpoint-listening/` | 5.4MB | 最终整句公开试听包 |

## 最终整句实验的关键文件

- 试听指南：`outputs/sentence-cutpoint-listening/20260710-sample03-s24-s32/listening-guide.md`
- 完整匿名版：`outputs/sentence-cutpoint-listening/20260710-sample03-s24-s32/full/`
- 四个切口放大镜：`outputs/sentence-cutpoint-listening/20260710-sample03-s24-s32/cut-01/` 至 `cut-04/`
- 质检：`outputs/sentence-cutpoint-diagnostics/20260710-sample03-s24-s32/qc.json`
- 切点清单：`outputs/sentence-cutpoint-diagnostics/20260710-sample03-s24-s32/cut_manifest.json`
- 盲键：`outputs/sentence-cutpoint-diagnostics/20260710-sample03-s24-s32/blind_key.json`

## 恢复历史实验的安全方式

不要在当前未提交工作树上直接切换分支。优先使用独立 worktree 查看 `legacy`，再将本地 `samples/` 和 `outputs/` 作为外部资产接入。历史手册中的重建命令都需要在 `legacy` 代码上运行。

## 保存风险

- `outputs/`、`samples/wav/`、`samples/audio/` 和 `samples/asr/` 不会随 Git 备份。
- 本次已将路径、数据规模、关键结果和主观结论写入可跟踪文档，并将关键报告、QC、切点清单和盲键复制到 `experiments/snapshots/`，但没有将 210MB 音频纳入 Git。
- `legacy` 也是本地分支；若未来要做远程备份，需要单独确认发布目标、隐私和仓库体积。
