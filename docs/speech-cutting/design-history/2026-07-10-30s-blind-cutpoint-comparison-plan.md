---
title: 30 秒多切点盲听实施计划
date: 2026-07-10
status: archived
audience: both
tags: [implementation-plan, blind-test, legacy]
---

# 30-Second Blind Cutpoint Comparison Implementation Plan

> 本计划已完成并归档；文中路径与命令对应 `legacy` 分支。

**Status:** Completed on 2026-07-10. Independent review findings were incorporated before final delivery.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce a reproducible 28–32 second blind-listening package with eight identical semantic removals rendered using FunASR, CTC, MFA, and hybrid-safe cut boundaries.

**Architecture:** A focused `blind_comparison` module loads one manifest, extracts a complete-token source window, normalizes provider alignments, converts the same removal specs into four edit plans, renders identical keep intervals, and writes blind copies plus QC metadata. The real runner executes inside the installed CTC research environment so the CTC model loads once; MFA remains an isolated subprocess.

**Tech Stack:** Python 3.11+, standard library, existing `cutpoint_lab` transcript/alignment/RMS modules, FFmpeg/FFprobe, optional `ctc-forced-aligner`, MFA 3.4.

**Repository note:** This workspace is not a Git repository, so commit steps are intentionally omitted; verification artifacts and exact commands are retained instead.

---

### Task 1: Manifest and semantic removal model

**Files:**
- Create: `samples/manifests/blind_cutpoint_sample03.json`
- Create: `src/cutpoint_lab/blind_comparison.py`
- Create: `tests/test_blind_comparison.py`

- [ ] **Step 1: Write failing manifest tests** that load the sample-03 window `240–35920ms`, resolve eight removal token ranges, reject overlap, and require a kept token on both sides of every removal.
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_blind_comparison -v` and confirm the missing module fails.
- [ ] **Step 3: Implement immutable models** `TokenRef`, `RemovalSpec`, `ComparisonSpec`, and `ResolvedRemoval`, plus `load_comparison_spec()` and `resolve_removals()`.
- [ ] **Step 4: Re-run the focused test** and require eight ordered, non-overlapping removals whose FunASR estimate leaves 28–32 seconds.

The manifest will use these exact semantic removals:

```json
[
  {"label":"remove_question","start":{"segment_id":"sentence_0003","token_index":0},"end":{"segment_id":"sentence_0003","token_index":7}},
  {"label":"remove_intro_filler_1","start":{"segment_id":"sentence_0005","token_index":0},"end":{"segment_id":"sentence_0005","token_index":1}},
  {"label":"remove_inner_er","start":{"segment_id":"sentence_0005","token_index":3},"end":{"segment_id":"sentence_0005","token_index":3}},
  {"label":"remove_intro_filler_2","start":{"segment_id":"sentence_0006","token_index":0},"end":{"segment_id":"sentence_0006","token_index":1}},
  {"label":"remove_intro_filler_3","start":{"segment_id":"sentence_0007","token_index":0},"end":{"segment_id":"sentence_0007","token_index":1}},
  {"label":"remove_redundant_prompt","start":{"segment_id":"sentence_0007","token_index":10},"end":{"segment_id":"sentence_0007","token_index":13}},
  {"label":"remove_terminal_filler","start":{"segment_id":"sentence_0008","token_index":6},"end":{"segment_id":"sentence_0008","token_index":6}},
  {"label":"remove_repeated_lead_in","start":{"segment_id":"sentence_0009","token_index":4},"end":{"segment_id":"sentence_0009","token_index":7}}
]
```

### Task 2: Provider boundary planning and fallback

**Files:**
- Modify: `src/cutpoint_lab/blind_comparison.py`
- Modify: `tests/test_blind_comparison.py`

- [ ] **Step 1: Write failing tests** using fake alignments for: exact FunASR boundaries, CTC character boundaries, MFA coarser tokens, `<unk>` ambiguity, provider fallback, and repeated target occurrences.
- [ ] **Step 2: Run the focused test** and confirm missing planning functions fail.
- [ ] **Step 3: Implement** `build_provider_plan()` using `locate_target_boundary(reference_text=...)`. Each removal starts at the aligned end of the kept token on its left and ends at the aligned start of the kept token on its right; an unresolved MFA side falls back only for that side to FunASR and records the reason.
- [ ] **Step 4: Implement** `build_hybrid_plan()` with outward-only RMS searches: the removal may expand by at most 140ms on either side, must never shrink the CTC interval, and remains unchanged when local energy evidence is weak.
- [ ] **Step 5: Re-run focused tests** and require eight valid, ordered removal intervals for every strategy.

### Task 3: Deterministic audio rendering and blind package

**Files:**
- Modify: `src/cutpoint_lab/blind_comparison.py`
- Modify: `tests/test_blind_comparison.py`
- Create: `scripts/build_blind_cutpoint_comparison.py`

- [ ] **Step 1: Write a failing FFmpeg integration test** using a synthetic WAV, two removals, and a 4ms equal-power crossfade; assert decode success and expected duration within 80ms.
- [ ] **Step 2: Implement** source-window extraction, complement keep-range calculation, FFmpeg `atrim` + chained `acrossfade`, 48kHz mono PCM WAV output, and 192kbps AAC/M4A transcode.
- [ ] **Step 3: Implement deterministic blind mapping** with seed `20260710`; write only A–D, the reference, scorecard, and README to a public listening directory, while keeping `blind_key.json`, named files, QC, and cut manifests in a separate private diagnostics directory.
- [ ] **Step 4: Add QC** for FFprobe duration, stream type, join count, semantic-removal labels, RMS around joins, and source/result fingerprints.
- [ ] **Step 5: Re-run focused tests** and require all renderer/package tests to pass.

### Task 4: Real 35-second alignment and render

**Files:**
- Generate: `outputs/blind-cutpoint-listening/20260710-sample03-30s/**`
- Generate: `outputs/blind-cutpoint-diagnostics/20260710-sample03-30s/**`

- [ ] **Step 1: Run the builder** inside the installed CTC environment:

```bash
PYTHONPATH=src "$HOME/.local/share/uv/tools/ctc-forced-aligner/bin/python" \
  scripts/build_blind_cutpoint_comparison.py \
  --manifest samples/manifests/blind_cutpoint_sample03.json \
  --output-dir outputs/blind-cutpoint-diagnostics/20260710-sample03-30s \
  --listening-dir outputs/blind-cutpoint-listening/20260710-sample03-30s \
  --mfa-binary "$HOME/.local/share/cutpoint-lab/mfa/bin/mfa"
```

- [ ] **Step 2: Confirm** CTC and MFA return real alignments, every provider has eight joins, fallbacks are explicit, and no provider silently reuses another provider's full plan.
- [ ] **Step 3: Confirm** all four anonymous M4A files decode, contain audio, and fall within 28–32 seconds.
- [ ] **Step 4: Inspect** per-join RMS/discontinuity QC and re-render only if a deterministic render or boundary-mapping defect is found; do not tune one version by subjective preference.

### Task 5: Independent review and final verification

**Files:**
- Review all files created or modified in Tasks 1–4.

- [ ] **Step 1: Run** `uv run python -m unittest discover -s tests -v`; require zero failures and zero skips.
- [ ] **Step 2: Run an independent read-only review** for semantic fairness, provider leakage, blind mapping reproducibility, FFmpeg correctness, and false-success paths.
- [ ] **Step 3: Fix high/medium findings with regression tests**, then repeat full tests and artifact QC.
- [ ] **Step 4: Deliver** clickable local links to `reference_original.m4a`, blind `A–D`, the scorecard, and keep the mapping key separate so the user can listen before revealing it.
