from __future__ import annotations

import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

try:
    from .directory_autocomplete import DirectoryAutocomplete
except ImportError:
    from directory_autocomplete import DirectoryAutocomplete

VIDEO_EXTENSIONS = {".mp4", ".m4v", ".webm", ".mov", ".mkv", ".avi", ".ogv", ".mpeg", ".mpg", ".ts"}


class ScriptRunnerMixin:
    def open_script_runner(self):
        existing = getattr(self, "script_runner_window", None)
        if existing and existing.winfo_exists():
            existing.deiconify(); existing.lift(); return
        window = tk.Toplevel(self); window.title("Run script"); window.geometry("720x500"); window.minsize(620, 420); window.transient(self)
        self.script_runner_window = window
        body = tk.Frame(window, bg="white"); body.pack(fill="both", expand=True, padx=26, pady=24)
        tk.Label(body, text="Run a media script", bg="white", fg="#101828", font=("Segoe UI", 15, "bold")).pack(anchor="w")
        tk.Label(body, text="Choose a script and storage directory. It will run for every video below that directory.", bg="white", fg="#667085", wraplength=640, justify="left").pack(anchor="w", pady=(4, 20))
        scripts = {"Generate previews": "preview_generator.py", "Detect credits": "credit_detector.py"}
        tk.Label(body, text="Script", bg="white").pack(anchor="w")
        script_var = tk.StringVar(value=next(iter(scripts))); ttk.Combobox(body, textvariable=script_var, values=list(scripts), state="readonly", width=60).pack(fill="x", pady=(4, 14))
        tk.Label(body, text="Storage directory", bg="white").pack(anchor="w")
        folder_var = tk.StringVar(value=getattr(self, "script_runner_folder", self.upload_path)); self.script_runner_folder_var = folder_var
        folder_row = tk.Frame(body, bg="white"); folder_row.pack(fill="x", pady=(4, 20))
        folder_options = sorted({"", self.upload_path, *[item["path"] for item in self.upload_items if item["type"] == "folder"]})
        DirectoryAutocomplete(folder_row, api=self.api, initial_values=folder_options, textvariable=folder_var, width=52).pack(side="left", fill="x", expand=True); ttk.Button(folder_row, text="Choose...", command=lambda: self.open_script_folder_picker(window, folder_var)).pack(side="left", padx=(8, 0))
        status = tk.Label(body, text="Ready", bg="white", fg="#667085", anchor="w"); status.pack(fill="x", pady=(0, 7)); progress = ttk.Progressbar(body, mode="determinate", maximum=1); progress.pack(fill="x")
        self.script_runner_status = status; self.script_runner_progress = progress
        actions = tk.Frame(body, bg="white"); actions.pack(fill="x", pady=(22, 0)); detail_button = ttk.Button(actions, text="Show detail", command=self.open_script_detail, state="disabled"); detail_button.pack(side="left"); start_button = ttk.Button(actions, text="Run script", style="Primary.TButton"); start_button.pack(side="right"); ttk.Button(actions, text="Close", command=window.withdraw).pack(side="right", padx=(0, 8))
        self.script_runner_detail_button = detail_button; self.script_runner_start_button = start_button; self.script_runner_script_var = script_var
        window.protocol("WM_DELETE_WINDOW", window.withdraw)
        def start():
            self.script_runner_folder = folder_var.get().strip("/"); self.script_runner_log = []; self.script_runner_running = True; detail_button.configure(state="normal"); start_button.configure(state="disabled"); status.configure(text="Finding videos..."); self.run_script_for_folder(window, status, progress, start_button, self.script_runner_folder, scripts[script_var.get()])
        start_button.configure(command=start)

    def open_script_detail(self):
        existing = getattr(self, "script_detail_window", None)
        if existing and existing.winfo_exists(): existing.deiconify(); existing.lift(); return
        window = tk.Toplevel(self); window.title("Script output"); window.geometry("900x520"); window.minsize(600, 300); window.transient(self); self.script_detail_window = window
        text = tk.Text(window, wrap="word", state="disabled", bg="#111827", fg="#e5e7eb", insertbackground="white"); text.pack(fill="both", expand=True, padx=12, pady=12); self.script_detail_text = text
        window.protocol("WM_DELETE_WINDOW", window.withdraw); self.refresh_script_detail()

    def append_script_log(self, line):
        self.script_runner_log = getattr(self, "script_runner_log", []) + [line.rstrip()]
        self.refresh_script_detail()

    def refresh_script_detail(self):
        text = getattr(self, "script_detail_text", None)
        if not text or not text.winfo_exists(): return
        text.configure(state="normal"); text.delete("1.0", "end"); text.insert("end", "\n".join(getattr(self, "script_runner_log", []))); text.see("end"); text.configure(state="disabled")

    def open_script_folder_picker(self, parent, folder_var):
        picker = tk.Toplevel(parent); picker.title("Choose storage directory"); picker.geometry("600x460"); picker.transient(parent); picker.grab_set(); current = tk.StringVar(value=folder_var.get()); tk.Label(picker, textvariable=current, anchor="w").pack(fill="x", padx=14, pady=(14, 8)); tree = ttk.Treeview(picker, show="tree", selectmode="browse"); tree.pack(fill="both", expand=True, padx=14, pady=(0, 10))
        def load(folder):
            current.set(folder or "Library root"); tree.delete(*tree.get_children())
            def worker():
                try:
                    entries = self.api.storage_list(folder); folders = sorted(entry.get("name") or "" for entry in entries if entry.get("id") is None and entry.get("name") != ".emptyFolderPlaceholder")
                    self.after(0, lambda: [tree.insert("", "end", iid=f"{folder}/{name}" if folder else name, text=name) for name in folders])
                except Exception as exc: self.after(0, lambda error=exc: messagebox.showerror("Folder list failed", str(error), parent=picker))
            threading.Thread(target=worker, daemon=True).start()
        tree.bind("<Double-1>", lambda _event: load(tree.selection()[0]) if tree.selection() else None)
        def choose(): folder_var.set("" if current.get() == "Library root" else current.get()); self.script_runner_folder = folder_var.get(); picker.destroy()
        def up(): value = current.get(); load(value.rpartition("/")[0] if value != "Library root" else "")
        load(folder_var.get().strip("/")); buttons = tk.Frame(picker); buttons.pack(fill="x", padx=14, pady=(0, 14)); ttk.Button(buttons, text="Up", command=up).pack(side="left"); ttk.Button(buttons, text="Select directory", style="Primary.TButton", command=choose).pack(side="right"); ttk.Button(buttons, text="Cancel", command=picker.destroy).pack(side="right", padx=8)

    def find_script_videos(self, prefix=""):
        found = []
        for entry in self.api.storage_list(prefix):
            name = entry.get("name") or ""
            if not name or name == ".emptyFolderPlaceholder": continue
            path = f"{prefix}/{name}" if prefix else name
            if entry.get("id") is None: found.extend(self.find_script_videos(path))
            elif Path(name).suffix.lower() in VIDEO_EXTENSIONS: found.append(path)
        return found

    def run_script_for_folder(self, window, status, progress, start_button, folder, script_name):
        def worker():
            try:
                videos = self.find_script_videos(folder)
                if not videos:
                    self.after(0, lambda: (status.configure(text="No videos found in that directory."), start_button.configure(state="normal"))); return
                self.after(0, lambda: (progress.configure(maximum=len(videos), value=0), status.configure(text=f"0/{len(videos)} videos")))
                script_path = Path(__file__).resolve().parent / script_name; key = self.config.get("SUPABASE_SECRET_KEY") or self.config.get("SUPABASE_SERVICE_ROLE_KEY", "")
                for index, video in enumerate(videos, 1):
                    self.after(0, lambda item=video, number=index: status.configure(text=f"Running {script_name} for {item} ({number}/{len(videos)})")); self.after(0, lambda item=video: self.append_script_log(f"\n--- {item} ---"))
                    command = [sys.executable, str(script_path), "--supabase-url", self.config["SUPABASE_URL"], "--supabase-key", key, "--bucket", self.config.get("MEDIA_BUCKET", "media"), "--video", video]
                    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", bufsize=1)
                    for line in process.stdout or []: self.after(0, lambda output=line: self.append_script_log(output))
                    result = process.wait()
                    if result != 0: self.after(0, lambda item=video, code=result: self.append_script_log(f"Process exited with code {code} for {item}"))
                    self.after(0, lambda number=index: progress.configure(value=number))
                self.after(0, lambda: status.configure(text=f"Finished processing {len(videos)} video(s).")); self.after(0, lambda: setattr(self, "script_runner_running", False))
            except Exception as exc: self.after(0, lambda error=exc: messagebox.showerror("Script run failed", str(error), parent=window))
            finally: self.after(0, lambda: start_button.configure(state="normal"))
        threading.Thread(target=worker, daemon=True).start()
