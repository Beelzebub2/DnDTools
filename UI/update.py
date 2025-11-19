"""Update utilities and standalone updater UI for DnDTools.

This module serves two purposes:
1. Provide the :class:`UpdateManager` used by the main application to
   check for updates and orchestrate handing off control to the updater executable.
2. Expose a CLI entry point (``python update.py``) that runs the visual updater
   experience which is compiled into ``update.exe``.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

import psutil
import requests


LOGGER = logging.getLogger("dndtools.update")


class UpdateError(RuntimeError):
    """Raised when an update operation fails."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class UpdateManifest:
    """Strongly typed manifest payload."""

    version: str
    url: str
    sha256: Optional[str] = None
    notes: Optional[str] = None
    release_tag: Optional[str] = None
    channel: Optional[str] = None

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "UpdateManifest":
        if not isinstance(payload, dict):
            raise UpdateError("Manifest payload must be a JSON object")

        version = str(payload.get("version") or "").strip()
        url = str(payload.get("url") or "").strip()
        if not version or not url:
            raise UpdateError("Manifest missing required fields: version/url")

        sha256_value = payload.get("sha256")
        notes_value = payload.get("notes")
        release_tag = payload.get("release_tag") or payload.get("_release_tag")
        channel = payload.get("channel") or payload.get("_channel")
        return cls(
            version=version,
            url=url,
            sha256=sha256_value or None,
            notes=notes_value or None,
            release_tag=release_tag or None,
            channel=channel or None,
        )

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "version": self.version,
            "url": self.url,
            "sha256": self.sha256 or "",
            "notes": self.notes or "",
        }
        if self.release_tag:
            payload["release_tag"] = self.release_tag
        if self.channel:
            payload["channel"] = self.channel
        return payload


class UpdateState:
    """Tracks mutable update status with thread safety handled by :class:`UpdateManager`."""

    __slots__ = ("in_progress", "last_error")

    def __init__(self) -> None:
        self.in_progress: bool = False
        self.last_error: Optional[str] = None

    def snapshot(self) -> dict[str, Any]:
        return {"in_progress": self.in_progress, "last_error": self.last_error}


class UpdateManager:
    """Encapsulates update manifest retrieval and updater process orchestration."""

    def __init__(
        self,
        current_version: str,
        manifest_url: str,
        cache_duration: int = 300,
        auto_update_silent: bool = True,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.current_version = str(current_version)
        self.manifest_url = manifest_url
        self.cache_duration = max(cache_duration, 0)
        self.auto_update_silent = bool(auto_update_silent)
        self.logger = logger or LOGGER

        self._state = UpdateState()
        self._cache_lock = threading.RLock()
        self._state_lock = threading.RLock()
        self._manifest_cache: dict[str, UpdateManifest] = {}
        self._manifest_timestamp: dict[str, float] = {}

    # ---------------------------------------------------------------------
    # Manifest helpers
    # ---------------------------------------------------------------------
    @staticmethod
    def _normalize_version(value: Optional[str]) -> tuple[int, ...]:
        if not value:
            return ()
        parts = [segment for segment in re.split(r"[^0-9]+", str(value)) if segment]
        normalized: list[int] = []
        for part in parts:
            try:
                normalized.append(int(part))
            except ValueError:
                continue
        return tuple(normalized)

    @classmethod
    def _is_remote_newer(cls, remote: str, local: str) -> bool:
        remote_tuple = cls._normalize_version(remote)
        local_tuple = cls._normalize_version(local)
        length = max(len(remote_tuple), len(local_tuple))
        remote_padded = remote_tuple + (0,) * (length - len(remote_tuple))
        local_padded = local_tuple + (0,) * (length - len(local_tuple))
        return remote_padded > local_padded

    @staticmethod
    def _normalize_channel(channel: Optional[str]) -> str:
        value = (channel or "stable").strip().lower()
        if value in {"dev", "development", "test", "testing", "testers"}:
            return "dev"
        return "stable"

    def _download_manifest_from_url(self, url: str) -> Optional[dict[str, Any]]:
        try:
            response = requests.get(
                url,
                headers={"User-Agent": "DnDTools-Updater"},
                timeout=15,
            )
        except requests.RequestException as exc:
            self.logger.warning("Failed to download update manifest from %s: %s", url, exc)
            return None

        if response.status_code == 404:
            self.logger.info("Update manifest not found at %s (404)", url)
            return None

        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            self.logger.warning("Unexpected HTTP error retrieving manifest from %s: %s", url, exc)
            return None

        try:
            manifest = response.json()
        except ValueError as exc:
            self.logger.warning("Manifest at %s is not valid JSON: %s", url, exc)
            return None

        if not isinstance(manifest, dict):
            self.logger.warning("Manifest at %s must be a JSON object", url)
            return None

        return manifest

    def _download_manifest_asset(self, asset_url: str) -> Optional[dict[str, Any]]:
        try:
            asset_response = requests.get(
                asset_url,
                headers={"User-Agent": "DnDTools-Updater", "Accept": "application/octet-stream"},
                timeout=15,
            )
            asset_response.raise_for_status()
            manifest = asset_response.json()
        except requests.RequestException as exc:
            self.logger.warning("Failed downloading manifest asset from GitHub: %s", exc)
            return None
        except ValueError as exc:
            self.logger.warning("GitHub manifest asset is not valid JSON: %s", exc)
            return None

        if not isinstance(manifest, dict):
            self.logger.warning("GitHub manifest asset must be a JSON object")
            return None

        return manifest

    def _download_manifest_from_release(
        self,
        release_data: dict[str, Any],
        *,
        channel: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        assets = release_data.get("assets") or []
        asset_url = None
        for asset in assets:
            if isinstance(asset, dict) and asset.get("name") == "update-manifest.json":
                asset_url = asset.get("browser_download_url")
                if asset_url:
                    break

        if not asset_url:
            self.logger.warning("No update-manifest.json asset found in GitHub release %s", release_data.get("tag_name"))
            return None

        manifest = self._download_manifest_asset(asset_url)
        if not manifest:
            return None

        if release_data.get("body") and not manifest.get("notes"):
            manifest["notes"] = release_data["body"]

        release_tag = release_data.get("tag_name") or ""
        if release_tag:
            manifest.setdefault("_release_tag", release_tag)

        if channel:
            manifest.setdefault("_channel", channel)

        return manifest

    def _fetch_release_list(self, *, per_page: int = 20) -> Optional[list[dict[str, Any]]]:
        try:
            response = requests.get(
                "https://api.github.com/repos/Beelzebub2/DnDTools/releases",
                headers={"User-Agent": "DnDTools-Updater"},
                params={"per_page": per_page},
                timeout=15,
            )
            response.raise_for_status()
            releases = response.json()
        except requests.RequestException as exc:
            self.logger.warning("Unable to query GitHub releases list: %s", exc)
            return None

        if not isinstance(releases, list):
            self.logger.warning("GitHub releases API returned unexpected payload")
            return None

        return [rel for rel in releases if isinstance(rel, dict)]

    def _fetch_manifest_from_latest_test_release(self) -> Optional[dict[str, Any]]:
        releases = self._fetch_release_list()
        if not releases:
            return None

        candidates: list[dict[str, Any]] = []
        for release in releases:
            if not release.get("prerelease"):
                continue
            tag_name = str(release.get("tag_name") or "")
            display_name = str(release.get("name") or "")
            if tag_name.startswith("Test-") or display_name.startswith("Test-"):
                candidates.append(release)

        candidates.sort(
            key=lambda rel: rel.get("published_at") or rel.get("created_at") or "",
            reverse=True,
        )

        for release in candidates:
            manifest = self._download_manifest_from_release(release, channel="dev")
            if manifest:
                return manifest

        return None

    def _download_manifest_for_channel(self, channel: str) -> Optional[dict[str, Any]]:
        normalized = self._normalize_channel(channel)
        if normalized == "dev":
            manifest = self._fetch_manifest_from_latest_test_release()
            if manifest:
                return manifest
            self.logger.info("No development releases with Test- prefix were found on GitHub")
            return None

        manifest_payload = self._download_manifest_from_url(self.manifest_url)
        if manifest_payload:
            manifest_payload.setdefault("_channel", "stable")
            return manifest_payload

        manifest_payload = self._fetch_manifest_from_github_latest()
        if manifest_payload:
            manifest_payload.setdefault("_channel", "stable")
        return manifest_payload

    def _fetch_manifest_from_github_latest(self) -> Optional[dict[str, Any]]:
        try:
            api_response = requests.get(
                "https://api.github.com/repos/Beelzebub2/DnDTools/releases/latest",
                headers={"User-Agent": "DnDTools-Updater"},
                timeout=15,
            )
            api_response.raise_for_status()
            release_data = api_response.json()
        except requests.RequestException as exc:
            self.logger.warning("Unable to query GitHub releases API: %s", exc)
            return None

        manifest = self._download_manifest_from_release(release_data, channel="stable")
        if not manifest:
            self.logger.warning("No update-manifest.json asset found in latest GitHub release")
        return manifest

    def fetch_manifest(self, force: bool = False, channel: str = "stable") -> Optional[UpdateManifest]:
        normalized_channel = self._normalize_channel(channel)
        now = time.time()
        with self._cache_lock:
            cached_manifest = self._manifest_cache.get(normalized_channel)
            cached_timestamp = self._manifest_timestamp.get(normalized_channel, 0.0)
            if not force and cached_manifest and (now - cached_timestamp) < self.cache_duration:
                return cached_manifest

        manifest_payload = self._download_manifest_for_channel(normalized_channel)
        if manifest_payload is None:
            with self._cache_lock:
                self._manifest_cache.pop(normalized_channel, None)
                self._manifest_timestamp.pop(normalized_channel, None)
            return None

        manifest_payload.setdefault("_channel", normalized_channel)
        manifest = UpdateManifest.from_dict(manifest_payload)

        with self._cache_lock:
            self._manifest_cache[normalized_channel] = manifest
            self._manifest_timestamp[normalized_channel] = now

        return manifest

    # ------------------------------------------------------------------
    # State helpers
    # ------------------------------------------------------------------
    def snapshot_state(self) -> dict[str, Any]:
        with self._state_lock:
            return self._state.snapshot()

    def _set_state(self, *, in_progress: Optional[bool] = None, last_error: Optional[str] = None) -> None:
        with self._state_lock:
            if in_progress is not None:
                self._state.in_progress = in_progress
            if last_error is not None or last_error == "":
                self._state.last_error = last_error

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    @staticmethod
    def _extract_release_tag(manifest: Optional[UpdateManifest]) -> str:
        if not manifest:
            return ""
        if manifest.release_tag:
            return manifest.release_tag
        if manifest.url:
            match = re.search(r"/download/([^/]+)/", manifest.url)
            if match:
                return match.group(1)
        return ""

    def build_update_payload(self, manifest: Optional[UpdateManifest], channel: str = "stable") -> dict[str, Any]:
        requested_channel = self._normalize_channel(channel)
        effective_channel = manifest.channel or requested_channel if manifest else requested_channel
        remote_version = manifest.version if manifest else self.current_version
        release_tag = self._extract_release_tag(manifest)
        payload = {
            "currentVersion": self.current_version,
            "latestVersion": remote_version,
            "updateAvailable": bool(manifest and self._is_remote_newer(remote_version, self.current_version)),
            "notes": manifest.notes or "" if manifest else "",
            "downloadUrl": manifest.url if manifest else "",
            "sha256": manifest.sha256 or "" if manifest else "",
            "channel": requested_channel,
            "effectiveChannel": effective_channel,
            "releaseTag": release_tag,
        }
        payload.update(self.snapshot_state())
        return payload

    def check_for_updates(self, force: bool = False, channel: str = "stable") -> tuple[dict[str, Any], Optional[str]]:
        normalized_channel = self._normalize_channel(channel)
        manifest = None
        error = None
        try:
            manifest = self.fetch_manifest(force=force, channel=normalized_channel)
            if manifest is None:
                if normalized_channel == "dev":
                    payload = self.build_update_payload(None, normalized_channel)
                    payload["message"] = "No Test releases are currently available."
                    return payload, None
                error = "Unable to retrieve update manifest"
        except UpdateError as exc:
            error = str(exc)
        payload = self.build_update_payload(manifest, normalized_channel)
        if error:
            payload["error"] = error
        return payload, error

    # ------------------------------------------------------------------
    # Updater orchestration
    # ------------------------------------------------------------------
    def start_update(self, api: Optional[Any] = None, force: bool = True, channel: str = "stable") -> None:
        normalized_channel = self._normalize_channel(channel)
        with self._state_lock:
            if self._state.in_progress:
                raise UpdateError("Update already in progress", status_code=409)
            self._state.in_progress = True
            self._state.last_error = None

        manifest = self.fetch_manifest(force=force, channel=normalized_channel)
        if manifest is None:
            message = "No Test releases are currently available" if normalized_channel == "dev" else "Unable to retrieve update manifest"
            self._set_state(in_progress=False, last_error=message)
            raise UpdateError(message, status_code=503)

        if not self._is_remote_newer(manifest.version, self.current_version):
            self._set_state(in_progress=False, last_error="Already up to date")
            raise UpdateError("Already up to date", status_code=400)

        worker = threading.Thread(
            target=self._update_thread,
            args=(manifest, api, normalized_channel),
            name="DnDToolsUpdater",
            daemon=True,
        )
        worker.start()

    # ------------------------------------------------------------------
    # Internal implementation details
    # ------------------------------------------------------------------
    def _update_thread(self, manifest: UpdateManifest, api: Optional[Any], channel: str) -> None:
        update_context: Optional[dict[str, Any]] = None
        try:
            if api is not None:
                try:
                    update_context = api.prepare_for_update()
                except Exception as prep_exc:
                    self.logger.error("Failed to prepare application for update: %s", prep_exc, exc_info=True)

            context_path = self._write_update_context(manifest, channel)
            process = self._launch_updater_process(context_path)

            if process.poll() not in (None,):
                return_code = process.returncode
                raise UpdateError(f"Updater helper exited immediately with code {return_code}", status_code=500)

            if api is not None:
                try:
                    api._update_closing_overlay("Update helper started. The app will close to finish updating...")
                except Exception:
                    self.logger.debug("Unable to update closing overlay after launching updater", exc_info=True)

            time.sleep(1.5)
            os._exit(0)
        except Exception as exc:
            self.logger.error("Automatic update failed: %s", exc, exc_info=True)
            self._set_state(last_error=str(exc))
            if api is not None:
                try:
                    api.resume_after_update_failure(update_context or {}, str(exc))
                except Exception:
                    self.logger.debug("Failed to restore application state after update failure", exc_info=True)
        finally:
            self._set_state(in_progress=False)

    def _write_update_context(self, manifest: UpdateManifest, channel: str) -> Path:
        temp_dir = Path(tempfile.gettempdir()) / "DnDToolsUpdate"
        temp_dir.mkdir(parents=True, exist_ok=True)
        context_path = temp_dir / f"context-{os.getpid()}-{int(time.time())}.json"

        payload = {
            "manifest": manifest.to_dict(),
            "app": {
                "executable": str(self._resolve_app_executable()),
                "pid": os.getpid(),
                "workdir": str(self._resolve_app_directory()),
            },
            "options": {
                "silent": self.auto_update_silent,
                "channel": manifest.channel or channel,
            },
        }

        with context_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle)
        return context_path

    def _resolve_app_executable(self) -> Path:
        exe = Path(sys.executable).resolve()
        if exe.suffix.lower() == ".exe" and exe.exists():
            return exe

        override = os.environ.get("DNDTOOLS_EXE_PATH")
        if override:
            override_path = Path(override).expanduser().resolve()
            if override_path.exists():
                return override_path

        script_path = Path(sys.argv[0]).resolve()
        if script_path.suffix.lower() == ".py":
            candidate = script_path.with_name("DnDTools.exe")
            if candidate.exists():
                return candidate
        return exe

    def _resolve_app_directory(self) -> Path:
        exe = self._resolve_app_executable()
        return exe.parent if exe else Path.cwd()

    def _resolve_updater_candidate(self) -> tuple[Path, bool]:
        """Return the updater executable or script and whether it's an executable."""
        base_dir = self._resolve_app_directory()
        candidates = [
            base_dir / "update.exe",
            base_dir / "Update.exe",
        ]
        for candidate in candidates:
            if candidate.exists():
                return candidate, True

        script_candidate = Path(__file__).resolve()
        if script_candidate.exists():
            return script_candidate, False

        raise UpdateError("Updater helper not found", status_code=500)

    def _launch_updater_process(self, context_path: Path) -> subprocess.Popen[Any]:
        candidate, is_executable = self._resolve_updater_candidate()
        if is_executable:
            args = [str(candidate), "--context", str(context_path)]
        else:
            python_exe = sys.executable
            args = [python_exe, str(candidate), "--context", str(context_path)]

        if self.auto_update_silent:
            args.append("--silent")

        popen_kwargs: dict[str, Any] = {
            "close_fds": False,
            "cwd": str(candidate.parent),
        }

        if os.name == "nt":
            creation_flags = 0
            for flag_name in ("DETACHED_PROCESS", "CREATE_NEW_PROCESS_GROUP"):
                creation_flags |= getattr(subprocess, flag_name, 0)
            popen_kwargs["creationflags"] = creation_flags

        self.logger.info("Launching updater helper: %s", args[0])
        return subprocess.Popen(args, **popen_kwargs)


# ===========================================================================
# Standalone updater application (compiled into update.exe)
# ===========================================================================

class UpdaterUI:
    """Tkinter-based UI worker that orchestrates the update flow."""

    def __init__(self, manifest: UpdateManifest, app_info: dict[str, Any], options: dict[str, Any]):
        self.manifest = manifest
        self.app_info = app_info
        self.options = options
        self.logger = LOGGER
        self._queue: "queue.Queue[tuple[str, Any]]" = queue.Queue()
        self._download_path: Optional[Path] = None
        self._temp_dir = Path(tempfile.mkdtemp(prefix="dndtools-update-"))

        # Lazy import tkinter so importing this module in the main app doesn't require it
        import tkinter as tk
        from tkinter import ttk

        self.tk = tk
        self.ttk = ttk

        self.colors = {
            "bg": "#0d1118",
            "panel": "#151c27",
            "panel_border": "#1f2734",
            "panel_hover": "#1b2330",
            "text_primary": "#fdfbff",
            "text_muted": "#c4ccda",
            "text_subtle": "#8e99ad",
            "accent": "#ff7b26",
            "accent_hover": "#ff9553",
            "accent_dark": "#c8621d",
            "progress_trough": "#1c2433",
        }

        self.root = tk.Tk()
        self.root.title("DnDTools Updater")
        self.root.geometry("460x260")
        self.root.configure(bg=self.colors["bg"])
        self.root.resizable(False, False)

        try:
            self.root.iconbitmap(self._resolve_icon_path())
        except Exception:
            pass

        self.status_var = tk.StringVar(value="Preparing update...")
        self.detail_var = tk.StringVar(value="")

        outer = tk.Frame(self.root, bg=self.colors["bg"])
        outer.pack(fill=tk.BOTH, expand=True)

        card = tk.Frame(
            outer,
            bg=self.colors["panel"],
            highlightthickness=1,
            highlightbackground=self.colors["panel_border"],
            bd=0,
        )
        card.pack(expand=True, padx=24, pady=24, fill=tk.BOTH)

        accent_bar = tk.Frame(card, bg=self.colors["accent"], height=3, bd=0)
        accent_bar.pack(fill=tk.X, side=tk.TOP)

        padding = 20
        container = tk.Frame(card, bg=self.colors["panel"], padx=padding, pady=padding, bd=0)
        container.pack(fill=tk.BOTH, expand=True)

        title_label = tk.Label(
            container,
            text="Dark and Darker Tools",
            fg=self.colors["accent"],
            bg=self.colors["panel"],
            font=("Segoe UI Semibold", 16),
        )
        title_label.pack(anchor="w")

        subtitle_label = tk.Label(
            container,
            text="Update in progress",
            fg=self.colors["text_subtle"],
            bg=self.colors["panel"],
            font=("Segoe UI", 11),
        )
        subtitle_label.pack(anchor="w", pady=(4, 16))

        status_label = tk.Label(
            container,
            textvariable=self.status_var,
            fg=self.colors["text_primary"],
            bg=self.colors["panel"],
            font=("Segoe UI", 11),
        )
        status_label.pack(anchor="w")

        detail_label = tk.Label(
            container,
            textvariable=self.detail_var,
            fg=self.colors["text_muted"],
            bg=self.colors["panel"],
            font=("Segoe UI", 9),
            wraplength=360,
            justify="left",
        )
        detail_label.pack(anchor="w", pady=(6, 16))

        self.style = ttk.Style(self.root)
        try:
            self.style.theme_use("clam")
        except tk.TclError:
            pass
        self.style.configure(
            "Updater.Horizontal.TProgressbar",
            troughcolor=self.colors["progress_trough"],
            bordercolor=self.colors["progress_trough"],
            background=self.colors["accent"],
            lightcolor=self.colors["accent"],
            darkcolor=self.colors["accent_dark"],
            thickness=12,
            troughrelief="flat",
            relief="flat",
        )

        self.progress = ttk.Progressbar(
            container,
            orient="horizontal",
            mode="determinate",
            style="Updater.Horizontal.TProgressbar",
            length=360,
        )
        self.progress.pack(fill=tk.X)
        self.progress.configure(value=0, maximum=100)

        self.buttons_frame = tk.Frame(container, bg=self.colors["panel"])
        self.buttons_frame.pack(anchor="e", fill=tk.X, pady=(22, 0))

        self._button_palette = {
            "accent": {
                "bg": self.colors["accent"],
                "hover": self.colors["accent_hover"],
                "fg": self.colors["bg"],
            },
            "ghost": {
                "bg": self.colors["panel"],
                "hover": self.colors["panel_hover"],
                "fg": self.colors["accent"],
            },
        }

        self._workflow_thread = threading.Thread(target=self._workflow, name="UpdaterWorkflow", daemon=True)

    def _resolve_icon_path(self) -> str:
        base_dir = Path(__file__).resolve().parent
        icon_path = base_dir / "assets" / "logo.ico"
        if icon_path.exists():
            return str(icon_path)
        # Fallback to program directory when running from exe
        exe_dir = Path(sys.executable).resolve().parent
        candidate = exe_dir / "logo.ico"
        if candidate.exists():
            return str(candidate)
        return str(base_dir / "logo.ico")

    def run(self) -> None:
        self.root.after(200, self._workflow_thread.start)
        self.root.after(100, self._process_queue)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close_attempt)
        self.root.mainloop()

    def _on_close_attempt(self) -> None:
        # Prevent closing while update in progress
        if self._workflow_thread.is_alive():
            return
        self.root.destroy()

    # ------------------------------------------------------------------
    # UI helpers
    # ------------------------------------------------------------------
    def _enqueue(self, action: str, payload: Any = None) -> None:
        self._queue.put((action, payload))

    def _process_queue(self) -> None:
        try:
            while True:
                action, payload = self._queue.get_nowait()
                if action == "status":
                    message, detail = payload
                    self.status_var.set(message)
                    self.detail_var.set(detail or "")
                elif action == "progress":
                    value = float(payload)
                    self.progress.configure(value=max(0.0, min(100.0, value)))
                elif action == "complete":
                    self._show_completion_buttons()
                elif action == "error":
                    message = str(payload or "Update failed")
                    self._show_error_buttons(message)
                elif action == "download_name":
                    self.detail_var.set(payload)
        except queue.Empty:
            pass
        finally:
            self.root.after(100, self._process_queue)

    def _clear_buttons(self) -> None:
        for child in list(self.buttons_frame.winfo_children()):
            child.destroy()

    def _create_button(self, parent, text: str, command, variant: str = "accent"):
        colors = self._button_palette.get(variant, self._button_palette["accent"])
        button = self.tk.Button(
            parent,
            text=text,
            command=command,
            bg=colors["bg"],
            activebackground=colors["hover"],
            fg=colors["fg"],
            activeforeground=colors["fg"],
            relief="flat",
            bd=0,
            padx=16,
            pady=8,
            font=("Segoe UI Semibold", 10),
            cursor="hand2",
            highlightthickness=0,
        )

        button.bind("<Enter>", lambda _e: button.configure(bg=colors["hover"]))
        button.bind("<Leave>", lambda _e: button.configure(bg=colors["bg"]))
        return button

    def _show_completion_buttons(self) -> None:
        self._clear_buttons()
        can_launch = bool(self.app_info.get("executable")) and not self.options.get("no_launch")

        close_button = self._create_button(
            self.buttons_frame, "Close", self.root.destroy, variant="ghost"
        )
        close_button.pack(side=self.tk.RIGHT)

        if can_launch:
            launch_button = self._create_button(
                self.buttons_frame,
                "Launch DnDTools",
                self._launch_app,
                variant="accent",
            )
            launch_button.pack(side=self.tk.RIGHT, padx=(0, 8))

    def _show_error_buttons(self, message: str | None = None) -> None:
        self._clear_buttons()

        if message:
            self.detail_var.set(message)

        close_button = self._create_button(
            self.buttons_frame, "Close", self.root.destroy, variant="ghost"
        )
        close_button.pack(side=self.tk.RIGHT)

        open_logs_button = self._create_button(
            self.buttons_frame,
            "View logs",
            self._open_logs_directory,
            variant="accent",
        )
        open_logs_button.pack(side=self.tk.RIGHT, padx=(0, 8))

    def _launch_app(self) -> None:
        executable = self.app_info.get("executable")
        if not executable:
            self.root.destroy()
            return

        exe_path = Path(executable)
        if not exe_path.exists():
            self.root.destroy()
            return

        self.status_var.set("Launching DnDTools...")
        self.detail_var.set("Updater will close automatically in about 10 seconds.")

        try:
            process = subprocess.Popen([str(exe_path)], cwd=str(exe_path.parent))
        except Exception as exc:
            self.logger.error("Failed to launch app after update: %s", exc)
            self.detail_var.set(f"Launch failed: {exc}")
            return

        def _wait_for_launch() -> None:
            timeout = 10.0
            interval = 0.2
            elapsed = 0.0

            while elapsed < timeout:
                result = process.poll()
                if result is not None:
                    self.logger.debug("Launch process exited with code %s during wait", result)
                time.sleep(interval)
                elapsed += interval

            self.root.after(0, lambda: self.detail_var.set("Closing updater..."))
            self.root.after(0, self.root.destroy)

        threading.Thread(target=_wait_for_launch, daemon=True).start()

    def _open_logs_directory(self) -> None:
        log_dir = self.options.get("log_directory") or self.app_info.get("log_directory")
        if not log_dir:
            self.logger.info("No logs directory provided; falling back to temp directory")
            log_dir = str(self._temp_dir)

        path = Path(log_dir).expanduser()
        try:
            if sys.platform.startswith("win"):
                os.startfile(str(path))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except Exception as exc:
            self.logger.error("Failed to open logs directory: %s", exc)

    # ------------------------------------------------------------------
    # Workflow implementation
    # ------------------------------------------------------------------
    def _workflow(self) -> None:
        try:
            if self.options.get("demo"):
                self._run_demo_flow()
                return
            self._enqueue("status", ("Ensuring DnDTools is closed...", ""))
            self._ensure_application_closed()

            self._enqueue("status", ("Downloading update...", ""))
            installer_path = self._download_installer()
            self._download_path = installer_path

            self._enqueue("status", ("Launching installer...", ""))
            self._run_installer(installer_path, wait=False)
            self._notify_installation_in_progress()
            self.root.after(300, self.root.destroy)
            return
        except Exception as exc:
            self.logger.error("Update workflow failed: %s", exc, exc_info=True)
            self._enqueue("status", ("Update failed", str(exc)))
            self._enqueue("error", str(exc))
        finally:
            self._cleanup_temp()

    def _run_demo_flow(self) -> None:
        phantom_pid = self.options.get("phantom_pid", "?")
        statuses = [
            ("Ensuring DnDTools is closed...", f"Phantom PID {phantom_pid}", 20),
            ("Downloading update...", "Simulated download (demo mode)", 60),
            ("Applying update...", "Simulated installer (demo mode)", 90),
        ]

        progress = 0
        for status, detail, target in statuses:
            self._enqueue("status", (status, detail))
            while progress < target:
                progress = min(target, progress + 5)
                self._enqueue("progress", progress)
                time.sleep(0.15)

        final_message = (
            f"DnDTools {self.manifest.version} installed (demo)."
            if self.manifest else "Demo complete."
        )
        self._enqueue("status", ("Update complete", final_message))
        self._enqueue("progress", 100)
        self._enqueue("complete")

    def _ensure_application_closed(self) -> None:
        pid = self.app_info.get("pid")
        executable = self.app_info.get("executable")
        targets: dict[int, psutil.Process] = {}

        if pid:
            try:
                proc = psutil.Process(int(pid))
                targets[proc.pid] = proc
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

        if executable:
            exe_path = Path(executable).resolve()
            for proc in psutil.process_iter(["pid", "name", "exe"]):
                try:
                    proc_exe = Path(proc.exe()).resolve()
                except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
                    continue
                if proc_exe == exe_path:
                    targets[proc.pid] = proc

        deadline = time.time() + 25
        polling_targets = list(targets.values())
        while polling_targets and time.time() < deadline:
            remaining = []
            for proc in polling_targets:
                try:
                    if proc.is_running():
                        remaining.append(proc)
                except (psutil.NoSuchProcess, psutil.ZombieProcess):
                    continue
            if not remaining:
                return
            polling_targets = remaining
            time.sleep(0.4)

        # Attempt graceful termination
        for proc in polling_targets:
            try:
                proc.terminate()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        time.sleep(1.5)
        for proc in polling_targets:
            try:
                if proc.is_running():
                    proc.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

    def _download_installer(self) -> Path:
        url = self.manifest.url
        parsed = urlparse(url)
        filename = Path(parsed.path).name or f"DnDTools-Setup-{self.manifest.version}.exe"
        target_path = self._temp_dir / filename

        with requests.get(url, stream=True, timeout=60) as response:
            response.raise_for_status()
            total = int(response.headers.get("Content-Length", "0") or 0)
            downloaded = 0
            chunk_size = 1024 * 512
            with target_path.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=chunk_size):
                    if not chunk:
                        continue
                    handle.write(chunk)
                    downloaded += len(chunk)
                    if total:
                        percent = downloaded * 100 / total
                        self._enqueue("progress", percent)
                        self._enqueue("status", ("Downloading update...", f"{downloaded // 1024} KB / {total // 1024} KB"))
        self._enqueue("progress", 100)
        if self.manifest.sha256:
            self._verify_sha256(target_path, self.manifest.sha256)
        return target_path

    def _verify_sha256(self, file_path: Path, expected: str) -> None:
        import hashlib

        sha256 = hashlib.sha256()
        with file_path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                sha256.update(chunk)
        digest = sha256.hexdigest()
        if digest.lower() != expected.lower():
            raise UpdateError("Downloaded installer checksum mismatch")

    def _run_installer(self, installer_path: Path, wait: bool = True) -> None:
        args = [str(installer_path)]
        if self.options.get("silent", True):
            args.extend([
                "/VERYSILENT",
                "/SUPPRESSMSGBOXES",
                "/NORESTART",
                "/SP-",
                "/CLOSEAPPLICATIONS",
                "/RESTARTAPPLICATIONS",
            ])
        else:
            args.extend([
                "/SILENT",
                "/SUPPRESSMSGBOXES",
                "/NORESTART",
                "/SP-",
            ])

        popen_kwargs: dict[str, Any] = {
            "cwd": str(installer_path.parent),
            "close_fds": False,
        }
        if os.name == "nt":
            creation = 0
            for flag in ("DETACHED_PROCESS", "CREATE_NEW_PROCESS_GROUP"):
                creation |= getattr(subprocess, flag, 0)
            popen_kwargs["creationflags"] = creation

        proc = subprocess.Popen(args, **popen_kwargs)
        if wait:
            return_code = proc.wait()
            if return_code != 0:
                raise UpdateError(f"Installer exited with code {return_code}")

    def _notify_installation_in_progress(self) -> None:
        if os.name != "nt":
            return

        def _show_notification() -> None:
            try:
                import win32con
                import win32gui
            except ImportError:
                self.logger.info("pywin32 not available; skipping Windows notification")
                return

            hwnd = self.root.winfo_id()
            title = "Applying DnDTools update"
            message = "Update is installing. The app will reopen shortly."
            icon_path = self._resolve_icon_path()

            hicon = 0
            extra_icons: list[int] = []
            try:
                large_icons, small_icons = win32gui.ExtractIconEx(icon_path, 0, 1)
                if large_icons:
                    hicon = large_icons[0]
                    extra_icons.extend(large_icons[1:])
                elif small_icons:
                    hicon = small_icons[0]
                    extra_icons.extend(small_icons[1:])
            except Exception:
                hicon = 0

            flags = win32con.NIF_INFO | win32con.NIF_TIP
            if hicon:
                flags |= win32con.NIF_ICON

            tip = "DnDTools Updater"
            nid = (
                hwnd,
                1,
                flags,
                0,
                hicon,
                tip,
                message,
                8000,
                title,
                win32con.NIIF_INFO,
            )

            try:
                win32gui.Shell_NotifyIcon(win32gui.NIM_ADD, nid)
                win32gui.Shell_NotifyIcon(win32gui.NIM_MODIFY, nid)
            except win32gui.error as exc:
                self.logger.warning("Failed to display Windows notification: %s", exc)
            finally:
                def _cleanup() -> None:
                    try:
                        win32gui.Shell_NotifyIcon(win32gui.NIM_DELETE, nid)
                    except Exception:
                        pass
                    for handle in [hicon, *extra_icons]:
                        if handle:
                            try:
                                win32gui.DestroyIcon(handle)
                            except Exception:
                                pass

                threading.Timer(9, _cleanup).start()

        self.root.after(0, _show_notification)

    def _cleanup_temp(self) -> None:
        try:
            if self._download_path and self._download_path.exists():
                self._download_path.unlink(missing_ok=True)
        except Exception:
            pass

        try:
            shutil.rmtree(self._temp_dir, ignore_errors=True)
        except Exception:
            pass


def _load_context(path: Path) -> tuple[UpdateManifest, dict[str, Any], dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    manifest = UpdateManifest.from_dict(payload.get("manifest") or {})
    app_info = payload.get("app") or {}
    options = payload.get("options") or {}
    return manifest, app_info, options


def _parse_arguments(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="DnDTools Updater")
    parser.add_argument("--context", help="Path to update context JSON file")
    parser.add_argument("--manifest", help="Explicit manifest JSON file", default=None)
    parser.add_argument("--manifest-url", help="Manifest URL to fetch if no context provided", default=None)
    parser.add_argument("--silent", action="store_true", help="Apply update silently")
    parser.add_argument("--no-launch", action="store_true", help="Do not offer to relaunch the app")
    parser.add_argument("--demo", action="store_true", help="Run a demo UI with a phantom update process")
    return parser.parse_args(argv)


def _load_manifest_from_file(path: Path) -> UpdateManifest:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    return UpdateManifest.from_dict(payload)


def main(argv: Optional[list[str]] = None) -> None:
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(message)s")
    args = _parse_arguments(argv)

    manifest: Optional[UpdateManifest] = None
    app_info: dict[str, Any] = {}
    options: dict[str, Any] = {"silent": args.silent}
    has_manifest_args = any((args.context, args.manifest, args.manifest_url))
    demo_mode = bool(args.demo or not has_manifest_args)

    if args.context:
        context_path = Path(args.context)
        manifest, app_info, context_options = _load_context(context_path)
        options.update(context_options)
        try:
            context_path.unlink(missing_ok=True)
        except Exception:
            pass
    elif args.manifest:
        manifest = _load_manifest_from_file(Path(args.manifest))
    elif args.manifest_url:
        payload = UpdateManager("0", args.manifest_url).fetch_manifest(force=True, channel="stable")
        if payload is None:
            raise UpdateError("Unable to download manifest")
        manifest = payload
    elif demo_mode:
        phantom_pid = 424242
        manifest = UpdateManifest(
            version="demo-3.5.99",
            url="https://example.com/DnDTools-demo-installer.exe",
            notes="Demo run – no files will be downloaded."
        )
        app_info = {"phantom": True, "pid": phantom_pid, "executable": str(Path(__file__).resolve())}
        options.update({
            "silent": False,
            "demo": True,
            "no_launch": True,
            "phantom_pid": phantom_pid,
        })
    else:
        raise UpdateError("No manifest information supplied")

    if manifest is None:
        raise UpdateError("Manifest could not be resolved")

    if args.no_launch:
        options["no_launch"] = True
    if args.demo and not options.get("demo"):
        options["demo"] = True
        options.setdefault("silent", False)
        options.setdefault("no_launch", True)
        options.setdefault("phantom_pid", 424242)

    updater = UpdaterUI(manifest, app_info, options)
    updater.run()


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    try:
        main()
    except Exception as exc:  # pragma: no cover - best-effort logging
        logging.basicConfig(level=logging.ERROR)
        LOGGER.error("Updater failed: %s", exc, exc_info=True)
        raise
