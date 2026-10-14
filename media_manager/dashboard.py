"""Desktop manager coordinator. Feature workspaces live in the mixin modules."""
from __future__ import annotations

import tkinter as tk
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from tkinter import messagebox, ttk
import threading

try:
    from .credentials import ENV_FILE, configure_credentials, read_env, write_env
    from .supabase_client import Supabase
    from .permissions_view import PermissionsViewMixin
    from .ordering_view import OrderingViewMixin
    from .uploads_view import UploadsViewMixin
    from .uploads_actions import UploadActionsMixin
    from .media_view import MediaViewMixin
    from .script_runner import ScriptRunnerMixin
except ImportError:
    from credentials import ENV_FILE, configure_credentials, read_env, write_env
    from supabase_client import Supabase
    from permissions_view import PermissionsViewMixin
    from ordering_view import OrderingViewMixin
    from uploads_view import UploadsViewMixin
    from uploads_actions import UploadActionsMixin
    from media_view import MediaViewMixin
    from script_runner import ScriptRunnerMixin


@dataclass
class User:
    user_id: str
    email: str
    new_videos_access: bool = True


class MediaManager(PermissionsViewMixin, OrderingViewMixin, UploadsViewMixin, UploadActionsMixin, MediaViewMixin, ScriptRunnerMixin, tk.Tk):
    BG, NAVY, BLUE, MUTED = "#f4f6f8", "#17212b", "#2563eb", "#667085"

    def __init__(self, api: Supabase, config: dict[str, str]):
        super().__init__(); self.api, self.config = api, config; self.title("ADLV Media Manager"); self.geometry("1180x760"); self.minsize(940, 600); self.configure(bg=self.BG); self.protocol("WM_DELETE_WINDOW", self.handle_close)
        try:
            self._app_icon = tk.PhotoImage(file=Path(__file__).resolve().parent / "app_icon.png")
            self.iconphoto(True, self._app_icon)
        except tk.TclError:
            self._app_icon = None
        self.users, self.videos, self.orderings = [], [], []; self.selected_user = None; self.permission_drafts = {}; self.draft_access = {}; self.original_access = {}; self.draft_new_access = self.original_new_access = True; self.order_drafts = {}; self.current_order_folder = ""; self.upload_path = ""; self.upload_items = []; self.upload_queue = []; self.upload_queue_running = False; self.upload_queue_window = None; self.upload_queue_tree = None; self.upload_queue_body = None; self.upload_queue_counter = 0
        self.build_style(); self.build_shell(); self.load_data()

    def build_style(self):
        style = ttk.Style(self); style.theme_use("clam"); style.configure("TButton", font=("Segoe UI", 10), padding=(10, 6)); style.configure("Primary.TButton", background=self.BLUE, foreground="white", padding=(14, 8)); style.configure("Save.TButton", background="#d0d5dd", foreground="#667085", padding=(14, 8)); style.map("Save.TButton", background=[("disabled", "#d0d5dd"), ("active", self.BLUE)]); style.configure("Loading.Horizontal.TProgressbar", thickness=10, troughcolor="#eaecf0", background=self.BLUE); style.configure("TEntry", padding=7); style.configure("Treeview", rowheight=30, font=("Segoe UI", 10), background="white", fieldbackground="white")

    def build_shell(self):
        nav = tk.Frame(self, bg=self.NAVY, width=220); nav.pack(side="left", fill="y"); nav.pack_propagate(False); tk.Label(nav, text="ADLV", bg=self.NAVY, fg="white", font=("Segoe UI", 23, "bold")).pack(anchor="w", padx=24, pady=(28, 0)); tk.Label(nav, text="MEDIA MANAGER", bg=self.NAVY, fg="#9aa8b5", font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=26, pady=(0, 35))
        self.nav_buttons = {}
        for name, label in (("permissions", "People & access"), ("ordering", "Playback order"), ("uploads", "Manage uploads")):
            button = tk.Button(nav, text="  " + label, anchor="w", relief="flat", bd=0, bg=self.NAVY, fg="white", activebackground="#263747", font=("Segoe UI", 11), padx=12, pady=11, command=lambda n=name: self.show_page(n)); button.pack(fill="x", padx=12, pady=2); self.nav_buttons[name] = button
        tk.Frame(nav, bg=self.NAVY).pack(expand=True, fill="both"); tk.Label(nav, text="Direct Supabase connection", bg=self.NAVY, fg="#8795a1", font=("Segoe UI", 9)).pack(anchor="w", padx=26); tk.Button(nav, text="manage supabase credentials", relief="flat", bd=0, bg=self.NAVY, fg="#63d391", activebackground="#263747", font=("Segoe UI", 9), command=self.open_credentials_editor).pack(anchor="w", padx=22, pady=(3, 22))
        main = tk.Frame(self, bg=self.BG); main.pack(side="left", fill="both", expand=True); header = tk.Frame(main, bg="white", height=76); header.pack(fill="x"); header.pack_propagate(False); self.page_title = tk.Label(header, text="People & access", bg="white", fg="#101828", font=("Segoe UI", 19, "bold")); self.page_title.pack(side="left", padx=28); ttk.Button(header, text="Run script", command=self.open_script_runner).pack(side="right", padx=(8, 12)); self.save_button = ttk.Button(header, text="Save changes", style="Primary.TButton", command=self.save_changes); self.save_button.pack(side="right", padx=8); self.status = tk.Label(main, text="Connecting…", bg=self.BG, fg=self.MUTED, anchor="w"); self.status.pack(fill="x", padx=28, pady=(15, 4)); self.content = tk.Frame(main, bg=self.BG); self.content.pack(fill="both", expand=True, padx=28, pady=(0, 24)); self.show_page("permissions")

    def open_credentials_editor(self):
        values = read_env(ENV_FILE); editor = tk.Toplevel(self); editor.title("Manage Supabase credentials"); editor.geometry("520x260"); editor.transient(self); editor.grab_set(); body = tk.Frame(editor, bg="white"); body.pack(fill="both", expand=True, padx=24, pady=22)
        entries = {}
        for row, (label, key, show) in enumerate((("Project URL", "SUPABASE_URL", ""), ("Secret key", "SUPABASE_SECRET_KEY", "*"), ("Storage bucket", "MEDIA_BUCKET", ""))):
            tk.Label(body, text=label, bg="white").grid(row=row, column=0, sticky="w", pady=7); entry = ttk.Entry(body, width=46, show=show); entry.grid(row=row, column=1, pady=7); entry.insert(0, values.get(key, "")); entries[key] = entry
        def save():
            updated = {key: entry.get().strip() for key, entry in entries.items()}
            if not all(updated.values()): messagebox.showerror("Missing credentials", "All fields are required.", parent=editor); return
            write_env(updated); self.api = Supabase(updated); editor.destroy(); self.load_data()
        actions = tk.Frame(body, bg="white"); actions.grid(row=3, column=1, sticky="e", pady=14); ttk.Button(actions, text="Cancel", command=editor.destroy).pack(side="left", padx=5); ttk.Button(actions, text="Save credentials", style="Primary.TButton", command=save).pack(side="left")

    def clear_content(self):
        for child in self.content.winfo_children(): child.destroy()
        for name in ("permission_heading", "new_access_check", "user_search", "user_list", "video_search", "video_list", "folder_combo", "order_list"):
            if hasattr(self, name): delattr(self, name)
    def show_page(self, page):
        self.clear_content(); [button.configure(bg="#263747" if name == page else self.NAVY) for name, button in self.nav_buttons.items()]; self.page_title.configure(text={"permissions": "People & access", "ordering": "Playback order", "uploads": "Manage uploads"}[page]); (self.build_permissions_page if page == "permissions" else self.build_ordering_page if page == "ordering" else self.build_uploads_page)(); self.update_save_button()
    def card(self, parent): return tk.Frame(parent, bg="white", highlightthickness=1, highlightbackground="#e4e7ec")

    def load_data(self):
        self.status.configure(text="Loading users, videos, and folders…")
        def worker():
            try:
                users = self.load_users(); videos = self.api.request("GET", "/rest/v1/videos", params={"select": "path,name,user_access", "order": "path.asc"}) or []; orderings = self.api.request("GET", "/rest/v1/folder_orderings", params={"select": "folder_path,item_paths,ordered_video_paths,updated_at", "order": "folder_path.asc"}) or []; self.after(0, lambda: self.data_loaded(users, videos, orderings))
            except Exception as exc: self.after(0, lambda error=exc: self.status.configure(text=f"Connection failed: {error}"))
        threading.Thread(target=worker, daemon=True).start()

    def load_users(self):
        try:
            auth = self.api.request("GET", "/auth/v1/admin/users", params={"page": "1", "per_page": "1000"}) or {}
            profiles = self.api.request("GET", "/rest/v1/profiles", params={"select": "user_id,new_videos_access"}) or []
            by_id = {str(row.get("user_id")): row for row in profiles}
            return [{"user_id": str(user.get("id")), "email": user.get("email") or user.get("id"), "new_videos_access": by_id.get(str(user.get("id")), {}).get("new_videos_access", True)} for user in auth.get("users", [])]
        except Exception:
            return []

    def data_loaded(self, users, videos, orderings):
        self.permission_drafts = {}
        self.users = [User(str(user.get("id") or user.get("user_id")), user.get("email") or str(user.get("id") or user.get("user_id")), user.get("new_videos_access") is not False) for user in users]; self.videos, self.orderings = list(videos), list(orderings); self.status.configure(text=f"Ready · {len(self.users)} people · {len(self.videos)} videos");
        if self.users: self.select_user(self.users[0])
        self.update_save_button()

    def has_dirty(self):
        self.cache_current_permissions()
        permission_dirty = any(
            draft["new_access"] != draft["original_new_access"]
            or any(draft["access"].get(key, []) != draft["original"].get(key, []) for key in draft["access"])
            for draft in self.permission_drafts.values()
        )
        return bool(self.order_drafts) or permission_dirty
    def update_save_button(self):
        if not hasattr(self, "save_button"): return
        dirty = self.has_dirty()
        self.save_button.configure(style="Primary.TButton" if dirty else "Save.TButton", state="normal" if dirty else "disabled")

    def flatten_ordering(self, folder, item_paths, ordering_map, video_paths):
        flattened = []
        for item_path in item_paths:
            if item_path in video_paths:
                flattened.append(item_path)
            elif item_path in ordering_map:
                nested = ordering_map[item_path].get("ordered_video_paths") or ordering_map[item_path].get("item_paths") or []
                flattened.extend(self.flatten_ordering(item_path, nested, ordering_map, video_paths))
        return flattened

    def save_folder_ordering(self, folder, paths):
        ordering_map = {(row.get("folder_path") or ""): row for row in self.orderings}
        video_paths = {video.get("path") for video in self.videos}
        flattened = self.flatten_ordering(folder, paths, ordering_map, video_paths)
        body = {
            "folder_path": folder,
            "item_paths": paths,
            "ordered_video_paths": flattened,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        updated = self.api.request("PATCH", "/rest/v1/folder_orderings", params={"folder_path": f"eq.{folder}"}, body=body)
        if not updated:
            self.api.request("POST", "/rest/v1/folder_orderings", body=body)

    def handle_close(self):
        if self.has_pending_uploads() and not messagebox.askyesno("Uploads in progress", "Uploads are still running or queued. Close the manager anyway?", parent=self): return
        if self.has_dirty() and not messagebox.askyesno("Unsaved changes", "You have unsaved changes. Close without saving?", parent=self): return
        self.destroy()

    def save_changes(self):
        if not self.has_dirty(): self.status.configure(text="No changes to save"); return
        permission_snapshot = {user_id: {"access": {key: list(value) for key, value in draft["access"].items()}, "original": {key: list(value) for key, value in draft["original"].items()}, "new_access": draft["new_access"], "original_new_access": draft["original_new_access"]} for user_id, draft in self.permission_drafts.items()}
        user_emails = {user.user_id: user.email.casefold() for user in self.users}
        merged_access = {video["path"]: list(video.get("user_access") or []) for video in self.videos}
        for user_id, draft in permission_snapshot.items():
            email = user_emails.get(user_id)
            if not email: continue
            for path, access in draft["access"].items():
                if access == draft["original"].get(path, []): continue
                is_allowed = email in [item.casefold() for item in access] if access else draft["new_access"]
                current = [item for item in merged_access.get(path, []) if item.casefold() != email]
                if is_allowed: current.append(email)
                merged_access[path] = current
        self.save_button.configure(state="disabled"); self.status.configure(text="Saving changes…")
        def worker():
            try:
                for path, access in merged_access.items():
                    original = next((video.get("user_access") or [] for video in self.videos if video["path"] == path), [])
                    if access != original: self.api.request("PATCH", "/rest/v1/videos", params={"path": f"eq.{path}"}, body={"user_access": access})
                for user_id, draft in permission_snapshot.items():
                    if draft["new_access"] != draft["original_new_access"]: self.api.request("PATCH", "/rest/v1/profiles", params={"user_id": f"eq.{user_id}"}, body={"new_videos_access": draft["new_access"]})
                for folder, paths in self.order_drafts.items(): self.save_folder_ordering(folder, paths)
                self.after(0, lambda: (self.order_drafts.clear(), self.status.configure(text="Changes saved"), self.load_data()))
            except Exception as exc: self.after(0, lambda error=exc: (self.update_save_button(), messagebox.showerror("Save failed", str(error), parent=self)))
        threading.Thread(target=worker, daemon=True).start()


def main():
    root = tk.Tk(); root.withdraw(); config = configure_credentials(root); root.destroy()
    if not config: return 1
    MediaManager(Supabase(config), config).mainloop(); return 0

if __name__ == "__main__": raise SystemExit(main())
