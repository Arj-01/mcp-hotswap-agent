#!/usr/bin/env bash
# Load test: 12 queries across all servers with hot-swap verification
# Usage: bash scripts/load_test.sh

set -uo pipefail

API="http://localhost:8000"
SESSION="load-test-$$"
PASS=0
FAIL=0
QUERY_NUM=0

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

separator() {
    echo ""
    echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
}

run_query() {
    local desc="$1" query="$2" expect_tool="$3" expect_hotplug="$4"
    ((QUERY_NUM++))

    separator
    echo -e "${BOLD}Query $QUERY_NUM: $desc${NC}"
    echo -e "  Input: \"$query\""
    echo -e "  Expected tool: ${YELLOW}$expect_tool${NC}"
    echo -e "  Expected hot-plug: ${YELLOW}$expect_hotplug${NC}"
    echo ""

    # Capture logs before query
    local log_before
    log_before=$(docker compose logs api --tail=1 2>/dev/null | head -1)

    # Run query
    local result
    result=$(curl -sf -X POST -H "Content-Type: application/json" \
        -d "{\"query\":\"$query\",\"session_id\":\"$SESSION\"}" \
        "$API/query" 2>/dev/null)

    if [ -z "$result" ]; then
        echo -e "  ${RED}FAIL: No response from API${NC}"
        ((FAIL++))
        return
    fi

    # Parse response
    local tools_used answer hotplugged_servers duration
    tools_used=$(echo "$result" | python3 -c "import sys,json; d=json.load(sys.stdin); print(', '.join(d.get('tools_used',[])) or 'none')" 2>/dev/null)
    answer=$(echo "$result" | python3 -c "import sys,json; d=json.load(sys.stdin); a=d.get('answer',''); print(a[:120]+'...' if len(a)>120 else a)" 2>/dev/null)
    hotplugged_servers=$(echo "$result" | python3 -c "import sys,json; d=json.load(sys.stdin); print(', '.join(d.get('hotplugged_servers',[])) or 'none')" 2>/dev/null)
    duration=$(echo "$result" | python3 -c "import sys,json; d=json.load(sys.stdin); print(f\"{d.get('total_duration_ms',0):.0f}ms\")" 2>/dev/null)

    # Check hotplugged flag on individual tools
    local tool_hotplug_flags
    tool_hotplug_flags=$(echo "$result" | python3 -c "
import sys,json
d=json.load(sys.stdin)
flags = [f\"{t['tool']}={'YES' if t.get('hotplugged') else 'no'}\" for t in d.get('tool_results',[])]
print(', '.join(flags) if flags else 'n/a')
" 2>/dev/null)

    echo -e "  ${BOLD}Results:${NC}"
    echo -e "    Tools used:        $tools_used"
    echo -e "    Hot-plugged svrs:  $hotplugged_servers"
    echo -e "    Tool hotplug flag: $tool_hotplug_flags"
    echo -e "    Duration:          $duration"
    echo -e "    Answer:            $answer"

    # Verify expectations
    local passed=true

    # Check tool
    if [ "$expect_tool" != "any" ]; then
        if echo "$tools_used" | grep -q "$expect_tool"; then
            echo -e "    Tool check:        ${GREEN}PASS${NC} (found $expect_tool)"
        else
            echo -e "    Tool check:        ${RED}FAIL${NC} (expected $expect_tool, got $tools_used)"
            passed=false
        fi
    else
        echo -e "    Tool check:        ${GREEN}PASS${NC} (any tool OK)"
    fi

    # Check hot-plug
    if [ "$expect_hotplug" = "yes" ]; then
        if [ "$hotplugged_servers" != "none" ]; then
            echo -e "    Hot-plug check:    ${GREEN}PASS${NC} (hot-plugged: $hotplugged_servers)"
        else
            echo -e "    Hot-plug check:    ${RED}FAIL${NC} (expected hot-plug but got none)"
            passed=false
        fi
    elif [ "$expect_hotplug" = "no" ]; then
        if [ "$hotplugged_servers" = "none" ]; then
            echo -e "    Hot-plug check:    ${GREEN}PASS${NC} (no hot-plug as expected)"
        else
            echo -e "    Hot-plug check:    ${RED}FAIL${NC} (unexpected hot-plug: $hotplugged_servers)"
            passed=false
        fi
    fi

    if $passed; then
        ((PASS++))
    else
        ((FAIL++))
    fi
}

# ================================================================
# LOAD TEST START
# ================================================================

echo ""
echo -e "${BOLD}╔══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}║           MCP HOT-SWAP LOAD TEST (12 Queries)              ║${NC}"
echo -e "${BOLD}╚══════════════════════════════════════════════════════════════╝${NC}"

# Mark timestamp so we only analyze logs from THIS run
LOG_SINCE=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

echo ""
echo -e "${CYAN}Servers at startup:${NC}"
curl -sf "$API/servers" 2>/dev/null | python3 -c "
import sys,json
servers = json.load(sys.stdin)
for s in servers:
    print(f\"  - {s['name']} ({s.get('tool_count',0)} tools, status: {s.get('status','?')})\" )
if not servers:
    print('  (none)')
"
echo ""
echo -e "${CYAN}Dormant tools available for hot-plug: calculator (calculate, percentage, split_bill, unit_convert, loan_emi)${NC}"

# ---- Phase 1: General queries (no math) ----

echo ""
echo -e "${BOLD}═══ PHASE 1: General Queries (no calculator needed) ═══${NC}"

run_query "Web search" \
    "search for latest AI news" \
    "search_web" "no"

run_query "Direct LLM (no tools)" \
    "say hello in French, Spanish, and Japanese" \
    "any" "any"

run_query "Web fetch" \
    "fetch the contents of https://example.com" \
    "any" "no"

# ---- Phase 2: First math query (should hot-plug) ----

echo ""
echo -e "${BOLD}═══ PHASE 2: First Math Query (should HOT-PLUG calculator) ═══${NC}"

run_query "FIRST MATH — should hot-plug calculator" \
    "calculate 42 * 58" \
    "calculate" "yes"

# ---- Phase 3: Back to general queries ----

echo ""
echo -e "${BOLD}═══ PHASE 3: General Queries Again (calculator should be detached) ═══${NC}"

run_query "Web search after math" \
    "search for python tutorials" \
    "search_web" "no"

run_query "Direct LLM after math" \
    "what is the capital of France" \
    "any" "no"

run_query "Another web search" \
    "search for docker best practices" \
    "search_web" "no"

# ---- Phase 4: Second math query (should hot-plug AGAIN) ----

echo ""
echo -e "${BOLD}═══ PHASE 4: Second Math Query (should HOT-PLUG AGAIN) ═══${NC}"
echo -e "${YELLOW}KEY TEST: Calculator was detached after Query 4.${NC}"
echo -e "${YELLOW}This query proves it hot-plugs fresh every time.${NC}"

run_query "SECOND MATH — should hot-plug AGAIN" \
    "calculate 15% of 8500" \
    "calculate" "yes"

# ---- Phase 5: More variety ----

echo ""
echo -e "${BOLD}═══ PHASE 5: Mixed Queries ═══${NC}"

run_query "General knowledge" \
    "explain what is MCP protocol briefly" \
    "any" "any"

run_query "Web search" \
    "search for streamlit documentation" \
    "search_web" "no"

# ---- Phase 6: Third math query ----

echo ""
echo -e "${BOLD}═══ PHASE 6: Third Math Query (hot-plug one more time) ═══${NC}"

run_query "THIRD MATH — split bill" \
    "split a bill of 2400 among 3 people with 15 percent tip" \
    "any" "yes"

run_query "Unit conversion" \
    "convert 100 km to miles" \
    "unit_convert" "yes"

# ================================================================
# LOG ANALYSIS
# ================================================================

separator
echo ""
echo -e "${BOLD}╔══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}║                    LOG ANALYSIS                            ║${NC}"
echo -e "${BOLD}╚══════════════════════════════════════════════════════════════╝${NC}"
echo ""

echo -e "${CYAN}All hot-plug events from THIS run (since $LOG_SINCE):${NC}"
echo ""
docker compose logs api --since "$LOG_SINCE" 2>/dev/null | grep -E "Hot-plug|Auto-detach|Disconnect.*calculator|dormant" | while read -r line; do
    if echo "$line" | grep -q "Hot-plugging"; then
        echo -e "  ${GREEN}▶ ATTACH${NC}  $line"
    elif echo "$line" | grep -q "Hot-plugged"; then
        echo -e "  ${GREEN}  ✓ READY${NC}  $line"
    elif echo "$line" | grep -q "Auto-detach"; then
        echo -e "  ${RED}◀ DETACH${NC}  $line"
    elif echo "$line" | grep -q "Disconnected.*calculator"; then
        echo -e "  ${RED}  ✗ KILLED${NC} $line"
    elif echo "$line" | grep -q "dormant"; then
        echo -e "  ${YELLOW}  ↺ RESCAN${NC} $line"
    fi
done

echo ""

# Count hot-plug cycles (THIS run only)
ATTACH_COUNT=$(docker compose logs api --since "$LOG_SINCE" 2>/dev/null | grep -c "Hot-plugging server calculator_server")
DETACH_COUNT=$(docker compose logs api --since "$LOG_SINCE" 2>/dev/null | grep -c "Auto-detached hot-plugged server: calculator_server")

echo -e "${BOLD}Hot-plug cycle summary:${NC}"
echo -e "  Calculator attached:  ${GREEN}$ATTACH_COUNT times${NC}"
echo -e "  Calculator detached:  ${RED}$DETACH_COUNT times${NC}"
echo ""

if [ "$ATTACH_COUNT" -eq "$DETACH_COUNT" ] && [ "$ATTACH_COUNT" -gt 0 ]; then
    echo -e "  ${GREEN}✓ Every attach had a matching detach — true hot-swap confirmed!${NC}"
else
    echo -e "  ${YELLOW}⚠ Attach/detach count mismatch (may include events from earlier sessions)${NC}"
fi

# ================================================================
# FINAL RESULTS
# ================================================================

separator
echo ""
echo -e "${BOLD}╔══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}║                    FINAL RESULTS                           ║${NC}"
echo -e "${BOLD}╚══════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "  Total queries:  $QUERY_NUM"
echo -e "  Passed:         ${GREEN}$PASS${NC}"
echo -e "  Failed:         ${RED}$FAIL${NC}"
echo ""

# Cleanup
curl -sf -X DELETE "$API/chat/history/$SESSION" > /dev/null 2>&1

if [ "$FAIL" -eq 0 ]; then
    echo -e "  ${GREEN}${BOLD}ALL TESTS PASSED ✓${NC}"
    echo ""
    exit 0
else
    echo -e "  ${RED}${BOLD}SOME TESTS FAILED ✗${NC}"
    echo ""
    exit 1
fi
