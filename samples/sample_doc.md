# 示例文档：docmind 的第一份语料

这是一份纯文本样例，用来验证 `/ingest` 的入参形态。真实语料通常是 PDF，由 `pypdf` 抽取文本后再提交。

## 关键事实

- docmind 提供三个能力前缀：`/ingest`（入库）、`/ask`（问答）、`/memory`（长期记忆）。
- 所有外部依赖地址来自环境变量 `POWERCONTEXT_URL`，代码库中不保存任何凭据。
- 骨架阶段的路由只回显请求并附带 `status: "placeholder"`，用于确认服务与路由挂载正常。

## 可用于提问的样本问题

1. docmind 的三个路由前缀分别是什么？
2. PowerContext 的地址应该配置在哪里？
3. 为什么骨架阶段的路由要返回占位 JSON？
