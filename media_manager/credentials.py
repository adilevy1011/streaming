from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import simpledialog

APP_DIR = Path(__file__).resolve().parent
ENV_FILE = APP_DIR / ".env"

def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def write_env(values: dict[str, str]) -> None:
    ENV_FILE.write_text(
        "\n".join(f'{key}="{value.replace(chr(34), chr(92) + chr(34))}"' for key, value in values.items())
        + "\n",
        encoding="utf-8",
    )


def configure_credentials(parent: tk.Misc) -> dict[str, str] | None:
    local_values = read_env(ENV_FILE)
    if local_values.get("SUPABASE_URL") and (
        local_values.get("SUPABASE_SECRET_KEY") or local_values.get("SUPABASE_SERVICE_ROLE_KEY")
    ):
        return local_values

    values = dict(local_values)

    url = simpledialog.askstring(
        "Supabase project", "Supabase project URL:\n(e.g. https://your-project.supabase.co)", parent=parent
    )
    if not url:
        return None
    key = simpledialog.askstring(
        "Supabase secret key",
        "Paste the Supabase secret/service-role key.\nIt will be saved locally in media_manager/.env.",
        parent=parent,
        show="*",
    )
    if not key:
        return None
    bucket = simpledialog.askstring(
        "Media bucket", "Storage bucket name (optional):", initialvalue="media", parent=parent
    ) or "media"
    values.update({"SUPABASE_URL": url.rstrip("/"), "SUPABASE_SECRET_KEY": key, "MEDIA_BUCKET": bucket})
    write_env({k: values[k] for k in ("SUPABASE_URL", "SUPABASE_SECRET_KEY", "MEDIA_BUCKET")})
    return values



