---
title: 句子级跳剪盲听实施计划
date: 2026-07-10
status: archived
audience: both
tags: [implementation-plan, sentence-deletion, blind-test, legacy]
---

# Sentence-Level Blind Cutpoint Comparison Implementation Plan

> 本计划已完成并归档；文中路径与命令对应 `legacy` 分支。

**Status:** Implemented and verified on 2026-07-10.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a real four-way blind comparison that keeps sentences 1/3/5/7/9, completely removes sentences 2/4/6/8, and supplies four cut-by-cut magnifier groups.

**Architecture:** Reuse the existing alignment and rendering pipeline with a new sentence-level manifest, while removing the hard-coded eight-cut assumption. Add a focused `blind_zoom` module that computes output join positions and extracts anonymous three-second clips around each join. The public listening tree contains only anonymous audio and guides; named outputs, mappings, alignments, QC, and fingerprints remain in a separate diagnostics tree.

**Tech Stack:** Python 3.11+, standard library, existing `cutpoint_lab.blind_comparison`, real CTC forced aligner, MFA 3.4, FFmpeg/FFprobe, `unittest`.

---

### Task 1: Sentence-level manifest and semantic validation

**Files:**
- Create: `samples/manifests/blind_sentence_cut_sample03.json`
- Modify: `src/cutpoint_lab/blind_comparison.py`
- Modify: `tests/test_blind_comparison.py`

- [ ] Write a failing test requiring window `105650–154300ms`, exactly four removal ranges (`sentence_0025`, `0027`, `0029`, `0031`), and an estimated FunASR result between 28–32 seconds.
- [ ] Add `experiment_type` to `ComparisonSpec`, defaulting to `boundary_edits`, and validate `sentence_deletion` manifests.
- [ ] Create the manifest with blind seed `2026071024`; each removal covers every token in its deleted sentence.
- [ ] Run `uv run python -m unittest tests.test_blind_comparison -v` and require the new manifest tests to pass.

### Task 2: Generic join-count QC and cut-position math

**Files:**
- Create: `src/cutpoint_lab/blind_zoom.py`
- Create: `tests/test_blind_zoom.py`
- Modify: `scripts/build_blind_cutpoint_comparison.py`

- [ ] Write failing unit tests for `join_output_positions()`: two internal deletions must yield two ordered join centers after accounting for the 4ms crossfade.
- [ ] Implement join-position calculation from complement keep ranges; reject plans whose removal count does not match the semantic removals.
- [ ] Replace builder checks for exactly eight joins/labels with `expected_join_count=len(removals)`.
- [ ] Run the focused tests and retain the previous eight-cut manifest behavior.

### Task 3: Cutpoint magnifier package

**Files:**
- Modify: `src/cutpoint_lab/blind_zoom.py`
- Modify: `tests/test_blind_zoom.py`
- Modify: `scripts/build_blind_cutpoint_comparison.py`

- [ ] Write a failing FFmpeg integration test that creates `cut-01` and `cut-02`, each with `reference_original.m4a` and anonymous `A.m4a`–`D.m4a`; anonymous clips must decode and last about 3 seconds.
- [ ] Implement `extract_audio_clip()` and `create_cutpoint_zoom_package()` using 1.2 seconds before and 1.8 seconds after each rendered join.
- [ ] Extract each reference clip from the unedited source window using the FunASR semantic interval plus the same left/right context.
- [ ] Return private zoom metadata containing source intervals and per-strategy join positions without writing mappings into the public directory.
- [ ] Add public-directory allow-list validation for `reference_original.m4a`, `full/A–D`, `cut-01`–`cut-04`, `listening-guide.md`, and `blind-scorecard.md`.

### Task 4: Real sentence-level alignment and rendering

**Files:**
- Generate: `outputs/sentence-cutpoint-listening/20260710-sample03-s24-s32/**`
- Generate: `outputs/sentence-cutpoint-diagnostics/20260710-sample03-s24-s32/**`

- [ ] Run the builder in the CTC environment with the new manifest and separate public/private directories.
- [ ] Require real CTC and MFA alignments, more than half of MFA sides resolved by MFA, four unique strategy outputs, and a hybrid plan that covers every removed sentence without entering kept-sentence guards.
- [ ] Require all four full anonymous files to decode at 48kHz mono AAC and last 28–32 seconds.
- [ ] Require all 20 magnifier audio files to decode; each anonymous cut clip must be approximately 3 seconds and every folder must contain exactly five audio files.

### Task 5: Documentation, independent review, and final verification

**Files:**
- Create: `docs/handbook/sentence-level-blind-listening.md`
- Review all files above.

- [ ] Document the keep/delete sentence list, listening order, public/private separation, and exact rebuild command.
- [ ] Run `uv run python -m unittest discover -s tests -v` with zero failures.
- [ ] Run an independent read-only review for sentence-set fairness, provider leakage, join-position math, zoom extraction, public mapping leakage, and false-success paths.
- [ ] Fix every high/medium finding with regression coverage, rerun full tests, decode every public M4A, and deliver only anonymous links plus a separately marked spoiler key.
