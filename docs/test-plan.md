---
title: Paper Edit Studio 测试方案
date: 2026-07-13
status: active
audience: both
tags: [testing, studio, video-editing]
---

# Paper Edit Studio 测试方案

> 语音切点校准、对齐基准台和盲听测试已集中到 [语音切分测试与资产索引](speech-cutting/experiments/test-and-artifact-inventory.md)。本文只保留当前 main 分支的 Studio 产品测试。

## AI 自动剪辑闭环

| # | 场景 | 输入 | 预期输出 | 类型 |
|---|---|---|---|---|
| 1 | 提示词主题筛选 | transcript + 自定义 prompt | 候选只引用已有 segment_id，不生成新时间戳 | unit |
| 2 | 人工确认包 | transcript + prompt | 写出 candidates JSON、review Markdown、全文字幕 Markdown | unit |
| 3 | 候选转 ClipPlan | confirmed candidate ids | 输出 selected_segment_ids 和可导出 ranges | unit |
| 4 | 视频合成 | 临时生成 mp4 + clip plan | FFmpeg 导出 edited.mp4，时长接近计划范围 | integration |
| 5 | 字幕导出 | transcript + clip plan | 生成按剪辑后时间线重排的 SRT | unit |

## 本地纸面剪辑 Web 工具

| # | 场景 | 输入 | 预期输出 | 类型 |
|---|---|---|---|---|
| 1 | AI 默认勾选 | transcript + candidates | rows 中只勾选推荐 candidate 覆盖的 segment | unit |
| 2 | 字幕文本编辑 | rows 修改 text | 保存后的 transcript 保留 token 时间戳并更新 text | unit |
| 3 | 词级时间戳强校验 | 选中无 token 的 segment | 拒绝生成导出计划 | unit |
| 4 | 预览计划生成 | 选中有 token 的 rows + 切点策略 | 生成 ClipPlan，供页面跳播 | unit |
| 5 | 本地媒体 Range | 浏览器请求 `/media/source` | 返回 206，支持视频拖动和跳播 | smoke |
| 6 | 端到端导出 | 词级 transcript + source media | 生成剪辑视频和 SRT | integration |

## 运行命令

```bash
scripts/run_tests.py
```

## 当前边界

- ASR 仍复用 DashScope 转写结果或外部 ASR 脚本；缺少凭据时不自动转写真实视频。
- 视频导出默认重编码后 concat，优先保证切点可用和跨素材稳定性。
- 当前切点策略的专项测试边界见 [语音切分实验总结](speech-cutting/experiments/experiment-summary.md)。
