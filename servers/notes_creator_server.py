"""Notes MCP server — create, list, and read Markdown notes in the notes/ directory."""
import os
import re
import httpx
from datetime import datetime
from pathlib import Path
from mcp.server.fastmcp import FastMCP

OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.2")

mcp = FastMCP("notes-creator")

# Resolve notes/ relative to this file so the server works from any cwd
NOTES_DIR = Path(__file__).parent.parent / "notes"
NOTES_DIR.mkdir(exist_ok=True)


def _safe_filename(title: str) -> str:
    """Convert a title to a safe, lowercase filename (no spaces or special chars)."""
    slug = re.sub(r"[^\w\s-]", "", title.lower())
    slug = re.sub(r"[\s_-]+", "_", slug).strip("_")
    return slug or "untitled"


def _ollama(prompt: str) -> str:
    with httpx.Client(timeout=120) as client:
        resp = client.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False},
        )
        resp.raise_for_status()
        return resp.json().get("response", "").strip()


def _frontmatter(title: str) -> str:
    date = datetime.now().strftime("%Y-%m-%d")
    return f"---\ntitle: {title}\ndate: {date}\n---\n\n"


@mcp.tool()
def create_note(title: str, content: str) -> str:
    """Save a Markdown note with YAML frontmatter to the notes/ directory."""
    filename = _safe_filename(title) + ".md"
    path = NOTES_DIR / filename
    path.write_text(_frontmatter(title) + content, encoding="utf-8")
    return f"Note saved: notes/{filename}"


@mcp.tool()
def create_note_from_topic(topic: str) -> str:
    """Generate structured study notes about a topic using the local LLM and save them."""
    prompt = (
        f"Create structured study notes about: {topic}\n\n"
        "Format with these sections:\n"
        "# Title\n## Overview\n## Key Points\n## Summary\n\n"
        "Be thorough but concise."
    )
    try:
        content = _ollama(prompt)
    except Exception as exc:
        return f"LLM error: {exc}"

    filename = _safe_filename(topic) + ".md"
    path = NOTES_DIR / filename
    path.write_text(_frontmatter(topic) + content, encoding="utf-8")
    return f"{content}\n\n---\nSaved to: notes/{filename}"


@mcp.tool()
def list_notes() -> str:
    """List all Markdown notes in the notes/ directory with their titles and dates."""
    files = sorted(NOTES_DIR.glob("*.md"))
    if not files:
        return "No notes found."

    lines = []
    for f in files:
        # Try to extract date from frontmatter
        raw = f.read_text(encoding="utf-8")
        date_match = re.search(r"^date:\s*(.+)$", raw, re.MULTILINE)
        date = date_match.group(1).strip() if date_match else "unknown date"
        lines.append(f"- {f.stem}  ({date})")

    return f"Notes ({len(files)} total):\n" + "\n".join(lines)


@mcp.tool()
def read_note(title: str) -> str:
    """Read a note from the notes/ directory by title or filename stem."""
    slug = _safe_filename(title)

    # Exact match first, then fuzzy
    candidates = list(NOTES_DIR.glob("*.md"))
    for f in candidates:
        if f.stem == slug:
            return f.read_text(encoding="utf-8")

    # Case-insensitive partial match
    title_lower = title.lower()
    for f in candidates:
        if title_lower in f.stem.lower():
            return f.read_text(encoding="utf-8")

    return f"Note not found: {title!r}. Available: {[f.stem for f in candidates]}"


if __name__ == "__main__":
    mcp.run(transport="stdio")
