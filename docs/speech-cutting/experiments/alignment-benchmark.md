---
title: 中文词边界对齐实验台手册
date: 2026-07-10
status: archived
audience: both
---

# 中文词边界对齐实验台手册

> **历史复现手册**：本文对应的 `alignment_benchmark` 包、脚本和 manifest 已移到本地 `legacy` 分支。下列命令不能在当前 main 分支直接运行；请先在独立 worktree 中打开 `legacy`。

## 它解决什么

这个工具不直接剪视频，而是把“词首/词尾到底在哪一毫秒”拆成独立实验。它用同一段音频和同一份文字分别运行 FunASR 基线、CTC、MFA 和 ElevenLabs Forced Alignment，再把结果转成统一的相对毫秒边界。

真正的准确率必须依赖人工听感标注。两个模型给出相近时间，只说明它们意见相近，不证明它们都对。

## 目录和输出

```text
outputs/alignment-benchmark/20260710/
├── cases.json                 # 24 个边界 case，人工 gold 也填在这里
├── review.md                 # 按 case 审听的清单
├── clips/*.wav               # 每个边界的局部 WAV
├── providers/funasr.json     # 从原转写裁出的基线时间戳
└── run/
    ├── results/*.json        # 各 provider 的标准化输出
    └── report/
        ├── report.json
        ├── summary.csv
        └── report.md
```

## 1. 构建样本包

```bash
PYTHONPATH=src uv run python -m cutpoint_lab.alignment_benchmark build-cases \
  --manifest samples/manifests/alignment_benchmark_samples.json \
  --output-dir outputs/alignment-benchmark/20260710 \
  --cases-per-sample 8 \
  --context-ms 1500
```

当前 manifest 包含 3 个中文口播 WAV，每个样本抽 8 个边界，并尽量覆盖短、中、长停顿。
如果固定上下文窗口刚好切进首尾 token，构建器会把 clip 向外扩到该 token 的完整时间区间，避免用完整文字强制对齐残缺语音。

## 2. 运行 provider

### FunASR 预计算基线

```bash
PYTHONPATH=src uv run python -m cutpoint_lab.alignment_benchmark run \
  --cases outputs/alignment-benchmark/20260710/cases.json \
  --output-dir outputs/alignment-benchmark/20260710/run \
  --precomputed funasr=outputs/alignment-benchmark/20260710/providers/funasr.json
```

### CTC forced aligner

先在独立 Python 环境中安装 [ctc-forced-aligner](https://github.com/MahmoudAshraf97/ctc-forced-aligner)。对无空格中文必须用字级切分；项目批处理入口已固定这个行为，并且只加载一次模型。

```bash
PYTHONPATH=src python scripts/run_ctc_batch_alignment.py \
  --cases outputs/alignment-benchmark/20260710/cases.json \
  --output outputs/alignment-benchmark/20260710/run/results/ctc_mms_300m.json \
  --language cmn \
  --device cpu \
  --batch-size 4
```

`samples/manifests/ctc_alignment_command.json` 仅是验证通用命令 provider 的逐 case debug 备用路径，provider 名为 `ctc_mms_300m_per_case_debug`。它会重复启动模型，不用于本报告，也不得覆盖上面 batch 生成的 `ctc_mms_300m.json`。

注意：该项目代码是 BSD，但它默认使用的 `mms-300m-1130-forced-aligner` 权重是 CC BY-NC 4.0。本实验只把默认权重用于研究比较，商业产品必须换成许可兼容的模型并重新验证。

### MFA Mandarin

[MFA 3.4 的 `align_one_hf`](https://montreal-forced-aligner.readthedocs.io/en/stable/user_guide/workflows/alignment.html) 会从 Hugging Face 模型包读取声学模型、字典和 G2P。Mandarin 模型页说明它主要由较干净的朗读语音训练，口语、噪声或快速语音可能需要更大 beam，这与本次实测中的失败现象一致。模型说明见 [Mandarin MFA acoustic model](https://mfa-models.readthedocs.io/en/latest/acoustic/Mandarin/Mandarin%20MFA%20acoustic%20model%20v3_0_0.html)。

可复现的隔离安装示例：

```bash
micromamba create -y -p "$HOME/.local/share/cutpoint-lab/mfa" \
  -c conda-forge montreal-forced-aligner
"$HOME/.local/share/cutpoint-lab/mfa/bin/python" -m pip install \
  spacy-pkuseg dragonmapper hanziconv
```

如果环境没有 `pip`，先用 micromamba 将 `pip` 安装进同一 prefix。当前 provider 使用 `--use_g2p --beam 100`，并自动把 MFA 同目录子命令加入 `PATH`。

```bash
PYTHONPATH=src uv run python -m cutpoint_lab.alignment_benchmark run \
  --cases outputs/alignment-benchmark/20260710/cases.json \
  --output-dir outputs/alignment-benchmark/20260710/run \
  --mfa \
  --mfa-binary "$HOME/.local/share/cutpoint-lab/mfa/bin/mfa"
```

### ElevenLabs Forced Alignment

[ElevenLabs 官方 API](https://elevenlabs.io/docs/api-reference/forced-alignment/create?explorer=true) 接收音频文件和原文本，返回字级与词级起止时间。实验台优先用 `characters[]`，只在字级结果缺失时才退回 `words[]`，避免中文合并词掩盖词内边界。官方能力页明确列出中文支持，但不支持 diarization：[Forced Alignment overview](https://elevenlabs.io/docs/overview/capabilities/forced-alignment)。

Key 只通过当前进程的环境变量传入，不得写入仓库或结果 JSON。

```bash
export ELEVENLABS_API_KEY='replace-with-local-secret'
PYTHONPATH=src uv run python -m cutpoint_lab.alignment_benchmark run \
  --cases outputs/alignment-benchmark/20260710/cases.json \
  --output-dir outputs/alignment-benchmark/20260710/run \
  --elevenlabs
```

没有 Key 时 provider 会为每个 case 记录 `unavailable`，不会退回一个看似有效的假时间戳。
CLI 只允许官方 HTTPS endpoint；`localhost` / loopback 例外只用于不消耗 API 的本地回归测试，防止真实 Key 被转发给第三方主机。

## 3. 人工标注

1. 打开 `review.md`，按 case 播放对应 `clips/*.wav`。
2. 在 `cases.json` 中找到相同 `case_id`。
3. 填写 `acceptable_start_ms` 和 `acceptable_end_ms`，表示这个区间内任意一点切分都不吞前词、不切后词。
4. 时间是相对当前 clip 的毫秒，不是源视频绝对时间。
5. 无法安全切分的区域可写入 `forbidden_intervals`；听不清的 case 应先保持未标注，不猜毫秒值。

`acceptable_*` 与 `forbidden_intervals` 不得重叠。报告会分别统计安全区间命中率和禁区命中率；`accuracy_available` 只在全部 case 都完成 gold 时才为 `true`。

建议两轮标注：第一轮独立听音，第二轮对所有 provider 分歧大于 150ms 的 case 复核。不要在第一轮先看模型预测，避免锚定偏差。

## 4. 生成报告

```bash
PYTHONPATH=src uv run python -m cutpoint_lab.alignment_benchmark report \
  --cases outputs/alignment-benchmark/20260710/cases.json \
  --results-dir outputs/alignment-benchmark/20260710/run/results \
  --output-dir outputs/alignment-benchmark/20260710/run/report
```

看报告时先看三件事：

- `coverage_rate`：能不能在这个中文口语 case 上找到目标边界。
- `safe_interval_hit_rate`：只有人工标注后才有意义。
- `forbidden_hit_rate`：有禁切区标注时，预测落入禁区的比例，越低越好。
- `pairwise agreement`：两个 provider 的预测相差，用于找出值得人工复核的样本，不是准确率。

每个 provider 结果都带 `case_set_fingerprint`，由 WAV 内容哈希、文本、目标词、时间坐标和边界方向共同生成。重建 clip 或修改文本后，旧 provider JSON 会因指纹不匹配而被报告命令拒绝，需要重新运行。

## 5. 当前限制

- case 来自 3 个中文口播样本，不代表全部性别、口音、BGM、噪声和多人 overlap。
- 当前没有人工 gold，不能排准确率名次。
- ElevenLabs 实测依赖外部 Key 和 API 额度；本地没有 Key 时只验证了请求适配器，没有生成真实时间戳。
- 实验只评估“文字已知时的对齐”，不评估 ASR 文字识别正确率。
- 切点投入视频剪辑前，还应在不同编码和帧率上验证实际渲染结果。
