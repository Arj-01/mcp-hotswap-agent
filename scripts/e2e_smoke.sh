#!/usr/bin/env bash
# E2E smoke test against the live Docker stack.
# Usage: bash scripts/e2e_smoke.sh
# Prerequisites: docker compose up -d (and ollama model pulled)

set -uo pipefail

API="http://localhost:8000"
UI="http://localhost:8501"
PROM="http://localhost:9090"
GRAFANA="http://localhost:3000"

PASS=0
FAIL=0

check() {
    local desc="$1" url="$2" expect="$3"
    if curl -sf "$url" 2>/dev/null | tr -d '\n' | grep -q "$expect"; then
        echo "  PASS: $desc"
        ((PASS++))
    else
        echo "  FAIL: $desc ($url)"
        ((FAIL++))
    fi
}

check_post() {
    local desc="$1" url="$2" body="$3" expect="$4"
    if curl -sf -X POST -H "Content-Type: application/json" -d "$body" "$url" 2>/dev/null | tr -d '\n' | grep -q "$expect"; then
        echo "  PASS: $desc"
        ((PASS++))
    else
        echo "  FAIL: $desc ($url)"
        ((FAIL++))
    fi
}

echo "=== MCP Assistant — E2E Smoke Tests ==="
echo ""

echo "--- Infrastructure ---"
check "API health"          "$API/health"       '"status":"ok"'
check "Redis connected"     "$API/health"       '"redis":"ok"'
check "Prometheus targets"  "$PROM/api/v1/targets" "mcp-assistant"
check "Grafana health"      "$GRAFANA/api/health"  '"database"'
check "Streamlit UI"        "$UI"               "Streamlit"
echo ""

echo "--- Endpoints ---"
check "GET /servers"   "$API/servers"   '"name"'
check "GET /tools"     "$API/tools"     '"tool_name"'
check "GET /metrics"   "$API/metrics"   "queries_total"
echo ""

echo "--- Query: web search ---"
check_post "POST /query (search)" "$API/query" \
    '{"query":"search for python programming","session_id":"e2e-smoke"}' \
    '"query"'

echo "--- Query: math (hot-swap) ---"
check_post "POST /query (math)" "$API/query" \
    '{"query":"calculate 100 * 25","session_id":"e2e-smoke"}' \
    '"calculate"'

echo "--- Query: hot-swap indicator ---"
# Verify the response includes hotplugged_servers field
result=$(curl -sf -X POST -H "Content-Type: application/json" \
    -d '{"query":"calculate 7 * 8","session_id":"e2e-hp"}' \
    "$API/query" 2>/dev/null)
if echo "$result" | grep -q '"hotplugged_servers"'; then
    echo "  PASS: hotplugged_servers field present"
    ((PASS++))
else
    echo "  FAIL: hotplugged_servers field missing"
    ((FAIL++))
fi
echo ""

echo "--- Chat History ---"
check "GET /chat/history" "$API/chat/history/e2e-smoke" '"role"'
echo ""

echo "--- Cleanup ---"
curl -sf -X DELETE "$API/chat/history/e2e-smoke" > /dev/null 2>&1
curl -sf -X DELETE "$API/chat/history/e2e-hp" > /dev/null 2>&1
echo "  Cleaned up test sessions"
echo ""

echo "=== Results: $PASS passed, $FAIL failed ==="
[ "$FAIL" -eq 0 ] && exit 0 || exit 1
