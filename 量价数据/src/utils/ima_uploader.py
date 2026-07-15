"""Upload processed Markdown files to an IMA knowledge base.

Flow: duplicate check -> create_media -> signed COS PUT -> add_knowledge.
Credentials are read from environment variables and are never persisted.
"""

from __future__ import annotations

import hashlib
import hmac
import http.client
import json
import logging
import os
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Callable


class IMAUploadError(RuntimeError):
    """Raised when an IMA or COS upload operation fails."""


class IMAUploader:
    BASE_URL = "https://ima.qq.com/openapi/wiki/v1"
    MEDIA_TYPE_MARKDOWN = 7

    def __init__(
        self,
        root: str | Path,
        client_id: str,
        api_key: str,
        knowledge_base_id: str,
        folder_map: dict[str, str] | None = None,
        timeout: int = 30,
        max_markdown_bytes: int = 10 * 1024 * 1024,
        logger: logging.Logger | None = None,
        api_caller: Callable[[str, dict], dict] | None = None,
        cos_uploader: Callable[[Path, dict, str], None] | None = None,
        now: Callable[[], datetime] | None = None,
    ):
        if not client_id or not api_key or not knowledge_base_id:
            raise ValueError("IMA client ID, API key, and knowledge base ID are required")
        self.root = Path(root)
        self.client_id = client_id
        self.api_key = api_key
        self.knowledge_base_id = knowledge_base_id
        self.folder_map = folder_map or {}
        for key, value in self.folder_map.items():
            if not isinstance(key, str) or not isinstance(value, str) or not value.strip():
                raise ValueError("IMA folder map keys and folder IDs must be non-empty strings")
        self.timeout = int(timeout)
        self.max_markdown_bytes = int(max_markdown_bytes)
        self.logger = logger or logging.getLogger("knowledge_base.ima_uploader")
        self.api_caller = api_caller or self._call_api
        self.cos_uploader = cos_uploader or self._upload_cos
        self.now = now or datetime.now

    @staticmethod
    def is_enabled() -> bool:
        return os.environ.get("IMA_UPLOAD_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}

    @classmethod
    def from_environment(cls, config: dict, logger: logging.Logger | None = None) -> "IMAUploader":
        client_id = os.environ.get("IMA_OPENAPI_CLIENT_ID", "").strip()
        api_key = os.environ.get("IMA_OPENAPI_API_KEY", "").strip()
        knowledge_base_id = os.environ.get("IMA_KNOWLEDGE_BASE_ID", "").strip()
        knowledge_base_name = os.environ.get("IMA_KNOWLEDGE_BASE_NAME", "").strip()
        missing = [
            name
            for name, value in (
                ("IMA_OPENAPI_CLIENT_ID", client_id),
                ("IMA_OPENAPI_API_KEY", api_key),
            )
            if not value
        ]
        if missing:
            raise IMAUploadError(f"missing IMA environment variables: {', '.join(missing)}")
        if not knowledge_base_id and not knowledge_base_name:
            raise IMAUploadError("set IMA_KNOWLEDGE_BASE_ID or IMA_KNOWLEDGE_BASE_NAME")
        raw_map = os.environ.get("IMA_FOLDER_MAP_JSON", "").strip()
        try:
            folder_map = json.loads(raw_map) if raw_map else {}
        except json.JSONDecodeError as exc:
            raise IMAUploadError(f"IMA_FOLDER_MAP_JSON is not valid JSON: {exc}") from exc
        if not isinstance(folder_map, dict):
            raise IMAUploadError("IMA_FOLDER_MAP_JSON must be a JSON object")
        uploader = cls(
            root=config["root"],
            client_id=client_id,
            api_key=api_key,
            knowledge_base_id=knowledge_base_id or "pending-name-resolution",
            folder_map=folder_map,
            timeout=int(config.get("ima_upload_timeout_seconds", 30)),
            max_markdown_bytes=int(config.get("ima_max_markdown_bytes", 10 * 1024 * 1024)),
            logger=logger,
        )
        if not knowledge_base_id:
            matches = []
            cursor = ""
            while True:
                data = uploader.api_caller(
                    "get_addable_knowledge_base_list",
                    {"cursor": cursor, "limit": 50},
                )
                matches.extend(
                    item for item in data.get("addable_knowledge_base_list", [])
                    if item.get("name") == knowledge_base_name and item.get("id")
                )
                if data.get("is_end", True):
                    break
                cursor = data.get("next_cursor", "")
                if not cursor:
                    break
            if len(matches) != 1:
                raise IMAUploadError(
                    f"expected exactly one addable knowledge base named '{knowledge_base_name}', found {len(matches)}"
                )
            uploader.knowledge_base_id = matches[0]["id"]
        return uploader

    def _call_api(self, endpoint: str, payload: dict) -> dict:
        request = urllib.request.Request(
            f"{self.BASE_URL}/{endpoint}",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "ima-openapi-clientid": self.client_id,
                "ima-openapi-apikey": self.api_key,
                "User-Agent": "a-share-knowledge-base/1.0",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                result = json.load(response)
        except Exception as exc:
            raise IMAUploadError(f"IMA {endpoint} request failed: {exc}") from exc
        status_code = result.get("retcode") if "retcode" in result else result.get("code")
        if status_code != 0:
            message = result.get("errmsg") or result.get("msg") or "unknown error"
            raise IMAUploadError(f"IMA {endpoint} failed: {message}")
        data = result.get("data")
        if not isinstance(data, dict):
            raise IMAUploadError(f"IMA {endpoint} returned invalid data")
        return data

    @staticmethod
    def _hmac_sha1(key: str, data: str) -> str:
        return hmac.new(key.encode("utf-8"), data.encode("utf-8"), hashlib.sha1).hexdigest()

    @staticmethod
    def _sha1(data: str) -> str:
        return hashlib.sha1(data.encode("utf-8")).hexdigest()

    def _upload_cos(self, file_path: Path, credential: dict, content_type: str) -> None:
        required = ("secret_id", "secret_key", "token", "bucket_name", "region", "cos_key")
        missing = [key for key in required if not credential.get(key)]
        if missing:
            raise IMAUploadError(f"COS credential missing fields: {', '.join(missing)}")
        body = file_path.read_bytes()
        hostname = f"{credential['bucket_name']}.cos.{credential['region']}.myqcloud.com"
        pathname = "/" + str(credential["cos_key"]).lstrip("/")
        current = int(time.time())
        start_time = str(credential.get("start_time") or current)
        expired_time = str(credential.get("expired_time") or current + 3600)
        key_time = f"{start_time};{expired_time}"
        signed_headers = {"content-length": str(len(body)), "host": hostname}
        header_keys = sorted(signed_headers)
        http_headers = "&".join(
            f"{key}={urllib.parse.quote(signed_headers[key], safe='~()*!\'')}" for key in header_keys
        )
        http_string = f"put\n{pathname}\n\n{http_headers}\n"
        sign_key = self._hmac_sha1(str(credential["secret_key"]), key_time)
        string_to_sign = f"sha1\n{key_time}\n{self._sha1(http_string)}\n"
        signature = self._hmac_sha1(sign_key, string_to_sign)
        authorization = "&".join(
            (
                "q-sign-algorithm=sha1",
                f"q-ak={credential['secret_id']}",
                f"q-sign-time={key_time}",
                f"q-key-time={key_time}",
                f"q-header-list={';'.join(header_keys)}",
                "q-url-param-list=",
                f"q-signature={signature}",
            )
        )
        connection = http.client.HTTPSConnection(hostname, 443, timeout=self.timeout)
        try:
            connection.request(
                "PUT",
                pathname,
                body=body,
                headers={
                    "Content-Type": content_type,
                    "Content-Length": str(len(body)),
                    "Authorization": authorization,
                    "x-cos-security-token": str(credential["token"]),
                },
            )
            response = connection.getresponse()
            response_body = response.read().decode("utf-8", errors="replace")
            if not 200 <= response.status < 300:
                raise IMAUploadError(f"COS upload failed with HTTP {response.status}: {response_body[:500]}")
        finally:
            connection.close()

    def _state_path(self, source: str) -> Path:
        return self.root / "data" / "state" / "uploads" / "ima" / f"{source}.json"

    def _load_state(self, source: str) -> dict:
        path = self._state_path(source)
        if not path.exists():
            return {"source": source, "folders": {}, "uploads": {}}
        state = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(state.get("uploads"), dict):
            raise IMAUploadError(f"invalid IMA upload state: {path}")
        state.setdefault("folders", {})
        if not isinstance(state["folders"], dict):
            raise IMAUploadError(f"invalid IMA folder state: {path}")
        return state

    def _write_state(self, source: str, state: dict) -> None:
        path = self._state_path(source)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)

    def _folder_for(self, ts_code: str, event_date: str) -> str | None:
        return (
            self.folder_map.get(f"{ts_code}/{event_date}")
            or self.folder_map.get(ts_code)
            or self.folder_map.get(event_date)
            or self.folder_map.get("*")
        )

    def _list_child_folders(self, parent_folder_id: str | None) -> list[dict]:
        folders: list[dict] = []
        cursor = ""
        while True:
            payload = {"knowledge_base_id": self.knowledge_base_id, "cursor": cursor, "limit": 50}
            if parent_folder_id:
                payload["folder_id"] = parent_folder_id
            data = self.api_caller("get_knowledge_list", payload)
            for item in data.get("knowledge_list", []):
                media_id = item.get("media_id")
                is_folder = (
                    item.get("media_type") == 99
                    or bool(item.get("folder_id"))
                    or (isinstance(media_id, str) and media_id.startswith("folder_"))
                    or item.get("file_number") is not None
                    or item.get("folder_number") is not None
                )
                folder_id = item.get("folder_id") or (media_id if is_folder else None)
                name = item.get("name") or item.get("title")
                if folder_id and name:
                    folders.append({"folder_id": str(folder_id), "name": str(name)})
            if data.get("is_end", True):
                break
            cursor = data.get("next_cursor", "")
            if not cursor:
                break
        return folders

    def _ensure_folder(
        self,
        state: dict,
        source: str,
        remote_path: str,
        name: str,
        parent_folder_id: str | None,
    ) -> str:
        cached = state["folders"].get(remote_path)
        if isinstance(cached, dict) and cached.get("folder_id"):
            return str(cached["folder_id"])
        matches = [item for item in self._list_child_folders(parent_folder_id) if item["name"] == name]
        if len(matches) > 1:
            raise IMAUploadError(f"multiple IMA folders named '{name}' under the same parent")
        if matches:
            folder_id = matches[0]["folder_id"]
            status = "existing"
        else:
            payload = {"knowledge_base_id": self.knowledge_base_id, "name": name}
            if parent_folder_id:
                payload["folder_id"] = parent_folder_id
            data = self.api_caller("create_folder", payload)
            folder_id = data.get("media_id") or data.get("folder_id")
            if not folder_id:
                raise IMAUploadError("IMA create_folder response is missing the new folder ID")
            folder_id = str(folder_id)
            status = "created"
        state["folders"][remote_path] = {
            "folder_id": folder_id,
            "name": name,
            "parent_folder_id": parent_folder_id,
            "status": status,
            "updated_at": self.now().replace(microsecond=0).isoformat(),
        }
        self._write_state(source, state)
        self.logger.info("IMA folder %s: %s", status, remote_path)
        return folder_id

    def _ensure_folder_tree(self, state: dict, source: str, relative_parent: Path) -> str:
        parent_folder_id = None
        accumulated: list[str] = []
        for name in (source, *relative_parent.parts):
            accumulated.append(name)
            remote_path = "/".join(accumulated)
            parent_folder_id = self._ensure_folder(
                state,
                source,
                remote_path,
                name,
                parent_folder_id,
            )
        return parent_folder_id

    @staticmethod
    def _with_folder(payload: dict, folder_id: str | None) -> dict:
        if folder_id:
            payload["folder_id"] = folder_id
        return payload

    def _upload_one(self, file_path: Path, ts_code: str, event_date: str, folder_id: str | None) -> dict:
        file_size = file_path.stat().st_size
        if file_size > self.max_markdown_bytes:
            raise IMAUploadError(f"Markdown exceeds {self.max_markdown_bytes} bytes: {file_path}")
        title = file_path.name
        duplicate_data = self.api_caller(
            "check_repeated_names",
            self._with_folder(
                {
                    "params": [{"name": title, "media_type": self.MEDIA_TYPE_MARKDOWN}],
                    "knowledge_base_id": self.knowledge_base_id,
                },
                folder_id,
            ),
        )
        results = duplicate_data.get("results") or []
        if any(item.get("name") == title and item.get("is_repeated") for item in results):
            return {"status": "already_exists", "title": title}
        create_data = self.api_caller(
            "create_media",
            {
                "file_name": title,
                "file_size": file_size,
                "content_type": "text/markdown",
                "knowledge_base_id": self.knowledge_base_id,
                "file_ext": "md",
            },
        )
        media_id = create_data.get("media_id")
        credential = create_data.get("cos_credential")
        if not media_id or not isinstance(credential, dict):
            raise IMAUploadError("IMA create_media response is missing media_id or cos_credential")
        self.cos_uploader(file_path, credential, "text/markdown")
        add_payload = self._with_folder(
            {
                "media_type": self.MEDIA_TYPE_MARKDOWN,
                "media_id": media_id,
                "title": title,
                "knowledge_base_id": self.knowledge_base_id,
                "file_info": {
                    "cos_key": credential.get("cos_key"),
                    "file_size": file_size,
                    "file_name": title,
                },
            },
            folder_id,
        )
        self.api_caller("add_knowledge", add_payload)
        return {"status": "uploaded", "title": title, "media_id": media_id}

    def upload_tree(self, processed_root: str | Path, source: str) -> dict:
        processed_root = Path(processed_root)
        summary = {"enabled": True, "uploaded": 0, "skipped": 0, "failed": 0, "failures": []}
        if not processed_root.exists():
            return summary
        state = self._load_state(source)
        uploads = state["uploads"]
        for file_path in sorted(processed_root.rglob("*.md")):
            relative = file_path.relative_to(processed_root).as_posix()
            parts = Path(relative).parts
            if len(parts) != 3 or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", parts[1]):
                summary["failed"] += 1
                summary["failures"].append(f"invalid stock/date path: {relative}")
                continue
            ts_code, event_date = parts[0], parts[1]
            content_hash = hashlib.sha256(file_path.read_bytes()).hexdigest()
            folder_id = self._folder_for(ts_code, event_date)
            if not folder_id:
                try:
                    relative_parent = file_path.relative_to(processed_root).parent
                    folder_id = self._ensure_folder_tree(state, source, relative_parent)
                except Exception as exc:
                    summary["failed"] += 1
                    summary["failures"].append(f"{relative}: folder creation failed: {exc}")
                    self.logger.exception("IMA folder creation failed for %s", relative)
                    continue
            previous = uploads.get(relative)
            if (
                previous
                and previous.get("content_hash") == content_hash
                and previous.get("knowledge_base_id") == self.knowledge_base_id
                and previous.get("folder_id") == folder_id
                and previous.get("status") in {"uploaded", "already_exists"}
            ):
                summary["skipped"] += 1
                continue
            try:
                result = self._upload_one(file_path, ts_code, event_date, folder_id)
                uploads[relative] = {
                    "content_hash": content_hash,
                    "status": result["status"],
                    "title": result["title"],
                    "media_id": result.get("media_id"),
                    "knowledge_base_id": self.knowledge_base_id,
                    "folder_id": folder_id,
                    "uploaded_at": self.now().replace(microsecond=0).isoformat(),
                }
                self._write_state(source, state)
                if result["status"] == "uploaded":
                    summary["uploaded"] += 1
                else:
                    summary["skipped"] += 1
                self.logger.info("IMA %s: %s", result["status"], relative)
            except Exception as exc:
                summary["failed"] += 1
                summary["failures"].append(f"{relative}: {exc}")
                self.logger.exception("IMA upload failed for %s", relative)
        if summary["failed"]:
            raise IMAUploadError("; ".join(summary["failures"]))
        return summary
