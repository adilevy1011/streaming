"""Editable storage-directory combobox with folder-aware suggestions."""

from __future__ import annotations

import threading
from tkinter import ttk


class DirectoryAutocomplete(ttk.Combobox):
    """Suggest storage folders based on the path currently being typed."""

    def __init__(self, parent, *, api, initial_values=(), **kwargs):
        self._api = api
        self._known_values = {value.strip("/") for value in initial_values}
        self._known_values.add("")
        self._request_id = 0
        self._pending_refresh = None
        # Keep this explicitly editable even when a caller supplies other
        # combobox options. Suggestions should never turn the field readonly.
        kwargs["state"] = "normal"
        super().__init__(parent, values=sorted(self._known_values), **kwargs)
        self.bind("<KeyRelease>", self._schedule_refresh, add="+")

    def _schedule_refresh(self, _event=None):
        if self._pending_refresh is not None:
            self.after_cancel(self._pending_refresh)
        self._pending_refresh = self.after(120, self._refresh_suggestions)

    def _refresh_suggestions(self):
        self._pending_refresh = None
        query = self.get().strip().lstrip("/")
        parent, separator, fragment = query.rpartition("/")
        fragment = fragment.casefold()
        known = self._filter_values(parent, fragment)
        self.configure(values=known)

        self._request_id += 1
        request_id = self._request_id

        def worker():
            try:
                entries = self._api.storage_list(parent)
                folders = {
                    f"{parent}/{entry.get('name')}" if parent else entry.get("name")
                    for entry in entries
                    if entry.get("id") is None
                    and entry.get("name")
                    and entry.get("name") != ".emptyFolderPlaceholder"
                }
            except Exception:
                folders = set()
            self.after(0, lambda: self._apply_remote_suggestions(request_id, query, folders))

        threading.Thread(target=worker, daemon=True).start()

    def _filter_values(self, parent, fragment):
        prefix = f"{parent}/" if parent else ""
        return sorted(
            value for value in self._known_values
            if value.startswith(prefix)
            and value[len(prefix):].casefold().startswith(fragment)
            and "/" not in value[len(prefix):]
        )

    def _apply_remote_suggestions(self, request_id, query, folders):
        if request_id != self._request_id or self.get().strip().lstrip("/") != query:
            return
        self._known_values.update(folders)
        parent, _, fragment = query.rpartition("/")
        values = self._filter_values(parent, fragment.casefold())
        values = sorted(set(values) | {value for value in folders if value.casefold().startswith(query.casefold())})
        self.configure(values=values)
        if values and self.focus_get() is self and query:
            self.tk.call("ttk::combobox::Post", str(self))
