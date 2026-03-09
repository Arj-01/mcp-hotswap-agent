"""Streamlit frontend for the MCP Multi-Agent Assistant."""

import os
import uuid

import requests
import streamlit as st

BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")

# ---------------------------------------------------------------- page config
st.set_page_config(
    page_title="MCP Assistant",
    page_icon="🤖",
    layout="wide",
)

# ---------------------------------------------------------------- session state
if "session_id" not in st.session_state:
    st.session_state.session_id = str(uuid.uuid4())[:8]

if "messages" not in st.session_state:
    st.session_state.messages = []


# ---------------------------------------------------------------- helpers
def api_get(path: str):
    try:
        r = requests.get(f"{BACKEND_URL}{path}", timeout=10)
        r.raise_for_status()
        return r.json()
    except requests.RequestException as e:
        st.error(f"API error: {e}")
        return None


def api_post(path: str, json_body: dict):
    try:
        r = requests.post(f"{BACKEND_URL}{path}", json=json_body, timeout=120)
        r.raise_for_status()
        return r.json()
    except requests.RequestException as e:
        st.error(f"API error: {e}")
        return None


def api_delete(path: str):
    try:
        r = requests.delete(f"{BACKEND_URL}{path}", timeout=10)
        r.raise_for_status()
        return r.json()
    except requests.RequestException as e:
        st.error(f"API error: {e}")
        return None


# ---------------------------------------------------------------- sidebar
with st.sidebar:
    st.title("MCP Assistant")
    st.caption(f"Session: `{st.session_state.session_id}`")

    # Health check
    health = api_get("/health")
    if health:
        col1, col2 = st.columns(2)
        col1.metric("Servers", health.get("servers", 0))
        col2.metric("Tools", health.get("tools", 0))
        redis_status = health.get("redis", "unknown")
        if redis_status == "ok":
            st.success("Redis: connected")
        else:
            st.error("Redis: disconnected")
    else:
        st.warning("Backend unreachable")

    st.divider()

    # Connected servers
    st.subheader("Connected Servers")
    servers = api_get("/servers")
    if servers:
        for srv in servers:
            with st.expander(srv.get("name", "unknown")):
                st.write(f"**Tools:** {srv.get('tool_count', 0)}")
                st.write(f"**Status:** {srv.get('status', 'unknown')}")
                if st.button("Disconnect", key=f"disc_{srv['name']}"):
                    api_delete(f"/servers/{srv['name']}")
                    st.rerun()
    else:
        st.info("No servers connected")

    st.divider()

    # Available tools
    st.subheader("Available Tools")
    tools = api_get("/tools")
    if tools:
        for tool in tools:
            with st.expander(tool.get("tool_name", "unknown")):
                st.write(f"**Server:** {tool.get('server_name', 'unknown')}")
                st.write(f"**Description:** {tool.get('description', 'N/A')}")
                params = tool.get("parameters", [])
                if params:
                    st.write("**Parameters:**")
                    for p in params:
                        required = " *(required)*" if p.get("required") else ""
                        st.write(f"- `{p.get('name', '?')}`: {p.get('description', '')}{required}")
    else:
        st.info("No tools available")

    st.divider()

    # Clear chat
    if st.button("Clear Chat"):
        api_delete(f"/chat/history/{st.session_state.session_id}")
        st.session_state.messages = []
        st.rerun()

    # New session
    if st.button("New Session"):
        st.session_state.session_id = str(uuid.uuid4())[:8]
        st.session_state.messages = []
        st.rerun()

# ---------------------------------------------------------------- main chat area
st.header("Chat")

# Display existing messages
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if msg["role"] == "assistant" and msg.get("tools_used"):
            hotplugged = msg.get("hotplugged_servers", [])
            if hotplugged:
                st.info(f"Plugin auto-loaded: {', '.join(hotplugged)}", icon="\u26a1")
            with st.expander("Tool Details"):
                for tr in msg.get("tool_results", []):
                    label = f"**{tr['tool']}** ({tr['duration_ms']}ms)"
                    if tr.get("hotplugged"):
                        label += " \u26a1 *hot-plugged*"
                    st.write(label)
                    st.code(tr.get("result", ""), language="text")

# Chat input
if prompt := st.chat_input("Ask me anything..."):
    # Add user message
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    # Query the backend
    with st.chat_message("assistant"):
        with st.spinner("Thinking..."):
            resp = api_post("/query", {
                "query": prompt,
                "session_id": st.session_state.session_id,
            })

        if resp:
            st.markdown(resp["answer"])
            tools_used = resp.get("tools_used", [])
            tool_results = resp.get("tool_results", [])
            hotplugged = resp.get("hotplugged_servers", [])

            if hotplugged:
                st.info(f"Plugin auto-loaded: {', '.join(hotplugged)}", icon="\u26a1")

            if tools_used:
                with st.expander(f"Tools used: {', '.join(tools_used)}"):
                    for tr in tool_results:
                        label = f"**{tr['tool']}** ({tr['duration_ms']}ms)"
                        if tr.get("hotplugged"):
                            label += " \u26a1 *hot-plugged*"
                        st.write(label)
                        st.code(tr.get("result", ""), language="text")

            st.caption(f"Total: {resp.get('total_duration_ms', 0):.0f}ms")

            st.session_state.messages.append({
                "role": "assistant",
                "content": resp["answer"],
                "tools_used": tools_used,
                "tool_results": tool_results,
                "hotplugged_servers": hotplugged,
            })
        else:
            error_msg = "Failed to get a response from the backend."
            st.error(error_msg)
            st.session_state.messages.append({
                "role": "assistant",
                "content": error_msg,
            })
