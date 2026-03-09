"""Router Agent — the brain of the system.

Receives a user query, asks Ollama which tools to call, executes them via
MCPClient, then synthesises a final answer.
"""
import asyncio
import json
import logging
import re
import time

import httpx
from pydantic import BaseModel

from agents.config import Settings
from agents.mcp_client import MCPClient
from agents.tool_registry import ToolRegistry

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------- response model

class AgentResponse(BaseModel):
    query: str
    tools_used: list[str]
    tool_results: list[dict]   # [{tool, params, result, duration_ms, hotplugged}]
    answer: str
    total_duration_ms: float
    hotplugged_servers: list[str] = []


# ---------------------------------------------------------------- router agent

class RouterAgent:
    def __init__(
        self,
        tool_registry: ToolRegistry,
        mcp_client: MCPClient,
        config: Settings,
    ):
        self._registry = tool_registry
        self._mcp = mcp_client
        self._config = config

    # ---------------------------------------------------------- private helpers

    async def _llm(self, prompt: str, timeout: float = 300.0) -> str:
        """POST to Ollama /api/generate and return the response text."""
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                f"{self._config.ollama_base_url}/api/generate",
                json={
                    "model": self._config.ollama_model,
                    "prompt": prompt,
                    "stream": False,
                },
            )
            resp.raise_for_status()
            return resp.json().get("response", "").strip()

    @staticmethod
    def _strip_fences(text: str) -> str:
        """Remove ```json / ``` markdown fences from LLM output."""
        text = re.sub(r"```(?:json)?\s*", "", text)
        return text.replace("```", "").strip()

    def _parse_tool_calls(self, text: str) -> list[dict] | None:
        """Extract and parse a JSON array of tool calls from LLM output.

        Returns the list on success, None if parsing fails.
        """
        cleaned = self._strip_fences(text)
        # Grab the first [...] block (LLMs sometimes add prose before/after)
        match = re.search(r"\[.*?\]", cleaned, re.DOTALL)
        if match:
            cleaned = match.group(0)
        try:
            parsed = json.loads(cleaned)
            if isinstance(parsed, list):
                return parsed
        except (json.JSONDecodeError, ValueError):
            pass
        return None

    def _offline_response(self, query: str, elapsed_ms: float) -> AgentResponse:
        return AgentResponse(
            query=query,
            tools_used=[],
            tool_results=[],
            answer="LLM is offline. Start Ollama with: ollama serve",
            total_duration_ms=elapsed_ms,
        )

    def _direct_response(self, query: str, answer: str, elapsed_ms: float) -> AgentResponse:
        return AgentResponse(
            query=query,
            tools_used=[],
            tool_results=[],
            answer=answer,
            total_duration_ms=elapsed_ms,
        )

    # ----------------------------------------------------------- main entrypoint

    async def route_query(self, query: str, session_id: str) -> AgentResponse:
        start = time.monotonic()

        def elapsed() -> float:
            return round((time.monotonic() - start) * 1000, 1)

        # STEP 1 — get tools summary (loaded + dormant plugins)
        tools_summary = await self._registry.get_tools_summary()
        dormant_summary = self._mcp.get_dormant_tools_summary()

        all_tools_block = f"Loaded tools:\n{tools_summary}"
        if dormant_summary:
            all_tools_block += f"\n\nAvailable plugins (will be auto-loaded if needed):\n{dormant_summary}"

        # STEP 2 — ask Ollama which tools to call
        routing_prompt = (
            "You are a tool-routing assistant. Pick the right tool for the query.\n\n"
            f"{all_tools_block}\n\n"
            "Special option:\n"
            "- NONE — use this when no tool is needed (greetings, general knowledge, "
            "simple factual questions like 'capital of France', 'what is gravity', 'say hello')\n\n"
            f"User query: {query}\n\n"
            "Rules:\n"
            "- Respond ONLY with a JSON array\n"
            '- Format: [{"tool": "tool_name", "params": {"key": "value"}}]\n'
            '- For no tools: [{"tool": "NONE", "params": {}}]\n'
            "- search_web: ONLY for queries that explicitly say 'search' or need current/live info\n"
            "- calculate/percentage/split_bill/unit_convert/loan_emi: for math and calculations\n"
            "- NONE: for greetings, general knowledge, simple facts, explanations\n"
            "- Use ONLY tools listed above. Do NOT invent tool names"
        )

        try:
            llm_raw = await self._llm(routing_prompt)
        except (httpx.ConnectError, httpx.ConnectTimeout):
            return self._offline_response(query, elapsed())
        except httpx.ReadTimeout:
            logger.error("Ollama routing call timed out")
            return self._direct_response(query, "LLM request timed out. The model may still be loading — try again in a moment.", elapsed())
        except Exception as exc:
            logger.error("Ollama routing call failed: %s", exc)
            return self._direct_response(query, f"LLM error: {exc}", elapsed())

        # STEP 3 — parse tool calls, retry once on failure
        tool_calls = self._parse_tool_calls(llm_raw)

        if tool_calls is None:
            logger.warning("Failed to parse tool calls from: %r — retrying", llm_raw[:200])
            retry_prompt = (
                f"Return ONLY a valid JSON array for this query: {query}\n"
                f"Available tools:\n{tools_summary}\n"
                'Format: [{"tool": "name", "params": {}}]\n'
                "Respond with [] if no tools are needed. No other text."
            )
            try:
                retry_raw = await self._llm(retry_prompt)
                tool_calls = self._parse_tool_calls(retry_raw)
            except (httpx.ConnectError, httpx.ConnectTimeout):
                return self._offline_response(query, elapsed())
            except Exception as exc:
                logger.error("Retry call failed: %s", exc)
                tool_calls = None

        # Still unparseable — answer directly without tools
        if tool_calls is None:
            logger.warning("Falling back to direct LLM answer for: %r", query)
            try:
                answer = await self._llm(query, timeout=120.0)
            except (httpx.ConnectError, httpx.ConnectTimeout):
                return self._offline_response(query, elapsed())
            except Exception as exc:
                answer = f"Could not get answer: {exc}"
            return self._direct_response(query, answer, elapsed())

        # Validate — hot-plug dormant servers if needed
        known = {t["tool_name"] for t in await self._registry.get_all_tools()}
        hotplugged_servers: set[str] = set()
        for tc in tool_calls:
            if not isinstance(tc, dict):
                continue
            tool_name = tc.get("tool", "")
            if tool_name and tool_name not in known:
                # Try to hot-plug the server that provides this tool
                server_name = self._mcp.get_dormant_server_for_tool(tool_name)
                if server_name and await self._mcp.hotplug_for_tool(tool_name):
                    logger.info("Hot-plugged server %s for tool: %s", server_name, tool_name)
                    hotplugged_servers.add(server_name)
                    known = {t["tool_name"] for t in await self._registry.get_all_tools()}

        valid_calls = [
            tc for tc in tool_calls
            if isinstance(tc, dict) and tc.get("tool") in known
        ]

        # No valid tools — answer directly
        if not valid_calls:
            try:
                answer = await self._llm(query, timeout=120.0)
            except (httpx.ConnectError, httpx.ConnectTimeout):
                return self._offline_response(query, elapsed())
            except Exception as exc:
                answer = f"Could not get answer: {exc}"
            return self._direct_response(query, answer, elapsed())

        # STEP 4 — fix param names & execute all tool calls (independent → parallel)
        for tc in valid_calls:
            tool_name = tc["tool"]
            params = tc.get("params", {}) or {}
            tool_info = await self._registry.find_tool(tool_name)
            if tool_info and params:
                expected = set(tool_info.get("schema", {}).get("properties", {}).keys())
                provided = set(params.keys())
                # If no param names match and counts are equal, remap by position
                if expected and not (provided & expected) and len(provided) == len(expected):
                    corrected = dict(zip(sorted(expected), [params[k] for k in params]))
                    logger.info("Corrected params for %s: %s -> %s", tool_name, params, corrected)
                    tc["params"] = corrected

        async def _exec(tc: dict) -> dict:
            tool_name = tc["tool"]
            params = tc.get("params", {}) or {}
            t0 = time.monotonic()
            try:
                result = await self._mcp.call_tool(tool_name, params)
            except Exception as exc:
                logger.error("Tool %s failed: %s", tool_name, exc)
                result = f"Tool error: {exc}"
            tool_info = await self._registry.find_tool(tool_name)
            server_name = tool_info["server_name"] if tool_info else None
            return {
                "tool": tool_name,
                "params": params,
                "result": result,
                "duration_ms": round((time.monotonic() - t0) * 1000, 1),
                "hotplugged": server_name in hotplugged_servers,
            }

        tool_results: list[dict] = list(
            await asyncio.gather(*[_exec(tc) for tc in valid_calls])
        )
        tools_used = [r["tool"] for r in tool_results]

        # STEP 5 — synthesise final answer
        if len(tool_results) == 1:
            answer = tool_results[0]["result"]
        else:
            results_block = "\n\n".join(
                f"[{r['tool']}]\n{r['result']}" for r in tool_results
            )
            synthesis_prompt = (
                f"The user asked: {query}\n\n"
                f"Results from tools:\n\n{results_block}\n\n"
                "Combine these into a single, clear, helpful response."
            )
            try:
                answer = await self._llm(synthesis_prompt, timeout=120.0)
            except Exception:
                # Graceful fallback: join results without LLM
                answer = "\n\n".join(
                    f"**{r['tool']}**: {r['result']}" for r in tool_results
                )

        # STEP 6 — auto-detach hot-plugged servers (true hot-swap)
        for server_name in hotplugged_servers:
            try:
                await self._mcp.disconnect_server(server_name)
                logger.info("Auto-detached hot-plugged server: %s", server_name)
            except Exception as exc:
                logger.warning("Failed to auto-detach %s: %s", server_name, exc)
        # Re-scan so detached tools are available for next query
        if hotplugged_servers:
            self._mcp.scan_dormant_plugins(
                self._config.mcp_server_dir,
                exclude=["daily_digest_server.py"],
            )

        # STEP 7 — return
        return AgentResponse(
            query=query,
            tools_used=tools_used,
            tool_results=tool_results,
            answer=answer,
            total_duration_ms=elapsed(),
            hotplugged_servers=sorted(hotplugged_servers),
        )
