"""Prometheus metrics for the MCP Assistant."""
from prometheus_client import Counter, Gauge, Histogram

queries_total = Counter(
    "queries_total",
    "Total number of queries processed",
    ["status"],  # success | error
)

tool_calls_total = Counter(
    "tool_calls_total",
    "Total tool calls made",
    ["server", "tool", "status"],  # success | error | timeout
)

query_duration_seconds = Histogram(
    "query_duration_seconds",
    "End-to-end query duration in seconds",
    buckets=[0.1, 0.25, 0.5, 1, 2, 5, 10],
)

tool_call_duration_seconds = Histogram(
    "tool_call_duration_seconds",
    "Individual tool call duration in seconds",
    ["server", "tool"],
)

active_servers_count = Gauge(
    "active_servers_count",
    "Number of currently connected MCP servers",
)

available_tools_count = Gauge(
    "available_tools_count",
    "Number of tools registered in the tool registry",
)


def track_query(status: str, duration: float) -> None:
    """Increment query counter and record duration. status: 'success' | 'error'."""
    queries_total.labels(status=status).inc()
    query_duration_seconds.observe(duration)


def track_tool_call(server: str, tool: str, status: str, duration: float) -> None:
    """Increment tool-call counter and record duration. status: 'success' | 'error' | 'timeout'."""
    tool_calls_total.labels(server=server, tool=tool, status=status).inc()
    tool_call_duration_seconds.labels(server=server, tool=tool).observe(duration)


def update_server_count(count: int) -> None:
    active_servers_count.set(count)


def update_tool_count(count: int) -> None:
    available_tools_count.set(count)
