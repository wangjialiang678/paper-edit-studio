---
title: DashScope（百炼）热词表（Vocabulary）管理 REST API 调研
date: 2026-07-22
status: active
audience: both
tags: [research, dashscope, asr, vocabulary, hotwords, fun-asr]
type: 原始调研
sources: [help.aliyun.com/model-studio, github.com/dashscope/dashscope-sdk-python]
verified: 2026-07-22
shelf_life: 需定期更新
---

# 调研报告: DashScope（百炼）热词表管理 REST API

**日期**: 2026-07-22
**任务**: 为本地纯 stdlib（urllib）工具调研 fun-asr 热词表的查询/更新/创建 REST API，支撑「就地维护 `ASR_BASE_VOCABULARY_ID` 对应词表」的功能。

---

## 调研摘要

DashScope 定制热词的增删改查全部走**同一个 POST 端点**，用请求体里的 `input.action` 字段区分动作（`create_vocabulary` / `list_vocabulary` / `query_vocabulary` / `update_vocabulary` / `delete_vocabulary`），鉴权用 `Authorization: Bearer $DASHSCOPE_API_KEY`。文档 2026-07-21 更新后，**官方主推的端点已从经典 `dashscope.aliyuncs.com` 换成按业务空间（Workspace）区分的 `https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/...`**，但文档明确"现有域名仍可正常使用"，且英文版 SDK 示例、及本项目当前依赖的 `bin/mp4-md`/`ASR_BASE_VOCABULARY_ID` 约定都还是经典域名风格，因此本地工具建议先用经典域名（无需额外获取 WorkspaceId），预留切换空间。权限归属官方文档只写明"热词管理 API 与语音识别 API 必须使用同一账号"，未见明确的 API Key 级隔离说明；但 Python SDK 的 `VocabularyService(workspace=...)` 参数与新端点的 WorkspaceId 前缀，暗示词表实际可能是按"业务空间"隔离的——这一点官方文档没有逐字确认，标记为推测，建议实测验证。

---

## 现有代码分析

### 相关文件
- `/Users/michael/projects/自用小工具/AI视频剪辑工具/.env.example` — 已预留 `ASR_BASE_VOCABULARY_ID`（可选），说明项目已设计好"热词表 ID 透传给转写"的位置，但目前**词表本身的创建/维护是站外手动完成的**（控制台或临时脚本），没有本地管理入口。
- `/Users/michael/projects/自用小工具/AI视频剪辑工具/src/cutpoint_lab/studio/asr_runner.py:159-161` — 读取 `ASR_BASE_VOCABULARY_ID`，通过 `--vocab` 参数传给 `bin/mp4-md` 二进制（只读用途，不涉及词表管理）。
- `/Users/michael/projects/自用小工具/AI视频剪辑工具/src/cutpoint_lab/studio/llm_client.py:95-119` — 项目里唯一已有的"纯 stdlib urllib + Bearer 鉴权"HTTP 调用范式，本次新工具应沿用同一风格（`urllib.request.Request` + `urlopen` + `HTTPError`/`URLError` 分开捕获）。

### 现有模式
- 项目对外 HTTP 调用统一用 `urllib.request`，不引入 `requests`；错误处理区分 `urllib.error.HTTPError`（读 body 截断 500 字符）与 `urllib.error.URLError`/`TimeoutError`/`OSError`。
- 密钥统一走 `.env` + `DASHSCOPE_API_KEY` 环境变量，不硬编码。

### 可复用组件
- `llm_client.py` 的 `_post_chat` 方法结构可直接套用（换 URL、换 payload 结构）。

---

## 事实核查（逐条附来源）

### 1. REST 端点与四个动作

所有动作共用一个 POST 端点，`model` 固定为 `"speech-biasing"`，用 `input.action` 区分：

| 动作 | `action` 值 |
|------|------------|
| 创建词表 | `create_vocabulary` |
| 批量查询/列表 | `list_vocabulary` |
| 查询单个词表内容 | `query_vocabulary` |
| 更新词表（整表替换） | `update_vocabulary` |
| 删除词表 | `delete_vocabulary`（题目未要求但文档一并给出） |

**端点 URL（两种风格并存，均已在官方文档验证）**：

- 经典域名（无需 WorkspaceId，"现有域名仍可正常使用"）：
  - 中国大陆（北京）：`https://dashscope.aliyuncs.com/api/v1/services/audio/asr/customization`
  - 新加坡/国际：`https://dashscope-intl.aliyuncs.com/api/v1/services/audio/asr/customization`
- 新版按业务空间区分（2026-07-21 文档更新后的官方主推写法）：
  - 北京：`https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/api/v1/services/audio/asr/customization`
  - 新加坡：`https://{WorkspaceId}.ap-southeast-1.maas.aliyuncs.com/api/v1/services/audio/asr/customization`
  - `{WorkspaceId}` 需替换为百炼控制台的业务空间 ID。

来源：
- [定制热词HTTP API参考](https://help.aliyun.com/zh/model-studio/vocabulary-http-api)（中文版，2026-07-02 发布，抓取时页面已带 WorkspaceId 端点+"现有域名仍可正常使用"提示，2026-07-22 复核）
- [custom vocabulary HTTP API reference（英文版）](https://help.aliyun.com/en/model-studio/vocabulary-http-api) — 英文版 curl 示例仍用经典 `dashscope.aliyuncs.com`
- [Custom hotwords Python SDK reference（英文版）](https://help.aliyun.com/en/model-studio/vocabulary-python-sdk) — 示例代码里 `dashscope.base_http_api_url = 'https://dashscope.aliyuncs.com/api/v1'`，佐证经典域名仍有效

**响应包络（4 个动作共用结构）**：`{"output": {...}, "usage": {"count": 1}, "request_id": "..."}`

---

### 2. 词条字段与词表容量限制

| 字段 | 类型 | 必填 | 取值范围 / 规则 |
|------|------|------|----------------|
| `text` | string | 是 | 需为实际词语（非任意字符组合）；**含非 ASCII 字符**（汉字/假名/谚文/西里尔等）时总字符数 ≤ 15；**纯 ASCII** 时按空格切分片段数 ≤ 7 |
| `weight` | int | 是 | 取值范围 `[1, 5]`，推荐 4（1~2 轻微偏好，3~4 明显偏好/推荐区间，5 强制偏好、易误伤发音相近词） |
| `lang` | string | 否 | 语种代码，限定该热词生效的语种；未知时可省略。Paraformer 支持 `zh/en/ja/yue/ko/de/fr/ru`；Fun-ASR 支持 `zh/en/ja`。注意：这是词条内可选字段，和语音识别接口的 `language_hints` 参数是两回事——`language_hints` 一旦设置，只有匹配语种的热词会生效，其余会被忽略 |

**容量限制**：
- 单个词表最多 **500** 个热词。
- **每账号最多 10 个词表**，所有模型共享配额。
- 计费：免费。

来源：[提升识别准确率](https://help.aliyun.com/zh/model-studio/improve-asr-accuracy)（更新时间 2026-07-21，含"限制与计费"表格与热词文本规范示例）

---

### 3. vocabulary_id 格式与 target_model 对应关系

- **格式**：`vocab-{prefix}-{随机字符串}`，例：`vocab-testpfx-5112c3de3705486baxxxxxxx`。`prefix` 由调用方在创建时指定，仅允许数字和小写字母，长度 ≤10 字符。
- **target_model 是创建词表的必填参数**，声明该词表归属哪个语音识别模型；**转写时使用的模型必须与创建时的 `target_model` 完全一致**，否则接口不报错、转写正常返回，但热词**静默不生效**（官方原文："两者不一致时接口不会报错，识别仍能返回结果，但热词不生效"）。
- 仅 **Fun-ASR 系列**与 **Paraformer 系列**支持热词；SenseVoice 等其他系列调用时同样不报错，但识别结果不含热词增强。
- 录音文件识别（fun-asr / Transcription 接口）：在请求参数里传 `vocabulary_id`；实时识别：在 Recognition/WebSocket 连接参数里传 `vocabulary_id`。两种场景下 `target_model` 都必须等于实际调用的模型名（含日期后缀的具体版本，如 `fun-asr-2025-11-07`，也需要精确匹配，非模糊匹配到大版本）。

来源：[定制热词HTTP API参考](https://help.aliyun.com/zh/model-studio/vocabulary-http-api)、[提升识别准确率 - 常见问题](https://help.aliyun.com/zh/model-studio/improve-asr-accuracy)

---

### 4. 权限与归属（多用户/多 API Key 场景）

官方原文（中文用户指南）："热词管理 API 与语音识别 API 必须使用同一账号，否则识别接口无法访问对应的热词列表。"——这是**唯一**一句明确谈到归属范围的话，只提到"账号（阿里云账号）"级别，**没有**逐字说明是否按 API Key 或按业务空间（Workspace）隔离。

需要标注为**推测、未在文档中直接证实**的两点：
- Python SDK 的 `VocabularyService(api_key: str = None, workspace: str = None, model: str = None)` 构造函数带有可选 `workspace` 参数；且新版 HTTP 端点本身就是 `{WorkspaceId}.xxx.maas.aliyuncs.com`——这两点共同暗示词表资源实际上可能是**按业务空间（Workspace）**存储，而不是笼统的"账号"级别。
- 如果暗示成立：**同一账号、同一业务空间下**的不同 API Key 大概率能共享读写同一词表 ID；**不同业务空间**（即便同账号）可能相互隔离——但这一推论没有找到官方逐字确认的段落。

来源：[自定义热词](https://help.aliyun.com/zh/model-studio/custom-hot-words-user-guide)、[定制热词Python SDK参考](https://help.aliyun.com/zh/model-studio/vocabulary-python-sdk)（`VocabularyService` 构造函数签名含 `workspace` 参数）

**建议**：在本地工具里，不要假设"多个 API Key 共用同一 vocabulary_id 一定安全"。若确实需要多用户/多 Key 共享或隔离，建议先用两把不同的 API Key 做一次 `create_vocabulary`（Key A）→ `query_vocabulary`（Key B）的手工探测，确认边界后再定隔离策略。这一步官方文档没有替你回答。

---

### 5. 认证方式与更新生效时机

- **认证**：所有动作统一用 `Authorization: Bearer <DASHSCOPE_API_KEY>` + `Content-Type: application/json`，与项目里 ASR 转写、LLM 调用用的是**同一把 Key**（`DASHSCOPE_API_KEY`）。来源：[定制热词HTTP API参考](https://help.aliyun.com/zh/model-studio/vocabulary-http-api)（请求头表格）
- **`update_vocabulary` 语义**：新的 `vocabulary` 数组会**完全替换**原有内容（整表覆盖，不是增量 merge）。官方原文（更新热词列表小节）明确写"新的热词列表将完全替换原有内容"。
- **是否立即生效**：官方文档**没有**逐字承诺"更新后下一次转写立即生效"。唯一相关的旁证是：
  - `list_vocabulary`/`query_vocabulary` 返回的 `status` 字段有两个取值：`OK`（可调用）/`UNDEPLOYED`（不可调用），官方快速开始示例在**创建后**会轮询 `query_vocabulary` 直到 `status == 'OK'` 才发起转写；
  - 常见问题里排查"热词不生效"时也建议"通过查询接口确认 `status` 为 `OK`"。
  - 文档没有单独说明 `update_vocabulary` 之后是否也存在类似的 `UNDEPLOYED` 窗口期。**保守做法**（推测，未经官方文字确认）：更新后同样调用一次 `query_vocabulary` 确认 `status == 'OK'` 再继续，比假设"改完立刻生效"更稳妥。

来源：[提升识别准确率 - 快速开始/常见问题](https://help.aliyun.com/zh/model-studio/improve-asr-accuracy)、[定制热词HTTP API参考 - 批量查询响应参数](https://help.aliyun.com/zh/model-studio/vocabulary-http-api)

---

## urllib 调用示例（可直接照抄）

以下示例走**经典域名**（无需 WorkspaceId，贴合项目现状），认证复用 `.env` 里已有的 `DASHSCOPE_API_KEY`。

### 创建词表（create_vocabulary）

```python
import json
import os
import urllib.error
import urllib.request

API_KEY = os.environ["DASHSCOPE_API_KEY"]
URL = "https://dashscope.aliyuncs.com/api/v1/services/audio/asr/customization"

payload = {
    "model": "speech-biasing",
    "input": {
        "action": "create_vocabulary",
        "target_model": "fun-asr",       # 必须和后续转写用的 model 完全一致
        "prefix": "myproj",              # 仅数字+小写字母，<=10 字符
        "vocabulary": [
            {"text": "赛德克巴莱", "weight": 4, "lang": "zh"},
        ],
    },
}

request = urllib.request.Request(
    URL,
    data=json.dumps(payload).encode("utf-8"),
    headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {API_KEY}",
    },
    method="POST",
)
try:
    with urllib.request.urlopen(request, timeout=30) as response:
        body = json.loads(response.read().decode("utf-8"))
except urllib.error.HTTPError as exc:
    detail = exc.read().decode("utf-8", errors="replace")[:500]
    raise RuntimeError(f"create_vocabulary HTTP {exc.code}: {detail}") from exc

vocabulary_id = body["output"]["vocabulary_id"]
print(vocabulary_id)  # 形如 vocab-myproj-5112c3de3705486baxxxxxxx
```

**响应示例**：
```json
{
  "output": {"vocabulary_id": "vocab-myproj-5112c3de3705486baxxxxxxx"},
  "usage": {"count": 1},
  "request_id": "aee47022-2352-40fe-acfa-xxxx"
}
```

### 查询词表内容（query_vocabulary）

```python
payload = {
    "model": "speech-biasing",
    "input": {
        "action": "query_vocabulary",
        "vocabulary_id": "vocab-myproj-5112c3de3705486baxxxxxxx",
    },
}
# 复用上面的 request 构造 + urlopen 逻辑，body["output"] 即词表详情：
# {
#   "gmt_create": "2025-12-19 11:47:11",
#   "gmt_modified": "2025-12-19 11:47:11",
#   "status": "OK",                 # 或 UNDEPLOYED（暂不可用）
#   "target_model": "fun-asr",
#   "vocabulary": [{"lang": "zh", "text": "赛德克巴莱", "weight": 4}]
# }
```

### 就地更新词表（update_vocabulary，整表替换）

```python
payload = {
    "model": "speech-biasing",
    "input": {
        "action": "update_vocabulary",
        "vocabulary_id": "vocab-myproj-5112c3de3705486baxxxxxxx",
        "vocabulary": [
            {"text": "赛德克巴莱", "weight": 4, "lang": "zh"},
            {"text": "语音实验室", "weight": 4},   # lang 可省略
        ],
    },
}
# 注意：vocabulary 数组会完全替换原内容，不是增量追加。
# 响应 body["output"] 为空对象 {}，需自行再 query_vocabulary 校验落地结果。
```

---

## 推荐方案

**推荐**：本地工具用**经典域名**（`dashscope.aliyuncs.com` / `dashscope-intl.aliyuncs.com`）+ `Bearer $DASHSCOPE_API_KEY` 实现 create/query/update 三个动作，不引入 WorkspaceId 依赖。

**理由**：
- 官方文档明确"现有域名仍可正常使用"，英文版 SDK 示例仍在用，短期内不会失效。
- 项目当前只有 `DASHSCOPE_API_KEY` 一个凭据（`.env.example`），引入 WorkspaceId 会多一个用户需要单独去控制台找的参数，增加使用门槛，与 CLAUDE.md "免 OSS、只需一个 Key" 的既定目标冲突。
- 三个动作字段结构简单、单端点、无需分页复杂度（`list_vocabulary` 本次用不到，可以后续需要时再加）。

---

## 实施建议

### 关键步骤
1. 新建 `src/cutpoint_lab/dashscope_vocabulary.py`（或并入现有 `dashscope.py`），封装 `create_vocabulary(prefix, target_model, vocabulary)` / `query_vocabulary(vocabulary_id)` / `update_vocabulary(vocabulary_id, vocabulary)` 三个函数，复用 `llm_client.py` 的 urllib 请求范式（`HTTPError` 读 body 截断报错、`URLError`/`TimeoutError`/`OSError` 归一异常）。
2. `target_model` 建议做成显式参数而非硬编码，并在函数文档里强调"必须和转写时用的 model 完全一致，包括日期后缀"，因为不一致时**接口不报错**，容易踩坑却很难定位。
3. `update_vocabulary` 调用后建议自动追加一次 `query_vocabulary` 轮询/校验 `status == 'OK'`（参照官方 create 后轮询的范式），因为"更新是否立即生效"官方没有文字承诺。
4. 词条数量校验（≤500）、`prefix` 正则（`^[a-z0-9]{1,10}$`）、`text` 长度规则（非 ASCII ≤15 / 纯 ASCII 空格分段 ≤7）建议在本地先做一次预校验，避免把明显超限的请求发到远端才报错。
5. `.env.example` 里已有的 `ASR_BASE_VOCABULARY_ID` 可以直接作为默认操作对象；新增 CLI/命令时建议同时支持"传入 `--vocabulary-id` 覆盖默认值"，方便管理多个词表。

### 风险点
- **权限归属边界未经官方逐字确认**（见事实核查第 4 条）：如果项目未来出现"多个 API Key 共用一个 vocabulary_id"的场景，务必先手工探测验证，不要凭推测下结论。
- **端点迁移风险**：官方文档已经把主推示例换成 WorkspaceId 端点，经典域名虽然"仍可用"，但不排除未来某个时间点被收紧。建议封装时把 base URL 做成可配置项（环境变量），而不是写死常量，方便后续无痛切换到 WorkspaceId 端点。
- **`target_model` 精确匹配失败是静默的**：接口不报错、转写也返回正常结果，只是热词不生效——这类 bug 极难在没有专门测试用例时发现，实施时建议加一条"创建后立即用同一 target_model 跑一次真实转写并人工核对热词是否命中"的验收步骤。

### 依赖项
- 仅需 Python stdlib（`json`、`urllib.request`、`urllib.error`、`os`），无第三方包依赖，符合任务约束。
- 复用现有 `DASHSCOPE_API_KEY` 环境变量，无需新增凭据。

---

## 参考来源

- [定制热词HTTP API参考](https://help.aliyun.com/zh/model-studio/vocabulary-http-api) — 支撑事实 1、2（部分）、3（部分）、5（认证/更新语义）
- [提升识别准确率](https://help.aliyun.com/zh/model-studio/improve-asr-accuracy) — 支撑事实 2（容量限制/文本规则）、3（target_model 匹配规则）、5（status 轮询）
- [自定义热词](https://help.aliyun.com/zh/model-studio/custom-hot-words-user-guide) — 支撑事实 3、4（账号归属原句）
- [定制热词Python SDK参考](https://help.aliyun.com/zh/model-studio/vocabulary-python-sdk) — 支撑事实 4（`VocabularyService(workspace=...)` 签名，交叉印证经典域名仍可用）
- [custom vocabulary HTTP API reference（英文版）](https://help.aliyun.com/en/model-studio/vocabulary-http-api) — 交叉验证经典域名 curl 示例
- [Custom hotwords Python SDK reference（英文版）](https://help.aliyun.com/en/model-studio/vocabulary-python-sdk) — 交叉验证 `dashscope.base_http_api_url = 'https://dashscope.aliyuncs.com/api/v1'`
- [dashscope/dashscope-sdk-python](https://github.com/dashscope/dashscope-sdk-python) — 官方 SDK 仓库，佐证 SDK 层封装存在（未直接取到 REST 层源码细节）
