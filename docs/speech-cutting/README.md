---
title: 语音切分算法资料库
date: 2026-07-13
status: active
audience: both
tags: [speech-cutting, cutpoint, alignment, voiceprint, testing]
---

# 语音切分算法资料库

> 本目录是项目内语音切分算法的唯一文档入口，集中保存问题定义、讨论结论、学术与工程调研、实验记录、历史设计和后续路线。

## 建议阅读顺序

1. [当前结论](conclusions.md)：现在已经知道什么，哪些事还不能下结论。
2. [讨论记录](discussion-record.md)：从初始动机到盲听和声纹过滤假设的演进过程。
3. [实验总结](experiments/experiment-summary.md)：实验设计、数据、客观结果和主观反馈。
4. [测试与资产索引](experiments/test-and-artifact-inventory.md)：当前代码、`legacy` 分支、样本和试听产物的位置。
5. [后续路线](roadmap.md)：未来真正值得继续做的实验。

## 目录结构

| 目录 | 内容 | 使用方式 |
|---|---|---|
| `research/` | 声纹、VAD、强制对齐、video-use 等调研 | 理解原理和备选方案 |
| `experiments/` | 24 边界对齐、30 秒盲听、整句跳剪、测试与资产索引 | 回看证据和复现条件 |
| `design-history/` | 已完成的设计、实施计划、TDD 方案和交接文档 | 仅供追溯，不代表当前代码 |
| `archive/` | 已被更完整报告取代的早期调研 | 历史参考 |
| `sources/` | video-use 等原始资料 | 溯源 |

## 一句话现状

当前 Studio 使用 `hybrid_valley` 作为工程默认。历史整句盲听比较的是另一个 `hybrid_safe`、FunASR、CTC 和 MFA；用户没有听出四版明显差别。因此该实验既不能给四者排名，也不能证明当前 `hybrid_valley` 更好。后续应收集真实失败切点，再决定是否引入 TS-VAD、目标说话人提取或更强的对齐模型。

## 代码与实验位置

- 当前产品代码和默认切点策略在 `main` 分支。
- 对齐基准台、盲听生成器、说话人脚本和完整历史测试在本地 `legacy` 分支。
- 试听音频和诊断 JSON 在本地 `outputs/`，共约 210MB，被 `.gitignore` 排除，不属于 Git 备份。
- 关键数字和用户听感已固化到本目录的实验文档，即使本地输出丢失，结论仍可追溯。

> `legacy` 目前是本地分支，没有上游跟踪分支。是否推送到远程属于对外发布，本次整理没有执行。
