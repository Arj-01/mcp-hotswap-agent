"""YouTube transcript MCP server — fetch transcripts and answer questions about videos."""
import os
import re
import httpx
from youtube_transcript_api import YouTubeTranscriptApi
from mcp.server.fastmcp import FastMCP

OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.2")

mcp = FastMCP("youtube-summary")

_MAX_TRANSCRIPT = 5000


def _extract_video_id(url: str) -> str:
    """Parse a YouTube URL and return the video ID, or raise ValueError."""
    patterns = [
        r"youtube\.com/watch\?.*v=([A-Za-z0-9_-]{11})",
        r"youtu\.be/([A-Za-z0-9_-]{11})",
        r"youtube\.com/embed/([A-Za-z0-9_-]{11})",
        r"youtube\.com/shorts/([A-Za-z0-9_-]{11})",
    ]
    for pattern in patterns:
        m = re.search(pattern, url)
        if m:
            return m.group(1)
    raise ValueError(f"Could not extract video ID from: {url}")


def _get_raw_transcript(youtube_url: str) -> str:
    video_id = _extract_video_id(youtube_url)
    ytt = YouTubeTranscriptApi()
    try:
        transcript = ytt.fetch(video_id)
    except Exception:
        # Fallback: try any available language
        transcript_list = ytt.list(video_id)
        first = next(iter(transcript_list))
        transcript = first.fetch()
    text = " ".join(snippet.text for snippet in transcript)
    return text[:_MAX_TRANSCRIPT]


def _ollama(prompt: str) -> str:
    with httpx.Client(timeout=120) as client:
        resp = client.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False},
        )
        resp.raise_for_status()
        return resp.json().get("response", "").strip()


@mcp.tool()
def get_transcript(youtube_url: str) -> str:
    """Fetch the transcript of a YouTube video and return up to 5000 characters of text."""
    try:
        return _get_raw_transcript(youtube_url)
    except Exception as exc:
        return f"Error getting transcript: {exc}"


@mcp.tool()
def summarize_video(youtube_url: str) -> str:
    """Fetch a YouTube transcript and return a 5-7 bullet-point summary from the local LLM."""
    try:
        transcript = _get_raw_transcript(youtube_url)
    except Exception as exc:
        return f"Error getting transcript: {exc}"

    prompt = (
        "Summarize the following video transcript in 5-7 concise bullet points.\n"
        "Each bullet should capture a key idea:\n\n"
        f"{transcript}"
    )
    try:
        return _ollama(prompt)
    except Exception as exc:
        return f"LLM error: {exc}"


@mcp.tool()
def ask_about_video(youtube_url: str, question: str) -> str:
    """Answer a specific question about a YouTube video using its transcript."""
    try:
        transcript = _get_raw_transcript(youtube_url)
    except Exception as exc:
        return f"Error getting transcript: {exc}"

    prompt = (
        f"Based on the following video transcript, answer this question:\n"
        f"Question: {question}\n\n"
        f"Transcript:\n{transcript}"
    )
    try:
        return _ollama(prompt)
    except Exception as exc:
        return f"LLM error: {exc}"


if __name__ == "__main__":
    mcp.run(transport="stdio")
