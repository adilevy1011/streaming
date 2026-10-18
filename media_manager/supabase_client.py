from __future__ import annotations

import base64
import json
import mimetypes
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

class SupabaseError(RuntimeError):
    pass

class UploadPaused(RuntimeError):
    pass

class UploadCancelled(RuntimeError):
    pass


class Supabase:
    def __init__(self, config: dict[str, str]):
        self.url = config["SUPABASE_URL"].rstrip("/")
        self.key = config.get("SUPABASE_SECRET_KEY") or config.get("SUPABASE_SERVICE_ROLE_KEY", "")
        self.bucket = config.get("MEDIA_BUCKET", "media")
        self.storage_url = config.get("SUPABASE_STORAGE_URL", "").rstrip("/") or self._default_storage_url()

    def _default_storage_url(self) -> str:
        if self.url.endswith(".supabase.co"):
            return self.url[:-len(".supabase.co")] + ".storage.supabase.co"
        return self.url

    def request(self, method: str, path: str, *, params: dict[str, str] | None = None, body: object | None = None) -> object:
        if params:
            path += "?" + urlencode(params)
        payload = None if body is None else json.dumps(body).encode("utf-8")
        request = Request(
            self.url + path,
            data=payload,
            method=method,
            headers={
                "apikey": self.key,
                "Authorization": f"Bearer {self.key}",
                "Content-Type": "application/json",
                "Prefer": "return=representation",
            },
        )
        try:
            with urlopen(request, timeout=30) as response:
                raw = response.read()
        except (HTTPError, URLError) as exc:
            detail = getattr(exc, "read", lambda: b"")()
            try:
                message = json.loads(detail.decode("utf-8")).get("message") or json.loads(detail.decode("utf-8")).get("hint")
            except Exception:
                message = str(exc)
            raise SupabaseError(str(message or exc)) from exc
        if not raw:
            return None
        return json.loads(raw.decode("utf-8"))

    def storage_request(self, method: str, path: str, *, body: object | None = None) -> object:
        payload = None if body is None else json.dumps(body).encode("utf-8")
        request = Request(
            self.url + path,
            data=payload,
            method=method,
            headers={"apikey": self.key, "Authorization": f"Bearer {self.key}", "Content-Type": "application/json"},
        )
        try:
            with urlopen(request, timeout=60) as response:
                raw = response.read()
        except (HTTPError, URLError) as exc:
            detail = getattr(exc, "read", lambda: b"")()
            try:
                parsed = json.loads(detail.decode("utf-8"))
                message = parsed.get("message") or parsed.get("error") or parsed.get("statusCode")
            except Exception:
                message = str(exc)
            raise SupabaseError(str(message or exc)) from exc
        return json.loads(raw.decode("utf-8")) if raw else None

    def storage_list(self, folder: str) -> list[dict]:
        return list(self.storage_request("POST", f"/storage/v1/object/list/{quote(self.bucket, safe='')}", body={
            "prefix": folder.strip("/"), "limit": 1000, "offset": 0, "sortBy": {"column": "name", "order": "asc"},
        }) or [])

    def storage_move(self, source: str, destination: str) -> None:
        self.storage_request("POST", "/storage/v1/object/move", body={"bucketId": self.bucket, "sourceKey": source, "destinationKey": destination})

    def storage_delete(self, paths: list[str]) -> None:
        self.storage_request("DELETE", f"/storage/v1/object/{quote(self.bucket, safe='')}", body={"prefixes": paths})

    def create_folder(self, placeholder: str) -> None:
        request = Request(
            f"{self.url}/storage/v1/object/{quote(self.bucket, safe='')}/{quote(placeholder, safe='/')}",
            data=b"",
            method="POST",
            headers={"apikey": self.key, "Authorization": f"Bearer {self.key}", "Content-Type": "application/octet-stream", "Content-Length": "0", "x-upsert": "false"},
        )
        try:
            with urlopen(request, timeout=60):
                pass
        except (HTTPError, URLError) as exc:
            raise SupabaseError(f"Folder creation failed: {exc}") from exc

    def object_request(self, method: str, path: str, *, data: bytes = b"", content_type: str = "application/octet-stream") -> bytes:
        request = Request(
            f"{self.url}/storage/v1/object/{quote(self.bucket, safe='')}/{quote(path, safe='/')}",
            data=data,
            method=method,
            headers={"apikey": self.key, "Authorization": f"Bearer {self.key}", "Content-Type": content_type, "x-upsert": "true"},
        )
        try:
            with urlopen(request, timeout=60) as response:
                return response.read()
        except (HTTPError, URLError) as exc:
            raise SupabaseError(f"Storage request failed: {exc}") from exc

    def download_object(self, path: str) -> bytes:
        return self.object_request("GET", path)

    def put_object(self, path: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        self.object_request("PUT", path, data=data, content_type=content_type)

    def upload_file(self, local_path: Path, destination: str, progress, should_pause=None, should_cancel=None, upload_state=None) -> None:
        size = local_path.stat().st_size
        content_type = mimetypes.guess_type(local_path.name)[0] or "application/octet-stream"
        upload_state = upload_state if upload_state is not None else {}
        location = upload_state.get("location")
        offset = int(upload_state.get("offset", 0))
        if not location:
            metadata = ",".join(
                f"{key} {base64.b64encode(value.encode()).decode()}"
                for key, value in (("bucketName", self.bucket), ("objectName", destination), ("contentType", content_type))
            )
            start = Request(
                f"{self.storage_url}/storage/v1/upload/resumable",
                method="POST",
                headers={"apikey": self.key, "Authorization": f"Bearer {self.key}", "Tus-Resumable": "1.0.0", "Upload-Length": str(size), "Upload-Metadata": metadata, "x-upsert": "true"},
            )
            try:
                with urlopen(start, timeout=60) as response:
                    location = response.headers.get("Location")
            except (HTTPError, URLError) as exc:
                raise SupabaseError(f"Unable to start upload: {exc}") from exc
            if not location:
                raise SupabaseError("Storage did not return an upload location.")
            if location.startswith("/"):
                location = self.storage_url + location
            upload_state["location"] = location
        upload_state["offset"] = offset
        chunk_size = 6 * 1024 * 1024
        with local_path.open("rb") as source:
            source.seek(offset)
            while offset < size:
                if should_cancel and should_cancel(): raise UploadCancelled()
                if should_pause and should_pause():
                    raise UploadPaused()
                chunk = source.read(chunk_size)
                request = Request(location, data=chunk, method="PATCH", headers={"apikey": self.key, "Authorization": f"Bearer {self.key}", "Tus-Resumable": "1.0.0", "Upload-Offset": str(offset), "Content-Type": "application/offset+octet-stream", "Content-Length": str(len(chunk))})
                try:
                    with urlopen(request, timeout=300) as response:
                        offset = int(response.headers.get("Upload-Offset", offset + len(chunk)))
                except (HTTPError, URLError) as exc:
                    raise SupabaseError(f"Upload failed: {exc}") from exc
                upload_state["offset"] = offset
                progress(offset, size)



