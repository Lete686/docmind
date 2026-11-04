# docmind

围绕 PowerContext 的文档服务，三条链路都已接真实上游：`/ingest` 解析并写入 PDF，`/memory` 读写用户偏好，`/ask` 带着偏好去检索并给出抽取式答案。

要求 Python 3.11+。

## 0. 先启动 PowerContext（一次性）

本项目依赖一个运行中的 PowerContext 实例。若本机尚未安装：

```bash
uv tool install --force "powercontext[cli,server]==1.4.1"
export PATH="$HOME/.local/bin:$PATH"
powercontext server run --host 127.0.0.1 --port 8787 --role all
```

该命令前台阻塞，请保留此终端；后面的健康检查、建 scope 以及启动 DocMind 都另开一个终端执行。

启动约 20 秒，确认健康：

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8787/health/live   # 期望 200
```

创建文档 scope（幂等键固定，可重复执行）：

```bash
curl -s -X POST http://127.0.0.1:8787/v1/scopes -H "Content-Type: application/json" \
  -d '{"title":"DocMind Demo","summary":"PDF page evidence Q&A","idempotency_key":"docmind-demo-v1"}'
```

把返回的 `scope_id` 填进 `.env` 的 `POWERCONTEXT_SCOPE`，再继续下面的「快速开始」。

注意：PowerContext 首次启动会初始化 SQLite；不要与其它重任务并发（4 核机器上并发时检索会从 0.06s 退化到十余秒）。

## 快速开始

```bash
pip install -e .
cp .env.example .env      # 填 POWERCONTEXT_URL 与 POWERCONTEXT_SCOPE
bash scripts/run.sh       # 另开终端执行（前台运行）：http://127.0.0.1:8000 ，首页即项目介绍，/docs 可试接口
```

下面是分步说明。

## 1. 安装

```bash
cd docmind
python -m venv .venv
source .venv/bin/activate          # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -e .
cp .env.example .env               # 编辑 .env，填 POWERCONTEXT_URL 与 POWERCONTEXT_SCOPE
```

依赖：`fastapi`、`uvicorn[standard]`、`pypdf`、`httpx`（见 `pyproject.toml`）。

## 2. 配置环境变量

具体的安装、启动与 scope 创建命令见上方「0. 先启动 PowerContext」。docmind 不内置任何地址或凭据，取值只能来自环境：

| 变量 | 必填 | 用途 |
|---|---|---|
| `POWERCONTEXT_URL` | 是 | PowerContext 根地址，不带末尾斜杠 |
| `POWERCONTEXT_SCOPE` | 是 | 文档记忆的 `scope_id`，一个知识库一个值（`/ingest` 写入、`/ask` 检索都用它） |
| `POWERCONTEXT_MEMORY_SCOPE` | 否 | 用户偏好的 `scope_id`。留空则启动时自动创建（见下方 `/memory` 行为） |

另外三条与环境表本身无关、但必须遵守的约定：

- 需要鉴权时把 token 也放环境变量（`.env.example` 里预留了 `POWERCONTEXT_TOKEN`），不要写进代码，也不要提交 `.env`。
- docmind 依赖四个上游端点：`POST /v1/scopes`（建偏好 scope）、`POST /v1/memory/remember`（入库与写偏好）、`POST /v1/memory/search`（检索）、`POST /v1/memory/entries/list`（读偏好）。
- 客户端固定直连 `POWERCONTEXT_URL`（`trust_env=False`）：本机开着系统代理时，代理常会把发往 `127.0.0.1` 的请求接走并返回空 body 的 502，这里显式绕开。

## 3. 启动本服务

```bash
bash scripts/run.sh
```

脚本加载 `.env`，在 `http://127.0.0.1:8000` 起服务（`--reload`），监听地址可用 `DOCMIND_HOST` / `DOCMIND_PORT` 覆盖。

- 首页：<http://127.0.0.1:8000/>
- OpenAPI 文档：<http://127.0.0.1:8000/docs>

配置缺失时服务照样能起，`/ingest` 会返回 503 并打印缺哪个变量、怎么补。

## 4. 跑样例

没有现成 PDF 就先造一份（6 页：第 2、5 页无内容，第 6 页只有空白，第 3 页 9854 字节用于触发分块）：

```bash
python samples/make_sample_pdf.py     # 写出 samples/sample_doc.pdf
```

另开一个终端：

```bash
bash samples/run_samples.sh                                   # 默认本机 8000
DOCMIND_BASE=http://127.0.0.1:9000 bash samples/run_samples.sh  # 换端口
DOCMIND_SAMPLE_PDF=/path/to/your.pdf bash samples/run_samples.sh
```

### /ingest 的行为

- `pypdf` 逐页 `extract_text()`，去首尾空白后为空的页跳过，但仍计入 `pages`，数量体现在返回值的 `skipped_pages`。
- 每页按 **7000 UTF-8 字节**上限切块，按字符累加，绝不切断多字节字符；定位符 `[<document_id> p<页码>]` 也计入这 7000 字节，所以发出去的每一块整体不超限。`document_id` 取文件名去扩展名。
- 每块 `POST {POWERCONTEXT_URL}/v1/memory/remember`，body 为 `{"scope_id", "kind": "pdf-page", "text"}`；`httpx.AsyncClient`，超时 30s；失败按 1s、2s 指数退避重试 2 次；**任一 4xx 立即中止**并把上游错误原文放进 502 的 `detail`。
- 幂等：整份 PDF 的 sha256 记在 `samples/manifest.json`，同哈希再来就返回 `{"status":"skipped"}`，一次上游请求都不发。该文件是本地运行状态，已在 `.gitignore` 里；想强制重灌就删掉对应条目。
- 成功返回 `{"status":"ok","document_id":…,"pages":N,"chunks":M,"skipped_pages":K}`。

样例文件的预期结果：`pages=6`、`chunks=4`、`skipped_pages=3`；再跑一次变 `skipped`。

`/ingest/preview` 只解析不写库，返回前 3 页各自前 120 字符（带 `empty` / `truncated` 标记），适合评审时先确认解析质量。

### /ask 的行为

- 入参 `{"question": str, "top_k": 5}`；调 `POST {POWERCONTEXT_URL}/v1/memory/search`，body 为 `{"scope_id", "query", "limit", "mode": "fts"}`，其中 `limit = min(max(top_k * 3, top_k), 50)`——上游按 3 倍召回以保证重排有余量，**返回给前端的 `citations` 截到 `top_k` 条**。
- 记忆复用：先取偏好（等价于 `GET /memory`），把前 2 条偏好文本拼到问题后面作为 `expanded_query` 去检索；响应里同时给 `original_query`、`expanded_query` 和 `applied_preferences`，便于核对到底用了什么查询。若 `expanded_query` 一条没命中，则退回原 query 再检一次。偏好只是增强项：读不到就退化成"无偏好"、直接用原 query，不会让整条问答失败（原因写进日志的 `preferences_error`）。
- 命中逐条过滤：只留 `memory.kind == "pdf-page"`；用 `^\[(?P<doc>[^\]]+)\s+p(?P<page>\d+)\]\s*` 解析定位符，解析不了的直接丢弃；剥掉前缀后取前 300 字符作 `snippet`。
- 按 `(document_id, page)` 去重，保留 `score` 最高的那条，再按分数降序输出。
- `answer` 是**抽取式**的：前 3 条 `snippet` 用 `\n---\n` 连接，最前面加一行 `以下为命中原文片段（抽样式，非生成式）：`，不做任何生成，也就不编造。
- 没有可用命中时返回 `{"status":"no_hit","answer":"未在已入库文档中找到相关内容","citations":[]}`，外加同样的 `mode` / `original_query` / `expanded_query` / `applied_preferences`。
- 每次提问（含 no_hit）追加一行 JSON 到 `samples/queries.log`：`{"ts","question","hits":[[doc,page],…],"expanded_query","applied_preferences","used_query","fallback_used"}`，`fallback_used` 即"扩展没命中、退回原 query"的审计标记。该文件同样已 gitignore。
- 错误：上游 4xx/5xx → 本服务 502 并带上游原文；连不上 → 503 并提示检查 `POWERCONTEXT_URL`；配置缺失 → 503 并点名缺哪个变量。

`citations` 每项为 `{document_id, page, score, artifact_id, revision, snippet}`。

### /memory 的行为

- 偏好 scope 自动创建：服务启动时（lifespan）若未配置 `POWERCONTEXT_MEMORY_SCOPE`，调 `POST {POWERCONTEXT_URL}/v1/scopes`，body 固定为 `{"title":"DocMind Preferences","summary":"User preferences for DocMind","idempotency_key":"docmind-pref-scope-v1"}`，取响应里的 `scope_id` 存进模块级变量。幂等键固定，所以重复启动拿回的是同一个 scope，不会重复创建；配了该变量则直接用，连 `/v1/scopes` 都不发。
- 预热失败不阻止启动：PowerContext 还没起来或返回错误时只记一条 warning，首次调用 `/memory`、`/ask` 时再补一次。
- `POST /memory`：body `{"text": str}`，非空且 ≤2000 字符（空白文本与超长都 422）；转成 `POST /v1/memory/remember`，body `{"scope_id": <偏好scope>, "kind": "user-preference", "text": text}`，成功返回 `{"status":"ok"}`。
- `GET /memory`：调 `POST /v1/memory/entries/list`，body `{"scope_id": <偏好scope>}`；`entries` 里只保留 `kind == "user-preference"`，返回 `{"status":"ok","preferences":[{"text","artifact_id","revision"}]}`。
- 上游 4xx/5xx → 502 带原文；连不上 → 503 提示检查 `POWERCONTEXT_URL`。

## 目录结构

```
docmind/
├── app/
│   ├── main.py       # 挂载三个路由前缀 + 静态首页 + 启动预热偏好 scope + 503 处理
│   ├── config.py     # POWERCONTEXT_URL / POWERCONTEXT_SCOPE / POWERCONTEXT_MEMORY_SCOPE
│   ├── ingest.py     # /ingest、/ingest/preview
│   ├── ask.py        # /ask：偏好扩展 + fts 检索 + 抽取式作答
│   └── memory.py     # /memory：偏好 scope 幂等创建 + 偏好读写
├── web/index.html    # 根路径静态页
├── samples/
│   ├── make_sample_pdf.py  # 手写 PDF 生成器（仅标准库）
│   ├── sample_doc.pdf      # 由上面脚本生成
│   ├── sample_doc.md       # 早期文本样例
│   ├── manifest.json       # 入库台账（gitignore）
│   ├── queries.log         # 提问与检索轨迹日志（gitignore）
│   └── run_samples.sh
├── scripts/run.sh
├── .env.example
└── pyproject.toml
```
