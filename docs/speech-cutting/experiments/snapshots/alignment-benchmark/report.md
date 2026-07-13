---
title: 中文边界对齐实验原始报告快照
date: 2026-07-10
status: archived
audience: both
tags: [snapshot, alignment, benchmark]
---

# Alignment Benchmark Report

- Cases: 24
- Human-annotated cases: 0

> Accuracy ranking is unavailable because no case has a human acceptable interval.
> Provider agreement below is not evidence of correctness.

| Provider | OK | Unavailable | Failed | Coverage | Safe hit | Forbidden hit | P50 error ms | P95 error ms |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| ctc_mms_300m | 24 | 0 | 0 | 1 | — | — | — | — |
| elevenlabs_forced_alignment | 0 | 24 | 0 | 0 | — | — | — | — |
| funasr | 24 | 0 | 0 | 1 | — | — | — | — |
| mfa_mandarin | 19 | 0 | 5 | 0.7917 | — | — | — | — |

## Pairwise agreement

- ctc_mms_300m vs elevenlabs_forced_alignment: n=0, P50=— ms, P95=— ms
- ctc_mms_300m vs funasr: n=24, P50=120 ms, P95=314 ms
- ctc_mms_300m vs mfa_mandarin: n=19, P50=130 ms, P95=281 ms
- elevenlabs_forced_alignment vs funasr: n=0, P50=— ms, P95=— ms
- elevenlabs_forced_alignment vs mfa_mandarin: n=0, P50=— ms, P95=— ms
- funasr vs mfa_mandarin: n=19, P50=50 ms, P95=92 ms
