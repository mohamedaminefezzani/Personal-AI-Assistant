"""Server-selected file namespaces; model arguments never choose the user."""

from contextvars import ContextVar
from pathlib import Path
import os
import uuid
from dotenv import load_dotenv


load_dotenv(Path(__file__).parent / ".env")
FILES_BASE_DIR = Path(os.path.expanduser(os.getenv("FILES_BASE_DIR", "~/assistant_files")))
file_user: ContextVar[str | None] = ContextVar("file_user", default=None)


def user_directory(user_id: str | None = None) -> Path:
    identity = user_id or file_user.get()
    if not identity:
        raise ValueError("File access requires an authenticated user context.")
    identity = str(uuid.UUID(identity))
    root = FILES_BASE_DIR.resolve()
    base = root / identity
    if base.is_symlink() or base.resolve().parent != root:
        raise ValueError("Invalid user directory.")
    base.mkdir(parents=True, exist_ok=True)
    return base.resolve()


def safe_path(filename: str, user_id: str | None = None) -> Path:
    base = user_directory(user_id)
    relative = Path(filename)
    if relative.is_absolute() or relative.drive or ".." in relative.parts:
        raise ValueError("Access denied: path must stay inside your files directory.")
    target = (base / relative).resolve()
    if not target.is_relative_to(base):
        raise ValueError("Access denied: path escapes your files directory.")
    return target
