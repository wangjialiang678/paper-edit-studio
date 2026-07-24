"""仍在使用的 AI 输出协议。

字段名、取值和 id 规则必须与对应解析代码同步，因此协议不放进用户可编辑的
提示词文件，而由 PromptStore 在运行时拼接。
"""

MODE_PROTOCOLS = {
    "koubo_tighten": """

## 输出格式

只输出一个 JSON object：

```json
{
  "mode": "koubo_tighten",
  "summary": "一两句话说明整体取舍思路",
  "decisions": [
    {
      "segment_id": "sentence_0001",
      "keep": true,
      "reason": "开场即抛出核心观点",
      "labels": ["hook"]
    }
  ]
}
```

- `decisions` 必须覆盖输入的每一个 `segment_id`，不得遗漏、不得新增。
- `labels` 可选值：`hook` / `insight` / `golden_quote` / `case` / `method` /
  `transition` / `filler` / `repeat` / `smalltalk` / `broken` / `asr_suspect`。
- `reason` 用一句话说清依据，人工复核时要能看懂。

## 硬约束

1. 只能引用输入中已有的 `segment_id`，禁止编造。
2. 禁止输出任何时间戳。
3. 只输出 JSON，不要有任何其他文字。

{{USER_BRIEF}}
""",
    "quality_review": """

## 输出格式

只输出一个 JSON object：

```json
{
  "findings": [
    {
      "segment_id": "sentence_0055",
      "span_text": "超导",
      "verdict": "auto_fix",
      "replacement": "超脑",
      "reason": "上下文讲机构名，已知词表含（超脑）",
      "confidence": 0.95
    }
  ]
}
```

- `verdict` 只能是 `auto_fix` / `ask_user` / `ok`。
- `span_text` 必须与输入中标注的存疑片段逐字一致。
- `confidence` 为 0–1 的小数。

## 硬约束

1. 只能针对输入中标注的存疑片段输出 finding，禁止扩大范围。
2. 禁止输出任何时间戳；禁止改动句子结构，只做词语级替换建议。
3. 只输出 JSON，不要有任何其他文字。

{{USER_BRIEF}}
""",
    "compose_align": """

## 输出格式

只输出 JSON：

```json
{
  "matches": [
    {
      "paragraph_index": 0,
      "segment_ids": ["sentence_0012"],
      "confidence": 0.9,
      "reason": "改写自该句"
    }
  ]
}
```

`segment_ids` 只能来自输入清单；找不到就给空数组。只输出 JSON。

{{USER_BRIEF}}
""",
    "content_map": """

## 输出格式

只输出一个 JSON object：

```json
{
  "claims": [
    {
      "id": "c1",
      "text": "主张原文",
      "segment_ids": ["sentence_0012"],
      "reason": "为什么值得传播"
    }
  ],
  "backgrounds": [
    {
      "id": "b1",
      "text": "游戏设计营",
      "segment_ids": ["sentence_0003"],
      "kind": "background"
    }
  ],
  "topics": [
    {
      "id": "t1",
      "name": "主题名",
      "summary": "主题摘要",
      "segment_ids": ["sentence_0012"],
      "suggested_duration_s": 60,
      "status": "pending"
    }
  ]
}
```

- `backgrounds[].kind` 只能是 `background` / `case` / `event`。
- `topics[].status` 只能是 `pending` / `confirmed`；AI 初稿通常使用 `pending`。
- 每个 `segment_id` 最多归属一个 topic。
- 不要输出 `duration_ms`；后端会按字幕时间重新计算。

## 硬约束

1. 只能引用输入中已有的 `segment_id`，禁止编造。
2. 禁止输出任何时间戳。
3. 只输出 JSON，不要有任何其他文字。

{{USER_BRIEF}}
""",
    "quote_candidates": """

## 输出格式

只输出一个 JSON object：

```json
{
  "candidates": [
    {
      "id": "q1",
      "topic_id": "t1",
      "segment_id": "sentence_0012",
      "type": "claim",
      "context": "前后句的一句话摘要",
      "reason": "单句能立住且适合传播"
    }
  ]
}
```

- 每个已确认主题返回 3–5 个候选，并按强度从高到低排列。
- `type` 只能是 `claim` / `hook` / `background` / `question` / `action`。
- `segment_id` 必须属于对应 `topic_id` 的句子集合。

## 硬约束

1. 只能引用输入中已有的 `segment_id` 和 `topic_id`，禁止编造。
2. 禁止输出任何时间戳。
3. 只输出 JSON，不要有任何其他文字。

{{USER_BRIEF}}
""",
}
