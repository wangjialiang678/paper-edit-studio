---
title: 语音切分历史测试方案
date: 2026-07-13
status: archived
audience: both
tags: [testing, cutpoint, alignment, legacy]
---

# 语音切分历史测试方案

> 本文从项目总测试方案中拆出，保存历史语音切点和对齐基准的验证要求。对齐基准和盲听生成代码已在 `legacy` 分支，本文不代表 main 分支当前命令可直接运行。

## 音频切点校准 MVP

| 场景 | 预期行为 |
|---|---|
| token padding baseline | 有 token 时提前/延后保护，无 token 时回退 segment 边界 |
| 相邻片段合并 | 间隔小于 merge gap 时合并 |
| RMS 低能量吸附 | 有可用 gap 时吸附，无 gap 时回退 token 护栏 |
| VAD non-speech 吸附 | 使用独立 non-speech gap；缺少 VAD 时保守回退 |
| 未知片段 ID | 抛错，不静默忽略 |
| token 乱序 | 排序后再计算边界 |
| 媒体时长 clamp | 所有范围不得超过媒体时长 |
| 音频内容验证 | 不只检查文件存在，还要验证可解码、采样率、声道和 RMS |

## 中文词边界对齐实验台

| 场景 | 预期行为 |
|---|---|
| case 数据校验 | 越界时间、非法相对路径、重叠 gold/禁切区被拒绝 |
| 中文分词差异 | 使用 `target_occurrence` 在字级/词级结果中找正确目标 |
| 无人工 gold | 只算覆盖率和两两分歧，不算准确率 |
| ElevenLabs 缺 Key | case 显式记为 `unavailable`，不伪造时间戳 |
| 命令型 provider | 成功、缺命令、超时和非零退出互相隔离 |
| MFA 特殊 token | 过滤 `<eps>` / `<unk>`，目标未命中时记失败 |
| 中文 CTC 粒度 | 使用 `char`，不把整句当一词 |
| 24 case 构建 | 生成 24 个唯一 case、24 个可解码 WAV 和审阅清单 |
| 报告一致性 | JSON/CSV/Markdown 计数一致 |
| 旧结果拒绝 | WAV/文本指纹变化时拒绝混用历史 provider JSON |
| 禁切区 | 预测落入 forbidden interval 时单独统计 |

## 历史真实运行检查

- `cases.json` 包含 24 个不重复 `case_id`，24 个局部 WAV 可解码。
- FunASR、CTC、MFA、ElevenLabs 各自输出独立 JSON，任一 provider 失败不影响其他 provider。
- 报告中 provider 覆盖数不超过 24，两两可比数不超过双方有效数。
- 未填写 `acceptable_start_ms` / `acceptable_end_ms` 时，`accuracy_available` 必须为 `false`。
- 当时使用的 CTC 默认权重为非商业许可，不得直接并入商业产品。

## 盲听验收

- 公开试听目录不得出现算法名、文件哈希、具名成品或盲键。
- 匿名版必须完整解码，输出时长、声道、采样率和接缝数与 manifest 一致。
- 各策略成品必须互不相同，避免因拷贝错误生成伪对比。
- 自动 QC 只能证明产物有效、切点可复现和语义删除完整，不能代替最终听感。
