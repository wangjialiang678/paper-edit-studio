---
title: 中文语音边界对齐实验台实施计划
date: 2026-07-10
status: archived
audience: both
tags: [implementation-plan, tdd, forced-alignment]
---

# Chinese Boundary Alignment Benchmark Implementation Plan

> 本计划已完成并作为历史记录保留；文中原始路径对应 `legacy` 分支。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a repeatable benchmark that compares normalized word-boundary outputs from FunASR/precomputed JSON, ElevenLabs Forced Alignment, MFA, and command-based CTC aligners against human acceptable intervals.

**Architecture:** A new `cutpoint_lab.alignment_benchmark` package owns benchmark-only models, provider adapters, case generation, metrics, and reporting. Providers never call existing cut strategies; they return normalized words and explicit status so unavailable external systems cannot silently affect rankings.

**Tech Stack:** Python 3.11+, standard library, existing `unittest`, FFmpeg, optional external MFA/CTC commands, ElevenLabs HTTP API.

---

### Task 1: Benchmark models and validation

**Files:**
- Create: `src/cutpoint_lab/alignment_benchmark/__init__.py`
- Create: `src/cutpoint_lab/alignment_benchmark/models.py`
- Test: `tests/test_alignment_benchmark_models.py`

- [ ] **Step 1: Write failing model tests** for valid/invalid case intervals, provider status, target occurrence, and serialization.
- [ ] **Step 2: Run** `uv run python -m unittest tests.test_alignment_benchmark_models -v` and verify import failures.
- [ ] **Step 3: Implement frozen dataclasses** `BoundaryCase`, `AlignedWord`, `AlignmentResult`, and `BoundaryObservation`, with `to_dict/from_dict` and millisecond validation.
- [ ] **Step 4: Re-run the focused tests** and expect all model tests to pass.

### Task 2: Target mapping and metrics

**Files:**
- Create: `src/cutpoint_lab/alignment_benchmark/metrics.py`
- Test: `tests/test_alignment_benchmark_metrics.py`

- [ ] **Step 1: Write failing tests** for repeated target text, start/end side selection, safe interval hit, distance to interval, unannotated cases, P50/P95, and unavailable provider exclusion.
- [ ] **Step 2: Run the focused test** and verify missing functions fail.
- [ ] **Step 3: Implement** exact normalized-text matching, occurrence selection, observation construction, aggregation, and pairwise provider agreement.
- [ ] **Step 4: Re-run focused tests** and expect all metrics tests to pass.

### Task 3: Precomputed and payload parsers

**Files:**
- Create: `src/cutpoint_lab/alignment_benchmark/providers/__init__.py`
- Create: `src/cutpoint_lab/alignment_benchmark/providers/base.py`
- Create: `src/cutpoint_lab/alignment_benchmark/providers/precomputed.py`
- Create: `src/cutpoint_lab/alignment_benchmark/providers/parsers.py`
- Test: `tests/test_alignment_benchmark_providers.py`

- [ ] **Step 1: Write failing tests** for standard `words[]`, CTC `segments[]`, MFA `tiers.*.entries`, invalid intervals, and provider failure isolation.
- [ ] **Step 2: Run focused tests** and verify missing providers fail.
- [ ] **Step 3: Implement** `AlignmentProvider`, `PrecomputedProvider`, and parser functions returning normalized `AlignedWord` objects.
- [ ] **Step 4: Re-run focused tests** and expect parser/provider tests to pass.

### Task 4: ElevenLabs Forced Alignment provider

**Files:**
- Create: `src/cutpoint_lab/alignment_benchmark/providers/elevenlabs.py`
- Test: `tests/test_alignment_benchmark_elevenlabs.py`

- [ ] **Step 1: Write failing tests** using a local HTTP server for multipart fields, API key header, word/loss parsing, missing key, HTTP failure, and secret redaction.
- [ ] **Step 2: Run focused tests** and verify provider import fails.
- [ ] **Step 3: Implement** standard-library multipart POST with injectable endpoint and timeout; never serialize the key.
- [ ] **Step 4: Re-run focused tests** and expect all ElevenLabs tests to pass without network access.

### Task 5: MFA and command CTC providers

**Files:**
- Create: `src/cutpoint_lab/alignment_benchmark/providers/command.py`
- Create: `src/cutpoint_lab/alignment_benchmark/providers/mfa.py`
- Test: `tests/test_alignment_benchmark_commands.py`

- [ ] **Step 1: Write failing tests** with temporary executable scripts for successful JSON output, missing command, timeout, non-zero exit, MFA JSON parsing, and command placeholder expansion.
- [ ] **Step 2: Run focused tests** and verify missing implementations fail.
- [ ] **Step 3: Implement** `CommandAlignmentProvider` and `MfaAlignmentProvider`; subprocess environment is explicit and results distinguish `failed` from `unavailable`.
- [ ] **Step 4: Re-run focused tests** and expect command tests to pass.

### Task 6: Build 24 review cases

**Files:**
- Create: `src/cutpoint_lab/alignment_benchmark/case_builder.py`
- Test: `tests/test_alignment_benchmark_case_builder.py`
- Create: `samples/manifests/alignment_benchmark_samples.json`

- [ ] **Step 1: Write failing tests** for deterministic gap-stratified selection, clip-relative timestamps, duplicate avoidance, FFmpeg failure, and exact requested count.
- [ ] **Step 2: Run focused tests** and verify missing builder fails.
- [ ] **Step 3: Implement** transcript loading, short/medium/long gap stratification, context extraction, WAV clipping, `cases.json`, and annotation template generation.
- [ ] **Step 4: Re-run focused tests** and expect builder tests to pass.

### Task 7: Runner, report, and CLI

**Files:**
- Create: `src/cutpoint_lab/alignment_benchmark/runner.py`
- Create: `src/cutpoint_lab/alignment_benchmark/report.py`
- Create: `src/cutpoint_lab/alignment_benchmark/__main__.py`
- Test: `tests/test_alignment_benchmark_cli.py`

- [ ] **Step 1: Write failing CLI tests** for `build-cases`, `run`, and `report`, including unannotated warning and unavailable-provider reporting.
- [ ] **Step 2: Run focused tests** and verify CLI module is missing.
- [ ] **Step 3: Implement CLI** with JSON outputs, CSV summary, Markdown report, and deterministic exit codes.
- [ ] **Step 4: Re-run focused tests** and expect all CLI tests to pass.

### Task 8: Real package generation and project documentation

**Files:**
- Modify: `docs/test-plan.md`
- Modify: `README.md`
- Create: `docs/handbook/alignment-benchmark.md`
- Generate: `outputs/alignment-benchmark/20260710/cases.json`
- Generate: `outputs/alignment-benchmark/20260710/review.md`
- Generate: `outputs/alignment-benchmark/20260710/clips/*.wav`

- [ ] **Step 1: Run the full test suite** with `uv run python -m unittest discover -s tests -v`; expect zero failures and zero skips.
- [ ] **Step 2: Build 24 real draft cases** from the three existing sample transcripts and WAV files.
- [ ] **Step 3: Run available providers**; record absent ElevenLabs Key and missing MFA/CTC commands as `unavailable` without fabricated scores.
- [ ] **Step 4: Generate report package** and verify JSON/CSV/Markdown can be opened and counts match.
- [ ] **Step 5: Update docs** with exact commands, external prerequisites, annotation workflow, and interpretation limits.

### Task 9: Independent review and final verification

**Files:**
- Review all files created or modified by Tasks 1–8.

- [ ] **Step 1: Run independent read-only code review** for correctness, secret handling, subprocess safety, metric validity, and false-success paths.
- [ ] **Step 2: Fix every high-priority finding** with a failing regression test first.
- [ ] **Step 3: Run focused tests, then full suite**; expect zero failures and zero skips.
- [ ] **Step 4: Verify output package** reports unannotated/external-unavailable limitations truthfully.
