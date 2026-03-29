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
    """Search news via Google News RSS. Free, no API key required."""
    try:
        import urllib.request
        import urllib.parse
        import ssl
        import xml.etree.ElementTree as ET

        # Google News RSS
        encoded_query = urllib.parse.quote(query)
        url = f"https://news.google.com/rss/search?q={encoded_query}&hl=ru&gl=RU&ceid=RU:ru"

        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        
        # Disable SSL verification
        ctx_ssl = ssl.create_default_context()
        ctx_ssl.check_hostname = False
        ctx_ssl.verify_mode = ssl.CERT_NONE
        
        with urllib.request.urlopen(req, timeout=10, context=ctx_ssl) as resp:
            xml_data = resp.read().decode("utf-8")

        # Parse RSS XML
        root = ET.fromstring(xml_data)
        results = []
        
        # Find all items in RSS
        for item in root.findall(".//item")[:max_results]:
            title_elem = item.find("title")
            link_elem = item.find("link")
            pub_date_elem = item.find("pubDate")
            description_elem = item.find("description")
            
            if title_elem is not None and link_elem is not None:
                results.append({
                    "title": title_elem.text or "",
                    "url": link_elem.text or "",
                    "published": pub_date_elem.text if pub_date_elem is not None else "",
                    "description": (description_elem.text or "")[:200] if description_elem is not None else "",
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
            "results": results,
            "source": "Google News"
        }, ensure_ascii=False, indent=2)

    except Exception as e:
        log.warning("Google News search failed: %s", e)
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
def _control_volume(ctx: ToolContext, action: str, amount: int = 10) -> str:
    """Control macOS system volume.
    
    Args:
        action: "up" (increase), "down" (decrease), "set" (set to specific level), "mute", "unmute"
        amount: Volume change amount (0-100)
    """
    try:
        import subprocess
        
        if action == "up":
            # Increase volume
            result = subprocess.run(
                ["osascript", "-e", f"set volume output volume (output volume of (get volume settings) + {amount})"],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0:
                # Get current volume
                vol_result = subprocess.run(
                    ["osascript", "-e", "output volume of (get volume settings)"],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                current_vol = vol_result.stdout.strip()
                return json.dumps({
                    "success": True,
                    "action": "volume_up",
                    "amount": amount,
                    "current_volume": current_vol,
                }, ensure_ascii=False, indent=2)
        
        elif action == "down":
            # Decrease volume
            result = subprocess.run(
                ["osascript", "-e", f"set volume output volume (output volume of (get volume settings) - {amount})"],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0:
                vol_result = subprocess.run(
                    ["osascript", "-e", "output volume of (get volume settings)"],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                current_vol = vol_result.stdout.strip()
                return json.dumps({
                    "success": True,
                    "action": "volume_down",
                    "amount": amount,
                    "current_volume": current_vol,
                }, ensure_ascii=False, indent=2)
        
        elif action == "set":
            # Set specific volume
            result = subprocess.run(
                ["osascript", "-e", f"set volume output volume {amount}"],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0:
                return json.dumps({
                    "success": True,
                    "action": "volume_set",
                    "volume": amount,
                }, ensure_ascii=False, indent=2)
        
        elif action == "mute":
            result = subprocess.run(
                ["osascript", "-e", "set volume output muted true"],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0:
                return json.dumps({
                    "success": True,
                    "action": "muted",
                }, ensure_ascii=False, indent=2)
        
        elif action == "unmute":
            result = subprocess.run(
                ["osascript", "-e", "set volume output muted false"],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0:
                return json.dumps({
                    "success": True,
                    "action": "unmuted",
                }, ensure_ascii=False, indent=2)
        
        return json.dumps({
            "error": f"Unknown action: {action}",
        }, ensure_ascii=False, indent=2)
            
    except Exception as e:
        return json.dumps({
            "error": f"Volume control failed: {str(e)}",
        }, ensure_ascii=False, indent=2)


def _play_music(ctx: ToolContext, song_name: str) -> str:
    """Search and play music on zvuk.com.
    
    Opens zvuk.com search page and automatically clicks the play button.
    """
    try:
        from ouroboros.tools.browser import _ensure_browser
        import urllib.parse
        import time
        
        page = _ensure_browser(ctx)
        
        # Stop any currently playing music by clicking pause button in player
        try:
            # Look for the player pause button (same as play button, toggles)
            pause_button = page.query_selector("button.styles_button__Mys2r.styles_btn__uPjUi")
            if pause_button and pause_button.is_visible():
                pause_button.click()
                time.sleep(0.5)
        except Exception as e:
            log.debug(f"Failed to stop current music: {e}")
        
        # Encode song name for URL
        encoded_song = urllib.parse.quote(song_name)
        search_url = f"https://zvuk.com/search?query={encoded_song}"
        
        # Navigate to search page
        page.goto(search_url, wait_until="domcontentloaded", timeout=15000)
        
        # Wait for content to load
        time.sleep(2)
        
        play_clicked = False
        
        try:
            # STEP 1: Click on "Треки" tab to show tracks list
            # Find the tab by text and click it
            tracks_tab_clicked = False
            try:
                # Try to find and click the "Треки" tab using XPath
                page.click("//span[contains(text(), 'Треки')]/..", timeout=3000)
                tracks_tab_clicked = True
                log.info("Clicked on 'Треки' tab")
                # Wait longer for tracks to load after clicking tab
                time.sleep(3)
            except Exception as e:
                log.debug(f"Failed to click Треки tab: {e}")
                # Fallback: try clicking any element with "Треки" text
                try:
                    page.click("text=Треки", timeout=3000)
                    tracks_tab_clicked = True
                    time.sleep(3)
                except Exception:
                    pass
            
            # STEP 2: Wait for track list to appear
            page.wait_for_selector("button[class*='PlayButton']", timeout=5000, state="visible")
            
            # STEP 3: Click play button on first track
            play_buttons = page.query_selector_all("button.PlayButton_button__f5eC3, button[class*='PlayButton']")
            if play_buttons:
                for btn in play_buttons:
                    if btn.is_visible():
                        btn.click()
                        play_clicked = True
                        time.sleep(1)
                        log.info("Clicked play button on first track")
                        break
            
            # Fallback: click on first track row
            if not play_clicked:
                first_track = page.query_selector("div[class*='TrackItem']")
                if first_track and first_track.is_visible():
                    first_track.click()
                    time.sleep(2)
                    
                    # Try to click "Слушать" button on track page
                    listen_button = page.query_selector("button:has-text('Слушать')")
                    if listen_button and listen_button.is_visible():
                        listen_button.click()
                        play_clicked = True
                        time.sleep(1)
            
        except Exception as e:
            log.warning(f"Track play failed: {e}")
        
        if play_clicked:
            return json.dumps({
                "success": True,
                "action": "music_playing",
                "song": song_name,
                "url": search_url,
                "message": f"Запущена песня '{song_name}' на zvuk.com",
            }, ensure_ascii=False, indent=2)
        else:
            return json.dumps({
                "success": False,
                "action": "opened_music_search",
                "song": song_name,
                "url": search_url,
                "message": f"Открыт поиск '{song_name}' на zvuk.com, но не удалось автоматически запустить. Кликните на трек вручную.",
            }, ensure_ascii=False, indent=2)
        
    except Exception as e:
        log.warning(f"Music playback failed: {e}")
        # Fallback: just open in Safari
        import subprocess
        import urllib.parse
        
        encoded_song = urllib.parse.quote(song_name)
        search_url = f"https://zvuk.com/search?query={encoded_song}"
        
        subprocess.run(["open", search_url], timeout=5)
        
        return json.dumps({
            "success": True,
            "action": "opened_music_search",
            "song": song_name,
            "url": search_url,
            "message": f"Открыт поиск '{song_name}' на zvuk.com в Safari. Кликните на трек для воспроизведения.",
        }, ensure_ascii=False, indent=2)


def _control_music(ctx: ToolContext, action: str) -> str:
    """Control music playback on zvuk.com (pause/play/stop).
    
    Args:
        action: "pause" (pause playback), "play" (resume playback), "stop" (stop and close)
    """
    try:
        from ouroboros.tools.browser import _ensure_browser, cleanup_browser
        import time
        
        if action == "stop":
            # Close the browser
            try:
                cleanup_browser(ctx)
                return json.dumps({
                    "success": True,
                    "action": "music_stopped",
                    "message": "Музыка остановлена.",
                }, ensure_ascii=False, indent=2)
            except Exception as e:
                log.debug(f"Browser cleanup failed: {e}")
                return json.dumps({
                    "success": True,
                    "action": "music_stopped",
                    "message": "Музыка остановлена (браузер уже закрыт).",
                }, ensure_ascii=False, indent=2)
        
        elif action in ["pause", "play"]:
            # Get existing browser page
            page = _ensure_browser(ctx)
            
            try:
                if action == "pause":
                    # Look for pause button with SVG containing two vertical bars (pause icon)
                    log.info("Looking for pause button...")
                    try:
                        # Find button that contains SVG with pause icon path
                        # The pause icon has path with d="M8.25 3.09v13.82..." (two vertical rectangles)
                        pause_selector = 'button:has(svg path[d*="M8.25 3.09"])'
                        page.wait_for_selector(pause_selector, timeout=3000, state="visible")
                        pause_button = page.query_selector(pause_selector)
                        if pause_button:
                            page.evaluate("(element) => element.click()", pause_button)
                            time.sleep(0.5)
                            log.info("Clicked pause button (SVG icon)")
                            return json.dumps({
                                "success": True,
                                "action": "music_pause",
                                "message": "Музыка приостановлена.",
                            }, ensure_ascii=False, indent=2)
                    except Exception as e:
                        log.debug(f"Pause button with SVG not found: {e}")
                    
                    # Fallback: Space key
                    log.info("Fallback: pressing Space for pause")
                    page.keyboard.press("Space")
                    time.sleep(0.5)
                    return json.dumps({
                        "success": True,
                        "action": "music_pause",
                        "message": "Музыка приостановлена (Space).",
                    }, ensure_ascii=False, indent=2)
                
                elif action == "play":
                    # After pause, find and click the play button with SVG play icon (triangle)
                    log.info("Looking for play button...")
                    try:
                        # Find button that contains SVG with play icon path
                        # The play icon has path with d="M17.194 9.639..." (triangle)
                        play_selector = 'button:has(svg path[d*="M17.194"])'
                        page.wait_for_selector(play_selector, timeout=3000, state="visible")
                        play_button = page.query_selector(play_selector)
                        if play_button:
                            page.evaluate("(element) => element.click()", play_button)
                            time.sleep(0.5)
                            log.info("Clicked play button (SVG icon)")
                            return json.dumps({
                                "success": True,
                                "action": "music_play",
                                "message": "Музыка продолжена.",
                            }, ensure_ascii=False, indent=2)
                    except Exception as e:
                        log.debug(f"Play button with SVG not found: {e}")
                    
                    # Fallback: Space key
                    log.info("Fallback: pressing Space for play")
                    page.keyboard.press("Space")
                    time.sleep(0.5)
                    return json.dumps({
                        "success": True,
                        "action": "music_play",
                        "message": "Музыка продолжена (Space).",
                    }, ensure_ascii=False, indent=2)
                
            except Exception as e:
                log.debug(f"Control failed: {e}")
                return json.dumps({
                    "error": f"Не удалось управлять плеером: {str(e)}",
                }, ensure_ascii=False, indent=2)
        
        else:
            return json.dumps({
                "error": f"Неизвестное действие: {action}. Используй 'pause', 'play' или 'stop'.",
            }, ensure_ascii=False, indent=2)
            
    except Exception as e:
        return json.dumps({
            "error": f"Не удалось управлять музыкой: {str(e)}",
        }, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------

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

        ToolEntry("control_volume", {
            "name": "control_volume",
            "description": (
                "Control macOS system volume. "
                "Actions: 'up' (increase), 'down' (decrease), 'set' (set level), 'mute', 'unmute'. "
                "Amount: volume change in points (default 10, range 0-100). "
                "Examples: action='up' amount=20 (increase by 20), action='down' amount=10 (decrease by 10)"
            ),
            "parameters": {"type": "object", "properties": {
                "action": {
                    "type": "string",
                    "enum": ["up", "down", "set", "mute", "unmute"],
                    "description": "Volume action to perform"
                },
                "amount": {
                    "type": "integer",
                    "default": 10,
                    "description": "Volume change amount (0-100)"
                },
            }, "required": ["action"]},
        }, _control_volume),

        ToolEntry("play_music", {
            "name": "play_music",
            "description": (
                "Search and play music on zvuk.com. "
                "Opens zvuk.com search page with the song name and auto-clicks play. "
                "Example: song_name='Кино Группа крови'"
            ),
            "parameters": {"type": "object", "properties": {
                "song_name": {"type": "string", "description": "Song name or artist + song"},
            }, "required": ["song_name"]},
        }, _play_music),

        ToolEntry("control_music", {
            "name": "control_music",
            "description": (
                "Control music playback on zvuk.com. "
                "Actions: 'pause' (pause music), 'play' (resume music), 'stop' (stop and close). "
                "Examples: action='pause' (pause), action='play' (continue), action='stop' (stop)"
            ),
            "parameters": {"type": "object", "properties": {
                "action": {
                    "type": "string",
                    "enum": ["pause", "play", "stop"],
                    "description": "Music control action"
                },
            }, "required": ["action"]},
        }, _control_music),
    ]


def _read_emails(ctx: ToolContext, count: int = 5) -> str:
    """Read recent emails from Outlook.
    
    Args:
        count: Number of recent emails to read (default 5, max 20)
    """
    try:
        from ouroboros.tools.browser import _ensure_browser
        import time
        
        count = min(count, 20)  # Limit to 20
        
        page = _ensure_browser(ctx)
        
        # Navigate to Outlook inbox
        log.info("Opening Outlook inbox...")
        page.goto("https://outlook.live.com/mail/", wait_until="domcontentloaded", timeout=15000)
        time.sleep(3)
        
        # Wait for email list to load (long timeout for login)
        try:
            log.info("Waiting for email list (you have 60 seconds to login)...")
            page.wait_for_selector("div[role='listbox']", timeout=60000, state="visible")
        except Exception as e:
            log.warning(f"Email list not found: {e}")
            return json.dumps({
                "error": "Не удалось загрузить список писем. Возможно, требуется авторизация.",
            }, ensure_ascii=False, indent=2)
        
        time.sleep(2)
        
        # Get email items - they are role="option" elements
        emails = []
        email_items = page.query_selector_all("div[role='option'][data-convid]")
        
        log.info(f"Found {len(email_items)} email items")
        
        for i, item in enumerate(email_items[:count]):
            try:
                # Extract email info from aria-label
                aria_label = item.get_attribute("aria-label") or ""
                
                email_data = {}
                
                # Try to parse from aria-label (contains all info)
                if aria_label:
                    # aria-label format: "Unread Microsoft account team Microsoft account password change 2/16/2026 ..."
                    parts = aria_label.split()
                    
                    # Check if unread
                    email_data["unread"] = "Unread" in aria_label or "unread" in aria_label.lower()
                    
                    # Get text content
                    text = item.inner_text()
                    lines = [l.strip() for l in text.split('\n') if l.strip()]
                    
                    # Lines structure:
                    # 0: Sender name
                    # 1: Subject
                    # 2: Date/time
                    # 3+: Preview text
                    if len(lines) >= 2:
                        email_data["from"] = lines[0]  # Sender
                        email_data["subject"] = lines[1]  # Subject
                        
                        # Find date line (contains "/" or numbers)
                        for idx in range(2, min(len(lines), 5)):
                            if '/' in lines[idx] or any(c.isdigit() for c in lines[idx]):
                                email_data["time"] = lines[idx]
                                # Preview is everything after time
                                if idx + 1 < len(lines):
                                    email_data["preview"] = ' '.join(lines[idx+1:])[:150]
                                break
                
                if email_data and email_data.get("from"):
                    emails.append(email_data)
                    log.debug(f"Parsed email {i+1}: {email_data.get('subject', 'N/A')}")
                    
            except Exception as e:
                log.debug(f"Failed to parse email {i}: {e}")
                continue
        
        if not emails:
            return json.dumps({
                "error": "Не удалось прочитать письма. Проверь авторизацию.",
            }, ensure_ascii=False, indent=2)
        
        return json.dumps({
            "success": True,
            "count": len(emails),
            "emails": emails,
        }, ensure_ascii=False, indent=2)
        
    except Exception as e:
        log.error(f"Read emails failed: {e}")
        return json.dumps({
            "error": f"Ошибка чтения почты: {str(e)}",
        }, ensure_ascii=False, indent=2)


def _get_unread_summary(ctx: ToolContext) -> str:
    """Get summary of all unread emails from Outlook."""
    try:
        from ouroboros.tools.browser import _ensure_browser
        import time
        
        page = _ensure_browser(ctx)
        
        # Navigate to Outlook inbox
        log.info("Opening Outlook inbox...")
        page.goto("https://outlook.live.com/mail/", wait_until="domcontentloaded", timeout=15000)
        time.sleep(3)
        
        # Wait for email list (long timeout for login)
        try:
            log.info("Waiting for email list (you have 60 seconds to login)...")
            page.wait_for_selector("div[role='listbox']", timeout=60000, state="visible")
        except Exception as e:
            return json.dumps({
                "error": "Не удалось загрузить почту.",
            }, ensure_ascii=False, indent=2)
        
        time.sleep(2)
        
        # Get unread count from UI
        unread_count = 0
        unread_emails = []
        
        email_items = page.query_selector_all("div[role='option'][data-convid]")
        
        log.info(f"Found {len(email_items)} email items")
        
        for item in email_items[:20]:  # Check first 20
            try:
                # Check if unread from aria-label
                aria_label = item.get_attribute("aria-label") or ""
                
                if "Unread" in aria_label or "unread" in aria_label.lower():
                    unread_count += 1
                    
                    # Get detailed info including preview
                    email_data = {}
                    text = item.inner_text()
                    lines = [l.strip() for l in text.split('\n') if l.strip()]
                    
                    if len(lines) >= 2:
                        email_data["from"] = lines[0]
                        email_data["subject"] = lines[1]
                        
                        # Find date and preview
                        for idx in range(2, min(len(lines), 5)):
                            if '/' in lines[idx] or any(c.isdigit() for c in lines[idx]):
                                email_data["time"] = lines[idx]
                                # Get preview text after date
                                if idx + 1 < len(lines):
                                    email_data["preview"] = ' '.join(lines[idx+1:])[:200]
                                break
                    
                    if email_data:
                        unread_emails.append(email_data)
                        
            except Exception as e:
                log.debug(f"Failed to check unread: {e}")
                continue
        
        return json.dumps({
            "success": True,
            "unread_count": unread_count,
            "unread_emails": unread_emails,
        }, ensure_ascii=False, indent=2)
        
    except Exception as e:
        log.error(f"Get unread summary failed: {e}")
        return json.dumps({
            "error": f"Ошибка: {str(e)}",
        }, ensure_ascii=False, indent=2)


def _search_emails(ctx: ToolContext, query: str) -> str:
    """Search emails in Outlook by keyword.
    
    Args:
        query: Search query (keywords to find in emails)
    """
    try:
        from ouroboros.tools.browser import _ensure_browser
        import time
        
        page = _ensure_browser(ctx)
        
        # Navigate to Outlook inbox first
        log.info(f"Opening Outlook to search for: {query}")
        page.goto("https://outlook.live.com/mail/", wait_until="domcontentloaded", timeout=15000)
        time.sleep(3)
        
        # Find search input field
        try:
            search_input = page.query_selector("input[aria-label*='Search'], input[placeholder*='Search'], input[type='search']")
            if not search_input:
                return json.dumps({
                    "error": "Не удалось найти поле поиска.",
                }, ensure_ascii=False, indent=2)
            
            # Enter search query and press Enter
            log.info(f"Entering search query: {query}")
            search_input.fill(query)
            time.sleep(1)
            search_input.press("Enter")
            time.sleep(5)  # Wait for search results
            
        except Exception as e:
            return json.dumps({
                "error": f"Не удалось выполнить поиск: {str(e)}",
            }, ensure_ascii=False, indent=2)
        
        # Get search results
        results = []
        email_items = page.query_selector_all("div[role='option'][data-convid]")
        
        log.info(f"Found {len(email_items)} search results")
        
        for i, item in enumerate(email_items[:10]):  # First 10 results
            try:
                email_data = {}
                
                text = item.inner_text()
                lines = [l.strip() for l in text.split('\n') if l.strip()]
                
                if len(lines) >= 2:
                    email_data["from"] = lines[0]
                    email_data["subject"] = lines[1]
                    
                    # Find date and preview
                    for idx in range(2, min(len(lines), 5)):
                        if '/' in lines[idx] or any(c.isdigit() for c in lines[idx]):
                            email_data["time"] = lines[idx]
                            if idx + 1 < len(lines):
                                email_data["preview"] = ' '.join(lines[idx+1:])[:150]
                            break
                
                if email_data and email_data.get("from"):
                    results.append(email_data)
                    
            except Exception as e:
                log.debug(f"Failed to parse result {i}: {e}")
                continue
        
        return json.dumps({
            "success": True,
            "query": query,
            "count": len(results),
            "results": results,
        }, ensure_ascii=False, indent=2)
        
    except Exception as e:
        log.error(f"Search emails failed: {e}")
        return json.dumps({
            "error": f"Ошибка поиска: {str(e)}",
        }, ensure_ascii=False, indent=2)


def get_tools() -> list[ToolEntry]:
    """Get all macOS-specific tools."""
    return [
        ToolEntry("control_volume", {
            "name": "control_volume",
            "description": (
                "Control macOS system volume. "
                "Actions: 'up' (increase), 'down' (decrease), 'set' (set level), 'mute', 'unmute'. "
                "Amount: volume change in points (default 10, range 0-100). "
                "Examples: action='up' amount=20 (increase by 20), action='down' amount=10 (decrease by 10)"
            ),
            "parameters": {"type": "object", "properties": {
                "action": {
                    "type": "string",
                    "enum": ["up", "down", "set", "mute", "unmute"],
                    "description": "Volume action to perform"
                },
                "amount": {
                    "type": "integer",
                    "default": 10,
                    "description": "Volume change amount (0-100)"
                },
            }, "required": ["action"]},
        }, _control_volume),

        ToolEntry("play_music", {
            "name": "play_music",
            "description": (
                "Search and play music on zvuk.com. "
                "Opens zvuk.com search page with the song name and auto-clicks play. "
                "Example: song_name='Кино Группа крови'"
            ),
            "parameters": {"type": "object", "properties": {
                "song_name": {"type": "string", "description": "Song name or artist + song"},
            }, "required": ["song_name"]},
        }, _play_music),

        ToolEntry("control_music", {
            "name": "control_music",
            "description": (
                "Control music playback on zvuk.com. "
                "Actions: 'pause' (pause music), 'play' (resume music), 'stop' (stop and close). "
                "Examples: action='pause' (pause), action='play' (continue), action='stop' (stop)"
            ),
            "parameters": {"type": "object", "properties": {
                "action": {
                    "type": "string",
                    "enum": ["pause", "play", "stop"],
                    "description": "Music control action"
                },
            }, "required": ["action"]},
        }, _control_music),

        ToolEntry("read_emails", {
            "name": "read_emails",
            "description": (
                "Read recent emails from Outlook inbox. "
                "Returns sender, subject, preview, time, and unread status. "
                "Example: count=10 (read 10 recent emails)"
            ),
            "parameters": {"type": "object", "properties": {
                "count": {
                    "type": "integer",
                    "default": 5,
                    "description": "Number of emails to read (max 20)"
                },
            }, "required": []},
        }, _read_emails),

        ToolEntry("get_unread_summary", {
            "name": "get_unread_summary",
            "description": (
                "Get summary of all unread emails from Outlook. "
                "Returns count and list of unread emails with sender and subject."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        }, _get_unread_summary),

        ToolEntry("search_emails", {
            "name": "search_emails",
            "description": (
                "Search emails in Outlook by keyword. "
                "Returns matching emails with sender, subject, and preview. "
                "Example: query='invoice' (find emails about invoices)"
            ),
            "parameters": {"type": "object", "properties": {
                "query": {"type": "string", "description": "Search query"},
            }, "required": ["query"]},
        }, _search_emails),
    ]
