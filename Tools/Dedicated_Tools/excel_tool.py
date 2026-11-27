
import csv
import json
import shutil
from functools import wraps

from langchain_core.tools import tool
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font
from openpyxl.utils import column_index_from_string
from openpyxl.utils.cell import coordinate_from_string, range_boundaries

from pathlib import Path
import os

EXTS = {".xlsx", ".xlsm"}
MAX_READ_ROWS = 200
MAX_WRITE_CELLS = 10_000
INVALID_SHEET_CHARS = set("[]:*?/\\")
SUMMARY_OPS = {"sum", "average", "min", "max", "count", "count_all"}

HOME = Path.home()
WORKSPACE = Path(os.getenv("TARZ_WORKSPACE",
                 HOME / "TARZ_Workspace")).resolve()
WORKSPACE.mkdir(parents=True, exist_ok=True)

# Windows system dirs that must never be touched, whatever the user says
_BLOCKED = [
    Path("C:/Windows"),
    Path("C:/Program Files"),
    Path("C:/Program Files (x86)"),
]


def _safe(path: str) -> Path:
    """Resolve any path the user gives.
    - Bare filenames (no folder separator) land in TARZ_Workspace.
    - ~ and environment variables are expanded.
    - Absolute paths are accepted as-is, except Windows system directories.
    """
    p = Path(os.path.expandvars(path)).expanduser()
    if not p.is_absolute() and "/" not in path and "\\" not in path:
        p = WORKSPACE / p          # bare name  →  workspace
    p = p.resolve()
    if any(p == b or p.is_relative_to(b) for b in _BLOCKED):
        raise PermissionError(f"Blocked system path: {p}")
    return p


# ── helpers ──────────────────────────────────────────────────────

def _guard(fn):
    """Turn any exception into a readable tool reply instead of crashing the agent loop."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            return f"Excel error: {exc}"
    return wrapper


def _book(path: str, must_exist: bool = True):
    p = _safe(path)
    if p.suffix.lower() not in EXTS:
        raise ValueError("only .xlsx or .xlsm workbooks are supported")
    if must_exist and not p.is_file():
        raise FileNotFoundError(f"workbook not found: {p.name}")
    return p


def _load(p, data_only: bool = False):
    return load_workbook(p, data_only=data_only, keep_vba=p.suffix.lower() == ".xlsm")


def _save(wb, p):
    if p.exists():
        shutil.copy2(p, p.with_name(p.stem + "_backup" + p.suffix))
    wb.save(p)


def _ws(wb, sheet: str):
    if not sheet:
        return wb.active
    if sheet not in wb.sheetnames:
        raise KeyError(
            f"sheet '{sheet}' not found. Sheets: {', '.join(wb.sheetnames)}")
    return wb[sheet]


def _clean_sheet_name(name: str) -> str:
    name = name.strip()
    if not name or len(name) > 31:
        raise ValueError("sheet name must be 1-31 characters")
    if any(c in INVALID_SHEET_CHARS for c in name):
        raise ValueError("sheet name can't contain []:*?/\\")
    return name


def _grid(text: str) -> list:
    """Parse a JSON 2D list. A flat list is treated as one row."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        raise ValueError(
            'values must be JSON, e.g. [["Name","Qty"],["Pen",3]]')
    if not isinstance(data, list) or not data:
        raise ValueError("values must be a non-empty JSON list")
    if not all(isinstance(r, list) for r in data):
        data = [data]
    if sum(len(r) for r in data) > MAX_WRITE_CELLS:
        raise ValueError(f"too many cells (max {MAX_WRITE_CELLS} per call)")
    return data


def _anchor(cell: str):
    letters, row = coordinate_from_string(cell.strip().upper())
    return column_index_from_string(letters), row


def _write_grid(ws, row: int, col: int, data: list):
    for i, r in enumerate(data):
        for j, v in enumerate(r):
            ws.cell(row + i, col + j, value=v)


def _last_row(ws) -> int:
    for r in range(ws.max_row, 0, -1):
        if any(c.value not in (None, "") for c in ws[r]):
            return r
    return 0


def _find_column(ws, column: str) -> int:
    """Accept a header name (case-insensitive) first, then a column letter like 'C'."""
    col = column.strip()
    for cell in ws[1]:
        if cell.value is not None and str(cell.value).strip().lower() == col.lower():
            return cell.column
    if col.isalpha():
        return column_index_from_string(col.upper())
    raise ValueError(f"column '{column}' not found in the header row (row 1)")


def _num(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v.replace(",", "").strip())
        except ValueError:
            return None
    return None


def _fmt(x: float) -> str:
    return f"{int(x):,}" if float(x).is_integer() else f"{x:,.2f}"


def _sort_key(v):
    if v is None or v == "":
        return (2, 0, "")
    n = _num(v)
    if n is not None:
        return (0, n, "")
    return (1, 0, str(v).lower())


def _has_charts_or_images(wb) -> bool:
    return any(getattr(ws, "_charts", None) or getattr(ws, "_images", None) for ws in wb.worksheets)


def _note(wb) -> str:
    return ("\nNote: this workbook has charts or images; they may not survive an edit."
            if _has_charts_or_images(wb) else "")


# ── read-only tools ──────────────────────────────────────────────

@tool
@_guard
def excel_info(path: str) -> str:
    """List the sheets of an Excel workbook (.xlsx/.xlsm) with their size and used range."""
    p = _book(path)
    wb = _load(p, data_only=True)
    lines = [f"{p.name}: {len(wb.sheetnames)} sheet(s)"]
    for ws in wb.worksheets:
        lines.append(
            f"- {ws.title}: {ws.dimensions} ({ws.max_row} rows x {ws.max_column} cols)")
    return "\n".join(lines)


@tool
@_guard
def excel_read(path: str, sheet: str = "", cell_range: str = "", max_rows: int = 50) -> str:
    """Read cells of a workbook as a table. sheet blank = first sheet. cell_range like 'A1:D20'
    blank = the whole used area. Shows computed values of formulas, not the formula text."""
    p = _book(path)
    ws = _ws(_load(p, data_only=True), sheet)
    if cell_range.strip():
        min_col, min_row, max_col, max_row = range_boundaries(
            cell_range.strip().upper())
    else:
        min_col, min_row, max_col, max_row = 1, 1, ws.max_column, ws.max_row
    limit = min(max(int(max_rows), 1), MAX_READ_ROWS)
    last = min(max_row, min_row + limit - 1)
    lines = []
    for row in ws.iter_rows(min_row=min_row, max_row=last, min_col=min_col,
                            max_col=max_col, values_only=True):
        lines.append(" | ".join("" if v is None else str(v) for v in row))
    header = f"{ws.title} (rows {min_row}-{last})"
    if max_row > last:
        header += f", more rows exist: raise max_rows (up to {MAX_READ_ROWS}) or set cell_range"
    return header + "\n" + ("\n".join(lines) or "(empty)")


@tool
@_guard
def excel_summarize(path: str, sheet: str = "", column: str = "", operation: str = "sum") -> str:
    """Calculate sum, average, min, max, count (numeric cells) or count_all (non-empty cells)
    for one column. column = header name from row 1 (e.g. 'Amount') or a letter like 'C'.
    Row 1 is always treated as the header."""
    op = operation.strip().lower()
    if op not in SUMMARY_OPS:
        return f"Unknown operation '{operation}'. Use one of: {', '.join(sorted(SUMMARY_OPS))}"
    p = _book(path)
    ws = _ws(_load(p, data_only=True), sheet)
    col = _find_column(ws, column)
    last = _last_row(ws)
    if last < 2:
        return "No data rows below the header"
    values = [ws.cell(r, col).value for r in range(2, last + 1)]
    header = ws.cell(1, col).value or f"column {col}"
    present = [v for v in values if v not in (None, "")]
    nums = [n for n in (_num(v) for v in values) if n is not None]
    if op == "count_all":
        return f"Non-empty cells in '{header}': {len(present)}"
    if op == "count":
        return f"Numeric cells in '{header}': {len(nums)}"
    if not nums:
        return f"No numeric values in '{header}'"
    result = {"sum": sum(nums), "average": sum(nums) / len(nums),
              "min": min(nums), "max": max(nums)}[op]
    skipped = len(present) - len(nums)
    return (f"{op} of '{header}': {_fmt(result)} "
            f"(from {len(nums)} numeric cells, {skipped} non-numeric skipped)")


@tool
@_guard
def excel_to_csv(path: str, sheet: str = "", out_name: str = "") -> str:
    """Export one sheet to a CSV file. Saved next to the workbook unless out_name is given
    (relative names go inside TARZ_Workspace). Refuses to overwrite an existing CSV."""
    p = _book(path)
    ws = _ws(_load(p, data_only=True), sheet)
    out = _safe(out_name) if out_name.strip() else p.with_suffix(".csv")
    if out.suffix.lower() != ".csv":
        return "Refused: output name must end in .csv"
    if out.exists():
        return f"Refused: {out.name} already exists"
    last = _last_row(ws)
    with out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        for row in ws.iter_rows(min_row=1, max_row=last, values_only=True):
            writer.writerow(["" if v is None else v for v in row])
    return f"Exported {last} row(s) from '{ws.title}' to {out}"


# ── create / write tools ─────────────────────────────────────────

@tool
@_guard
def excel_create(path: str, sheet_name: str = "Sheet1", headers: str = "", rows: str = "") -> str:
    """Create a NEW .xlsx workbook. headers = comma-separated column names (optional).
    rows = optional JSON 2D list of data rows. Refuses if the file already exists."""
    p = _book(path, must_exist=False)
    if p.suffix.lower() == ".xlsm":
        return "Refused: create .xlsx files (macros are not supported)"
    if p.exists():
        return f"Refused: {p.name} already exists"
    wb = Workbook()
    ws = wb.active
    ws.title = _clean_sheet_name(sheet_name)
    data = []
    if headers.strip():
        data.append([h.strip() for h in headers.split(",")])
    if rows.strip():
        data += _grid(rows)
    if data:
        _write_grid(ws, 1, 1, data)
    p.parent.mkdir(parents=True, exist_ok=True)
    wb.save(p)
    return f"Created {p} with sheet '{ws.title}'"


@tool
@_guard
def excel_write(path: str, sheet: str = "", cell: str = "A1", values: str = "",
                overwrite: bool = False) -> str:
    """Write values into a workbook starting at a cell. values = JSON 2D list, e.g.
    [["Pen", 3], ["Book", 5]]. Text starting with '=' is stored as a formula.
    Refuses to replace non-empty cells unless overwrite=true (only when the user asked to replace)."""
    p = _book(path)
    wb = _load(p)
    ws = _ws(wb, sheet)
    col, row = _anchor(cell)
    data = _grid(values)
    if not overwrite:
        for i, r in enumerate(data):
            for j in range(len(r)):
                target = ws.cell(row + i, col + j)
                if target.value not in (None, ""):
                    return (f"Refused: {target.coordinate} already has data. "
                            "Use overwrite=true only if the user asked to replace it.")
    _write_grid(ws, row, col, data)
    _save(wb, p)
    width = max(len(r) for r in data)
    return f"Wrote {len(data)} row(s) x {width} col(s) at {cell.upper()} in '{ws.title}'.{_note(wb)}"


@tool
@_guard
def excel_append_rows(path: str, sheet: str = "", rows: str = "") -> str:
    """Add rows after the last filled row of a sheet. rows = JSON 2D list, e.g. [["Pen", 3]]."""
    p = _book(path)
    wb = _load(p)
    ws = _ws(wb, sheet)
    data = _grid(rows)
    start = _last_row(ws) + 1
    _write_grid(ws, start, 1, data)
    _save(wb, p)
    return f"Added {len(data)} row(s) at row {start} in '{ws.title}'.{_note(wb)}"


@tool
@_guard
def excel_add_sheet(path: str, name: str) -> str:
    """Add a new empty sheet to an existing workbook."""
    p = _book(path)
    wb = _load(p)
    name = _clean_sheet_name(name)
    if name in wb.sheetnames:
        return f"Refused: sheet '{name}' already exists"
    wb.create_sheet(title=name)
    _save(wb, p)
    return f"Added sheet '{name}'"


@tool
@_guard
def excel_rename_sheet(path: str, sheet: str, new_name: str) -> str:
    """Rename a sheet in a workbook."""
    p = _book(path)
    wb = _load(p)
    ws = _ws(wb, sheet)
    new_name = _clean_sheet_name(new_name)
    if new_name in wb.sheetnames and new_name != ws.title:
        return f"Refused: sheet '{new_name}' already exists"
    old = ws.title
    ws.title = new_name
    _save(wb, p)
    return f"Renamed sheet '{old}' to '{new_name}'"


@tool
@_guard
def excel_delete_sheet(path: str, sheet: str) -> str:
    """Delete a sheet and everything on it. The sheet name is required. Needs user confirmation."""
    if not sheet.strip():
        return "Refused: name the sheet to delete"
    p = _book(path)
    wb = _load(p)
    ws = _ws(wb, sheet)
    if len(wb.sheetnames) == 1:
        return "Refused: a workbook must keep at least one sheet"
    name = ws.title
    wb.remove(ws)
    _save(wb, p)
    return f"Deleted sheet '{name}' (a backup of the previous version was kept)"


@tool
@_guard
def excel_sort(path: str, sheet: str = "", column: str = "", descending: bool = False) -> str:
    """Sort the data rows of a sheet by one column (row 1 stays as the header). Numbers sort
    as numbers. Refused if the sheet has formulas, because moving rows would break them.
    Needs user confirmation."""
    p = _book(path)
    wb = _load(p)
    ws = _ws(wb, sheet)
    col = _find_column(ws, column)
    last = _last_row(ws)
    if last < 2:
        return "Nothing to sort: needs a header row and data"
    rows = [[c.value for c in ws[r]] for r in range(2, last + 1)]
    if any(isinstance(v, str) and v.startswith("=") for row in rows for v in row):
        return ("Refused: the sheet contains formulas, and sorting would break their references. "
                "Remove the formulas or sort manually.")
    width = len(rows[0])
    rows.sort(key=lambda r: _sort_key(r[col - 1]), reverse=descending)
    _write_grid(ws, 2, 1, [r[:width] for r in rows])
    _save(wb, p)
    order = "descending" if descending else "ascending"
    return f"Sorted {len(rows)} row(s) by column {col} ({order}).{_note(wb)}"


@tool
@_guard
def excel_format(path: str, sheet: str = "") -> str:
    """Make the header row bold, freeze it, and auto-fit column widths on a sheet."""
    p = _book(path)
    wb = _load(p)
    ws = _ws(wb, sheet)
    for c in ws[1]:
        if c.value is not None:
            c.font = Font(bold=True)
    ws.freeze_panes = "A2"
    for col in ws.iter_cols():
        letter = col[0].column_letter
        longest = max((len(str(c.value))
                      for c in col if c.value is not None), default=8)
        ws.column_dimensions[letter].width = min(max(longest + 2, 8), 60)
    _save(wb, p)
    return f"Formatted '{ws.title}': bold header, frozen top row, widths fitted.{_note(wb)}"


EXCEL_TOOLS = [
    excel_info, excel_read, excel_summarize, excel_to_csv,
    excel_create, excel_write, excel_append_rows,
    excel_add_sheet, excel_rename_sheet, excel_delete_sheet,
    excel_sort, excel_format,
]

# HITL confirm: these change or remove data beyond a single cell write
RISKY = {"excel_delete_sheet", "excel_sort"}
