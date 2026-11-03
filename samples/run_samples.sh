#!/usr/bin/env bash
# 对运行中的 docmind 打一轮请求：预览 -> 入库 -> 问答 -> 记忆。
# 用法：bash samples/run_samples.sh
# 请求体刻意只用 ASCII：Windows 终端可能以本地代码页编码中文参数，导致服务端收到乱码 JSON。
set -euo pipefail

BASE="${DOCMIND_BASE:-http://127.0.0.1:8000}"
PDF="${DOCMIND_SAMPLE_PDF:-samples/sample_doc.pdf}"

section() { printf '\n>>> %s\n' "$1"; }

section "POST /ingest/preview（只解析，不写库）"
curl -sS -X POST "$BASE/ingest/preview" \
  -H 'Content-Type: application/json' \
  -d "{\"source\":\"$PDF\"}"

section "POST /ingest（写入 PowerContext；重复跑同一文件会返回 skipped）"
curl -sS -X POST "$BASE/ingest" \
  -H 'Content-Type: application/json' \
  -d "{\"source\":\"$PDF\"}"

section "POST /memory（写入一条用户偏好，1-2000 字符）"
curl -sS -X POST "$BASE/memory" \
  -H 'Content-Type: application/json' \
  -d '{"text":"prefer short answers with page citations"}'

section "GET /memory（列出偏好，来自自动创建的偏好 scope）"
curl -sS "$BASE/memory"

section "POST /ask（先读偏好扩展 query；扩展无命中则退回原 query）"
curl -sS -X POST "$BASE/ask" \
  -H 'Content-Type: application/json' \
  -d '{"question":"what is the docmind sample document about?","top_k":3}'

printf '\n'
