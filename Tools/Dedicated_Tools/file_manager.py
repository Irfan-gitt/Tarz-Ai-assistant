import os
import shutil
from pathlib import Path
from datetime import datetime
from langchain_core.tools import tool
from send2trash import send2trash   # pip install send2trash

# ── Fixed folders ────────────────────────────────────────────────
HOME = Path.home()
WORKSPACE = Path(os.getenv("TARZ_WORKSPACE",
                 HOME / "TARZ_Workspace")).resolve()
WORKSPACE.mkdir(parents=True, exist_ok=True)

ALLOWED_ROOTS = [WORKSPACE, HOME / "Desktop", HOME / "Documents",
                 HOME / "Downloads", HOME / "Pictures"]
PROTECTED = [Path("C:/Windows"), Path("C:/Program Files"),
             Path("C:/Program Files (x86)")]
BLOCKED_EXT = {".exe", ".dll", ".msi", ".scr", ".sys"}
MAX_CHARS = 200_000

CATEGORIES = {
    "Images":    {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg"},
    "Documents": {".pdf", ".doc", ".docx", ".txt", ".md", ".ppt", ".pptx", ".xls", ".xlsx", ".csv"},
    "Videos":    {".mp4", ".mkv", ".mov", ".avi", ".webm"},
    "Audio":     {".mp3", ".wav", ".flac", ".m4a", ".ogg"},
    "Archives":  {".zip", ".rar", ".7z", ".tar", ".gz"},
    "Code":      {".py", ".js", ".html", ".css", ".json", ".ipynb"},
}


def _safe(path: str) -> Path:
    """Relative paths resolve inside WORKSPACE. Absolute paths must be in an allowed root."""
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = WORKSPACE / p
    p = p.resolve()
    if any(p == b or p.is_relative_to(b) for b in PROTECTED):
        raise PermissionError(f"Protected path: {p}")
    if not any(p == r or p.is_relative_to(r) for r in ALLOWED_ROOTS):
        raise PermissionError(f"Outside allowed folders: {p}")
    return p


def _fmt_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"

# ── Read-only tools ──────────────────────────────────────────────


@tool
def list_dir(path: str = ".") -> str:
    """List files and folders. '.' means the TARZ workspace folder."""
    p = _safe(path)
    if not p.is_dir():
        return f"Not a folder: {p}"
    rows = []
    for e in sorted(p.iterdir(), key=lambda x: (x.is_file(), x.name.lower()))[:100]:
        size = "DIR" if e.is_dir() else _fmt_size(e.stat().st_size)
        rows.append(f"{size:>9}  {e.name}")
    return f"{p}\n" + ("\n".join(rows) or "(empty)")


@tool
def search_files(query: str, root: str = ".", ext: str = "") -> str:
    """Find files by name (case-insensitive), optionally by extension like '.pdf'.
    Search the workspace by default; pass root='~/Documents' etc. to search elsewhere."""
    base = _safe(root)
    hits = []
    for f in base.rglob("*"):
        if query.lower() in f.name.lower() and (not ext or f.suffix.lower() == ext.lower()):
            hits.append(str(f))
            if len(hits) >= 25:
                break
    return "\n".join(hits) or "No matches"


@tool
def file_info(path: str) -> str:
    """Size, type, and created/modified dates of a file or folder."""
    p = _safe(path)
    if not p.exists():
        return f"Not found: {p}"
    st = p.stat()
    def fmt(t): return datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M")
    kind = "folder" if p.is_dir() else (p.suffix or "file")
    size = sum(f.stat().st_size for f in p.rglob("*")
               if f.is_file()) if p.is_dir() else st.st_size
    return f"{p}\ntype: {kind}\nsize: {_fmt_size(size)}\ncreated: {fmt(st.st_ctime)}\nmodified: {fmt(st.st_mtime)}"


@tool
def read_text(path: str, max_chars: int = 5000) -> str:
    """Read the start of a text file (txt, md, py, json, csv...)."""
    p = _safe(path)
    if not p.is_file():
        return f"Not a file: {p}"
    raw = p.read_bytes()[:max_chars * 4]
    if b"\x00" in raw:
        return "Refused: looks like a binary file"
    return raw.decode("utf-8", errors="replace")[:max_chars]

# ── Create / write tools ─────────────────────────────────────────


@tool
def create_folder(path: str) -> str:
    """Create a folder (and parents). Relative paths go inside the workspace."""
    p = _safe(path)
    p.mkdir(parents=True, exist_ok=True)
    return f"Folder ready: {p}"


@tool
def create_file(path: str, content: str = "") -> str:
    """Create a NEW text file. Relative paths go inside the workspace. Refuses if it already exists."""
    p = _safe(path)
    if p.suffix.lower() in BLOCKED_EXT:
        return f"Refused: {p.suffix} files not allowed"
    if p.exists():
        return f"Refused: {p} already exists"
    if len(content) > MAX_CHARS:
        return "Refused: content too large"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"Created {p}"


@tool
def append_file(path: str, content: str) -> str:
    """Append text to the end of an existing text file."""
    p = _safe(path)
    if not p.is_file():
        return f"Refused: {p} doesn't exist (use create_file)"
    if len(content) > MAX_CHARS:
        return "Refused: content too large"
    with p.open("a", encoding="utf-8") as f:
        f.write(content)
    return f"Appended to {p}"

# ── Copy / move / rename / delete ────────────────────────────────


@tool
def copy_file(src: str, dst: str) -> str:
    """Copy a file or folder. If dst is a folder, keeps the original name. Never overwrites."""
    s, d = _safe(src), _safe(dst)
    if not s.exists():
        return f"Not found: {s}"
    if d.is_dir():
        d = d / s.name
    if d.exists():
        return f"Refused: {d} already exists"
    d.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(s, d) if s.is_dir() else shutil.copy2(s, d)
    return f"Copied to {d}"


@tool
def move_file(src: str, dst: str) -> str:
    """Move a file or folder. If dst is a folder, keeps the original name. Never overwrites."""
    s, d = _safe(src), _safe(dst)
    if not s.exists():
        return f"Not found: {s}"
    if d.is_dir():
        d = d / s.name
    if d.exists():
        return f"Refused: {d} already exists"
    d.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(s), str(d))
    return f"Moved to {d}"


@tool
def rename_file(path: str, new_name: str) -> str:
    """Rename a file or folder in place. new_name is just a name, not a path."""
    p = _safe(path)
    if not p.exists():
        return f"Not found: {p}"
    if any(c in new_name for c in '/\\:*?"<>|'):
        return "Refused: invalid characters in name"
    target = p.with_name(new_name)
    if target.exists():
        return f"Refused: {target.name} already exists"
    p.rename(target)
    return f"Renamed to {target.name}"


@tool
def delete_file(path: str) -> str:
    """Send a file or folder to the Recycle Bin (recoverable)."""
    p = _safe(path)
    if not p.exists():
        return f"Not found: {p}"
    if p in ALLOWED_ROOTS:
        return "Refused: can't delete a root folder"
    send2trash(str(p))
    return f"Sent to Recycle Bin: {p}"

# ── Extras ───────────────────────────────────────────────────────


@tool
def open_file(path: str) -> str:
    """Open a file with its default app."""
    p = _safe(path)
    if not p.is_file():
        return f"Not a file: {p}"
    os.startfile(p)
    return f"Opened {p.name}"


@tool
def open_folder(path: str = ".") -> str:
    """Open a folder in Windows Explorer. '.' opens the TARZ workspace."""
    p = _safe(path)
    if not p.is_dir():
        return f"Not a folder: {p}"
    os.startfile(p)
    return f"Opened {p}"


@tool
def zip_folder(path: str) -> str:
    """Zip a folder into a .zip next to it."""
    p = _safe(path)
    if not p.is_dir():
        return f"Not a folder: {p}"
    out = shutil.make_archive(str(p), "zip", root_dir=p)
    return f"Created {out}"


@tool
def organize_folder(path: str, dry_run: bool = True) -> str:
    """Sort loose files in a folder into Images/Documents/Videos/Audio/Archives/Code subfolders.
    ALWAYS call with dry_run=True first, show the plan, and only run dry_run=False after the user confirms."""
    p = _safe(path)
    if not p.is_dir():
        return f"Not a folder: {p}"
    plan = []
    for f in p.iterdir():
        if not f.is_file():
            continue
        cat = next((c for c, exts in CATEGORIES.items()
                   if f.suffix.lower() in exts), None)
        if cat and not (p / cat / f.name).exists():
            plan.append((f, p / cat / f.name))
    if not plan:
        return "Nothing to organize"
    if dry_run:
        return "PLAN (nothing moved yet):\n" + "\n".join(f"{s.name} -> {d.parent.name}/" for s, d in plan)
    for s, d in plan:
        d.parent.mkdir(exist_ok=True)
        shutil.move(str(s), str(d))
    return f"Organized {len(plan)} files"


FILE_TOOLS = [list_dir, search_files, file_info, read_text, create_folder, create_file,
              append_file, copy_file, move_file, rename_file, delete_file,
              open_file, open_folder, zip_folder, organize_folder]

RISKY = {"delete_file", "move_file", "rename_file",
         "organize_folder"}  # HITL confirm
