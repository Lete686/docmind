# DEVLOG — Qoder 开发记录

本文按阶段记录 **Qoder 在本项目中承担的工作**：需求拆解、编码、调试、验证。
配合 `docs/qoder-screenshots/`（关键对话截图）与 Qoder 账号 UID 供核对。

- 项目：DocMind — 把你的 PDF 变成能引用的问答知识库
- Qoder 使用形态：IDE 智能体（自然语言下达需求，Qoder 完成拆解、编码、调试、验证）
- 技术结合：PowerContext 1.4.1（Python/HTTP API），承担**记忆存储**与**检索**

---

## 阶段 P1 — 骨架与可达性

**下达的需求**：搭 FastAPI 项目骨架，三个路由（`/ingest`、`/ask`、`/memory`）+ 静态页 `/`，配置只从环境变量读，不硬编码凭据。

**Qoder 完成**：目录结构、`config.py`（缺配置时 503 并点名变量）、占位路由、`pyproject.toml`、`scripts/run.sh`、README 骨架。

**验证**：服务可启动；`/`、`/docs` 均 200；四条占位路由返回 `status: "placeholder"`；`top_k=0` 正确 422；设置 `POWERCONTEXT_URL` 后健康检查变 `configured: true`。

**期间发现并修复**：Windows 终端按本地代码页编码中文参数，导致请求体乱码（`error parsing the body`）→ 样例脚本改用纯 ASCII 请求体。

---

## 阶段 P2 — PDF 解析与页级入库

**下达的需求**：pypdf 逐页解析；按 ≤7000 字节切块（不切断多字节字符）；每块加定位符 `[<document_id> p<page>]`；逐块写入 PowerContext 记忆（`kind=pdf-page`）；幂等；失败重试；提供只解析不写库的预览接口。

**Qoder 完成**：`app/ingest.py` 全部实现；分块预算把定位符也算进 7000 字节；4xx 立即中止、5xx 指数退避重试 2 次；sha256 台账 `samples/manifest.json` 做幂等。

**验证**：自建 mock 断言 4 块、最大块恰好 7000 字节、定位符正确、重跑返回 `skipped`、强制 500 后重试成功、强制 400 立即中止且台账未写入。

**期间发现并修复的两个真问题**：

1. **Windows 系统代理污染**：机器上 `127.0.0.1:7890` 的代理被 httpx 默认继承，发往本机 PowerContext 的请求被代理掉，返回空 body 的 502。修复：`trust_env=False` 强制直连。
2. **纯空白页判定**：`_build_chunks` 只判 `not text`，纯空白页未被跳过。修复：strip 后统一判定，并补一页"只有空格"的样例覆盖。

---

## 阶段 P3 — 带页码引用的问答

**下达的需求**：调 PowerContext `POST /v1/memory/search`，从命中里解析定位符、按 `(doc, page)` 去重、按分数排序，返回**抽取式**答案（不调模型）与引用列表；无命中不得编造；记录查询日志；错误码映射。

**Qoder 完成**：`app/ask.py` 按**真实响应结构**（`hits[].memory.{artifact,kind,text}`，无 `citation` 字段）实现；`citations[:top_k]` 截断；`samples/queries.log` 记录实际使用的查询。

**验证**：mock 覆盖乱序分、同页重复、`kind≠pdf-page`、无定位符、超长正文、含空格与中文的 doc id；`limit` 计算 1/5/20/50 → 3/15/50/50；`top_k=0/51` → 422；no_hit / 400 / 503 / 非 JSON 四条分支。

**真机验收**（Ubuntu + PowerContext 1.4.1，详见 `VERIFY.md`）：
解析 503 页 → 503 块、0 跳过、38.4s；提问 `What was PepsiCo net revenue in 2022?` → 返回原文表格行 `Net revenue $ 86,392 $ 79,474 9 %`，引用 p71(0.0164)/p40/p74/p41/p70。

---

## 阶段 P4 — 记忆的保存与复用

**下达的需求**：偏好存到**独立 scope**（幂等创建）；`POST/GET /memory` 读写；`/ask` 检索前拼入偏好（`expanded_query`），零命中则回退原查询；响应回传 `applied_preferences`。

**Qoder 完成**：`app/memory.py`；启动时 lifespan 内幂等建偏好 scope（固定 `idempotency_key`）；`/ask` 组装 `original_query` / `expanded_query` / `applied_preferences`；`queries.log` 增加 `used_query`、`fallback_used`。

**验证**：mock 侧验证幂等（两次启动只建一个 scope）、`kind` 过滤、扩展命中与回退两条路径；**真机**侧：写偏好 → 读回一致 → 泛化提问 `What changed in 2022?` 的 `expanded_query` 含偏好文本、命中收敛到 p40。

**期间发现并修复**：`text` 字段缺 `max_length`，2001 字符被放行 → 补校验后 2001 返回 422、2000 通过。

---

## 阶段 P5 — 展示站与发布

**下达的需求**：单页展示站，六板块（Hero / 解决的问题 / 核心功能 / 实测数据 / 演示与体验 / 页脚），纯静态、零外部资源、离线可开。

**Qoder 完成**：`web/index.html`（纯 HTML+CSS、零 JS、零 CDN）；六板块结构与文案；纯 CSS 流程图。

**期间发现并修复**：页面文件被外部工具以错误编码写坏（字节层面既非合法 UTF-8 也非合法 GBK，混合有损，无法靠重新解码救回）→ Qoder 整份重新生成，并以 Write 工具（而非文本重定向）写入，自查严格 UTF-8 解码 + U+FFFD 计数为 0。

**发布**：`prepare_site → get_publish_status(canPublish: true) → publish_site → 轮询 published: true`，`webDirectory: web`；访问策略 `mode: public`。
站点：https://docmind-ggmuz5gz7rq.qoder.zone

**QA 记录**：静态站里 `/docs` 原为可点链接（后端路由，静态站必然 404）→ 改为纯文本；`README.md#快速开始` 相对链接在静态站 404 → 改为仓库绝对链接。

---

## 我（开发者）如何核对 Qoder 的工作

1. 每个阶段都要求 Qoder 给出**可执行的验证**（curl 命令 + 预期输出），而不是"已完成"的口头结论；
2. 关键阶段在**独立环境**（Ubuntu 22.04 + 真实 PowerContext 1.4.1）复跑，结果记入 `VERIFY.md`；
3. 对 Qoder 无法复现的数据（如 503 页真机指标），要求在文档中注明来源，不把未验证数字写成"实测"。
