---
title: 字幕质量闭环（transcript quality）整体设计
date: 2026-07-22
status: approved
audience: both
---

# 字幕质量闭环：架构 / 技术方案 / 体验设计

## 动机

字幕（transcript）是本工具的源真相：剪辑决策、成片字幕、导出 SRT 全部由它派生。质量闭环的目标：

1. **机器自动修确定的**：纠错词典命中、AI 高置信纠正——不打扰人；
2. **人只裁决不确定的**：低置信 + 上下文可疑 + 专名不确定 → 高亮 + 建议，一键采纳；
3. **每次人工纠正反哺系统**：追加纠错词典、提示加热词——同一个错误不犯第二次；
4. **能力模块化**：引擎层独立包，UI 与 CLI 同权调用，可单独复用，不与剪辑业务耦合。

## 架构

新引擎子包 `src/cutpoint_lab/quality/`（只依赖 models/io/llm_client/vocabulary，不依赖 studio 业务）：

```
quality/
  __init__.py        门面：analyze() / apply_changes() / undo_changes() / suggest_hotwords()
  corrections.py     纠错词典（CorrectionSet）存取 + 确定性批量替换（生成 ChangeSet）
  confidence.py      词/句级置信度扫描（低置信片段 → issue）
  ai_review.py       LLM 复核：上下文+置信度+已知专名 → 自动纠正/存疑高亮（协议下沉，随解析器维护）
  align_reference.py 外部字幕（SRT/VTT）时间对齐 + 差异 issue（参考校对，不做数据源）
```

### 统一数据契约（跨 UI/CLI 的通用语言）

- **Issue**（质检问题，所有检查器的统一输出）：
  `{segment_id, kind: low_confidence|dict_hit|ai_suspect|term_candidate|ref_mismatch, span: {text, token_start?, token_end?}, suggestion?: str, confidence: 0-1, reason, source: dict|ai|confidence|reference}`
- **QualityReport**：`{project_id, generated_at, issues: [Issue], stats}` → 存 `workspace/<id>/quality_report.json`
- **CorrectionSet**（纠错词典，全局）：`workspace/_settings/corrections.json`
  `{pairs: [{wrong: ["web coding","web courting"], right: "vibe coding", is_term: true}]}`
- **ChangeSet**（一次批量修改的完整记录，可预览/可撤销）：
  `{change_id, label, changes: [{segment_id, field: text, old, new}], applied_at}`
  → 存 `workspace/<id>/changesets/`；撤销=逆向应用。剪气口批量应用同样走 ChangeSet（统一撤销机制）。

### 接入点（薄适配，不耦合）

- **Studio 流水线**：ASR 完成后自动跑 词典替换（确定性，直接应用+ChangeSet 记录）→ 置信度扫描 →（可选异步，同 auto_ai）AI 复核；报告落盘，UI 渲染高亮。
- **CLI**（对齐现有 `pe` 门面）：`pe check <id>`（输出报告 JSON/Markdown）、`pe fix <id> --dict-only|--auto`（应用词典/AI 高置信纠正）、`pe corrections add "错词=>正词"`、`pe undo <id> <change_id>`。
- **UI**：见体验设计。

## 关键技术决策

1. **置信度前置工程（M0）**：fun-asr 词级 confidence 现在只进 vad.json；改 `video2md.py` 让 token 携带 confidence 进 transcript（`TranscriptToken.confidence` 字段已存在）。旧 transcript 无置信度 → 置信度类检查自动跳过，向后兼容。
2. **替换只动文字、不动时间轴**：所有纠正只改 `row.text` / segment.text（导出 SRT 即正确），词级 token 时间不动；替换若造成词块面板 token 文本与整句文本不一致，属可接受的显示性差异（词块仍用于定位切点）。
3. **AI 自动纠正的安全闸**：只允许"高置信 + 替换前后语义为同音/近音专名修正"的自动改（如 超导→超脑）；拿不准一律降级为高亮建议。全部自动改动进同一个 ChangeSet，UI/CLI 一键整批撤销。
4. **协议下沉惯例**：ai_review 的输出 JSON 协议放代码（与解析器同处），提示词理念层面向用户开放（与三模式提示词同一套 PromptStore 机制，mode=`quality_review`）。
5. **缓存与共享（M3）**：转写缓存 key=源文件 SHA-256，目录默认 `workspace/_cache/transcripts/`，环境变量/设置面板可改为共享同步目录（坚果云/NAS）→ 同事间零服务器复用。装饰器 `CachingAsrRunner` 包在引擎层，Studio 与 CLI 同享；「重新转写」按钮绕过缓存。

## 体验设计

- **行内富渲染（本轮前端核心投资，一个组件三处复用）**：字幕行文字从纯 textarea 升级为"渲染视图 + 点击进入编辑"：
  - 剪气口/句内剪切：被剪词**就地删除线**，点删除线词=恢复，点正常词=剪掉（不进微调面板也全程可见可操作）；
  - 质检存疑词：**黄色波浪下划线**，悬停/点击浮层显示建议与理由，[采纳] [忽略] [加词典]；
  - 参考校对差异：蓝色下划线，浮层展示外部字幕原文。
- **一键剪气口**：应用后状态栏"剪除 N 处 [撤销]"；工具栏出现「撤销剪气口」直到下一次操作。
- **改一处提全部**：行内编辑保存时做词级 diff，检出替换对后若旧词在其他行还出现 N 次 → 顶部提示条"「超导」还有 N 处，全部改为「超脑」？[全部替换] [加入纠错词典] [忽略]"。
- **质检面板**（右侧新 tab「质检」）：问题按类型分组列表，点击跳行定位；顶部批量操作：[自动修复全部高置信] [全部忽略]；报告统计（低置信 n 句 / 词典命中 n 处 / AI 存疑 n 处）。
- **导出质检门**：导出对话时若报告仍有未处理存疑项，列出提醒（可无视继续）。
- **热词联动**：纠错应用后，对 `is_term: true` 的正词提示"加入热词表？"（批量勾选一键入表，接口现成）。

## 里程碑（依赖排序）

| # | 内容 | 面 | 备注 |
|---|------|----|------|
| M0 | 置信度进 transcript tokens | 后端 | 小改，向后兼容 |
| M1 | 行内富渲染 + 剪气口划线可见/点击恢复/一键撤销 | 前端 | 富渲染组件是 M4/M5 的地基 |
| M2 | corrections.py + 批量替换（预览/应用/撤销）+ 改一处提全部 + 热词联动 + `pe corrections/fix --dict-only` | 前后端+CLI | 纯确定性，无 LLM |
| M3 | CachingAsrRunner 内容指纹缓存 + 共享目录配置 + 「重新转写」 | 引擎 | Studio/CLI 同享 |
| M4 | confidence.py + ai_review.py + 质检面板 + `pe check/fix --auto` | 全栈 | AI 自动纠正走安全闸 |
| M5 | align_reference.py 外部字幕参考校对 | 全栈 | 复用 M4 的 issue/高亮机制 |

## 后续候选（本轮不做）

- ASR 完成自动应用纠错词典的开关化、术语一致性检查（AI/ai/A I 统一）、数字格式统一、导出前敏感词检查、热词表↔纠错词典对账（词典正词未入热词提示批量加入）。
