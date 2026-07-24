# AI 选段提示词库（剪辑理念层）

这里的每个 `.md` 文件只包含对应模式的**剪辑理念**——纯自然语言的判断标准与取舍偏好，小白也能放心改。发送给 LLM 的完整 system prompt 由系统在运行时拼装：

```
剪辑理念（本目录 .md，或 workspace/_settings/prompts/ 覆盖层）
  + 输出协议（src/cutpoint_lab/studio/prompt_protocols.py，JSON 格式/模式硬约束/占位符注入点）
  + 系统级硬约束（ai_selector.HARD_CONSTRAINTS）
```

输出协议与解析代码（`_normalize_*`）一一对应，**改协议必须同步改解析器**，所以不放在可编辑文件里；界面「高级选项」可查看拼装后的完整提示词。字幕摘要（`[segment_id] 起-止 文本`）作为 user 消息发送。

| 文件 | 模式 | 适用场景 |
|------|------|----------|
| `koubo-tighten.md` | 口播精剪 | 单条口播视频去水、保干货，输出逐句保留/删除决策 |
| `content-map.md` | 大主题内容地图 | 长视频按可独立成片的完整叙事分大主题，同时提取主张与背景 |
| `quote-candidates.md` | 金句候选 | 在每个大主题内按强度挑选 3–5 条候选 |

`topic_slicing` 与 `highlight_remix` 独立模式已并入统一出方案管线。`topic-slicing.md` 已删除；`highlight-remix.md` 仅保留为历史策略参考，运行时不再加载。

## 硬约束（代码层也会二次校验）

1. 只能引用输入中已有的 `segment_id`，禁止编造。
2. 禁止输出任何时间戳，禁止修改句子起止时间——时间线由剪辑引擎负责。
3. 只输出一个 JSON object，不要输出解释性文字。
4. 对 ASR 明显错乱、无法判断含义的句子，宁可保留，交给人工判断。

模板里的 `{{USER_BRIEF}}`（用户补充要求）和 `{{TARGET_DURATION}}`（目标时长提示）会在调用时替换；不需要时留空即可。

## 调优建议

- **界面编辑（推荐）**：AI 选段面板右上"📝 提示词"直接查看/修改，保存即生效、重启保留。界面修改保存为 `workspace/_settings/prompts/<mode>.md` 覆盖层（不入库），本目录保持出厂默认；"恢复默认"=删除覆盖层。
- 改出厂默认才编辑本目录的 `.md`；无覆盖层时同样即时生效（每次调用重新读文件）。
- 想换模型/服务商：设置环境变量 `STUDIO_LLM_BASE_URL` / `STUDIO_LLM_API_KEY` / `STUDIO_LLM_MODEL`（默认 DashScope 兼容模式 + qwen-plus）。
