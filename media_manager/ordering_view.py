from __future__ import annotations

import tkinter as tk
from tkinter import ttk


class OrderingViewMixin:
    def build_ordering_page(self):
        if not hasattr(self, "content") or self.page_title.cget("text") != "Playback order": return
        self.clear_content(); box = self.card(self.content); box.pack(fill="both", expand=True)
        toolbar = tk.Frame(box, bg="white"); toolbar.pack(fill="x", padx=22, pady=20)
        tk.Label(toolbar, text="Folder", bg="white", font=("Segoe UI", 10, "bold")).pack(side="left")
        self.folder_var = tk.StringVar(); self.folder_combo = ttk.Combobox(toolbar, textvariable=self.folder_var, state="readonly", width=42); self.folder_combo.pack(side="left", padx=12); self.folder_combo.bind("<<ComboboxSelected>>", self.order_folder_selected)
        tk.Label(box, text="Move items with the buttons to set the playback sequence.", bg="white", fg=self.MUTED).pack(anchor="w", padx=22)
        area = tk.Frame(box, bg="white"); area.pack(fill="both", expand=True, padx=22, pady=14)
        self.order_list = tk.Listbox(area, relief="flat", bd=0, bg="#f8fafc", font=("Segoe UI", 11), selectbackground="#dbeafe", activestyle="none"); self.order_list.pack(side="left", fill="both", expand=True)
        buttons = tk.Frame(area, bg="white"); buttons.pack(side="left", fill="y", padx=14); ttk.Button(buttons, text="Move up", command=lambda: self.move_order(-1)).pack(pady=4); ttk.Button(buttons, text="Move down", command=lambda: self.move_order(1)).pack(pady=4)
        self.render_ordering()

    def order_folder_selected(self, _event=None):
        selected = self.folder_var.get()
        self.current_order_folder = "" if selected == "Library root" else selected
        self.render_ordering()

    def render_ordering(self):
        if not hasattr(self, "folder_combo"): return
        folders = [(o.get("folder_path") or "") for o in self.orderings]; self.folder_combo["values"] = [f or "Library root" for f in folders]
        if not folders: self.order_list.delete(0, "end"); return
        if self.current_order_folder not in folders: self.current_order_folder = folders[0]
        self.folder_var.set(self.current_order_folder or "Library root"); current = next((o for o in self.orderings if (o.get("folder_path") or "") == self.current_order_folder), {}); paths = self.order_drafts.get(self.current_order_folder, list(current.get("item_paths") or []))
        self.order_list.delete(0, "end")
        for path in paths: self.order_list.insert("end", path.rsplit("/", 1)[-1])

    def move_order(self, delta):
        index = self.order_list.curselection()
        if not index: return
        folder = self.current_order_folder; current = next((o for o in self.orderings if (o.get("folder_path") or "") == folder), {}); paths = list(self.order_drafts.get(folder, current.get("item_paths") or [])); new = index[0] + delta
        if not 0 <= new < len(paths): return
        paths[index[0]], paths[new] = paths[new], paths[index[0]]; self.order_drafts[folder] = paths; self.render_ordering(); self.order_list.selection_set(new); self.status.configure(text="Unsaved changes"); self.update_save_button()
