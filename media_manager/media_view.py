from __future__ import annotations

import os
from io import BytesIO
import subprocess
import sys
import threading
import time
import tkinter as tk
from datetime import datetime, timezone
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

try:
    from PIL import Image, ImageTk
except ImportError:
    Image = ImageTk = None

try:
    from .credentials import APP_DIR
except ImportError:
    from credentials import APP_DIR

class MediaViewMixin:
    def open_media_manager(self, item):
        window = tk.Toplevel(self)
        window.title(f"Media manager - {item['name']}")
        window.geometry("920x720")
        window.minsize(760, 560)
        window.transient(self)
        state = {"item": item, "data": {}, "window": window}
        tk.Label(window, text=item["name"], font=("Segoe UI", 18, "bold"), anchor="w").pack(fill="x", padx=24, pady=(20, 2))
        status = tk.Label(window, text=item["path"], fg=self.MUTED, anchor="w"); status.pack(fill="x", padx=24, pady=(0, 12))
        state["status"] = status
        notebook = ttk.Notebook(window); notebook.pack(fill="both", expand=True, padx=18, pady=(0, 18))

        subtitle_tab = tk.Frame(notebook, bg="white"); notebook.add(subtitle_tab, text="Subtitles")
        subtitle_name = tk.Label(subtitle_tab, text="Loading subtitleâ€¦", bg="white", fg=self.MUTED, anchor="w"); subtitle_name.pack(fill="x", padx=18, pady=(18, 8))
        subtitle_name.configure(text="")
        subtitle_loading = ttk.Progressbar(subtitle_tab, mode="indeterminate", length=80, style="Loading.Horizontal.TProgressbar"); subtitle_loading.pack(pady=(0, 12)); subtitle_loading.start(5)
        subtitle_text = tk.Text(subtitle_tab, wrap="none", undo=True, font=("Consolas", 10)); subtitle_text.pack(fill="both", expand=True, padx=18, pady=(0, 10))
        subtitle_button = ttk.Button(subtitle_tab, text="Save subtitles", command=lambda: self.save_media_subtitle(state, subtitle_text, subtitle_name, subtitle_button)); subtitle_button.pack(anchor="e", padx=18, pady=(0, 16))
        state.update({"subtitle_name": subtitle_name, "subtitle_loading": subtitle_loading, "subtitle_text": subtitle_text, "subtitle_button": subtitle_button})

        timeline_tab = tk.Frame(notebook, bg="white"); notebook.add(timeline_tab, text="Timeline & credits")
        timeline_info = tk.Label(timeline_tab, text="Loading timelineâ€¦", bg="white", fg=self.MUTED, anchor="w", justify="left"); timeline_info.pack(fill="x", padx=18, pady=(18, 8))
        timeline_info.configure(text="")
        timeline_loading = ttk.Progressbar(timeline_tab, mode="indeterminate", length=80, style="Loading.Horizontal.TProgressbar"); timeline_loading.pack(pady=(0, 12)); timeline_loading.start(5)
        frames = tk.Listbox(timeline_tab)
        timeline_area = tk.Frame(timeline_tab, bg="white"); timeline_area.pack(fill="both", expand=True, padx=18, pady=(0, 12))
        timeline_canvas = tk.Canvas(timeline_area, bg="white", highlightthickness=0); timeline_scroll = ttk.Scrollbar(timeline_area, orient="vertical", command=timeline_canvas.yview); timeline_xscroll = ttk.Scrollbar(timeline_area, orient="horizontal"); timeline_canvas.configure(yscrollcommand=timeline_scroll.set, xscrollcommand=timeline_xscroll.set); timeline_scroll.pack(side="right", fill="y"); timeline_xscroll.pack(side="bottom", fill="x"); timeline_canvas.pack(side="left", fill="both", expand=True)
        timeline_inner = tk.Frame(timeline_canvas, bg="white"); timeline_window = timeline_canvas.create_window((0, 0), window=timeline_inner, anchor="nw"); timeline_inner.bind("<Configure>", lambda _: timeline_canvas.configure(scrollregion=timeline_canvas.bbox("all")))
        credits_bar = tk.Frame(timeline_tab, bg="white"); credits_bar.pack(fill="x", padx=18, pady=(0, 18))
        tk.Label(credits_bar, text="Credits start (HH:MM:SS):", bg="white").pack(side="left")
        credits_input = ttk.Entry(credits_bar, width=14); credits_input.pack(side="left", padx=8); credits_input.insert(0, "00:00:00")
        credits_time = tk.Label(credits_bar, text="--:--:--", bg="white", fg=self.MUTED); credits_time.pack(side="left", padx=(0, 10))
        def update_credit_time(_=None):
            try: credits_time.configure(text=self.format_seconds(self.parse_timecode(credits_input.get())))
            except ValueError: credits_time.configure(text="--:--:--")
        credits_input.bind("<KeyRelease>", update_credit_time)
        credits_button = ttk.Button(credits_bar, text="Save timestamp", command=lambda: self.save_media_credits(state, credits_input, credits_button)); credits_button.pack(side="left")
        state.update({"timeline_info": timeline_info, "timeline_loading": timeline_loading, "timeline_canvas": timeline_canvas, "timeline_xscroll": timeline_xscroll, "timeline_window": timeline_window, "frames": frames, "timeline_inner": timeline_inner, "timeline_images": [], "credits_input": credits_input, "credits_time": credits_time, "credits_button": credits_button})
        timeline_xscroll.configure(command=lambda *args: self.scroll_timeline(state, *args))
        timeline_canvas.bind("<Configure>", lambda _: self.load_visible_timeline_frames(state))
        notebook.insert(0, timeline_tab)
        notebook.select(timeline_tab)

        image_tab = tk.Frame(notebook, bg="white"); notebook.add(image_tab, text="Picture")
        image_path = tk.Label(image_tab, text="Loading pictureâ€¦", bg="white", fg=self.MUTED, anchor="w"); image_path.pack(fill="x", padx=18, pady=(18, 8))
        image_path.configure(text="")
        image_loading = ttk.Progressbar(image_tab, mode="indeterminate", length=80, style="Loading.Horizontal.TProgressbar"); image_loading.pack(pady=(18, 12)); image_loading.start(5)
        image_preview = tk.Label(image_tab, text="", bg="white"); image_preview.pack(fill="both", expand=True, padx=18, pady=(0, 12))
        image_note = tk.Label(image_tab, text="The picture is used as the video's library artwork.", bg="white", fg=self.MUTED, anchor="w"); image_note.pack(fill="x", padx=18, pady=(0, 18))
        image_buttons = tk.Frame(image_tab, bg="white"); image_buttons.pack(anchor="w", padx=18)
        choose_image_button = ttk.Button(image_buttons, text="Choose picture", command=lambda: self.choose_media_picture(state)); choose_image_button.pack(side="left")
        remove_image_button = ttk.Button(image_buttons, text="Remove picture", command=lambda: self.remove_media_picture(state)); remove_image_button.pack(side="left", padx=8)
        state.update({"image_path": image_path, "image_loading": image_loading, "image_preview": image_preview, "image_photo": None, "remove_image_button": remove_image_button})

        scripts_tab = tk.Frame(notebook, bg="white"); notebook.add(scripts_tab, text="Scripts")
        tk.Label(scripts_tab, text="Run repository scripts locally for this video.", bg="white", fg=self.MUTED, anchor="w").pack(fill="x", padx=18, pady=(18, 8))
        script_buttons = tk.Frame(scripts_tab, bg="white"); script_buttons.pack(anchor="w", padx=18, pady=(0, 12))
        preview_button = ttk.Button(script_buttons, text="Generate previews", command=lambda: self.run_media_script(state, "previews", preview_button)); preview_button.pack(side="left")
        credits_script_button = ttk.Button(script_buttons, text="Detect credits", command=lambda: self.run_media_script(state, "credits", credits_script_button)); credits_script_button.pack(side="left", padx=8)
        output = tk.Text(scripts_tab, wrap="none", state="disabled", bg="#101828", fg="#d0d5dd", insertbackground="white", font=("Consolas", 9)); output.pack(fill="both", expand=True, padx=18, pady=(0, 18))
        state.update({"output": output, "preview_button": preview_button, "credits_script_button": credits_script_button})

        def load():
            try:
                path = item["path"]
                video_rows = self.api.request("GET", "/rest/v1/videos", params={"select": "path,name,folder_path,subtitle_path,preview_image_path,preview_image_updated_at", "path": f"eq.{path}", "limit": "1"}) or []
                video = video_rows[0] if video_rows else {"path": path, "name": item["name"], "folder_path": path.rpartition("/")[0]}
                credits_rows = self.api.request("GET", "/rest/v1/video_credits", params={"select": "media_path,credits_start_seconds,credits_end_seconds,confidence_score,detected_via,updated_at", "media_path": f"eq.{path}", "limit": "1"}) or []
                previews = self.api.request("GET", "/rest/v1/video_previews", params={"select": "media_path,sheets,duration_seconds,interval_seconds,columns,rows,updated_at", "media_path": f"eq.{path}", "limit": "1"}) or []
                data = {"video": video, "credits": credits_rows[0] if credits_rows else None, "preview": previews[0] if previews else None}
                image_path = video.get("preview_image_path") or ""
                if image_path:
                    try: data["image_bytes"] = self.api.download_object(image_path)
                    except Exception: data["image_bytes"] = None
                subtitle_path = video.get("subtitle_path") or ""
                if subtitle_path:
                    try: data["subtitle_content"] = self.api.download_object(subtitle_path).decode("utf-8", errors="replace")
                    except Exception as exc: data["subtitle_content"] = f"Unable to load subtitles: {exc}"
                self.after(0, lambda: self.media_loaded(state, data))
            except Exception as exc:
                self.after(0, lambda error=exc: self.media_load_failed(state, error))
        threading.Thread(target=load, daemon=True).start()

    @staticmethod
    def format_seconds(seconds: float) -> str:
        value = max(0, int(seconds)); return f"{value // 3600:02d}:{(value % 3600) // 60:02d}:{value % 60:02d}"

    @staticmethod
    def parse_timecode(value: str) -> float:
        parts = value.strip().split(":")
        if len(parts) != 3: raise ValueError("Use HH:MM:SS format")
        hours, minutes, seconds = int(parts[0]), int(parts[1]), float(parts[2])
        if hours < 0 or not 0 <= minutes < 60 or not 0 <= seconds < 60: raise ValueError("Invalid timecode")
        return hours * 3600 + minutes * 60 + seconds

    def media_load_failed(self, state, error):
        state["status"].configure(text=f"Unable to load media details: {error}")
        state["subtitle_loading"].stop(); state["subtitle_loading"].pack_forget()
        state["timeline_loading"].stop(); state["timeline_loading"].pack_forget()
        state["image_loading"].stop(); state["image_loading"].pack_forget(); state["image_preview"].configure(text="Unable to load image.")

    def media_loaded(self, state, data):
        state["data"] = data; video = data["video"]; state["status"].configure(text=state["item"]["path"])
        state["timeline_loading"].stop(); state["timeline_loading"].pack_forget()
        subtitle_path = video.get("subtitle_path") or ""
        state["subtitle_loading"].stop(); state["subtitle_loading"].pack_forget()
        state["subtitle_name"].configure(text=Path(subtitle_path).name if subtitle_path else "No subtitle file attached yet.")
        state["subtitle_text"].delete("1.0", "end"); state["subtitle_text"].insert("1.0", data.get("subtitle_content", ""))
        credit = data.get("credits") or {}; start = credit.get("credits_start_seconds")
        state["credits_input"].delete(0, "end");
        if start is not None: state["credits_input"].insert(0, self.format_seconds(float(start))); state["credits_time"].configure(text=self.format_seconds(float(start)))
        preview = data.get("preview") or {}; sheets = preview.get("sheets") or []
        if sheets:
            duration = float(preview.get("duration_seconds") or 0); interval = float(preview.get("interval_seconds") or 5); frame_count = max(1, int((duration + interval - 0.001) // interval)); state["timeline_info"].configure(text=f"{duration:.0f}s video Â· {interval:g}s per frame Â· {len(sheets)} spritesheet(s)")
            state["frames"].delete(0, "end")
            for frame in range(frame_count): state["frames"].insert("end", f"{self.format_seconds(frame * interval)}  Â·  spritesheet {min(frame // max(1, int(preview.get('columns', 1)) * int(preview.get('rows', 1))), len(sheets) - 1) + 1}")
        else: state["timeline_info"].configure(text="No timeline spritesheets are available yet.")
        image = video.get("preview_image_path") or ""
        state["image_path"].configure(text=Path(image).name if image else "No picture attached yet."); state["remove_image_button"].configure(state="normal" if image else "disabled")

        self.render_canvas_timeline(state, preview)
        state["image_loading"].stop(); state["image_loading"].pack_forget()
        state["image_photo"] = self.make_photo(data.get("image_bytes"), 820, 420)
        state["image_preview"].configure(image=state["image_photo"] or "", text="" if state["image_photo"] else ("Pillow is required to display this image." if image and Image is None else "No picture attached yet."))

    @staticmethod
    def make_photo(raw, max_width, max_height):
        if not raw or Image is None or ImageTk is None: return None
        try:
            image = Image.open(BytesIO(raw)).convert("RGB"); image.thumbnail((max_width, max_height), Image.Resampling.LANCZOS); return ImageTk.PhotoImage(image)
        except Exception: return None

    def render_timeline_images(self, state, preview, raw_sheets):
        for child in state["timeline_inner"].winfo_children(): child.destroy()
        state["timeline_images"] = []
        sheets = preview.get("sheets") or []
        if not sheets: return
        duration = float(preview.get("duration_seconds") or 0); interval = float(preview.get("interval_seconds") or 5); state["timeline_info"].configure(text=f"{duration:.0f}s video · {interval:g}s per frame · {len(sheets)} spritesheet(s)")
        columns = max(1, int(preview.get("columns", 1))); rows = max(1, int(preview.get("rows", 1)))
        strip = tk.Frame(state["timeline_inner"], bg="white", width=max(168, frame_count * 168), height=124); strip.pack(anchor="w", pady=(4, 12)); strip.pack_propagate(False); state["timeline_inner"].configure(width=max(168, frame_count * 168), height=140)
        frame_number = 0
        for raw in raw_sheets:
            if not raw: continue
            try: sheet = Image.open(BytesIO(raw)).convert("RGB")
            except Exception: continue
            cell_width, cell_height = sheet.width // columns, sheet.height // rows
            for row in range(rows):
                for column in range(columns):
                    if frame_number * interval >= duration and duration: break
                    left, top = column * cell_width, row * cell_height
                    cell = sheet.crop((left, top, left + cell_width, top + cell_height)); cell.thumbnail((160, 90), Image.Resampling.LANCZOS)
                    photo = ImageTk.PhotoImage(cell) if ImageTk else None; state["timeline_images"].append(photo)
                    holder = tk.Frame(strip, bg="#151515", width=160, height=112); holder.pack(side="left", padx=4); holder.pack_propagate(False)
                    if photo: tk.Label(holder, image=photo, bg="#151515").pack(side="top")
                    tk.Label(holder, text=self.format_seconds(frame_number * interval), bg="#151515", fg="white", font=("Consolas", 9)).pack(side="bottom", fill="x")
                    frame_number += 1
                if frame_number * interval >= duration and duration: break
        if not frame_number: tk.Label(state["timeline_inner"], text="Unable to display timeline previews.", bg="white", fg=self.MUTED).pack(pady=10)

    def render_canvas_timeline(self, state, preview):
        canvas = state["timeline_canvas"]; canvas.delete("all"); state.update({"timeline_cells": [], "timeline_sheet_bytes": {}, "timeline_loading_sheets": set()})
        sheets = preview.get("sheets") or []
        if not sheets: return
        duration = float(preview.get("duration_seconds") or 0); interval = float(preview.get("interval_seconds") or 5); columns = max(1, int(preview.get("columns", 1))); rows = max(1, int(preview.get("rows", 1))); frame_count = max(1, int((duration + interval - 0.001) // interval)); thumb_width, thumb_height, gap = 320, 180, 12; width = thumb_width + gap; height = thumb_height + 34
        display_scale = max(1.0, float(self.tk.call("tk", "scaling"))); state["timeline_info"].configure(text=f"{duration:.0f}s video · {interval:g}s per frame · {len(sheets)} spritesheet(s)"); state.update({"timeline_frame_count": frame_count, "timeline_sheet_capacity": columns * rows, "timeline_frame_width": width, "timeline_photo_size": (round(thumb_width * display_scale), round(thumb_height * display_scale))})
        canvas.delete(state["timeline_window"]); canvas.configure(scrollregion=(0, 0, frame_count * width, height))
        for frame_number in range(frame_count):
            sheet_index, cell = divmod(frame_number, columns * rows); row, column = divmod(cell, columns); x = frame_number * width
            canvas.create_rectangle(x, 0, x + thumb_width, thumb_height + 28, fill="#151515", outline="", tags="timeline_frame")
            image_item = canvas.create_image(x + thumb_width // 2, thumb_height // 2, anchor="center", tags="timeline_frame")
            text_item = canvas.create_text(x + thumb_width // 2, thumb_height + 12, text=self.format_seconds(frame_number * interval), fill="white", font=("Consolas", 10), tags="timeline_frame")
            state["timeline_cells"].append({"image_item": image_item, "text_item": text_item, "sheet_index": sheet_index, "row": row, "column": column})
        canvas.xview_moveto(0)
        self.after(50, lambda: self.load_visible_timeline_frames(state))

    def render_lazy_timeline(self, state, preview):
        for child in state["timeline_inner"].winfo_children(): child.destroy()
        state.update({"timeline_cells": [], "timeline_sheet_bytes": {}, "timeline_loading_sheets": set()})
        sheets = preview.get("sheets") or []
        if not sheets: return
        duration = float(preview.get("duration_seconds") or 0); interval = float(preview.get("interval_seconds") or 5); columns = max(1, int(preview.get("columns", 1))); rows = max(1, int(preview.get("rows", 1))); frame_count = max(1, int((duration + interval - 0.001) // interval))
        state["timeline_info"].configure(text=f"{duration:.0f}s video · {interval:g}s per frame · {len(sheets)} spritesheet(s)")
        state.update({"timeline_frame_count": frame_count, "timeline_sheet_capacity": columns * rows, "timeline_frame_width": 168})
        strip = tk.Frame(state["timeline_inner"], bg="white"); strip.pack(anchor="w", pady=(4, 12))
        for frame_number in range(frame_count):
            sheet_index, cell = divmod(frame_number, columns * rows); row, column = divmod(cell, columns); holder = tk.Frame(strip, bg="#151515", width=160, height=112); holder.pack(side="left", padx=4); holder.pack_propagate(False)
            image_label = tk.Label(holder, text="Loading", bg="#151515", fg="#98a2b3", width=20, height=5); image_label.pack(side="top", fill="both", expand=True); tk.Label(holder, text=self.format_seconds(frame_number * interval), bg="#151515", fg="white", font=("Consolas", 9)).pack(side="bottom", fill="x")
            state["timeline_cells"].append({"holder": holder, "image_label": image_label, "sheet_index": sheet_index, "row": row, "column": column})
        self.after(50, lambda: self.load_visible_timeline_frames(state))

    def scroll_timeline(self, state, *args):
        state["timeline_canvas"].xview(*args); self.load_visible_timeline_frames(state); self.after(120, lambda: self.load_visible_timeline_frames(state))

    def load_visible_timeline_frames(self, state):
        if not state.get("timeline_cells"): return
        canvas = state["timeline_canvas"]; viewport = max(332, canvas.winfo_width()); region = [float(value) for value in str(canvas.cget("scrollregion")).split()]; content_width = max(viewport, region[2] - region[0]) if len(region) == 4 else viewport; scrollable_width = max(0, content_width - viewport); left = canvas.xview()[0] * scrollable_width; frame_width = state["timeline_frame_width"]; capacity = state["timeline_sheet_capacity"]
        first_frame = max(0, int(left // frame_width) - 2); last_frame = min(state["timeline_frame_count"] - 1, int((left + viewport) // frame_width) + 2)
        first_sheet = max(0, first_frame // capacity); last_sheet = last_frame // capacity
        for sheet_index in range(first_sheet, last_sheet + 1): self.load_timeline_sheet(state, sheet_index)

    def load_timeline_sheet(self, state, sheet_index):
        if sheet_index in state["timeline_sheet_bytes"] or sheet_index in state["timeline_loading_sheets"]: return
        sheets = state["data"].get("preview", {}).get("sheets", [])
        if sheet_index >= len(sheets): return
        state["timeline_loading_sheets"].add(sheet_index)
        state["timeline_info"].configure(text=f"Loading timeline sheet {sheet_index + 1} of {len(sheets)}…")
        def worker():
            error = None
            for attempt in range(3):
                try:
                    raw = self.api.download_object(sheets[sheet_index]); self.after(0, lambda: self.timeline_sheet_loaded(state, sheet_index, raw)); return
                except Exception as exc:
                    error = exc
                    if attempt < 2: time.sleep(1)
            self.after(0, lambda: self.timeline_sheet_failed(state, sheet_index, error))
        threading.Thread(target=worker, daemon=True).start()

    def timeline_sheet_failed(self, state, sheet_index, error):
        state["timeline_loading_sheets"].discard(sheet_index)
        state["timeline_info"].configure(text=f"Unable to load timeline sheet {sheet_index + 1}: {error}")
        for cell in state["timeline_cells"]:
            if cell["sheet_index"] == sheet_index: state["timeline_canvas"].itemconfigure(cell["text_item"], text="Unable to load")

    def timeline_sheet_loaded(self, state, sheet_index, raw):
        state["timeline_loading_sheets"].discard(sheet_index); state["timeline_sheet_bytes"][sheet_index] = raw
        preview = state["data"]["preview"]; state["timeline_info"].configure(text=f"{float(preview.get('duration_seconds') or 0):.0f}s video · {float(preview.get('interval_seconds') or 5):g}s per frame · {len(preview.get('sheets') or [])} spritesheet(s)")
        if Image is None or ImageTk is None: return
        try: sheet = Image.open(BytesIO(raw)).convert("RGB")
        except Exception: return
        columns = max(1, int(preview.get("columns", 1))); rows = max(1, int(preview.get("rows", 1))); cell_width, cell_height = sheet.width // columns, sheet.height // rows
        for cell in state["timeline_cells"]:
            if cell["sheet_index"] != sheet_index: continue
            image = sheet.crop((cell["column"] * cell_width, cell["row"] * cell_height, (cell["column"] + 1) * cell_width, (cell["row"] + 1) * cell_height)); image = image.resize(state["timeline_photo_size"], Image.Resampling.LANCZOS); photo = ImageTk.PhotoImage(image); cell["photo"] = photo; state["timeline_canvas"].itemconfigure(cell["image_item"], image=photo)

    def save_media_subtitle(self, state, text, button, name_label):
        content = text.get("1.0", "end-1c"); video = state["data"].get("video", {}); current = video.get("subtitle_path") or ""; folder = video.get("folder_path") or state["item"]["path"].rpartition("/")[0]; stem = Path(video.get("name") or state["item"]["name"]).stem; target = current or (f"{folder}/{stem}.srt" if folder else f"{stem}.srt")
        button.configure(state="disabled", text="Savingâ€¦")
        def worker():
            try:
                self.api.put_object(target, content.encode("utf-8"), "text/plain; charset=utf-8"); self.api.request("PATCH", "/rest/v1/videos", params={"path": f"eq.{state['item']['path']}"}, body={"subtitle_path": target}); self.after(0, lambda: (name_label.configure(text=Path(target).name), button.configure(state="normal", text="Saved")))
            except Exception as exc: self.after(0, lambda error=exc: (button.configure(state="normal", text="Save subtitles"), messagebox.showerror("Subtitle save failed", str(error), parent=state["window"])))
        threading.Thread(target=worker, daemon=True).start()

    def save_media_credits(self, state, entry, button):
        try: start = self.parse_timecode(entry.get())
        except ValueError: messagebox.showerror("Invalid timestamp", "Enter the timestamp as HH:MM:SS.", parent=state["window"]); return
        preview = state["data"].get("preview") or {}; duration = preview.get("duration_seconds")
        if start < 0 or (duration is not None and start > float(duration)): messagebox.showerror("Invalid timestamp", "The credits timestamp must be inside the video duration.", parent=state["window"]); return
        button.configure(state="disabled", text="Savingâ€¦")
        def worker():
            try:
                existing = state["data"].get("credits") or {}; end = existing.get("credits_end_seconds") if existing.get("credits_end_seconds") is not None else duration; row = {"media_path": state["item"]["path"], "credits_start_seconds": start, "credits_end_seconds": end, "detected_via": "manual", "updated_at": datetime.now(timezone.utc).isoformat()}; method = "PATCH" if existing else "POST"; params = {"media_path": f"eq.{state['item']['path']}"} if existing else {}; self.api.request(method, "/rest/v1/video_credits", params=params, body=row); self.after(0, lambda: (button.configure(state="normal", text="Saved"), state["credits_time"].configure(text=self.format_seconds(start))))
            except Exception as exc: self.after(0, lambda error=exc: (button.configure(state="normal", text="Save timestamp"), messagebox.showerror("Timestamp save failed", str(error), parent=state["window"])))
        threading.Thread(target=worker, daemon=True).start()

    def choose_media_picture(self, state):
        file_name = filedialog.askopenfilename(title="Choose picture", filetypes=[("Images", "*.jpg *.jpeg *.png *.webp *.gif *.avif *.bmp")], parent=state["window"])
        if not file_name: return
        local_path = Path(file_name); video = state["data"].get("video", {}); folder = video.get("folder_path") or state["item"]["path"].rpartition("/")[0]; stem = Path(video.get("name") or state["item"]["name"]).stem; target = f"{folder}/{stem}{local_path.suffix.lower()}" if folder else f"{stem}{local_path.suffix.lower()}"
        def worker():
            try:
                old = video.get("preview_image_path") or "";
                if old: self.api.storage_delete([old])
                self.api.upload_file(local_path, target, lambda *_: None); self.after(0, lambda: (state["image_path"].configure(text=local_path.name), state["remove_image_button"].configure(state="normal")))
            except Exception as exc: self.after(0, lambda error=exc: messagebox.showerror("Picture save failed", str(error), parent=state["window"]))
        threading.Thread(target=worker, daemon=True).start()

    def remove_media_picture(self, state):
        path = state["data"].get("video", {}).get("preview_image_path") or ""
        if not path: return
        if not messagebox.askyesno("Remove picture", "Remove this video's picture?", parent=state["window"]): return
        def worker():
            try: self.api.storage_delete([path]); self.after(0, lambda: (state["image_path"].configure(text="No picture attached yet."), state["remove_image_button"].configure(state="disabled")))
            except Exception as exc: self.after(0, lambda error=exc: messagebox.showerror("Picture removal failed", str(error), parent=state["window"]))
        threading.Thread(target=worker, daemon=True).start()

    def run_media_script(self, state, kind, button):
        video_path = state["item"]["path"]; script = APP_DIR / ("preview_generator.py" if kind == "previews" else "credit_detector.py"); command = [sys.executable, str(script), "--supabase-url", self.api.url, "--supabase-key", self.api.key, "--bucket", self.api.bucket, "--video", video_path]
        if kind == "previews" and state["data"].get("preview"): command.append("--force")
        output = state["output"]; button.configure(state="disabled"); output.configure(state="normal"); output.delete("1.0", "end"); output.insert("end", f"$ {' '.join(command)}\n\n"); output.configure(state="disabled")
        environment = os.environ.copy()
        def worker():
            try:
                process = subprocess.Popen(command, cwd=str(APP_DIR), env=environment, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
                assert process.stdout is not None
                for line in process.stdout:
                    self.after(0, lambda text=line: self.append_script_output(output, text))
                code = process.wait(); self.after(0, lambda: self.script_finished(state, button, code))
            except Exception as exc: self.after(0, lambda error=exc: (self.append_script_output(output, f"\n{error}\n"), button.configure(state="normal")))
        threading.Thread(target=worker, daemon=True).start()

    @staticmethod
    def append_script_output(output, text):
        output.configure(state="normal"); output.insert("end", text); output.see("end"); output.configure(state="disabled")

    def script_finished(self, state, button, code):
        button.configure(state="normal"); self.append_script_output(state["output"], f"\nFinished with exit code {code}.\n")
        if code == 0: self.load_media_manager_data(state)

    def load_media_manager_data(self, state):
        item = state["item"]; state["window"].destroy(); self.open_media_manager(item)


