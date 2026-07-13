---
title: 早期切点策略代理交付索引
date: 2026-06-07
status: archived
audience: both
tags: [handoff, cutpoint, testing, legacy]
---

# 代理交付约定

> 本目录保存早期 token padding、RMS、VAD 和 evaluator 四个并行实现任务的原始交付 JSON。其中路径和测试命令对应 `legacy` 分支。

每个并行代理完成后，在本目录写一个交付文件，文件名固定：

- `agent-a-token-padding.json`
- `agent-b-rms-snap.json`
- `agent-c-vad-snap.json`
- `agent-d-evaluator.json`

JSON 格式：

```json
{
  "agent": "A",
  "task": "token_padding",
  "status": "done",
  "files_changed": [],
  "tests_added": [],
  "test_command": "scripts/run_tests.py",
  "test_result": "pass",
  "known_issues": [],
  "notes": ""
}
```

如果任务阻塞：

```json
{
  "agent": "A",
  "task": "token_padding",
  "status": "blocked",
  "files_changed": [],
  "tests_added": [],
  "test_command": "",
  "test_result": "not_run",
  "known_issues": ["阻塞原因"],
  "notes": "需要人工处理的事项"
}
```

集成负责人会自动扫描本目录，并结合 `/tmp/cutpoint_baseline_manifest.json` 对比实际文件变化。
