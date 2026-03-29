"""Outlook Web tools via Playwright browser automation.

Tools:
  - outlook_read    : read recent emails from Outlook Web
  - outlook_search  : search emails by query
  - outlook_compose : open compose window with pre-filled fields
"""

from __future__ import annotations

import json
import logging
import pathlib
import time
from typing import Any, Dict, List

from ouroboros.tools.registry import ToolContext, ToolEntry

log = logging.getLogger(__name__)

OUTLOOK_URL = "https://outlook.office.com/mail/"
SESSION_DIR = pathlib.Path.home() / "Ouroboros" / "data" / "outlook_session"


def _get_outlook_page(ctx: ToolContext):
    """Get or create a browser page with Outlook session."""
    from ouroboros.tools.browser import _ensure_browser

    page = _ensure_browser(ctx)

    # Check if already on Outlook
    current_url = page.url
    if "outlook.office.com" in current_url:
        return page, None

    # Navigate to Outlook
    try:
        page.goto(OUTLOOK_URL, timeout=30000, wait_until="domcontentloaded")
        time.sleep(2)
    except Exception as e:
        return page, f"Failed to open Outlook: {e}"

    # Check if login required
    current_url = page.url
    if "login.microsoftonline.com" in current_url or "login.live.com" in current_url:
        return page, (
            "⚠️ Outlook требует авторизации. "
            "Пожалуйста, войдите в Outlook в браузере вручную, "
            "затем повторите команду."
        )

    return page, None


def _outlook_read(ctx: ToolContext, count: int = 10) -> str:
    """Read recent emails from Outlook Web."""
    try:
        page, err = _get_outlook_page(ctx)
        if err:
            return err

        # Wait for mail list to load
        try:
            page.wait_for_selector('[role="listitem"]', timeout=10000)
        except Exception:
            pass

        # Extract email list via JS
        emails = page.evaluate(f"""() => {{
            const items = [];
            const rows = document.querySelectorAll('[role="listitem"]');
            rows.forEach((row, idx) => {{
                if (idx >= {count}) return;
                const subject = row.querySelector('[data-testid="subject"]') ||
                               row.querySelector('.subject') ||
                               row.querySelector('[class*="subject"]');
                const sender = row.querySelector('[data-testid="sender"]') ||
                              row.querySelector('.sender') ||
                              row.querySelector('[class*="sender"]');
                const time = row.querySelector('time') ||
                            row.querySelector('[class*="time"]') ||
                            row.querySelector('[class*="date"]');
                const preview = row.querySelector('[class*="preview"]') ||
                               row.querySelector('[class*="body"]');
                if (subject || sender) {{
                    items.push({{
                        subject: subject ? subject.innerText.trim() : '(без темы)',
                        sender: sender ? sender.innerText.trim() : '',
                        time: time ? (time.getAttribute('datetime') || time.innerText.trim()) : '',
                        preview: preview ? preview.innerText.trim().substring(0, 100) : '',
                    }});
                }}
            }});
            return items;
        }}""")

        if not emails:
            # Fallback: get page text
            text = page.evaluate("() => document.body.innerText")
            return json.dumps({
                "status": "opened",
                "note": "Outlook открыт. Не удалось извлечь список писем автоматически.",
                "page_preview": (text or "")[:500]
            }, ensure_ascii=False)

        return json.dumps({
            "count": len(emails),
            "emails": emails
        }, ensure_ascii=False, indent=2)

    except Exception as e:
        return json.dumps({"error": f"Outlook read failed: {e}"}, ensure_ascii=False)


def _outlook_search(ctx: ToolContext, query: str) -> str:
    """Search emails in Outlook Web."""
    try:
        page, err = _get_outlook_page(ctx)
        if err:
            return err

        # Find search box and type query
        try:
            search_box = page.query_selector('[placeholder*="Search"]') or \
                        page.query_selector('[aria-label*="Search"]') or \
                        page.query_selector('input[type="search"]')

            if search_box:
                search_box.click()
                search_box.fill(query)
                page.keyboard.press("Enter")
                time.sleep(3)
            else:
                # Navigate with search query in URL
                import urllib.parse
                search_url = f"https://outlook.office.com/mail/search/{urllib.parse.quote(query)}"
                page.goto(search_url, timeout=20000, wait_until="domcontentloaded")
                time.sleep(2)
        except Exception as e:
            return json.dumps({"error": f"Search failed: {e}"}, ensure_ascii=False)

        # Extract results
        return _outlook_read(ctx, count=10)

    except Exception as e:
        return json.dumps({"error": f"Outlook search failed: {e}"}, ensure_ascii=False)


def _outlook_compose(ctx: ToolContext, to: str = "", subject: str = "",
                     body: str = "") -> str:
    """Open Outlook compose window with pre-filled fields."""
    try:
        page, err = _get_outlook_page(ctx)
        if err:
            return err

        # Click New Message button
        try:
            new_btn = page.query_selector('[aria-label*="New mail"]') or \
                     page.query_selector('[aria-label*="Новое письмо"]') or \
                     page.query_selector('[data-testid="compose-button"]')

            if new_btn:
                new_btn.click()
                time.sleep(2)
            else:
                # Try keyboard shortcut
                page.keyboard.press("n")
                time.sleep(2)
        except Exception:
            pass

        # Fill To field
        if to:
            try:
                to_field = page.query_selector('[aria-label*="To"]') or \
                          page.query_selector('[aria-label*="Кому"]')
                if to_field:
                    to_field.click()
                    to_field.fill(to)
                    page.keyboard.press("Tab")
            except Exception:
                pass

        # Fill Subject
        if subject:
            try:
                subj_field = page.query_selector('[aria-label*="Subject"]') or \
                            page.query_selector('[aria-label*="Тема"]') or \
                            page.query_selector('[placeholder*="Subject"]')
                if subj_field:
                    subj_field.click()
                    subj_field.fill(subject)
            except Exception:
                pass

        # Fill Body
        if body:
            try:
                body_field = page.query_selector('[aria-label*="Message body"]') or \
                            page.query_selector('[contenteditable="true"]')
                if body_field:
                    body_field.click()
                    body_field.fill(body)
            except Exception:
                pass

        return json.dumps({
            "status": "compose_opened",
            "to": to,
            "subject": subject,
            "note": "Форма написания письма открыта в Outlook"
        }, ensure_ascii=False, indent=2)

    except Exception as e:
        return json.dumps({"error": f"Outlook compose failed: {e}"}, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------

def get_tools() -> List[ToolEntry]:
    return [
        ToolEntry("outlook_read", {
            "name": "outlook_read",
            "description": (
                "Read recent emails from Outlook Web (outlook.office.com). "
                "Opens browser with Outlook and extracts email list. "
                "Use for: checking inbox, reading recent messages."
            ),
            "parameters": {"type": "object", "properties": {
                "count": {"type": "integer", "default": 10, "description": "Number of emails to read"},
            }, "required": []},
        }, _outlook_read),

        ToolEntry("outlook_search", {
            "name": "outlook_search",
            "description": (
                "Search emails in Outlook Web by subject, sender, or keyword. "
                "Use for: finding specific emails, searching by topic."
            ),
            "parameters": {"type": "object", "properties": {
                "query": {"type": "string", "description": "Search query — subject, sender name, or keyword"},
            }, "required": ["query"]},
        }, _outlook_search),

        ToolEntry("outlook_compose", {
            "name": "outlook_compose",
            "description": (
                "Open Outlook Web compose window with pre-filled recipient, subject and body. "
                "Use for: drafting and sending emails."
            ),
            "parameters": {"type": "object", "properties": {
                "to": {"type": "string", "description": "Recipient email address"},
                "subject": {"type": "string", "description": "Email subject"},
                "body": {"type": "string", "description": "Email body text"},
            }, "required": []},
        }, _outlook_compose),
    ]
