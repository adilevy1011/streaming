from __future__ import annotations

import threading
import tkinter as tk
from pathlib import Path
from tkinter import ttk


class UploadsViewMixin:
    def build_uploads_page(self):
        box = self.card(self.content); box.pack(fill="both", expand=True)
        toolbar = tk.Frame(box, bg="white"); toolbar.pack(fill="x", padx=22, pady=(18, 10))
        ttk.Button(toolbar, text="Upload files", style="Primary.TButton", command=self.choose_uploads).pack(side="left")
        ttk.Button(toolbar, text="Upload queue", command=self.open_upload_queue).pack(side="left", padx=(8, 0))
        ttk.Button(toolbar, text="New folder", command=self.create_upload_folder).pack(side="left", padx=8)
        self.upload_breadcrumb = tk.Frame(box, bg="white"); self.upload_breadcrumb.pack(fill="x", padx=22, pady=(0, 10))
        self.upload_status = tk.Label(box, text="Loading storage...", bg="white", fg=self.MUTED, anchor="w"); self.upload_status.pack(fill="x", padx=22, pady=(0, 8))
        self.upload_tree = ttk.Treeview(box, columns=("type", "size", "updated"), show="tree headings", selectmode="browse")
        for column, title in (("#0", "Name"), ("type", "Type"), ("size", "Size"), ("updated", "Updated")): self.upload_tree.heading(column, text=title)
        self.upload_tree.column("#0", width=430); self.upload_tree.column("type", width=110); self.upload_tree.column("size", width=110); self.upload_tree.column("updated", width=180); self.upload_tree.pack(fill="both", expand=True, padx=14, pady=(0, 14))
        self.upload_tree.bind("<Double-1>", self.open_upload_item); self.upload_tree.bind("<Button-3>", self.show_upload_menu)
        self.upload_menu = tk.Menu(self, tearoff=False); self.upload_menu.add_command(label="Open media manager", command=self.open_selected_media_manager); self.upload_menu.add_separator(); self.upload_menu.add_command(label="Rename", command=self.rename_upload_item); self.upload_menu.add_command(label="Delete", command=self.delete_upload_item); self.load_upload_folder(self.upload_path)

    def render_upload_breadcrumb(self):
        for child in self.upload_breadcrumb.winfo_children(): child.destroy()
        parts = [part for part in self.upload_path.split("/") if part]
        paths = [("Root", "")] + [(part, "/".join(parts[:index + 1])) for index, part in enumerate(parts)]
        for index, (label, path) in enumerate(paths):
            if index: tk.Label(self.upload_breadcrumb, text=" / ", bg="white", fg="#98a2b3").pack(side="left")
            tk.Button(self.upload_breadcrumb, text=label, relief="flat", bd=0, bg="white", fg=self.BLUE, command=lambda value=path: self.load_upload_folder(value)).pack(side="left")

    def load_upload_folder(self, path=""):
        if not hasattr(self, "upload_tree"): return
        self.upload_path = path.strip("/"); self.render_upload_breadcrumb(); [self.upload_tree.delete(item) for item in self.upload_tree.get_children()]; self.upload_status.configure(text="Loading storage...")
        def worker():
            try:
                entries = self.api.storage_list(self.upload_path); items = []
                for entry in entries:
                    name = entry.get("name") or ""
                    if not name or name == ".emptyFolderPlaceholder": continue
                    metadata = entry.get("metadata") or {}; item_type = "folder" if entry.get("id") is None else "file"; extension = Path(name).suffix.removeprefix(".").lower() or "unknown"
                    items.append({"name": name, "type": item_type, "type_label": item_type if item_type == "folder" else f"file, {extension}", "path": f"{self.upload_path}/{name}" if self.upload_path else name, "size": metadata.get("size"), "updated_at": entry.get("updated_at") or ""})
                items.sort(key=lambda value: (value["type"] != "folder", value["name"].casefold())); self.after(0, lambda: self.uploads_loaded(self.upload_path, items))
            except Exception as exc: self.after(0, lambda: self.upload_status.configure(text=f"Unable to load storage: {exc}"))
        threading.Thread(target=worker, daemon=True).start()

    def uploads_loaded(self, folder, items):
        if folder != self.upload_path: return
        self.upload_items = items
        for item in items:
            size = "Folder" if item["type"] == "folder" else self.format_bytes(item.get("size")); self.upload_tree.insert("", "end", iid=item["path"], text=item["name"], values=(item.get("type_label", item["type"]), size, item.get("updated_at", "")))
        self.upload_status.configure(text=f"{len(items)} item(s) - {self.upload_path or 'Library root'}")

    @staticmethod
    def format_bytes(value):
        try: size = float(value)
        except (TypeError, ValueError): return "-"
        units = ("B", "KB", "MB", "GB", "TB"); index = 0
        while size >= 1024 and index < len(units) - 1: size /= 1024; index += 1
        return f"{size:.1f} {units[index]}" if index else f"{int(size)} B"

    def selected_upload_item(self):
        selection = self.upload_tree.selection(); return next((item for item in self.upload_items if item["path"] == selection[0]), None) if selection else None
    def open_upload_item(self, _=None):
        item = self.selected_upload_item()
        if item and item["type"] == "folder": self.load_upload_folder(item["path"])
    def show_upload_menu(self, event):
        item_id = self.upload_tree.identify_row(event.y)
        if not item_id: return
        self.upload_tree.selection_set(item_id); self.upload_tree.focus(item_id); item = self.selected_upload_item(); is_video = bool(item and item["type"] == "file" and Path(item["name"]).suffix.lower() in {".mp4", ".m4v", ".webm", ".mov", ".mkv", ".avi", ".ogv", ".mpeg", ".mpg", ".ts"}); self.upload_menu.entryconfigure(0, state="normal" if is_video else "disabled"); self.upload_menu.tk_popup(event.x_root, event.y_root)
    def open_selected_media_manager(self):
        item = self.selected_upload_item()
        if item and item["type"] == "file": self.open_media_manager(item)
