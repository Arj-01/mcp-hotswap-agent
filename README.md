# ⚡ MCP Multi-Agent Assistant

An AI assistant powered by the **Model Context Protocol (MCP)** with hot-pluggable tool servers. One Router Agent dynamically selects from multiple MCP servers based on natural language queries using LLM-powered tool routing.

## Key Feature: Hot-Pluggable Tool Servers

Write a Python file with MCP tools → register via UI → agent uses it instantly.
No restart. No config changes. No redeployment.

```
User: "What is 15% tip on a $85 bill?"
 ↓
Router Agent → LLM picks split_bill tool → Calculator MCP Server → "$14.69"
```

## Quick Start

### Option 1: Docker (recommended)

```bash
bash scripts/start.sh
```

This starts all services (API, frontend, Redis, Ollama, Prometheus, Grafana) and pulls the LLM model.

### Option 2: Local development

```bash
# Install dependencies
pip install -e ".[dev]"

# Start Redis and Ollama
redis-server &
ollama serve &
ollama pull llama3.2

# Start the API
uvicorn agents.main:app --reload --port 8000

# Start the frontend (separate terminal)
streamlit run frontend/app.py
```

## Architecture

```
┌──────────────────────────────────────┐
│      Streamlit Frontend (:8501)      │
└──────────────────┬───────────────────┘
                   │ REST
┌──────────────────▼───────────────────┐
│    FastAPI Router Agent (:8000)      │
│    ├── /query       (chat)           │
│    ├── /tools       (list tools)     │
│    ├── /servers     (manage servers) │
│    ├── /health      (status)         │
│    └── /metrics     (prometheus)     │
└───┬──────────┬──────────┬────────────┘
    │          │          │
    ▼          ▼          ▼
 Tool       Router     Chat
Registry    Agent     History
 (Redis)   (Ollama)   (Redis)
    │          │
    ▼          ▼
  MCP Servers (stdio)
  ├── Calculator     (math, unit conversion, EMI)
  ├── Web Research   (search, fetch, summarize)
  ├── Notes Creator  (create, list, read notes)
  └── YouTube        (transcript, summary, Q&A)
```

**How it works:**
1. User sends a natural language query
2. Router Agent sends the query + available tools list to Ollama
3. LLM decides which tools to call (zero, one, or many)
4. Tools execute in parallel via MCP stdio sessions
5. Results are synthesized into a single response

## Built-in MCP Servers

| Server | Tools | Description |
|--------|-------|-------------|
| **Calculator** | `calculate`, `percentage`, `split_bill`, `unit_convert`, `loan_emi` | Math, conversions, finance |
| **Web Research** | `search_web`, `fetch_url`, `summarize_url` | DuckDuckGo search, page scraping |
| **Notes Creator** | `create_note`, `create_note_from_topic`, `list_notes`, `read_note` | Markdown notes with YAML frontmatter |
| **YouTube** | `get_transcript`, `summarize_video`, `ask_about_video` | Transcript extraction, video Q&A |

## Adding a Custom Tool Server

Create a Python file in `servers/`:

```python
# servers/my_tools_server.py
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("my-tools")

@mcp.tool()
def greet(name: str) -> str:
    """Say hello to someone."""
    return f"Hello, {name}!"

if __name__ == "__main__":
    mcp.run(transport="stdio")
```

Then register it via the UI or API:

```bash
curl -X POST http://localhost:8000/servers/register \
  -H "Content-Type: application/json" \
  -d '{"name": "my-tools", "script_path": "servers/my_tools_server.py"}'
```

The agent will immediately start using your tools — no restart required.

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/query` | Send a query to the router agent |
| `GET` | `/tools` | List all registered tools |
| `GET` | `/servers` | List connected MCP servers |
| `POST` | `/servers/register` | Register a new MCP server |
| `DELETE` | `/servers/{name}` | Disconnect a server |
| `GET` | `/chat/history/{session_id}` | Get conversation history |
| `DELETE` | `/chat/history/{session_id}` | Clear session history |
| `GET` | `/health` | Health check |
| `GET` | `/metrics` | Prometheus metrics |

## Monitoring

Prometheus and Grafana are included in the Docker stack:

- **Grafana**: http://localhost:3000 (admin/admin)
- **Prometheus**: http://localhost:9090

The dashboard tracks:
- Total queries, active servers, available tools, error rate
- Queries per minute, response time percentiles (p50/p95/p99)
- Tool calls by server, per-tool response times

## Project Structure

```
mcp-assistant/
├── agents/
│   ├── main.py            # FastAPI app with all endpoints
│   ├── router_agent.py    # LLM-powered tool routing
│   ├── mcp_client.py      # MCP stdio session manager
│   ├── tool_registry.py   # Redis-backed tool storage
│   ├── chat_history.py    # Conversation persistence
│   ├── config.py          # Settings (env vars)
│   └── metrics.py         # Prometheus counters/gauges
├── servers/
│   ├── calculator_server.py
│   ├── web_research_server.py
│   ├── notes_creator_server.py
│   └── youtube_summary_server.py
├── frontend/              # Streamlit UI
├── docker/
│   ├── Dockerfile
│   ├── Dockerfile.frontend
│   └── docker-compose.yml
├── monitoring/
│   ├── prometheus.yml
│   ├── grafana-dashboard.json
│   ├── grafana-datasource.yml
│   └── grafana-dashboard-provider.yml
├── scripts/
│   └── start.sh
├── tests/
│   ├── test_integration.py
│   ├── test_chat_history.py
│   └── test_tool_registry.py
└── pyproject.toml
```

## Development

```bash
# Run tests
pytest tests/ -v

# Run a single test file
pytest tests/test_integration.py -v

# Start with Docker
cd docker && docker compose up -d

# View logs
cd docker && docker compose logs -f api
```

## Tech Stack

- **Python 3.11** + **FastAPI** — async API server
- **MCP SDK** — Model Context Protocol for tool communication
- **Ollama** (llama3.2) — local LLM for tool routing
- **Redis** — tool registry + chat history persistence
- **Streamlit** — frontend UI
- **Prometheus + Grafana** — monitoring and dashboards
