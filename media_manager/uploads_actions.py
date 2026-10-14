from __future__ import annotations

import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

try:
    from .supabase_client import UploadCancelled, UploadPaused
except ImportError:
    from supabase_client import UploadCancelled, UploadPaused


class UploadActionsMixin:
    def create_upload_folder(self):
        name = simpledialog.askstring("New folder", "Folder name:", parent=self)
        if not name: return
        if "/" in name or "\\" in name or name in {".", ".."}:
            messagebox.showerror("Invalid folder", "Folder names cannot contain slashes.", parent=self); return
        destination = f"{self.upload_path}/{name}" if self.upload_path else name
        def worker():
            try: self.api.create_folder(f"{destination}/.emptyFolderPlaceholder"); self.after(0, lambda: self.load_upload_folder(self.upload_path))
            except Exception as exc: self.after(0, lambda: messagebox.showerror("Folder creation failed", str(exc), parent=self))
        threading.Thread(target=worker, daemon=True).start()

    def choose_uploads(self):
        files = filedialog.askopenfilenames(title="Choose media files", filetypes=[("Media", "*.mp4 *.mkv *.webm *.mov *.avi *.m4v *.jpg *.jpeg *.png *.webp *.gif *.srt"), ("All files", "*.*")], parent=self)
        if files: self.open_upload_staging([Path(file_name) for file_name in files])

    def open_upload_staging(self, files):
        window = tk.Toplevel(self); window.title("Stage uploads"); window.geometry("900x480"); window.minsize(720, 360); window.transient(self); window.grab_set()
        same_folder = tk.BooleanVar(value=True); tk.Label(window, text="Review upload names and destinations before starting.", anchor="w").pack(fill="x", padx=18, pady=(16, 8)); tk.Checkbutton(window, text="Upload all to the same folder", variable=same_folder, anchor="w").pack(fill="x", padx=18, pady=(0, 10))
        area = tk.Frame(window); area.pack(fill="both", expand=True, padx=18)
        canvas = tk.Canvas(area, highlightthickness=0)
        scrollbar = ttk.Scrollbar(area, orient="vertical", command=canvas.yview)
        scrollable = tk.Frame(canvas)
        scrollable.bind("<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas_window = canvas.create_window((0, 0), window=scrollable, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(canvas_window, width=event.width))
        canvas.pack(side="left", fill="both", expand=True); scrollbar.pack(side="right", fill="y")
        def scroll(event):
            canvas.yview_scroll(-1 * (event.delta // 120 or (-1 if event.delta < 0 else 1)), "units")
        window.bind("<MouseWheel>", scroll)
        header = tk.Frame(scrollable); header.pack(fill="x"); tk.Label(header, text="Local file", width=30, anchor="w").pack(side="left"); tk.Label(header, text="Upload name", width=25, anchor="w").pack(side="left", padx=8); tk.Label(header, text="Destination folder", anchor="w").pack(side="left")
        folder_options = sorted({self.upload_path, *[item["path"] for item in self.upload_items if item["type"] == "folder"]})
        rows = []
        for file_path in files:
            row = tk.Frame(scrollable); row.pack(fill="x", pady=3); tk.Label(row, text=file_path.name, width=30, anchor="w").pack(side="left"); name = tk.StringVar(value=file_path.name); folder = tk.StringVar(value=self.upload_path); ttk.Entry(row, textvariable=name, width=25).pack(side="left", padx=8); ttk.Combobox(row, textvariable=folder, values=folder_options, width=35).pack(side="left"); tk.Button(row, text="Choose...", command=lambda index=len(rows): self.open_folder_picker(window, rows, index, same_folder)).pack(side="left", padx=6); rows.append({"path": file_path, "name": name, "folder": folder})
        for index, row in enumerate(rows): row["folder"].trace_add("write", lambda *_args, current=index: self.staging_folder_changed(rows, current, same_folder))
        actions = tk.Frame(window); actions.pack(fill="x", padx=18, pady=16); ttk.Button(actions, text="Cancel", command=window.destroy).pack(side="right", padx=(8, 0)); ttk.Button(actions, text="Start uploads", style="Primary.TButton", command=lambda: self.confirm_staged_uploads(window, rows)).pack(side="right")

    def staging_folder_changed(self, rows, index, same_folder):
        if not same_folder.get() or not rows: return
        folder = rows[index]["folder"].get()
        for row_index, row in enumerate(rows):
            if row_index != index and row["folder"].get() != folder: row["folder"].set(folder)

    def open_folder_picker(self, staging_window, rows, index, same_folder):
        picker = tk.Toplevel(staging_window); picker.title("Choose upload folder"); picker.geometry("520x400"); picker.transient(staging_window); picker.grab_set()
        path = tk.StringVar(value=rows[index]["folder"].get()); tk.Label(picker, textvariable=path, anchor="w").pack(fill="x", padx=14, pady=(14, 8))
        tree = ttk.Treeview(picker, columns=("kind",), show="tree", selectmode="browse"); tree.pack(fill="both", expand=True, padx=14, pady=(0, 10))
        def load(folder):
            path.set(folder or "Library root"); tree.delete(*tree.get_children())
            def worker():
                try:
                    entries = self.api.storage_list(folder); folders = sorted((entry.get("name") or "") for entry in entries if entry.get("id") is None and entry.get("name") != ".emptyFolderPlaceholder")
                    self.after(0, lambda: [tree.insert("", "end", iid=(f"{folder}/{name}" if folder else name), text=name) for name in folders])
                except Exception as exc: self.after(0, lambda: messagebox.showerror("Folder list failed", str(exc), parent=picker))
            threading.Thread(target=worker, daemon=True).start()
        def open_selected(_=None):
            selected = tree.selection()
            if selected: load(selected[0])
        def select_folder():
            rows[index]["folder"].set(path.get() if path.get() != "Library root" else "")
            if same_folder.get(): self.staging_folder_changed(rows, index, same_folder)
            picker.destroy(); staging_window.grab_set()
        def go_up(): load(path.get().rpartition("/")[0] if path.get() != "Library root" else "")
        tree.bind("<Double-1>", open_selected); load(path.get())
        buttons = tk.Frame(picker); buttons.pack(fill="x", padx=14, pady=(0, 14)); ttk.Button(buttons, text="Up", command=go_up).pack(side="left"); ttk.Button(buttons, text="Select folder", style="Primary.TButton", command=select_folder).pack(side="right"); ttk.Button(buttons, text="Cancel", command=picker.destroy).pack(side="right", padx=8)

    def confirm_staged_uploads(self, window, rows):
        staged = []
        for row in rows:
            name = row["name"].get().strip(); folder = row["folder"].get().strip("/")
            if not name or "/" in name or "\\" in name: messagebox.showerror("Invalid upload name", "Names must be non-empty and cannot contain slashes.", parent=window); return
            staged.append((row["path"], f"{folder}/{name}" if folder else name))
        window.grab_release(); window.destroy()
        self.status.configure(text="Checking existing files before upload...")
        def worker():
            try:
                existing_by_folder = {}
                for _, destination in staged:
                    folder = destination.rpartition("/")[0]
                    if folder not in existing_by_folder:
                        existing_by_folder[folder] = {entry.get("name") for entry in self.api.storage_list(folder)}
                self.after(0, lambda: self.confirm_existing_uploads(staged, existing_by_folder))
            except Exception as exc:
                self.after(0, lambda error=exc: messagebox.showerror("Upload check failed", str(error), parent=self))
        threading.Thread(target=worker, daemon=True).start()

    def confirm_existing_uploads(self, staged, existing_by_folder):
        approved = []
        for local_path, destination in staged:
            folder, _, name = destination.rpartition("/")
            if name in existing_by_folder.get(folder, set()):
                replace = messagebox.askyesno(
                    "File already exists",
                    f"{destination} already exists.\n\nReplace it?",
                    parent=self,
                )
                if not replace:
                    continue
            approved.append((local_path, destination))
        for local_path, destination in approved:
            self.enqueue_upload(local_path, destination)
        if approved:
            self.status.configure(text=f"Queued {len(approved)} upload(s)")
        else:
            self.status.configure(text="No uploads queued")

    def enqueue_upload(self, local_path: Path, destination=None):
        destination = destination or (f"{self.upload_path}/{local_path.name}" if self.upload_path else local_path.name)
        self.upload_queue_counter += 1
        item = {
            "id": str(self.upload_queue_counter),
            "path": local_path,
            "destination": destination,
            "status": "Queued",
            "progress": 0,
            "pause_requested": threading.Event(),
            "cancel_requested": threading.Event(),
            "upload_state": {},
        }
        self.upload_queue.append(item)
        self.open_upload_queue()
        self.refresh_upload_queue()
        self.start_next_upload()

    def open_upload_queue(self):
        if self.upload_queue_window and self.upload_queue_window.winfo_exists(): self.upload_queue_window.deiconify(); self.upload_queue_window.lift(); self.refresh_upload_queue(); return
        window = tk.Toplevel(self)
        window.title("Upload queue")
        window.geometry("860x420")
        window.minsize(680, 240)
        window.transient(self)
        tk.Label(window, text="Uploads run one at a time. Pause the current upload to let another queued upload start.", anchor="w").pack(fill="x", padx=14, pady=(14, 4))
        area = tk.Frame(window)
        area.pack(fill="both", expand=True, padx=14, pady=10)
        canvas = tk.Canvas(area, highlightthickness=0)
        scrollbar = ttk.Scrollbar(area, orient="vertical", command=canvas.yview)
        body = tk.Frame(canvas)
        body.bind("<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas_window = canvas.create_window((0, 0), window=body, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(canvas_window, width=event.width))
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        def scroll(event):
            canvas.yview_scroll(-1 * (event.delta // 120 or (-1 if event.delta < 0 else 1)), "units")
        window.bind("<MouseWheel>", scroll)
        window.protocol("WM_DELETE_WINDOW", window.withdraw)
        self.upload_queue_window = window
        self.upload_queue_body = body
        self.refresh_upload_queue()

    def refresh_upload_queue(self):
        body = getattr(self, "upload_queue_body", None)
        if not body or not body.winfo_exists(): return
        for child in body.winfo_children(): child.destroy()
        if not self.upload_queue:
            tk.Label(body, text="No uploads in the queue.", fg="#667085").pack(anchor="w", pady=12)
            return
        for item in self.upload_queue:
            row = tk.Frame(body, bd=1, relief="solid", padx=8, pady=7)
            row.pack(fill="x", pady=3)
            row.grid_columnconfigure(0, weight=1)
            tk.Label(row, text=item["path"].name, anchor="w").grid(row=0, column=0, sticky="ew")
            tk.Label(row, text=item["status"], width=12, anchor="w", fg="#667085").grid(row=0, column=1, padx=10)
            progress = ttk.Progressbar(row, mode="determinate", maximum=100, value=item["progress"], length=180)
            progress.grid(row=0, column=2, padx=8)
            tk.Label(row, text=f"{item['progress']}%", width=5, anchor="e").grid(row=0, column=3)
            if item["status"] in {"Queued", "Uploading"}:
                ttk.Button(row, text="Pause", command=lambda upload_id=item["id"]: self.pause_upload(upload_id)).grid(row=0, column=4, padx=(12, 4))
            elif item["status"] == "Paused":
                ttk.Button(row, text="Resume", command=lambda upload_id=item["id"]: self.resume_upload(upload_id)).grid(row=0, column=4, padx=(12, 4))
            else:
                tk.Frame(row, width=68).grid(row=0, column=4, padx=(12, 4))
            remove_state = "disabled" if item["status"] in {"Pausing...", "Removing..."} else "normal"
            ttk.Button(row, text="Remove", state=remove_state, command=lambda upload_id=item["id"]: self.remove_upload(upload_id)).grid(row=0, column=5)

    def _queue_item(self, upload_id):
        return next((item for item in self.upload_queue if item["id"] == upload_id), None)

    def has_pending_uploads(self):
        return any(item["status"] in {"Queued", "Uploading", "Pausing...", "Paused", "Removing..."} for item in self.upload_queue)

    def pause_upload(self, upload_id):
        item = self._queue_item(upload_id)
        if not item: return
        if item["status"] == "Queued":
            item["status"] = "Paused"
            self.refresh_upload_queue()
        elif item["status"] == "Uploading":
            item["status"] = "Pausing..."
            item["pause_requested"].set()
            self.refresh_upload_queue()

    def resume_upload(self, upload_id):
        item = self._queue_item(upload_id)
        if not item or item["status"] != "Paused": return
        item["pause_requested"].clear()
        item["status"] = "Queued"
        self.refresh_upload_queue()
        self.start_next_upload()

    def remove_upload(self, upload_id):
        item = self._queue_item(upload_id)
        if not item: return
        if item["status"] == "Uploading":
            item["status"] = "Removing..."
            item["cancel_requested"].set()
            self.refresh_upload_queue()
            return
        self.upload_queue.remove(item)
        self.refresh_upload_queue()
        self.start_next_upload()

    def start_next_upload(self):
        if self.upload_queue_running: return
        item = next((entry for entry in self.upload_queue if entry["status"] == "Queued"), None)
        if not item: return
        self.upload_queue_running = True; item["status"] = "Uploading"; self.refresh_upload_queue(); self.upload_status.configure(text=f"Uploading {item['path'].name}...")
        def progress(done, total): self.after(0, lambda: self.upload_progress(item, int(done * 100 / total) if total else 100))
        def worker():
            try:
                self.api.upload_file(item["path"], item["destination"], progress, item["pause_requested"].is_set, item["cancel_requested"].is_set, item["upload_state"])
                self.after(0, lambda: self.upload_finished(item, None))
            except UploadPaused:
                self.after(0, lambda: self.upload_paused(item))
            except UploadCancelled:
                self.after(0, lambda: self.upload_cancelled(item))
            except Exception as exc:
                self.after(0, lambda error=exc: self.upload_finished(item, error))
        threading.Thread(target=worker, daemon=True).start()

    def upload_progress(self, item, percent):
        item["progress"] = percent; self.refresh_upload_queue(); self.upload_status.configure(text=f"Uploading {item['path'].name}... {percent}%")

    def upload_finished(self, item, error):
        if item not in self.upload_queue: return
        if item["cancel_requested"].is_set() or item["status"] == "Removing...":
            self.upload_cancelled(item)
            return
        self.upload_queue_running = False; item["status"] = "Failed" if error else "Complete"; item["progress"] = 0 if error else 100; self.refresh_upload_queue()
        if error: messagebox.showerror("Upload failed", str(error), parent=self)
        self.load_upload_folder(self.upload_path)
        if not error: self.after(5000, lambda: self.expire_upload_item(item))
        self.start_next_upload()

    def upload_paused(self, item):
        if item not in self.upload_queue: return
        self.upload_queue_running = False
        item["status"] = "Paused"
        self.refresh_upload_queue()
        self.start_next_upload()

    def upload_cancelled(self, item):
        if item not in self.upload_queue: return
        self.upload_queue_running = False
        self.upload_queue.remove(item)
        self.refresh_upload_queue()
        self.start_next_upload()

    def expire_upload_item(self, item):
        if item in self.upload_queue and item["status"] == "Complete":
            self.upload_queue.remove(item)
            self.refresh_upload_queue()

    def start_upload(self, local_path: Path):
        destination = f"{self.upload_path}/{local_path.name}" if self.upload_path else local_path.name
        self.upload_status.configure(text=f"Uploading {local_path.name}…")
        def progress(done, total): self.after(0, lambda: self.upload_status.configure(text=f"Uploading {local_path.name}… {int(done * 100 / total) if total else 100}%"))
        def worker():
            try: self.api.upload_file(local_path, destination, progress); self.after(0, lambda: self.load_upload_folder(self.upload_path))
            except Exception as exc: self.after(0, lambda: messagebox.showerror("Upload failed", str(exc), parent=self))
        threading.Thread(target=worker, daemon=True).start()

    def rename_upload_item(self):
        item = self.selected_upload_item()
        if not item: return
        name = simpledialog.askstring("Rename", "New name:", initialvalue=item["name"], parent=self)
        if not name or name == item["name"]: return
        if "/" in name or "\\" in name: messagebox.showerror("Invalid name", "Names cannot contain slashes.", parent=self); return
        destination = f"{self.upload_path}/{name}" if self.upload_path else name
        def worker():
            moved = []
            try:
                sources = self.storage_walk(item["path"]) if item["type"] == "folder" else [item["path"]]
                for source in sources:
                    target = destination + source[len(item["path"]):]
                    self.api.storage_move(source, target)
                    moved.append((source, target))
                self.api.request("POST", "/rest/v1/rpc/rewrite_media_paths", body={"old_path": item["path"], "new_path": destination})
                self.after(0, lambda: self.load_upload_folder(self.upload_path))
            except Exception as exc:
                for source, target in reversed(moved):
                    try: self.api.storage_move(target, source)
                    except Exception: pass
                self.after(0, lambda: messagebox.showerror("Rename failed", str(exc), parent=self))
        threading.Thread(target=worker, daemon=True).start()

    def storage_walk(self, root: str) -> list[str]:
        found, pending = [], [root]
        while pending:
            folder = pending.pop()
            for entry in self.api.storage_list(folder):
                name = entry.get("name") or ""; path = f"{folder}/{name}" if folder else name
                if name == ".emptyFolderPlaceholder" or entry.get("id") is not None: found.append(path)
                elif entry.get("id") is None: pending.append(path)
        return found

    def delete_upload_item(self):
        item = self.selected_upload_item()
        if not item or not messagebox.askyesno("Delete item", f"Delete {item['name']}?\nThis cannot be undone.", parent=self): return
        def worker():
            try:
                paths = self.storage_walk(item["path"]) if item["type"] == "folder" else [item["path"]]
                if paths: self.api.storage_delete(paths)
                self.after(0, lambda: self.load_upload_folder(self.upload_path))
            except Exception as exc: self.after(0, lambda: messagebox.showerror("Delete failed", str(exc), parent=self))
        threading.Thread(target=worker, daemon=True).start()
