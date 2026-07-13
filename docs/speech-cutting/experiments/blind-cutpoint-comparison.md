---
title: 30 秒多切点盲听对比手册
date: 2026-07-10
status: outdated
audience: both
---

# 30 秒多切点盲听对比手册

> **已被取代**：本轮主要删除静音、语气词或短停顿，用户无法明显感知跳剪。后续已用[句子级跳剪盲听](sentence-level-blind-listening.md)取代。重建脚本和 manifest 仅存在本地 `legacy` 分支。

## 用途

这套流程不依赖人工填写“正确切点区间”，而是把同一段连续中文口播按四种策略各剪一版，交给人直接盲听。四版删除相同的八段文字，只比较切点是否缺字、残留尾音、产生爆音或让语气不自然。

当前固定样本取自 `sample_03.wav` 的 0.24–35.92 秒。原始参照长 35.68 秒，四个剪辑版长约 28.50–29.35 秒。

## 四种策略

1. `funasr_direct`：直接使用 FunASR 词级时间戳。
2. `ctc_char`：使用 MMS-300M CTC 字符级强制对齐。该工具会省略部分“`一`”和阿拉伯数字，因此完整词无法定位时，只允许使用目标词的首字符起点或末字符终点；仍要求切点落在字符边缘。
3. `mfa_word`：使用 Mandarin MFA 词级强制对齐。单侧无法严格定位时才允许该侧回退 FunASR，并在清单中记录原因。
4. `hybrid_safe`：以 CTC 为锚点，在区间外侧 140ms 内寻找明显低能量位置。它只能扩大、不能缩小 CTC 的删除区间，因此不会因追求保留字头字尾而留下 CTC 已识别的待删内容。

所有版本统一使用 4ms 等功率交叉淡化；输出为 48kHz 单声道 WAV（16-bit PCM）和 M4A（192kbps AAC）。

## 生成命令

```bash
PYTHONPATH=src "$HOME/.local/share/uv/tools/ctc-forced-aligner/bin/python" \
  scripts/build_blind_cutpoint_comparison.py \
  --manifest samples/manifests/blind_cutpoint_sample03.json \
  --output-dir outputs/blind-cutpoint-diagnostics/20260710-sample03-30s \
  --listening-dir outputs/blind-cutpoint-listening/20260710-sample03-30s \
  --mfa-binary "$HOME/.local/share/cutpoint-lab/mfa/bin/mfa"
```

脚本会真实运行 CTC 和 MFA。任一真实对齐失败、MFA 全部边界退回 FunASR、两个策略成品完全相同，或任一文件质检不通过，构建都会失败。

## 试听顺序

1. 听 `reference_original.m4a`，熟悉原始内容。
2. 只打开公开试听目录，依次听 `A.m4a` 至 `D.m4a`。
3. 在同目录的 `blind_scorecard.md` 记录缺字、残音、爆音、自然度和时间点。
4. 完成评分后，再到单独的私有诊断目录打开 `blind_key.json` 揭晓策略。

## 产物说明

- 公开试听目录的 `A.m4a`–`D.m4a`：匿名盲听文件；该目录不含算法名、哈希或密钥。
- 私有诊断目录的 `named/*.m4a`、`named/*.wav`：按算法命名的调试文件。
- `cut_manifest.json`：八个语义删除动作、四套真实切点、边界来源和回退原因。
- `qc.json`：解码、时长、采样率、声道、逐切口 RMS、样本跳变和文件指纹。
- `alignments/*.json`：FunASR、CTC、MFA 的原始对齐证据。

## 当前质检门槛

- 四个匿名版本均包含 8 个衔接点。
- 每版时长必须在 28–32 秒。
- WAV 和 M4A 必须完整解码，时长误差分别不超过 80ms 和 120ms。
- 输出必须为 48kHz 单声道，四个算法成品的文件指纹必须互不相同。
- MFA 必须有超过半数边界来自真实 MFA；本次样本实际为 16/16 个边界均由 MFA 解析。
- 混合版的每个删除区间都必须完整覆盖对应 CTC 区间，不允许向内收缩。

## 结果边界

自动质检只能证明文件有效、策略没有串用、切点计算可复现，并不能证明哪个版本听起来最好。最终算法选择仍要以盲听评分为准。ElevenLabs 尚未加入本轮成品；配置 API Key 后可以按同样接口加入第五个具名对比版，或替换现有四版中的一个进行新一轮盲测。
