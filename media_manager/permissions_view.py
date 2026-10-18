from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

class PermissionsViewMixin:
    def build_permissions_page(self):
        wrap = tk.Frame(self.content, bg=self.BG)
        wrap.pack(fill="both", expand=True)
        left = self.card(wrap); left.pack(side="left", fill="y", padx=(0, 14)); left.configure(width=290); left.pack_propagate(False)
        tk.Label(left, text="People", bg="white", fg="#101828", font=("Segoe UI", 13, "bold")).pack(anchor="w", padx=18, pady=(18, 3))
        tk.Label(left, text="Choose a person to manage access", bg="white", fg=self.MUTED, font=("Segoe UI", 9)).pack(anchor="w", padx=18, pady=(0, 12))
        self.user_search = ttk.Entry(left); self.user_search.pack(fill="x", padx=14, pady=(0, 10)); self.user_search.insert(0, "Search people")
        self.user_search.bind("<FocusIn>", lambda _: self.user_search.delete(0, "end") if self.user_search.get() == "Search people" else None)
        self.user_search.bind("<KeyRelease>", lambda _: self.render_users())
        self.user_list = tk.Listbox(left, relief="flat", bd=0, activestyle="none", bg="white", selectbackground="#dbeafe", selectforeground="#17212b", font=("Segoe UI", 10), height=20)
        self.user_list.pack(fill="both", expand=True, padx=8, pady=(0, 12)); self.user_list.bind("<<ListboxSelect>>", self.user_selected)
        right = self.card(wrap); right.pack(side="left", fill="both", expand=True)
        top = tk.Frame(right, bg="white"); top.pack(fill="x", padx=22, pady=(18, 8))
        self.permission_heading = tk.Label(top, text="Select a person", bg="white", fg="#101828", font=("Segoe UI", 15, "bold")); self.permission_heading.pack(side="left")
        self.new_access_var = tk.BooleanVar(value=self.draft_new_access if self.selected_user else True)
        self.new_access_check = tk.Checkbutton(
            right,
            text="Automatically allow new videos",
            variable=self.new_access_var,
            command=self.new_access_changed,
            bg="white",
            activebackground="white",
            anchor="w",
            cursor="hand2",
        )
        self.new_access_check.pack(anchor="w", padx=18, pady=(4, 14))
        bar = tk.Frame(right, bg="white"); bar.pack(fill="x", padx=22, pady=(0, 10))
        self.video_search = ttk.Entry(bar); self.video_search.pack(side="left", fill="x", expand=True); self.video_search.insert(0, "Filter videos")
        self.video_search.bind("<FocusIn>", lambda _: self.video_search.delete(0, "end") if self.video_search.get() == "Filter videos" else None); self.video_search.bind("<KeyRelease>", lambda _: self.render_videos())
        ttk.Button(bar, text="Allow all", command=lambda: self.set_all(True)).pack(side="left", padx=(10, 4)); ttk.Button(bar, text="Clear all", command=lambda: self.set_all(False)).pack(side="left")
        self.video_list = tk.Frame(right, bg="white"); self.video_list.pack(fill="both", expand=True, padx=14, pady=(0, 14))
        self.render_users(); self.render_videos()

    def render_users(self):
        if not hasattr(self, "user_list"): return
        query = self.user_search.get().lower() if self.user_search.get() != "Search people" else ""
        self.user_list.delete(0, "end")
        self.visible_users = [u for u in self.users if query in u.email.lower()]
        for user in self.visible_users: self.user_list.insert("end", user.email)
        if self.selected_user in self.visible_users: self.user_list.selection_set(self.visible_users.index(self.selected_user))

    def user_selected(self, _=None):
        selection = self.user_list.curselection()
        if selection: self.select_user(self.visible_users[selection[0]])

    def select_user(self, user: User):
        self.cache_current_permissions()
        saved = self.permission_drafts.get(user.user_id)
        self.selected_user = user
        if saved:
            self.draft_access = {key: list(value) for key, value in saved["access"].items()}
            self.original_access = {key: list(value) for key, value in saved["original"].items()}
            self.draft_new_access, self.original_new_access = saved["new_access"], saved["original_new_access"]
        else:
            self.draft_access = {v["path"]: list(v.get("user_access") or []) for v in self.videos}
            self.original_access = {k: list(v) for k, v in self.draft_access.items()}
            self.draft_new_access = self.original_new_access = user.new_videos_access
        if hasattr(self, "permission_heading"): self.permission_heading.configure(text=user.email); self.new_access_var.set(self.draft_new_access); self.render_users(); self.render_videos()
        self.update_save_button()

    def cache_current_permissions(self):
        if not self.selected_user: return
        self.permission_drafts[self.selected_user.user_id] = {
            "access": {key: list(value) for key, value in self.draft_access.items()},
            "original": {key: list(value) for key, value in self.original_access.items()},
            "new_access": self.draft_new_access,
            "original_new_access": self.original_new_access,
        }

    def video_allowed(self, video):
        access = self.draft_access.get(video["path"], [])
        return self.draft_new_access if not access else self.selected_user.email.casefold() in [x.casefold() for x in access]

    def render_videos(self):
        if not hasattr(self, "video_list"): return
        for item in self.video_list.winfo_children(): item.destroy()
        query = self.video_search.get().lower() if self.video_search.get() != "Filter videos" else ""
        for video in self.videos:
            if query and query not in video.get("path", "").lower(): continue
            path = video.get("path", ""); name = video.get("name") or Path(path).stem
            row = tk.Frame(self.video_list, bg="white", highlightthickness=1, highlightbackground="#eaecf0"); row.pack(fill="x", pady=2)
            allowed = tk.BooleanVar(value=self.video_allowed(video))
            toggle = tk.Checkbutton(row, variable=allowed, bg="white", activebackground="white", cursor="hand2", command=lambda item=video, value=allowed: self.set_video_toggle(item, value.get()))
            toggle.pack(side="left", padx=(10, 6), pady=7)
            tk.Label(row, text=name, bg="white", fg="#101828", anchor="w").pack(side="left", fill="x", expand=True)
            tk.Label(row, text=path, bg="white", fg=self.MUTED, anchor="e").pack(side="right", padx=12)

    def set_video_toggle(self, video, checked):
        if not self.selected_user: return
        email = self.selected_user.email.casefold(); access = list(self.draft_access.get(video["path"], []))
        if checked: self.draft_access[video["path"]] = [] if not access and self.draft_new_access else (access if email in [item.casefold() for item in access] else access + [email])
        else: self.draft_access[video["path"]] = [item for item in access if item.casefold() != email] if access else [user.email.casefold() for user in self.users if user.email.casefold() != email]
        self.status.configure(text="Unsaved changes"); self.render_videos(); self.update_save_button()

    def new_access_changed(self):
        self.draft_new_access = self.new_access_var.get(); self.render_videos(); self.status.configure(text="Unsaved changes"); self.update_save_button()

    def set_all(self, allowed: bool):
        if not self.selected_user: return
        email = self.selected_user.email.casefold()
        for video in self.videos:
            current = self.draft_access.get(video["path"], [])
            if allowed: self.draft_access[video["path"]] = [] if self.draft_new_access else [email]
            else: self.draft_access[video["path"]] = [u.email.casefold() for u in self.users if u.email.casefold() != email] or [email]
        self.render_videos(); self.status.configure(text="Unsaved changes"); self.update_save_button()


