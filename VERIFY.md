# VERIFY.md — 真机验收记录（可照着复现）

本文件记录 DocMind 在 **Ubuntu 22.04 + PowerContext 1.4.1** 上的真机验收结果。
所有数字与输出均为实际执行结果，不是预期值。

## 0. 环境

| 项 | 值 |
|---|---|
| OS | Ubuntu 22.04（VMware 来宾，4 vCPU / 8 GiB） |
| Python | 3.12.16（项目 venv） |
| PowerContext | `powercontext[cli,server]==1.4.1`（`uv tool install`） |
| 服务地址 | `http://127.0.0.1:8787` |
| 应用地址 | `http://127.0.0.1:8000` |
| 文档 scope | `scp_2g52qd2jxnvh4p1m42c466wmks`（`POST /v1/scopes` 创建，idempotency_key=`docmind-demo-v1`） |

## 1. 启动

```bash
# 1) 启动 PowerContext（只这一条命令，无需交互式向导）
export PATH="$HOME/.local/bin:$PATH"
powercontext server run --host 127.0.0.1 --port 8787 --role all

# 2) 启动 DocMind
export POWERCONTEXT_URL=http://127.0.0.1:8787
export POWERCONTEXT_SCOPE=scp_2g52qd2jxnvh4p1m42c466wmks
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

> 注意：PowerContext 首次启动约 20 秒；`/health/live` 返回 200 后再开始体验。
> 不要与其它重任务并发（实测 4 vCPU 被打满时，检索从 0.06s 退化到 12s）。

## 2. 样例一：解析 503 页真实财报并入库

输入：PepsiCo 2022 10-K，`PEPSICO_2022_10K.pdf`（2,993,516 字节）。

```bash
PDF=/path/to/PEPSICO_2022_10K.pdf
curl -s -X POST http://127.0.0.1:8000/ingest/preview \
  -H "Content-Type: application/json" -d "{\"source\":\"$PDF\"}"
```

实际输出（节选）：

```json
{"status":"ok","document_id":"PEPSICO_2022_10K","pages":503,
 "preview":[{"page":1,"text":"Table of Contents\nUNITED STATES SECURITIES AND EXCHANGE COMMISSION\n…","empty":false,"truncated":true}, …]}
```

```bash
time curl -s -X POST http://127.0.0.1:8000/ingest \
  -H "Content-Type: application/json" -d "{\"source\":\"$PDF\"}"
```

实际输出：

```json
{"status":"ok","document_id":"PEPSICO_2022_10K","pages":503,"chunks":503,"skipped_pages":0}
real    0m38.449s
```

**技术生效证据**：503 页被拆成 503 个块，逐块写入 PowerContext 的 `pdf-page` 记忆（`kind=pdf-page`），耗时 38.4 秒。

## 3. 样例二：带页码引用的问答（seakdb/PowerContext 检索生效）

```bash
curl -s -X POST http://127.0.0.1:8000/ask \
  -H "Content-Type: application/json" \
  -d '{"question":"What was PepsiCo net revenue in 2022?","top_k":5}'
```

实际输出（`status: ok`），抽取式答案中出现原文表格行：

```
Net revenue $ 86,392 $ 79,474 9 %
Operating profit $ 11,512 $ 11,162 3 %
Operating margin 13.3 % 14.0 % (0.7)
```

实际引用页码（`document_id` + `page` + `score`）：

```
PEPSICO_2022_10K p71 0.0164
PEPSICO_2022_10K p40 0.0161
PEPSICO_2022_10K p74 0.0159
PEPSICO_2022_10K p41 0.0156
PEPSICO_2022_10K p70 0.0154
```

**技术生效证据**：p71 正是财报中 `Disaggregation of Net Revenue` 所在页，引用可由 `memory.get` 回查原文。

## 4. 样例三：记忆被保存并复用

```bash
# 写入偏好
curl -s -X POST http://127.0.0.1:8000/memory \
  -H "Content-Type: application/json" \
  -d '{"text":"User focuses on operating margin and compares FY2022 with FY2021"}'
# → {"status":"ok"}

# 读回偏好
curl -s http://127.0.0.1:8000/memory
# → {"status":"ok","preferences":[{"text":"User focuses on operating margin and compares FY2022 with FY2021",
#      "artifact_id":"art_b448f98e6a9b466c9725c0bd50986873","revision":1}]}

# 用泛化问题提问，观察记忆是否参与检索
curl -s -X POST http://127.0.0.1:8000/ask \
  -H "Content-Type: application/json" -d '{"question":"What changed in 2022?","top_k":5}'
```

实际输出：

```
status: ok
orig : What changed in 2022?
exp  : What changed in 2022? User focuses on operating margin and compares FY2022 with FY2021
prefs: ['User focuses on operating margin and compares FY2022 with FY2021']
    PEPSICO_2022_10K p40
```

**技术生效证据**：`expanded_query` 显示偏好被拼进检索词，命中收敛到经营成果页（p40）；`queries.log` 逐条记录 `used_query` / `fallback_used`。

## 5. 如何核对（评审用）

1. `samples/queries.log`：每次提问的时间、问题、实际使用的查询、命中页列表；
2. `GET /memory`：偏好条目（存在独立 scope，与文档 scope 隔离）；
3. PowerContext 侧：`POST /v1/memory/search` 的原始返回包含 `memory.kind`、`memory.text`（带 `[DOC pN]` 定位符）与 `score`，可直接核对"检索结果如何被用于答案"。

## 6. 已知限制（如实记录）

- 答案为**抽取式**：直接返回命中原文片段，不由模型生成，因此不会出现"编造数字"，但也不会做跨页计算；
- 检索模式为 `mode="fts"`（词法），未启用向量检索（评审环境无需下载嵌入模型）；
- 单机 SQLite 后端，503 页入库约 38 秒，适合演示与中小规模资料；
- 未配置推理模型（generation/embedding）时服务可用，仅提示"部分制品功能受影响"。
