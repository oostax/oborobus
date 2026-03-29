"""macOS-specific tools: find_files, get_running_apps, news_search.

Tools:
  - find_files       : search files on disk using Spotlight (mdfind)
  - get_running_apps : list currently running applications
  - news_search      : search news via DuckDuckGo (free, no API key)
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

from ouroboros.tools.registry import ToolContext, ToolEntry

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# find_files — Spotlight search (mdfind), whole disk, instant
# ---------------------------------------------------------------------------

def _find_files(ctx: ToolContext, query: str, search_type: str = "name",
                max_results: int = 20) -> str:
    """Find files on the entire disk using macOS Spotlight (mdfind).

    search_type:
      "name"    — search by filename (default)
      "content" — search by file content
      "kind"    — search by file type (e.g. "pdf", "docx", "xlsx")
    """
    if sys.platform != "darwin":
        # Fallback for non-macOS: use find command
        try:
            result = subprocess.run(
                ["find", "/", "-name", f"*{query}*", "-maxdepth", "8"],
                capture_output=True, text=True, timeout=15
            )
            lines = result.stdout.strip().splitlines()[:max_results]
            return json.dumps({"files": lines, "count": len(lines)}, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

    try:
        if search_type == "name":
            # Search by filename
            cmd = ["mdfind", "-name", query]
        elif search_type == "content":
            # Search by content
            cmd = ["mdfind", query]
        elif search_type == "kind":
            # Search by file kind/extension
            kind_map = {
                "pdf": "kMDItemFSName == '*.pdf'cd",
                "docx": "kMDItemFSName == '*.docx'cd",
                "xlsx": "kMDItemFSName == '*.xlsx'cd",
                "pptx": "kMDItemFSName == '*.pptx'cd",
                "txt": "kMDItemFSName == '*.txt'cd",
                "jpg": "kMDItemFSName == '*.jpg'cd",
                "png": "kMDItemFSName == '*.png'cd",
            }
            mdfind_query = kind_map.get(query.lower(), f"kMDItemFSName == '*.{query}'cd")
            cmd = ["mdfind", mdfind_query]
        else:
            cmd = ["mdfind", "-name", query]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        lines = [l for l in result.stdout.strip().splitlines() if l.strip()]

        # Filter out system/hidden paths for cleaner results
        filtered = [l for l in lines if not any(
            skip in l for skip in ["/System/", "/.Trash/", "/private/var/", "/Library/Caches/"]
        )]

        top = filtered[:max_results]
        
        # Open first file in Finder if files found
        if top:
            try:
                # Use 'open -R' to reveal file in Finder
                subprocess.run(["open", "-R", top[0]], timeout=5, check=False)
            except Exception:
                pass  # Don't fail if Finder open fails
        
        return json.dumps({
            "query": query,
            "search_type": search_type,
            "count": len(top),
            "files": top,
            "finder_opened": len(top) > 0
        }, ensure_ascii=False, indent=2)

    except subprocess.TimeoutExpired:
        return json.dumps({"error": "Search timed out after 10s"}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


# ---------------------------------------------------------------------------
# get_running_apps — list open applications
# ---------------------------------------------------------------------------

def _get_running_apps(ctx: ToolContext) -> str:
    """List currently running applications on macOS."""
    try:
        if sys.platform == "darwin":
            result = subprocess.run(
                ["osascript", "-e",
                 'tell application "System Events" to get name of every process where background only is false'],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                apps = [a.strip() for a in result.stdout.strip().split(",") if a.strip()]
                return json.dumps({"running_apps": apps, "count": len(apps)}, ensure_ascii=False, indent=2)

        # Fallback: ps aux
        result = subprocess.run(
            ["ps", "aux"], capture_output=True, text=True, timeout=5
        )
        lines = result.stdout.strip().splitlines()[1:]  # skip header
        apps = list({l.split()[-1].split("/")[-1] for l in lines if l.strip()})[:30]
        return json.dumps({"running_apps": sorted(apps), "count": len(apps)}, ensure_ascii=False, indent=2)

    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


# ---------------------------------------------------------------------------
# list_desktop — show files on Desktop
# ---------------------------------------------------------------------------

def _list_desktop(ctx: ToolContext) -> str:
    """List files and folders on the Desktop."""
    try:
        desktop = Path.home() / "Desktop"
        if not desktop.exists():
            return json.dumps({"error": "Desktop folder not found"}, ensure_ascii=False)
        
        files = []
        for item in desktop.iterdir():
            files.append({
                "name": item.name,
                "type": "folder" if item.is_dir() else "file",
                "path": str(item)
            })
        
        return json.dumps({
            "desktop": str(desktop),
            "files": sorted(files, key=lambda x: x["name"]),
            "count": len(files)
        }, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({"error": f"Failed to list desktop: {e}"}, ensure_ascii=False)


# ---------------------------------------------------------------------------
# news_search — DuckDuckGo (free, no API key)
# ---------------------------------------------------------------------------

def _news_search(ctx: ToolContext, query: str, region: str = "ru-ru",
                 max_results: int = 5) -> str:
    """Search news via DuckDuckGo Instant Answer API. Free, no API key required."""
    try:
        import urllib.request
        import urllib.parse
        import ssl

        # DuckDuckGo Instant Answer API
        params = urllib.parse.urlencode({
            "q": query,
            "format": "json",
            "no_html": "1",
            "skip_disambig": "1",
        })
        url = f"https://api.duckduckgo.com/?{params}"

        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        
        # Disable SSL verification for corporate proxies
        ctx_ssl = ssl.create_default_context()
        ctx_ssl.check_hostname = False
        ctx_ssl.verify_mode = ssl.CERT_NONE
        
        with urllib.request.urlopen(req, timeout=10, context=ctx_ssl) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        results = []

        # Abstract (main answer)
        if data.get("AbstractText"):
            results.append({
                "title": data.get("Heading", query),
                "text": data["AbstractText"][:500],
                "url": data.get("AbstractURL", ""),
                "source": data.get("AbstractSource", ""),
            })

        # Related topics
        for topic in data.get("RelatedTopics", [])[:max_results]:
            if isinstance(topic, dict) and topic.get("Text"):
                results.append({
                    "title": topic.get("Text", "")[:100],
                    "text": topic.get("Text", "")[:300],
                    "url": topic.get("FirstURL", ""),
                    "source": "DuckDuckGo",
                })

        if not results:
            return json.dumps({
                "query": query,
                "count": 0,
                "message": "No news found. Try different keywords.",
            }, ensure_ascii=False, indent=2)

        return json.dumps({
            "query": query,
            "count": len(results),
            "results": results[:max_results]
        }, ensure_ascii=False, indent=2)

    except Exception as e:
        log.warning("DuckDuckGo search failed: %s", e)
        return json.dumps({
            "error": f"Search failed: {str(e)}",
            "query": query,
        }, ensure_ascii=False, indent=2)


def _web_search_fallback(ctx: ToolContext, query: str) -> str:
    """Fallback: search via Chrome browser."""
    try:
        from ouroboros.tools.browser import _ensure_browser, cleanup_browser, _is_infrastructure_error
        import urllib.parse

        page = _ensure_browser(ctx)
        encoded = urllib.parse.quote_plus(query + " новости")
        url = f"https://duckduckgo.com/?q={encoded}&ia=news"
        try:
            page.goto(url, timeout=20000, wait_until="domcontentloaded")
        except Exception:
            pass

        text = page.evaluate("() => document.body.innerText")
        return json.dumps({
            "query": query,
            "source": "browser_fallback",
            "text": (text or "")[:2000]
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": f"Search failed: {e}"}, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Tool registration
def _open_app_or_url(ctx: ToolContext, target: str) -> str:
    """Open application or website on macOS."""
    try:
        import subprocess
        
        # Check if it's a URL
        if target.startswith("http://") or target.startswith("https://"):
            url = target
        elif "." in target and not target.endswith(".app"):
            # Looks like a domain
            url = f"https://{target}"
        else:
            # It's an application name
            result = subprocess.run(
                ["open", "-a", target],
                capture_output=True,
                text=True,
                timeout=5
            )
            
            if result.returncode == 0:
                return json.dumps({
                    "success": True,
                    "action": "opened_app",
                    "app": target,
                }, ensure_ascii=False, indent=2)
            else:
                return json.dumps({
                    "error": f"Failed to open app: {result.stderr}",
                    "app": target,
                }, ensure_ascii=False, indent=2)
        
        # Open URL
        result = subprocess.run(
            ["open", url],
            capture_output=True,
            text=True,
            timeout=5
        )
        
        if result.returncode == 0:
            return json.dumps({
                "success": True,
                "action": "opened_url",
                "url": url,
            }, ensure_ascii=False, indent=2)
        else:
            return json.dumps({
                "error": f"Failed to open URL: {result.stderr}",
                "url": url,
            }, ensure_ascii=False, indent=2)
            
    except Exception as e:
        return json.dumps({
            "error": f"Failed to open: {str(e)}",
            "target": target,
        }, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------

def get_tools() -> List[ToolEntry]:
    return [
        ToolEntry("find_files", {
            "name": "find_files",
            "description": (
                "Find files on the entire Mac disk using Spotlight. Fast and comprehensive. "
                "search_type: 'name' (by filename), 'content' (by text inside), 'kind' (by type: pdf/docx/xlsx/pptx/txt). "
                "Use for: finding documents, reports, presentations anywhere on the computer."
            ),
            "parameters": {"type": "object", "properties": {
                "query": {"type": "string", "description": "Search query — filename, keyword, or file type"},
                "search_type": {
                    "type": "string",
                    "enum": ["name", "content", "kind"],
                    "default": "name",
                    "description": "How to search: by name (default), by content, or by file kind"
                },
                "max_results": {"type": "integer", "default": 20, "description": "Max results to return"},
            }, "required": ["query"]},
        }, _find_files),

        ToolEntry("list_desktop", {
            "name": "list_desktop",
            "description": "List all files and folders on the Desktop.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        }, _list_desktop),

        ToolEntry("get_running_apps", {
            "name": "get_running_apps",
            "description": "List all currently running applications on macOS.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        }, _get_running_apps),

        ToolEntry("news_search", {
            "name": "news_search",
            "description": (
                "Search for current news and information via DuckDuckGo. Free, no API key. "
                "Use for: news about regions, government programs, strategies, current events. "
                "Returns titles, summaries and links."
            ),
            "parameters": {"type": "object", "properties": {
                "query": {"type": "string", "description": "Search query in Russian or English"},
                "region": {"type": "string", "default": "ru-ru", "description": "Region for results"},
                "max_results": {"type": "integer", "default": 5},
            }, "required": ["query"]},
        }, _news_search),

        ToolEntry("open_app_or_url", {
            "name": "open_app_or_url",
            "description": (
                "Open application or website on macOS. "
                "For apps: use app name (Calculator, Safari, TextEdit). "
                "For websites: use domain (sber.ru) or full URL (https://sber.ru). "
                "Examples: Calculator, Safari, sber.ru, https://google.com"
            ),
            "parameters": {"type": "object", "properties": {
                "target": {"type": "string", "description": "App name or website URL/domain"},
            }, "required": ["target"]},
        }, _open_app_or_url),
    ]
