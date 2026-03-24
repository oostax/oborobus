"""Office tools — Excel, Word, PowerPoint automation for business users.

Tools:
  - excel_create   : create/edit .xlsx (tables, formulas, formatting, charts)
  - excel_read     : read data from .xlsx
  - word_create    : create/edit .docx (text, tables, headings, styles)
  - pptx_create    : create .pptx presentations (slides, text, tables)
  - office_open    : open a file in the default OS application
"""

from __future__ import annotations

import logging
import os
import pathlib
import subprocess
import sys
from typing import Any, Dict, List, Optional

from ouroboros.tools.registry import ToolContext, ToolEntry

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_path(ctx: ToolContext, path: str) -> pathlib.Path:
    """Resolve path: absolute stays absolute, relative goes to ~/Desktop."""
    p = pathlib.Path(path).expanduser()
    if p.is_absolute():
        return p
    # Default save location: ~/Desktop/
    base = pathlib.Path.home() / "Desktop"
    base.mkdir(parents=True, exist_ok=True)
    return base / path


def _auto_open(path: pathlib.Path) -> None:
    """Open file in default OS app after creation."""
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        elif sys.platform == "win32":
            os.startfile(str(path))
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except Exception as e:
        log.warning("auto_open failed for %s: %s", path, e)


def _ensure_lib(name: str) -> Optional[str]:
    """Try to import a library, return error string if missing."""
    try:
        __import__(name)
        return None
    except ImportError:
        return f"⚠️ Library '{name}' not installed. Run: pip install {name}"


# ---------------------------------------------------------------------------
# Excel
# ---------------------------------------------------------------------------

def _excel_create(
    ctx: ToolContext,
    path: str,
    sheets: List[Dict[str, Any]],
    overwrite: bool = True,
) -> str:
    """Create or update an Excel file.

    Each sheet dict:
      name: str — sheet name
      headers: list[str] — column headers (optional)
      rows: list[list] — data rows
      col_widths: dict[str, int] — column letter → width (optional)
      freeze_top_row: bool — freeze header row (optional)
      formulas: list[{cell, formula}] — e.g. {cell: "D2", formula: "=B2*C2"} (optional)
      number_format: dict[str, str] — column letter → format string (optional)
    """
    err = _ensure_lib("openpyxl")
    if err:
        return err

    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.utils import get_column_letter

    target = _resolve_path(ctx, path)
    if not path.endswith(".xlsx"):
        target = target.with_suffix(".xlsx")
    target.parent.mkdir(parents=True, exist_ok=True)

    if target.exists() and not overwrite:
        wb = openpyxl.load_workbook(str(target))
    else:
        wb = openpyxl.Workbook()
        wb.remove(wb.active)  # remove default sheet

    for sheet_def in sheets:
        sheet_name = sheet_def.get("name", "Sheet1")
        headers = sheet_def.get("headers", [])
        rows = sheet_def.get("rows", [])
        col_widths = sheet_def.get("col_widths", {})
        freeze_top = sheet_def.get("freeze_top_row", bool(headers))
        formulas = sheet_def.get("formulas", [])
        number_format = sheet_def.get("number_format", {})

        if sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
        else:
            ws = wb.create_sheet(title=sheet_name)

        row_offset = 1
        if headers:
            for col_idx, header in enumerate(headers, 1):
                cell = ws.cell(row=1, column=col_idx, value=header)
                cell.font = Font(bold=True, color="FFFFFF")
                cell.fill = PatternFill("solid", fgColor="1F4E79")
                cell.alignment = Alignment(horizontal="center")
            row_offset = 2

        for row_idx, row_data in enumerate(rows, row_offset):
            for col_idx, value in enumerate(row_data, 1):
                ws.cell(row=row_idx, column=col_idx, value=value)

        # Apply formulas
        for f in formulas:
            cell_ref = f.get("cell", "")
            formula = f.get("formula", "")
            if cell_ref and formula:
                ws[cell_ref] = formula

        # Number formats
        for col_letter, fmt in number_format.items():
            for cell in ws[col_letter]:
                cell.number_format = fmt

        # Column widths
        for col_letter, width in col_widths.items():
            ws.column_dimensions[col_letter].width = width

        # Auto-width if no explicit widths
        if not col_widths:
            for col in ws.columns:
                max_len = 0
                col_letter = get_column_letter(col[0].column)
                for cell in col:
                    try:
                        max_len = max(max_len, len(str(cell.value or "")))
                    except Exception:
                        pass
                ws.column_dimensions[col_letter].width = min(max_len + 4, 50)

        if freeze_top:
            ws.freeze_panes = "A2"

    wb.save(str(target))
    _auto_open(target)
    return f"✅ Excel saved: {target}\n{len(sheets)} sheet(s), {sum(len(s.get('rows',[])) for s in sheets)} data rows."


def _excel_read(ctx: ToolContext, path: str, sheet: str = "", max_rows: int = 200) -> str:
    """Read data from an Excel file. Returns JSON with sheet names and data."""
    err = _ensure_lib("openpyxl")
    if err:
        return err

    import openpyxl
    import json

    target = _resolve_path(ctx, path)
    if not target.exists():
        return f"⚠️ File not found: {target}"

    wb = openpyxl.load_workbook(str(target), data_only=True)
    result = {}

    sheets_to_read = [sheet] if sheet and sheet in wb.sheetnames else wb.sheetnames
    for sname in sheets_to_read:
        ws = wb[sname]
        rows = []
        for i, row in enumerate(ws.iter_rows(values_only=True)):
            if i >= max_rows:
                rows.append(["... (truncated)"])
                break
            rows.append([str(v) if v is not None else "" for v in row])
        result[sname] = rows

    return json.dumps(result, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Word
# ---------------------------------------------------------------------------

def _word_create(
    ctx: ToolContext,
    path: str,
    content: List[Dict[str, Any]],
    title: str = "",
) -> str:
    """Create a Word document.

    content is a list of blocks:
      {type: "heading", text: "...", level: 1}
      {type: "paragraph", text: "..."}
      {type: "table", headers: [...], rows: [[...], ...]}
      {type: "bullet", items: ["...", "..."]}
      {type: "pagebreak"}
    """
    err = _ensure_lib("docx")
    if err:
        return "⚠️ Library 'python-docx' not installed. Run: pip install python-docx"

    from docx import Document
    from docx.shared import Pt, RGBColor
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    target = _resolve_path(ctx, path)
    if not path.endswith(".docx"):
        target = target.with_suffix(".docx")
    target.parent.mkdir(parents=True, exist_ok=True)

    doc = Document()

    if title:
        h = doc.add_heading(title, level=0)
        h.alignment = WD_ALIGN_PARAGRAPH.CENTER

    for block in content:
        btype = block.get("type", "paragraph")

        if btype == "heading":
            doc.add_heading(block.get("text", ""), level=int(block.get("level", 1)))

        elif btype == "paragraph":
            p = doc.add_paragraph(block.get("text", ""))
            if block.get("bold"):
                for run in p.runs:
                    run.bold = True

        elif btype == "bullet":
            for item in block.get("items", []):
                doc.add_paragraph(item, style="List Bullet")

        elif btype == "table":
            headers = block.get("headers", [])
            rows = block.get("rows", [])
            cols = max(len(headers), max((len(r) for r in rows), default=0))
            if cols == 0:
                continue
            table = doc.add_table(rows=1 + len(rows), cols=cols)
            table.style = "Table Grid"
            # Header row
            hdr_cells = table.rows[0].cells
            for i, h in enumerate(headers):
                hdr_cells[i].text = str(h)
                for para in hdr_cells[i].paragraphs:
                    for run in para.runs:
                        run.bold = True
            # Data rows
            for ri, row_data in enumerate(rows):
                row_cells = table.rows[ri + 1].cells
                for ci, val in enumerate(row_data):
                    row_cells[ci].text = str(val)

        elif btype == "pagebreak":
            doc.add_page_break()

    doc.save(str(target))
    _auto_open(target)
    return f"✅ Word document saved: {target}"


# ---------------------------------------------------------------------------
# PowerPoint
# ---------------------------------------------------------------------------

def _pptx_create(
    ctx: ToolContext,
    path: str,
    slides: List[Dict[str, Any]],
    title: str = "",
) -> str:
    """Create a PowerPoint presentation.

    Each slide dict:
      layout: "title" | "content" | "two_col" | "blank" (default: "content")
      title: str
      content: str | list[str]  — body text or bullet list
      table: {headers: [...], rows: [[...]]}  — optional table on slide
      notes: str  — speaker notes
    """
    err = _ensure_lib("pptx")
    if err:
        return "⚠️ Library 'python-pptx' not installed. Run: pip install python-pptx"

    from pptx import Presentation
    from pptx.util import Inches, Pt
    from pptx.dml.color import RGBColor
    from pptx.enum.text import PP_ALIGN

    prs = Presentation()
    # Widescreen 16:9
    prs.slide_width = Inches(13.33)
    prs.slide_height = Inches(7.5)

    layouts = {l.name: l for l in prs.slide_layouts}

    def _get_layout(name):
        mapping = {
            "title": "Title Slide",
            "content": "Title and Content",
            "two_col": "Two Content",
            "blank": "Blank",
        }
        preferred = mapping.get(name, "Title and Content")
        return layouts.get(preferred) or prs.slide_layouts[1]

    # Title slide
    if title:
        sl = prs.slides.add_slide(_get_layout("title"))
        if sl.shapes.title:
            sl.shapes.title.text = title
        for ph in sl.placeholders:
            if ph.placeholder_format.idx == 1:
                ph.text = ""

    for slide_def in slides:
        layout_name = slide_def.get("layout", "content")
        sl = prs.slides.add_slide(_get_layout(layout_name))

        # Title
        slide_title = slide_def.get("title", "")
        if sl.shapes.title and slide_title:
            sl.shapes.title.text = slide_title

        # Content / bullets
        content = slide_def.get("content", "")
        for ph in sl.placeholders:
            if ph.placeholder_format.idx == 1:
                tf = ph.text_frame
                tf.clear()
                if isinstance(content, list):
                    for i, item in enumerate(content):
                        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
                        p.text = str(item)
                        p.level = 0
                elif content:
                    tf.paragraphs[0].text = str(content)
                break

        # Table
        table_def = slide_def.get("table")
        if table_def:
            headers = table_def.get("headers", [])
            rows = table_def.get("rows", [])
            cols = max(len(headers), max((len(r) for r in rows), default=1))
            total_rows = len(rows) + (1 if headers else 0)
            if cols > 0 and total_rows > 0:
                left = Inches(0.5)
                top = Inches(3.5)
                width = Inches(12.0)
                height = Inches(0.5 * total_rows)
                tbl = sl.shapes.add_table(total_rows, cols, left, top, width, height).table
                row_offset = 0
                if headers:
                    for ci, h in enumerate(headers):
                        cell = tbl.cell(0, ci)
                        cell.text = str(h)
                        cell.text_frame.paragraphs[0].font.bold = True
                    row_offset = 1
                for ri, row_data in enumerate(rows):
                    for ci, val in enumerate(row_data):
                        tbl.cell(ri + row_offset, ci).text = str(val)

        # Speaker notes
        notes_text = slide_def.get("notes", "")
        if notes_text:
            notes_slide = sl.notes_slide
            notes_slide.notes_text_frame.text = notes_text

    target = _resolve_path(ctx, path)
    if not path.endswith(".pptx"):
        target = target.with_suffix(".pptx")
    target.parent.mkdir(parents=True, exist_ok=True)
    prs.save(str(target))
    _auto_open(target)
    return f"✅ Presentation saved: {target} ({len(slides)} slide(s))"


# ---------------------------------------------------------------------------
# Open file in OS default app
# ---------------------------------------------------------------------------

def _office_open(ctx: ToolContext, path: str) -> str:
    """Open a file in the default OS application (Excel, Word, etc.)."""
    target = _resolve_path(ctx, path)
    if not target.exists():
        return f"⚠️ File not found: {target}"
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", str(target)])
        elif sys.platform == "win32":
            os.startfile(str(target))
        else:
            subprocess.Popen(["xdg-open", str(target)])
        return f"✅ Opened: {target}"
    except Exception as e:
        return f"⚠️ Failed to open file: {e}"


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------

def get_tools() -> List[ToolEntry]:
    return [
        ToolEntry("excel_create", {
            "name": "excel_create",
            "description": (
                "Create or update an Excel (.xlsx) file. "
                "Supports multiple sheets, headers with formatting, data rows, formulas, column widths, number formats. "
                "Saves to ~/Documents/Ouroboros/ by default. "
                "Use for: financial tables, reports, budgets, loan calculations, client lists."
            ),
            "parameters": {"type": "object", "properties": {
                "path": {"type": "string", "description": "File path, e.g. 'report.xlsx' or '/Users/me/report.xlsx'"},
                "sheets": {
                    "type": "array",
                    "description": "List of sheet definitions",
                    "items": {"type": "object", "properties": {
                        "name": {"type": "string"},
                        "headers": {"type": "array", "items": {"type": "string"}},
                        "rows": {"type": "array", "items": {"type": "array"}},
                        "col_widths": {"type": "object"},
                        "freeze_top_row": {"type": "boolean"},
                        "formulas": {"type": "array", "items": {"type": "object"}},
                        "number_format": {"type": "object"},
                    }},
                },
                "overwrite": {"type": "boolean", "default": True},
            }, "required": ["path", "sheets"]},
        }, _excel_create),
        ToolEntry("excel_read", {
            "name": "excel_read",
            "description": "Read data from an Excel (.xlsx) file. Returns all sheets as JSON.",
            "parameters": {"type": "object", "properties": {
                "path": {"type": "string"},
                "sheet": {"type": "string", "description": "Sheet name (optional, reads all if omitted)"},
                "max_rows": {"type": "integer", "default": 200},
            }, "required": ["path"]},
        }, _excel_read),
        ToolEntry("word_create", {
            "name": "word_create",
            "description": (
                "Create a Word (.docx) document with headings, paragraphs, bullet lists, and tables. "
                "Use for: memos, reports, contracts, instructions, meeting minutes."
            ),
            "parameters": {"type": "object", "properties": {
                "path": {"type": "string", "description": "File path, e.g. 'memo.docx'"},
                "title": {"type": "string", "description": "Document title (optional)"},
                "content": {
                    "type": "array",
                    "description": "List of content blocks",
                    "items": {"type": "object", "properties": {
                        "type": {"type": "string", "enum": ["heading", "paragraph", "bullet", "table", "pagebreak"]},
                        "text": {"type": "string"},
                        "level": {"type": "integer", "description": "Heading level 1-4"},
                        "bold": {"type": "boolean"},
                        "items": {"type": "array", "items": {"type": "string"}, "description": "Bullet items"},
                        "headers": {"type": "array", "items": {"type": "string"}},
                        "rows": {"type": "array", "items": {"type": "array"}},
                    }},
                },
            }, "required": ["path", "content"]},
        }, _word_create),
        ToolEntry("pptx_create", {
            "name": "pptx_create",
            "description": (
                "Create a PowerPoint (.pptx) presentation. "
                "Supports title slides, content slides with bullets, tables, and speaker notes. "
                "Use for: board presentations, client pitches, training materials, quarterly reviews."
            ),
            "parameters": {"type": "object", "properties": {
                "path": {"type": "string", "description": "File path, e.g. 'presentation.pptx'"},
                "title": {"type": "string", "description": "Presentation title slide (optional)"},
                "slides": {
                    "type": "array",
                    "description": "List of slide definitions",
                    "items": {"type": "object", "properties": {
                        "layout": {"type": "string", "enum": ["title", "content", "two_col", "blank"]},
                        "title": {"type": "string"},
                        "content": {"description": "String or list of bullet strings"},
                        "table": {"type": "object", "properties": {
                            "headers": {"type": "array", "items": {"type": "string"}},
                            "rows": {"type": "array", "items": {"type": "array"}},
                        }},
                        "notes": {"type": "string"},
                    }},
                },
            }, "required": ["path", "slides"]},
        }, _pptx_create),
        ToolEntry("office_open", {
            "name": "office_open",
            "description": "Open a file (Excel, Word, PowerPoint, PDF, etc.) in the default OS application.",
            "parameters": {"type": "object", "properties": {
                "path": {"type": "string", "description": "File path to open"},
            }, "required": ["path"]},
        }, _office_open),
    ]
