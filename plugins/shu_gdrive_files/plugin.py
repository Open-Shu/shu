from typing import Any


class _Result:
    def __init__(self, status: str, data: dict[str, Any] | None = None, error: dict[str, Any] | None = None):
        self.status, self.data, self.error = status, data or {}, error

    @classmethod
    def ok(cls, data=None):
        return cls("success", data or {})

    @classmethod
    def err(cls, message, code="tool_error", details=None):
        return cls("error", error={"code": code, "message": str(message), "details": details or {}})


class GoogleDriveFilesPlugin:
    name = "gdrive_files"
    version = "1"

    def get_schema(self) -> dict[str, Any] | None:
        return {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "type": "object",
            "properties": {
                # Unified container id (drive or folder)
                "container_id": {
                    "type": "string",
                    "x-ui": {
                        "help": "Enter either a Shared Drive ID or a Folder ID. The plugin will probe Drive first; if not found, it will treat the value as a Folder ID."
                    },
                },
                "file_types": {
                    "type": ["array", "null"],
                    "items": {"type": "string"},
                    "default": ["pdf", "docx", "txt", "md", "py", "js"],
                    "x-ui": {"help": "Allowed file extensions (lowercase)."},
                },
                "max_file_size": {
                    "type": ["integer", "null"],
                    "default": 50 * 1024 * 1024,
                    "x-ui": {"help": "Maximum file size in bytes (default 50MB)."},
                },
                "include_shared": {
                    "type": ["boolean", "null"],
                    "default": True,
                    "x-ui": {"help": "Include shared files (if permitted)."},
                },
                "recursive": {
                    "type": ["boolean", "null"],
                    "default": True,
                    "x-ui": {"help": "Traverse subfolders when a folder is used as the container."},
                },
                "delete_missing": {
                    "type": ["boolean", "null"],
                    "default": False,
                    "x-ui": {
                        "help": "When enabled, delete Knowledge Objects for files Google Drive reports as removed during incremental sync. Use with caution."
                    },
                },
                # Execution
                "page_size": {
                    "type": ["integer", "null"],
                    "default": 100,
                    "x-ui": {"help": "Page size for Drive listing (max 1000)."},
                },
                "op": {
                    "type": ["string", "null"],
                    "enum": ["ingest"],
                    "default": "ingest",
                    "x-ui": {
                        "help": "Choose an operation.",
                        "enum_labels": {"ingest": "Ingest to KB"},
                        "enum_help": {"ingest": "Ingest matching files into the Knowledge Base."},
                    },
                },
                "kb_id": {
                    "type": ["string", "null"],
                    "x-ui": {"hidden": True, "help": "Target Knowledge Base to write documents."},
                },
                "debug": {
                    "type": ["boolean", "null"],
                    "default": None,
                    "x-ui": {
                        "help": "Include diagnostic info in output diagnostics (development only)",
                        "hidden": True,
                    },
                },
                "reset_cursor": {
                    "type": ["boolean", "null"],
                    "default": None,
                    "x-ui": {
                        "help": "Reset the incremental sync cursor before running (forces full rescan)",
                        "hidden": True,
                    },
                },
            },
            "required": ["container_id"],
            "additionalProperties": True,
        }

    def get_output_schema(self) -> dict[str, Any] | None:
        return {
            "type": "object",
            "properties": {
                "processed": {"type": "integer"},
                "ingested": {"type": "integer"},
                "skipped": {"type": "integer"},
                "diagnostics": {"type": ["array", "null"], "items": {"type": "string"}},
            },
        }

    async def execute(self, params: dict[str, Any], context: Any, host: Any) -> _Result:
        op = (params.get("op") or "ingest").lower()
        if op != "ingest":
            return _Result.err(f"Unsupported op: {op}")

        kb_id = params.get("kb_id")
        if not kb_id:
            return _Result.err("kb_id is required for op=ingest")
        if not hasattr(host, "kb"):
            return _Result.err("kb capability not available. Add 'kb' to manifest capabilities.")

        # Auth token via host.auth provider registry
        token, auth_target = await self._resolve_token(host)
        if not token:
            return _Result.err("No Google access token available. Configure host.auth or connect an account.")
        headers = {"Authorization": f"Bearer {token}"}

        # Unified container logic: try as Shared Drive first, then fallback to Folder
        container_id = params.get("container_id")
        drive_id: str | None = None
        folder_id: str | None = None
        if not container_id:
            return _Result.err("container_id is required")
        try:
            if await self._is_shared_drive(host, headers, container_id):
                drive_id = container_id
            else:
                folder_id = container_id
        except Exception:
            # If the probe fails, default to folder interpretation
            folder_id = container_id

        file_types = [str(x).lower() for x in (params.get("file_types") or ["pdf", "docx", "txt", "md", "py", "js"])]
        max_file_size = int(params.get("max_file_size") or 50 * 1024 * 1024)
        recursive = bool(params.get("recursive", True))
        page_size = max(1, min(1000, int(params.get("page_size") or 100)))
        include_shared = bool(params.get("include_shared", True))
        delete_missing = bool(params.get("delete_missing", False))

        debug_enabled = bool(params.get("debug")) or bool(getattr(getattr(host, "settings", None), "DEBUG", False))

        user_warnings: list[str] = []
        diag_warnings: list[str] = []
        processed_count = ingested_count = skipped_count = deleted_count = 0

        # Determine whether drive_id refers to a Shared Drive (vs a folder id)
        did_is_drive = False
        if drive_id:
            try:
                did_is_drive = await self._is_shared_drive(host, headers, drive_id)
            except Exception:
                did_is_drive = False

        # BFS over folders (if folder_id provided); otherwise list by drive scope
        folders: list[str] = [folder_id] if folder_id else []
        # Diagnostics (avoid leaking full IDs)
        try:
            exec_feed = None
            try:
                exec_feed = str(params.get("__schedule_id") or "")
            except Exception:
                exec_feed = ""
            diag_key = f"scope={'drive' if (did_is_drive and drive_id) else 'user'};d={(drive_id or '-')[-6:]};f={(folder_id or '-')[-6:]};kb={(kb_id or '')[-6:]}"
            if exec_feed:
                diag_key += f";feed={(exec_feed or '')[-6:]}"
            diag_warnings.append(f"diag:cursor_scope:{'feed' if exec_feed else 'adhoc'}")
            diag_warnings.append(f"diag:cursor_ctx:{diag_key}")
            # Emit minimal non-sensitive diagnostics unconditionally to aid troubleshooting
            mode_hint = str(params.get("auth_mode") or "").lower()
            diag_warnings.append(f"diag:auth_target:{auth_target or 'None'}")
            if mode_hint:
                diag_warnings.append(f"diag:auth_mode_param:{mode_hint}")
        except Exception:
            pass

        seen_folders = set([f for f in folders if f])

        async def list_files_in_folder(fid: str | None) -> tuple[list[dict[str, Any]], list[str]]:
            base = "https://www.googleapis.com/drive/v3/files"
            q_parts = ["trashed = false"]
            if fid:
                q_parts.append(f"'{fid}' in parents")
            # Do NOT filter by extensions at the Drive query level; filter client-side to avoid invalid queries
            params_q: dict[str, Any] = {
                "q": " and ".join(q_parts),
                "pageSize": page_size,
                "fields": "nextPageToken, files(id,name,mimeType,modifiedTime,md5Checksum,size,webViewLink,parents)",
                "supportsAllDrives": "true",
                "includeItemsFromAllDrives": "true" if bool(params.get("include_shared", True)) else "false",
            }
            # Ensure queries see Shared Drive folders when filtering by parent folder
            if fid:
                if bool(params.get("include_shared", True)):
                    params_q["corpora"] = "allDrives"
                else:
                    params_q["corpora"] = "user"
            # Use drive-scoped listing only when drive_id is a Shared Drive and no folder filter is applied
            if did_is_drive and not fid:
                params_q["driveId"] = drive_id
                params_q["corpora"] = "drive"
            # Always constrain to Drive space (excludes appDataFolder)
            params_q["spaces"] = "drive"
            files: list[dict[str, Any]] = []
            next_token = None
            while True:
                if next_token:
                    params_q["pageToken"] = next_token
                resp = await self._http_json(host, "GET", base, headers, params=params_q)
                # Handle potential error bodies gracefully
                resp_files = []
                if isinstance(resp, dict):
                    if resp.get("error"):
                        # Surface as warning by returning empty; caller will add diagnostics later
                        break
                    resp_files = resp.get("files", []) or []
                files += resp_files
                next_token = (resp or {}).get("nextPageToken") if isinstance(resp, dict) else None
                if not next_token:
                    break
            # Find subfolders
            child_folders = [f["id"] for f in files if f.get("mimeType") == "application/vnd.google-apps.folder"]
            return files, child_folders

        all_files: list[dict[str, Any]] = []
        if folder_id:
            queue = [folder_id]
            while queue:
                fid = queue.pop(0)
                if fid in seen_folders:
                    pass
                seen_folders.add(fid)
                files, subfolders = await list_files_in_folder(fid)
                all_files.extend([f for f in files if f.get("mimeType") != "application/vnd.google-apps.folder"])
                if recursive:
                    for sf in subfolders:
                        if sf not in seen_folders:
                            queue.append(sf)
        elif drive_id and not did_is_drive:
            queue = [drive_id]
            while queue:
                fid = queue.pop(0)
                if fid in seen_folders:
                    pass
                seen_folders.add(fid)
                files, subfolders = await list_files_in_folder(fid)
                all_files.extend([f for f in files if f.get("mimeType") != "application/vnd.google-apps.folder"])
                if recursive:
                    for sf in subfolders:
                        if sf not in seen_folders:
                            queue.append(sf)
        else:
            files, _ = await list_files_in_folder(None)
            all_files = [f for f in files if f.get("mimeType") != "application/vnd.google-apps.folder"]

        # Emit a minimal diagnostic about discovery counts so empty results are explainable
        try:
            diag_warnings.append(f"diag:discovered_files:{len(all_files)}")
        except Exception:
            pass

        # Process files

        async def _download_file(file: dict[str, Any]) -> tuple[bytes, str]:
            file_id = file["id"]
            mime = file.get("mimeType") or ""
            # Google Docs types: export
            if mime == "application/vnd.google-apps.document":
                url = f"https://www.googleapis.com/drive/v3/files/{file_id}/export"
                params_d = {"mimeType": "text/plain"}
                data = await self._http_bytes(host, "GET", url, headers, params=params_d)
                return data, "text/plain"
            if mime == "application/vnd.google-apps.spreadsheet":
                url = f"https://www.googleapis.com/drive/v3/files/{file_id}/export"
                params_d = {"mimeType": "text/csv"}
                data = await self._http_bytes(host, "GET", url, headers, params=params_d)
                return data, "text/csv"
            if mime == "application/vnd.google-apps.presentation":
                # No good text export; fallback to PDF and OCR
                url = f"https://www.googleapis.com/drive/v3/files/{file_id}/export"
                params_d = {"mimeType": "application/pdf"}
                data = await self._http_bytes(host, "GET", url, headers, params=params_d)
                return data, "application/pdf"
            # Binary files: download
            url = f"https://www.googleapis.com/drive/v3/files/{file_id}"
            params_d = {"alt": "media"}
            data = await self._http_bytes(host, "GET", url, headers, params=params_d)
            return data, (mime or "application/octet-stream")

        # If requested, reset cursor by clearing stored token
        reset_cursor = bool(params.get("reset_cursor"))
        if reset_cursor:
            try:
                if hasattr(host, "cursor"):
                    try:
                        await host.cursor.delete(kb_id)
                    except Exception:
                        pass
                diag_warnings.append("diag:reset_cursor:yes")
            except Exception:
                diag_warnings.append("diag:reset_cursor:error")
        # If we have a Changes API page token, use it for incremental sync
        try:
            existing_token = None
            if hasattr(host, "cursor"):
                existing_token = await host.cursor.get(kb_id)
        except Exception:
            existing_token = None
        diag_warnings.append(f"diag:existing_token:{'found' if existing_token else 'none'}")
        if existing_token and not reset_cursor:
            # Optional folder scoping for changes: precompute subtree IDs
            subtree_ids: set | None = None
            if folder_id:
                try:
                    ids = await self._list_child_folders(
                        host, headers, parent_id=folder_id, include_shared=include_shared, recursive=recursive
                    )
                    subtree_ids = set(ids + [folder_id])
                except Exception:
                    subtree_ids = set([folder_id])
            # Fetch changes
            changed_files, removed_ids, new_start = await self._iter_changes(
                host,
                headers,
                page_token=existing_token,
                did_is_drive=did_is_drive,
                drive_id=drive_id,
                include_shared=include_shared,
            )
            # Apply folder scope filter
            if subtree_ids is not None:
                scoped_files: list[dict[str, Any]] = []
                for f in changed_files:
                    parents = f.get("parents") or []
                    if any(p in subtree_ids for p in parents):
                        scoped_files.append(f)
                changed_files = scoped_files
            # Process deletions first
            if removed_ids:
                if delete_missing:
                    deleted_ids: list[str] = []
                    for rid in removed_ids:
                        try:
                            await host.kb.delete_ko(external_id=rid)
                            deleted_count += 1
                            deleted_ids.append(rid)
                        except Exception:
                            user_warnings.append(f"delete_failed:{rid}")
                    if deleted_ids:
                        diag_warnings.append(f"diag:deleted_ids:{','.join(deleted_ids[:10])}")
                        if len(deleted_ids) > 10:
                            diag_warnings.append(f"diag:deleted_ids_truncated:{len(deleted_ids) - 10}_more")
                else:
                    diag_warnings.append(f"diag:deletions_skipped:delete_missing_false:count={len(removed_ids)}")
            # Process upserts for changed files
            for f in changed_files:
                try:
                    # Skip folders - they have no downloadable content
                    mt = f.get("mimeType") or "application/octet-stream"
                    if mt == "application/vnd.google-apps.folder":
                        continue
                    size = int(f.get("size") or 0)
                    if max_file_size and size and size > max_file_size:
                        skipped_count += 1
                        try:
                            name = str(f.get("name") or f.get("id") or "")
                            user_warnings.append(
                                f"skip:too_large id={f.get('id')} name={name} size={size} max={max_file_size}"
                            )
                        except Exception:
                            pass
                        continue
                    name = str(f.get("name") or f.get("id") or "")
                    if file_types and "." in name:
                        ext = name.rsplit(".", 1)[-1].lower()
                        if ext not in file_types:
                            skipped_count += 1
                            try:
                                user_warnings.append(
                                    f"skip:ext_filtered id={f.get('id')} name={name} ext={ext} allowed={','.join(file_types)}"
                                )
                            except Exception:
                                pass
                            continue
                    blob, content_type = await _download_file(f)
                except Exception as e:
                    user_warnings.append(f"{f.get('id')}: {e}")
                    processed_count += 1
                    continue

                res_ing = await host.kb.ingest_document(
                    kb_id,
                    file_bytes=blob,
                    filename=name,
                    mime_type=content_type,
                    source_id=f.get("id"),
                    source_url=f.get("webViewLink"),
                    attributes={
                        "mimeType": mt,
                        "source_hash": f.get("md5Checksum"),
                        "modified_at": f.get("modifiedTime"),
                    },
                )
                if (res_ing or {}).get("word_count", 0) > 0:
                    ingested_count += 1
                else:
                    skipped_count += 1
                    try:
                        ex = (res_ing or {}).get("extraction") or {}
                        det = ex.get("details") or {}
                        reason = det.get("error") or "empty_extraction"
                        file_ext = det.get("file_extension") or ""
                        user_warnings.append(
                            f"skip:{reason} id={f.get('id')} name={name} mime={mt} ct={content_type} ext={file_ext}"
                        )
                    except Exception:
                        pass
                processed_count += 1
            # Persist new start token if provided
            try:
                if hasattr(host, "cursor") and new_start:
                    await host.cursor.set(kb_id, new_start)
                    diag_warnings.append("diag:saved_new_start_token:yes")
                else:
                    diag_warnings.append("diag:saved_new_start_token:no")
            except Exception:
                diag_warnings.append("diag:saved_new_start_token:error")
            warnings_out = user_warnings + (diag_warnings if debug_enabled else [])
            return _Result.ok(
                {
                    "processed": processed_count,
                    "ingested": ingested_count,
                    "skipped": skipped_count,
                    "deleted": deleted_count,
                    "diagnostics": warnings_out[:20],
                }
            )

        # Process initial scan results
        for f in all_files:
            try:
                size = int(f.get("size") or 0)
                if max_file_size and size and size > max_file_size:
                    skipped_count += 1
                    try:
                        name = str(f.get("name") or f.get("id") or "")
                        user_warnings.append(
                            f"skip:too_large id={f.get('id')} name={name} size={size} max={max_file_size}"
                        )
                    except Exception:
                        pass
                    continue
                name = str(f.get("name") or f.get("id") or "")
                mt = f.get("mimeType") or "application/octet-stream"
                # Simple file_types filter by extension
                if file_types and "." in name:
                    ext = name.rsplit(".", 1)[-1].lower()
                    if ext not in file_types:
                        skipped_count += 1
                        try:
                            user_warnings.append(
                                f"skip:ext_filtered id={f.get('id')} name={name} ext={ext} allowed={','.join(file_types)}"
                            )
                        except Exception:
                            pass
                        continue
                # Download/export
                blob, content_type = await _download_file(f)
            except Exception as e:
                user_warnings.append(f"{f.get('id')}: {e}")
                processed_count += 1
                continue
            # Ingest via host.kb to handle OCR, metadata, and indexing
            res_ing = await host.kb.ingest_document(
                kb_id,
                file_bytes=blob,
                filename=name,
                mime_type=content_type,
                source_id=f.get("id"),
                source_url=f.get("webViewLink"),
                attributes={
                    "mimeType": mt,
                    "source_hash": f.get("md5Checksum"),
                    "modified_at": f.get("modifiedTime"),
                },
            )
            if (res_ing or {}).get("word_count", 0) > 0:
                ingested_count += 1
            else:
                skipped_count += 1
                try:
                    ex = (res_ing or {}).get("extraction") or {}
                    det = ex.get("details") or {}
                    reason = det.get("error") or "empty_extraction"
                    file_ext = det.get("file_extension") or ""
                    user_warnings.append(
                        f"skip:{reason} id={f.get('id')} name={name} mime={mt} ct={content_type} ext={file_ext}"
                    )
                except Exception:
                    pass
            processed_count += 1

        # Initial full discovery path (no token yet)
        start_token: str | None = None
        try:
            start_token = await self._get_start_page_token(host, headers, did_is_drive=did_is_drive, drive_id=drive_id)
        except Exception:
            start_token = None

        # Persist initial start token for next incremental cycle
        try:
            if hasattr(host, "cursor") and start_token:
                await host.cursor.set(kb_id, start_token)
                diag_warnings.append("diag:saved_start_token:yes")
            else:
                diag_warnings.append("diag:saved_start_token:no")
        except Exception:
            diag_warnings.append("diag:saved_start_token:error")

        warnings_out = user_warnings + (diag_warnings if debug_enabled else [])
        return _Result.ok(
            {
                "processed": processed_count,
                "ingested": ingested_count,
                "skipped": skipped_count,
                "deleted": deleted_count,
                "diagnostics": warnings_out[:20],
            }
        )

    async def _is_shared_drive(self, host: Any, headers: dict[str, str], drive_id: str) -> bool:
        try:
            url = f"https://www.googleapis.com/drive/v3/drives/{drive_id}"
            data = await self._http_json(host, "GET", url, headers)
            return isinstance(data, dict) and bool(data.get("id"))
        except Exception:
            return False

    async def _get_start_page_token(
        self, host: Any, headers: dict[str, str], *, did_is_drive: bool, drive_id: str | None
    ) -> str | None:
        url = "https://www.googleapis.com/drive/v3/changes/startPageToken"
        params_q: dict[str, Any] = {"supportsAllDrives": "true"}
        if did_is_drive and drive_id:
            params_q["driveId"] = drive_id
        resp = await self._http_json(host, "GET", url, headers, params=params_q)
        if isinstance(resp, dict):
            return resp.get("startPageToken")
        return None

    async def _iter_changes(
        self,
        host: Any,
        headers: dict[str, str],
        *,
        page_token: str,
        did_is_drive: bool,
        drive_id: str | None,
        include_shared: bool,
    ) -> tuple[list[dict[str, Any]], list[str], str | None]:
        url = "https://www.googleapis.com/drive/v3/changes"
        params_q: dict[str, Any] = {
            "pageToken": page_token,
            "supportsAllDrives": "true",
            "includeItemsFromAllDrives": "true" if include_shared else "false",
            # Limit fields to reduce payload; include required file fields
            "fields": "nextPageToken,newStartPageToken,changes(fileId,removed,file(id,name,mimeType,modifiedTime,md5Checksum,size,webViewLink,parents,trashed))",
        }
        if did_is_drive and drive_id:
            params_q["driveId"] = drive_id
        files: list[dict[str, Any]] = []
        removed_ids: list[str] = []
        next_token: str | None = None
        new_start: str | None = None
        while True:
            if next_token:
                params_q["pageToken"] = next_token
            resp = await self._http_json(host, "GET", url, headers, params=params_q)
            if not isinstance(resp, dict):
                break
            new_start = resp.get("newStartPageToken") or new_start
            for ch in resp.get("changes") or []:
                if ch.get("removed"):
                    fid = ch.get("fileId") or (ch.get("file") or {}).get("id")
                    if fid:
                        removed_ids.append(fid)
                    continue
                f = ch.get("file")
                if not f:
                    continue
                # Treat trashed files as removed (user deleted -> trash)
                if f.get("trashed"):
                    fid = f.get("id")
                    if fid:
                        removed_ids.append(fid)
                    continue
                files.append(f)
            next_token = resp.get("nextPageToken")
            if not next_token:
                break
        return files, removed_ids, new_start

    async def _list_child_folders(
        self, host: Any, headers: dict[str, str], *, parent_id: str, include_shared: bool, recursive: bool = True
    ) -> list[str]:
        """List all child folder IDs under parent_id.

        If recursive=True (default), performs BFS to collect all nested subfolders.
        If recursive=False, only returns direct child folders.
        """
        base = "https://www.googleapis.com/drive/v3/files"

        async def _list_direct_children(fid: str) -> list[str]:
            params_q: dict[str, Any] = {
                "q": f"trashed = false and '{fid}' in parents and mimeType = 'application/vnd.google-apps.folder'",
                "pageSize": 1000,
                "fields": "nextPageToken, files(id)",
                "supportsAllDrives": "true",
                "includeItemsFromAllDrives": "true" if include_shared else "false",
            }
            children: list[str] = []
            next_token = None
            while True:
                if next_token:
                    params_q["pageToken"] = next_token
                resp = await self._http_json(host, "GET", base, headers, params=params_q)
                children.extend([f.get("id") for f in (resp or {}).get("files", []) if f.get("id")])
                next_token = (resp or {}).get("nextPageToken")
                if not next_token:
                    break
            return children

        # Get direct children first
        direct_children = await _list_direct_children(parent_id)

        if not recursive:
            return direct_children

        # BFS to collect all nested subfolders
        all_folders: list[str] = list(direct_children)
        seen: set = set(direct_children)
        queue = list(direct_children)

        while queue:
            fid = queue.pop(0)
            try:
                nested = await _list_direct_children(fid)
                for nid in nested:
                    if nid not in seen:
                        seen.add(nid)
                        all_folders.append(nid)
                        queue.append(nid)
            except Exception:
                # If we can't list a subfolder, skip it but continue
                pass

        return all_folders

    async def _resolve_token(self, host: Any) -> tuple[str | None, str | None]:
        auth = getattr(host, "auth", None)
        if not auth:
            return None, None
        try:
            # Resolve using host-provided selection and manifest op_auth scopes
            token, target = await auth.resolve_token_and_target("google")
        except Exception:
            return None, None
        if not token:
            return None, None
        return token, target

    async def _http_json(
        self,
        host: Any,
        method: str,
        url: str,
        headers: dict[str, str],
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        kwargs: dict[str, Any] = {"headers": headers}
        if params:
            kwargs["params"] = params
        if json_body is not None:
            kwargs["json"] = json_body
        resp = await host.http.fetch(method, url, **kwargs)
        body = resp.get("body") if isinstance(resp, dict) else None
        return body

    async def _http_bytes(
        self, host: Any, method: str, url: str, headers: dict[str, str], params: dict[str, Any] | None = None
    ) -> bytes:
        kwargs: dict[str, Any] = {"headers": headers}
        if params:
            kwargs["params"] = params
        if hasattr(host.http, "fetch_bytes"):
            resp = await host.http.fetch_bytes(method, url, **kwargs)
            content = resp.get("content") if isinstance(resp, dict) else None
            if content is not None:
                return content
        # Fallback: use text body
        resp = await host.http.fetch(method, url, **kwargs)
        body = resp.get("body") if isinstance(resp, dict) else None
        if isinstance(body, (bytes, bytearray)):
            return bytes(body)
        if isinstance(body, str):
            return body.encode("utf-8")
        raise RuntimeError("Failed to download bytes from Drive API response")
