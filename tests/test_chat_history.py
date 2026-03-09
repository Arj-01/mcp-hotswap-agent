import pytest
import pytest_asyncio

from agents.chat_history import ChatHistory


@pytest_asyncio.fixture
async def history(fake_redis):
    h = ChatHistory("redis://fake")
    h._redis = fake_redis
    return h


async def test_add_and_get_messages(history):
    await history.add_message("sess1", "user", "Hello")
    await history.add_message("sess1", "assistant", "Hi there")
    msgs = await history.get_history("sess1")
    assert len(msgs) == 2
    assert msgs[0]["role"] == "user"
    assert msgs[0]["content"] == "Hello"
    assert msgs[1]["role"] == "assistant"
    assert msgs[1]["content"] == "Hi there"


async def test_message_has_timestamp(history):
    await history.add_message("sess1", "user", "Hello")
    msgs = await history.get_history("sess1")
    assert "timestamp" in msgs[0]
    assert msgs[0]["timestamp"]  # non-empty


async def test_clear_history(history):
    await history.add_message("sess2", "user", "Hello")
    await history.add_message("sess2", "assistant", "Hi")
    await history.clear_history("sess2")
    msgs = await history.get_history("sess2")
    assert msgs == []


async def test_get_empty_session(history):
    msgs = await history.get_history("nonexistent")
    assert msgs == []


async def test_max_50_message_limit(history):
    for i in range(60):
        await history.add_message("sess3", "user", f"Message {i}")
    msgs = await history.get_history("sess3")
    assert len(msgs) == 50
    assert msgs[0]["content"] == "Message 10"
    assert msgs[-1]["content"] == "Message 59"


async def test_separate_sessions_are_isolated(history):
    await history.add_message("sessA", "user", "Hello A")
    await history.add_message("sessB", "user", "Hello B")
    msgs_a = await history.get_history("sessA")
    msgs_b = await history.get_history("sessB")
    assert len(msgs_a) == 1
    assert len(msgs_b) == 1
    assert msgs_a[0]["content"] == "Hello A"
    assert msgs_b[0]["content"] == "Hello B"
