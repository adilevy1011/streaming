from __future__ import annotations

import tkinter as tk
import threading
from tkinter import messagebox, ttk


class OrderingViewMixin:
    def build_ordering_page(self):
        if not hasattr(self, "content") or self.page_title.cget("text") != "Playback order": return
        self.clear_content(); box = self.card(self.content); box.pack(fill="both", expand=True)
        toolbar = tk.Frame(box, bg="white"); toolbar.pack(fill="x", padx=22, pady=20)
        tk.Label(toolbar, text="Folder", bg="white", font=("Segoe UI", 10, "bold")).pack(side="left")
        self.folder_var = tk.StringVar(); self.folder_combo = ttk.Combobox(toolbar, textvariable=self.folder_var, state="readonly", width=42); self.folder_combo.pack(side="left", padx=12); self.folder_combo.bind("<<ComboboxSelected>>", self.order_folder_selected)
        ttk.Button(toolbar, text="Repair orderings", command=self.repair_folder_orderings).pack(side="right")
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

    def repair_folder_orderings(self):
        if self.has_dirty() and not messagebox.askyesno(
            "Discard unsaved changes?",
            "Repairing folder orderings reloads data from Supabase and discards unsaved changes.",
            parent=self,
        ):
            return
        self.save_button.configure(state="disabled")
        self.status.configure(text="Repairing folder orderings...")

        def worker():
            try:
                result = self.api.request("POST", "/rest/v1/rpc/repair_folder_orderings", body={}) or {}
                self.after(0, lambda: self.folder_orderings_repaired(result))
            except Exception as exc:
                self.after(0, lambda error=exc: messagebox.showerror("Repair failed", str(error), parent=self))
                self.after(0, self.update_save_button)

        threading.Thread(target=worker, daemon=True).start()

    def folder_orderings_repaired(self, result):
        self.order_drafts.clear()
        self.status.configure(
            text=(
                f"Repair complete · {result.get('folders_refreshed', 0)} folder(s) refreshed · "
                f"{result.get('stale_rows_removed', 0)} stale row(s) removed"
            )
        )
        self.load_data()

    def move_order(self, delta):
        index = self.order_list.curselection()
        if not index: return
        folder = self.current_order_folder; current = next((o for o in self.orderings if (o.get("folder_path") or "") == folder), {}); paths = list(self.order_drafts.get(folder, current.get("item_paths") or [])); new = index[0] + delta
        if not 0 <= new < len(paths): return
        paths[index[0]], paths[new] = paths[new], paths[index[0]]; self.order_drafts[folder] = paths; self.render_ordering(); self.order_list.selection_set(new); self.status.configure(text="Unsaved changes"); self.update_save_button()
