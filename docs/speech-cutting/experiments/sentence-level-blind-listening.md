---
title: 句子级跳剪盲听与切点放大镜手册
date: 2026-07-10
status: active
audience: human
---

# 句子级跳剪盲听与切点放大镜手册

> **当前实验结果**：四个版本均完整删除目标句并通过自动 QC。用户最终反馈是 A/B/C/D 听不出明显区别。因此这个样本无法给四种方案排名，也不能单凭“没听出差别”就证明它们已在其他场景可用。完整数据见 [实验总结](experiment-summary.md)。

> **复现条件**：下方重建命令依赖的脚本和 manifest 已移到本地 `legacy` 分支，不能在当前 main 分支直接运行。

## 这次测试什么

本实验不再删除静音、语气词或短停顿，而是从一段连续录音中完整删除四句话。原始九句话保留第 1、3、5、7、9 句，删除第 2、4、6、8 句，四种算法只决定句子交界处的精确切点。

原始片段长 48.65 秒，四个匿名成品约 30–31 秒，每版都有四次明显的句子级跳剪。

## 推荐试听顺序

1. 打开公开目录的 `reference_original.m4a`，先听完整九句话。
2. 打开 `full/`，依次听 A、B、C、D，判断整体节奏。
3. 进入 `cut-01` 至 `cut-04`。每个目录先听 `reference_original.m4a`，再听 A、B、C、D。
4. 局部 A–D 都是三秒，切口固定在约 1.2 秒处，不需要在长音频里猜位置。
5. 在 `blind-scorecard.md` 记录吞字、残句、爆音和自然度；完成评分后再打开私有诊断目录的 `blind_key.json`。

## 完整删除的四句话

1. 嗯，但是问题是产品经理是完全用一个健全人的思维在在在思考。
2. 而且这个好像上上去反馈，他要修改的话，呃，我不知道排期会排到什么时候啊。
3. 嗯，啊，但是这些吐槽的信息他们不知道怎么样才能够让这些做工具的人真正看到他们的这些反馈，对。
4. 对对对。

## 输出隔离

- `outputs/sentence-cutpoint-listening/20260710-sample03-s24-s32/`：只含匿名音频、原始参考、指南和评分表。
- `outputs/sentence-cutpoint-diagnostics/20260710-sample03-s24-s32/`：包含算法名、对齐结果、切点、QC、哈希和揭晓密钥，不应在盲听前查看。

## 重建命令

```bash
PYTHONPATH=src "$HOME/.local/share/uv/tools/ctc-forced-aligner/bin/python" \
  scripts/build_blind_cutpoint_comparison.py \
  --manifest samples/manifests/blind_sentence_cut_sample03.json \
  --output-dir outputs/sentence-cutpoint-diagnostics/20260710-sample03-s24-s32 \
  --listening-dir outputs/sentence-cutpoint-listening/20260710-sample03-s24-s32 \
  --mfa-binary "$HOME/.local/share/cutpoint-lab/mfa/bin/mfa"
```

## 自动验收

- 四个完整版本必须各有四个跳切，时长在 28–32 秒，48kHz 单声道 AAC，可完整解码。
- 每个策略的删除区间必须完整覆盖该策略对齐出的待删句首尾。
- 每个 `cut-NN` 必须有原始+A/B/C/D 五个文件；A–D 均为三秒。
- CTC 和 MFA 必须真实运行；MFA 的真实边界必须超过半数。
- 混合版必须完整覆盖被删句且不得进入两侧保留句；四个完整成品必须互不相同。
- 公开目录不得出现算法名、哈希、具名成品或映射密钥。

自动验收不能代替最终听感；算法优胜者仍由逐切口盲听结果决定。
