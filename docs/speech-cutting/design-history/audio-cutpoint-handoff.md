---
title: 音频切点校准 MVP 交接说明
date: 2026-06-07
status: archived
audience: both
---

# 音频切点校准 MVP 交接说明

> 历史交接记录；所述实验 CLI 和原始路径对应 `legacy` 分支。

## 当前目标

在完整 App 之前，先做一个独立实验框架，对比三种切点校准策略：

1. `token_padding`：只用 token/word boundary + padding。
2. `rms_snap`：在边界附近找低能量/静音 gap。
3. `vad_snap`：在边界附近找 VAD non-speech gap。

框架应当能在没有真实视频时跑单元测试；等用户提供视频和字幕后，能通过 CLI 做真实对比。

## 文件约定

- `src/cutpoint_lab/`：实验框架代码。
- `tests/`：单元测试和 CLI smoke test。
- `examples/`：小型 JSON 示例，不放真实大视频。
- `docs/specs/audio-cutpoint-mvp-design.md`：方案设计。
- `docs/specs/audio-cutpoint-handoff.md`：交接说明。

## 关键原则

- AI 不写时间戳，只选择 `segment_id`。
- token/word timestamp 是硬护栏。
- VAD/RMS 只用于边界吸附。
- 找不到可靠 gap 时保守多留，不要切掉语音。
- 所有切点调整都要记录原因和置信度。
- 音频处理测试不能只看文件大小，要检查实际内容，例如 RMS/音量不应接近纯静音。
- `selected_segment_ids` 里不能有未知片段 ID；出现时应停止，而不是导出缺片段的视频。
- ASR token 即使乱序，也必须先按时间排序再计算边界。
- 所有输出边界都必须 clamp 到媒体时长内。

## 待接入真实素材时的步骤

1. 把视频放到项目外或 `samples/`，不要提交大文件。
2. 用 CLI 提取音频：

   ```bash
   python -m cutpoint_lab.cli extract-audio input.mp4 /tmp/cutpoint_audio.wav
   ```

3. 准备 transcript JSON，至少包含 `segments` 和 `selected_segment_ids`。
4. 可选准备 VAD JSON；没有 VAD 时 `vad_snap` 会回退到 baseline。
5. 跑三方案对比：

   ```bash
   python -m cutpoint_lab.cli compare \
     --transcript examples/sample_transcript.json \
     --audio /tmp/cutpoint_audio.wav \
     --vad examples/sample_vad.json \
     --out /tmp/cutpoint_compare.json
   ```

6. 人工试听边界，标注哪套策略更自然。

## 下一步扩展

- 接入 faster-whisper 生成 token timestamps。
- 接入 Silero VAD 生成 speech intervals。
- 加视频导出 smoke test：按 ClipPlan 用 FFmpeg 重编码导出小片段。
- 加人工评估表：吃字、气口、废话保留、音画同步。
