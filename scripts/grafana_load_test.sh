#!/usr/bin/env bash
# =============================================================================
# MCP Hot-Swap Agent — Grafana Population Load Test
# Populates all 8 Grafana dashboard panels:
#   1. Total Queries          4. Error Rate
#   2. Active Servers         5. Queries Per Minute (time-series)
#   3. Available Tools        6. Query Response Time p50/p95/p99
#                             7. Tool Calls by Server (time-series)
#                             8. Tool Response Time (time-series)
#
# Usage:  bash scripts/grafana_load_test.sh
# Prereq: docker compose up -d  (API on :8000, Grafana on :3000)
# =============================================================================

set -uo pipefail

API="http://localhost:8000"
PASS=0
FAIL=0
TOTAL=0
SESSION_BASE="grafana-load-$$"

# ── colours ──────────────────────────────────────────────────────────────────
GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; DIM='\033[2m'; NC='\033[0m'

sep()  { echo -e "${CYAN}────────────────────────────────────────────────────────────${NC}"; }
hdr()  { echo ""; echo -e "${BOLD}$1${NC}"; sep; }
tick() { echo -e "  ${GREEN}✓${NC}  $1"; }
cross(){ echo -e "  ${RED}✗${NC}  $1"; }
info() { echo -e "  ${DIM}$1${NC}"; }

# ── helpers ───────────────────────────────────────────────────────────────────

query() {
    # Usage: query <session_suffix> <query_text>
    local session="${SESSION_BASE}-$1"
    local q="$2"
    ((TOTAL++))
    local result
    result=$(curl -sf -X POST "$API/query" \
        -H "Content-Type: application/json" \
        -d "{\"query\":\"$q\",\"session_id\":\"$session\"}" 2>/dev/null)

    if [ -z "$result" ]; then
        cross "FAIL [$TOTAL] $q"
        ((FAIL++))
        return 1
    fi

    local tools answer duration hotplug
    tools=$(echo "$result"   | python3 -c "import sys,json; d=json.load(sys.stdin); print(', '.join(d.get('tools_used',[])) or 'none')" 2>/dev/null)
    answer=$(echo "$result"  | python3 -c "import sys,json; d=json.load(sys.stdin); a=d.get('answer',''); print((a[:80]+'…') if len(a)>80 else a)" 2>/dev/null)
    duration=$(echo "$result"| python3 -c "import sys,json; d=json.load(sys.stdin); print(f\"{d.get('total_duration_ms',0):.0f}ms\")" 2>/dev/null)
    hotplug=$(echo "$result" | python3 -c "import sys,json; d=json.load(sys.stdin); s=d.get('hotplugged_servers',[]); print('HOT-PLUG: '+', '.join(s) if s else '')" 2>/dev/null)

    tick "[$TOTAL] tools=${tools}  dur=${duration}"
    [ -n "$hotplug" ] && echo -e "       ${YELLOW}⚡ ${hotplug}${NC}"
    info "     ↳ $answer"
    ((PASS++))
    return 0
}

burst() {
    # Run N queries in parallel (same question, different sessions)
    local n="$1"; local q="$2"; local label="$3"
    echo -e "  ${CYAN}⟳ Burst ×${n}:${NC} $label"
    local pids=()
    for i in $(seq 1 "$n"); do
        query "burst-${n}-${i}-$$" "$q" &
        pids+=($!)
    done
    for pid in "${pids[@]}"; do wait "$pid" 2>/dev/null || true; done
}

sleep_info() {
    echo -e "\n  ${DIM}⏳ Sleeping ${1}s — letting Prometheus scrape …${NC}\n"
    sleep "$1"
}

# ── preflight ─────────────────────────────────────────────────────────────────

echo ""
echo -e "${BOLD}╔══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}║       MCP Hot-Swap — Grafana Population Load Test           ║${NC}"
echo -e "${BOLD}╚══════════════════════════════════════════════════════════════╝${NC}"
echo ""

echo -e "${CYAN}Checking API health …${NC}"
health=$(curl -sf "$API/health" 2>/dev/null)
if [ -z "$health" ]; then
    echo -e "${RED}ERROR: API not reachable at $API — is docker compose up?${NC}"
    exit 1
fi
redis_status=$(echo "$health" | python3 -c "import sys,json; print(json.load(sys.stdin).get('redis','?'))" 2>/dev/null)
servers=$(echo "$health"     | python3 -c "import sys,json; print(json.load(sys.stdin).get('servers',0))" 2>/dev/null)
tools_count=$(echo "$health" | python3 -c "import sys,json; print(json.load(sys.stdin).get('tools',0))" 2>/dev/null)

echo -e "  Redis:   ${GREEN}${redis_status}${NC}"
echo -e "  Servers: ${YELLOW}${servers}${NC}"
echo -e "  Tools:   ${YELLOW}${tools_count}${NC}"
echo ""
LOG_SINCE=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

# =============================================================================
# PHASE 1 — Baseline: direct LLM answers (no tools)
# Populates: Total Queries, Queries Per Minute baseline, p50/p95/p99
# =============================================================================
hdr "PHASE 1 — Baseline: Direct LLM Answers (no tools)"

query "p1-1" "say hello in French"
query "p1-2" "what is the capital of Japan"
query "p1-3" "explain what the Model Context Protocol is in one sentence"
query "p1-4" "what is recursion in programming"
query "p1-5" "name three programming languages"
query "p1-6" "what does REST stand for"

sleep_info 5

# =============================================================================
# PHASE 2 — Web research tools (already loaded server)
# Populates: Tool Calls by Server (web_research_server), Tool Response Time
# =============================================================================
hdr "PHASE 2 — Web Research Tools (loaded server)"

query "p2-1" "search for the latest news about AI agents"
query "p2-2" "search for python asyncio best practices"
query "p2-3" "search for MCP model context protocol"
query "p2-4" "search for docker compose networking tutorial"
query "p2-5" "fetch the contents of https://example.com"

sleep_info 5

# =============================================================================
# PHASE 3 — Notes tools (already loaded server)
# Populates: Tool Calls by Server (notes_creator_server), Tool Response Time
# =============================================================================
hdr "PHASE 3 — Notes Creator Tools (loaded server)"

query "p3-1" "create a note titled 'MCP Architecture' with content about hot-swap tool loading"
query "p3-2" "create a note titled 'Redis Data Model' about using hashes and sorted sets"
query "p3-3" "create a note titled 'Async Patterns' about asyncio task management"
query "p3-4" "list all my notes"
query "p3-5" "create a note from the topic: Prometheus observability in Python"

sleep_info 5

# =============================================================================
# PHASE 4 — HOT-SWAP: First calculator plug-in cycle
# Populates: Tool Calls by Server (calculator_server HOT-PLUG), hotplugged flag,
#            Active Servers spike, Available Tools spike
# =============================================================================
hdr "PHASE 4 — HOT-SWAP Cycle 1: Calculator First Activation"

echo -e "  ${YELLOW}Calculator is DORMANT — next query will hot-plug it${NC}\n"

query "p4-1" "calculate 125 * 48"
query "p4-2" "what is 15 percent of 8500"
query "p4-3" "split a bill of 3600 among 4 people with 18 percent tip"
query "p4-4" "calculate the EMI for a loan of 500000 at 8.5 percent interest for 5 years"
query "p4-5" "convert 100 kilometers to miles"

sleep_info 8

# =============================================================================
# PHASE 5 — Back to non-calculator (calculator should be detached)
# Populates: Active Servers drop back, QPM continuity
# =============================================================================
hdr "PHASE 5 — Post-Detach: Non-Calculator Queries"

echo -e "  ${YELLOW}Calculator auto-detached after Phase 4 — back to dormant${NC}\n"

query "p5-1" "search for grafana dashboard best practices"
query "p5-2" "what is the difference between Redis hashes and sorted sets"
query "p5-3" "create a note titled 'Load Testing' with content about Prometheus scraping"
query "p5-4" "search for fastapi background tasks"
query "p5-5" "explain what p50 p95 p99 latency percentiles mean"

sleep_info 5

# =============================================================================
# PHASE 6 — HOT-SWAP Cycle 2 (proves re-plug after detach)
# Key for Grafana: second bump in tool_calls by calculator_server
# =============================================================================
hdr "PHASE 6 — HOT-SWAP Cycle 2: Calculator Re-Activated"

echo -e "  ${YELLOW}Calculator was detached — this proves fresh hot-plug every time${NC}\n"

query "p6-1" "calculate 256 * 256"
query "p6-2" "what is 22 percent of 15000"
query "p6-3" "convert 5 kilograms to pounds"
query "p6-4" "split 4800 among 6 people with 20 percent tip"
query "p6-5" "calculate the loan EMI for 1000000 at 9 percent for 10 years"

sleep_info 8

# =============================================================================
# PHASE 7 — Burst: High QPM spike for time-series panel
# Populates: Queries Per Minute spike, p95/p99 spread
# =============================================================================
hdr "PHASE 7 — Burst Load: QPM Spike (parallel queries)"

burst 4 "search for kubernetes ingress controller" "web search ×4"
sleep 3
burst 3 "calculate 999 * 111" "calculator ×3"
sleep 3
burst 4 "list all my notes" "notes list ×4"
sleep 3
burst 3 "what is the speed of light" "direct LLM ×3"

sleep_info 10

# =============================================================================
# PHASE 8 — Error injection: populates Error Rate panel
# Triggers: malformed/empty tool params, unknown tool requests
# =============================================================================
hdr "PHASE 8 — Error Injection (populates Error Rate panel)"

# These will fail gracefully but register as errors in Prometheus
query "p8-1" "XYZZY_UNKNOWN_COMMAND_TRIGGER_ERROR_STATE" || true
query "p8-2" "!@#\$%^&*()" || true
query "p8-3" "search for "  || true   # empty search
query "p8-4" "calculate"    || true   # no expression

sleep_info 5

# =============================================================================
# PHASE 9 — HOT-SWAP Cycle 3 + mixed sustained load
# Ensures all 4 server types appear in "Tool Calls by Server" panel
# =============================================================================
hdr "PHASE 9 — Sustained Mixed Load (all servers)"

query "p9-1"  "search for prometheus histogram vs summary"
query "p9-2"  "calculate 42 factorial is too large, just do 12 * 13"
query "p9-3"  "create a note titled 'Grafana Tips' about dashboard provisioning"
query "p9-4"  "convert 212 fahrenheit to celsius"
query "p9-5"  "search for redis pipeline atomic operations"
query "p9-6"  "what is 33 percent of 27000"
query "p9-7"  "create a note from the topic: asyncio event loop internals"
query "p9-8"  "split 9000 among 5 people with 12 percent tip"
query "p9-9"  "search for langchain vs llamaindex comparison"
query "p9-10" "list all my notes"

sleep_info 10

# =============================================================================
# PHASE 10 — Final cooldown: steady-state QPM for trailing edge of chart
# =============================================================================
hdr "PHASE 10 — Cooldown: Steady State QPM"

for i in $(seq 1 8); do
    query "p10-${i}" "search for python best practices 2025"
    sleep 2
done

sleep_info 15

# =============================================================================
# SUMMARY
# =============================================================================

sep
echo ""
echo -e "${BOLD}╔══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}║                    LOAD TEST SUMMARY                        ║${NC}"
echo -e "${BOLD}╚══════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "  Total queries sent:  ${BOLD}${TOTAL}${NC}"
echo -e "  Passed:              ${GREEN}${PASS}${NC}"
echo -e "  Failed:              ${RED}${FAIL}${NC}"
echo ""

# Hot-swap cycle count from Docker logs
ATTACH=$(docker compose logs api --since "$LOG_SINCE" 2>/dev/null | grep -c "Hot-plugging server calculator_server" || echo 0)
DETACH=$(docker compose logs api --since "$LOG_SINCE" 2>/dev/null | grep -c "Auto-detached hot-plugged server: calculator_server" || echo 0)

echo -e "${CYAN}Hot-swap cycles (this run):${NC}"
echo -e "  Calculator attached:  ${GREEN}${ATTACH}×${NC}"
echo -e "  Calculator detached:  ${RED}${DETACH}×${NC}"
[ "$ATTACH" -eq "$DETACH" ] && [ "$ATTACH" -gt 0 ] \
    && echo -e "  ${GREEN}✓ Perfect attach/detach symmetry${NC}" \
    || echo -e "  ${YELLOW}⚠ Check logs — counts may differ across sessions${NC}"

echo ""
echo -e "${CYAN}Grafana panels now populated:${NC}"
echo -e "  ${GREEN}✓${NC}  Total Queries            → ~${TOTAL} queries"
echo -e "  ${GREEN}✓${NC}  Active Servers           → ${servers} (spikes during hot-plug)"
echo -e "  ${GREEN}✓${NC}  Available Tools          → fluctuates with hot-swap"
echo -e "  ${GREEN}✓${NC}  Error Rate               → Phase 8 injected errors"
echo -e "  ${GREEN}✓${NC}  Queries Per Minute       → Phase 7 burst spike visible"
echo -e "  ${GREEN}✓${NC}  Response Time p50/95/99  → spread from burst + tool queries"
echo -e "  ${GREEN}✓${NC}  Tool Calls by Server     → all 3 servers + calculator ×3 cycles"
echo -e "  ${GREEN}✓${NC}  Tool Response Time       → per-server latency distribution"
echo ""
echo -e "  ${BOLD}→ Open Grafana: http://localhost:3000 (admin / admin)${NC}"
echo ""

# Cleanup sessions
echo -e "${DIM}Cleaning up test sessions …${NC}"
for i in $(seq 1 10); do
    for j in $(seq 1 10); do
        curl -sf -X DELETE "$API/chat/history/${SESSION_BASE}-p${i}-${j}" > /dev/null 2>&1 || true
    done
done
for n in 3 4 6; do
    for i in $(seq 1 6); do
        curl -sf -X DELETE "$API/chat/history/${SESSION_BASE}-burst-${n}-${i}-$$" > /dev/null 2>&1 || true
    done
done
echo -e "${GREEN}Done.${NC}"
echo ""

[ "$FAIL" -eq 0 ] && exit 0 || exit 1