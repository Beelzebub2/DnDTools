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
        """Copy the updater to a temp directory and launch it from there.

        Why: ``update.exe`` lives inside the application install directory.
        Inno Setup's ``CloseApplications=force`` will kill every process
        whose executable resides under ``{app}``.  By copying the updater
        to ``%TEMP%`` first, Inno Setup cannot touch us and the updater
        stays alive through the entire installation, giving the user
        continuous visual feedback.
        """
        candidate, is_executable = self._resolve_updater_candidate()

        # ── Copy the updater binary to a temp location ──────────────
        staging_dir = Path(tempfile.gettempdir()) / "DnDToolsUpdate" / "bin"
        staging_dir.mkdir(parents=True, exist_ok=True)

        if is_executable:
            staged_exe = staging_dir / candidate.name
            try:
                shutil.copy2(str(candidate), str(staged_exe))
            except Exception as exc:
                # Do NOT silently fall back to launching from {app}.
                # The updater MUST run from %TEMP% so that Inno Setup's
                # CloseApplications=force doesn't kill it mid-install.
                raise UpdateError(
                    f"Could not copy updater to temp directory: {exc}. "
                    "Please check disk space and permissions on your temp folder.",
                    status_code=500,
                )
            args = [str(staged_exe), "--context", str(context_path)]
        else:
            python_exe = sys.executable
            args = [python_exe, str(candidate), "--context", str(context_path)]

        if self.auto_update_silent:
            args.append("--silent")

        popen_kwargs: dict[str, Any] = {
            "close_fds": False,
            "cwd": str(staging_dir) if is_executable else str(candidate.parent),
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


class _UpdateStep:
    """Describes a single step in the updater workflow for UI display."""

    __slots__ = ("key", "label", "icon_done", "icon_active", "icon_pending")

    def __init__(self, key: str, label: str) -> None:
        self.key = key
        self.label = label
        self.icon_done = "✓"
        self.icon_active = "›"
        self.icon_pending = " "


# The ordered list of steps shown on the left rail of the updater window.
_STEPS: list[_UpdateStep] = [
    _UpdateStep("close", "Close DnDTools"),
    _UpdateStep("download", "Download update"),
    _UpdateStep("verify", "Verify download"),
    _UpdateStep("install", "Install update"),
    _UpdateStep("done", "Done"),
]


class UpdaterUI:
    """Tkinter-based UI worker that orchestrates the update flow.

    The updater runs from a **temp directory** copy of ``update.exe`` so that
    it is *not* inside ``{app}`` and Inno Setup's ``CloseApplications=force``
    cannot kill it.  This lets us stay alive through the entire install and
    give the user continuous feedback.
    """

    def __init__(self, manifest: UpdateManifest, app_info: dict[str, Any], options: dict[str, Any]):
        self.manifest = manifest
        self.app_info = app_info
        self.options = options
        self.logger = LOGGER
        self._queue: "queue.Queue[tuple[str, Any]]" = queue.Queue()
        self._download_path: Optional[Path] = None
        self._temp_dir = Path(tempfile.mkdtemp(prefix="dndtools-update-"))
        self._installer_proc: Optional[subprocess.Popen[Any]] = None

        import tkinter as tk
        from tkinter import ttk

        self.tk = tk
        self.ttk = ttk

        # ── Colour palette (dark theme, gold accent) ──────────────────
        self.colors = {
            "bg": "#0b0b0b",
            "panel": "#111111",
            "panel_border": "#222222",
            "panel_hover": "#1a1a1a",
            "rail": "#0e0e0e",
            "text_primary": "#e6e6e6",
            "text_secondary": "#b0b0b0",
            "text_muted": "#888888",
            "text_subtle": "#555555",
            "accent": "#cfa346",
            "accent_hover": "#e0b85d",
            "accent_dark": "#8b6914",
            "success": "#5cb85c",
            "error": "#d9534f",
            "progress_trough": "#1a1a1a",
            "step_done": "#5cb85c",
            "step_active": "#cfa346",
            "step_pending": "#444444",
        }

        # ── Window ────────────────────────────────────────────────────
        self.root = tk.Tk()
        self.root.title("DnDTools Updater")
        self.root.configure(bg=self.colors["panel_border"])
        self.root.resizable(False, False)
        self.root.overrideredirect(True)  # frameless window

        # Centre the window on the primary monitor
        win_w, win_h = 540, 340
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        x = (screen_w - win_w) // 2
        y = (screen_h - win_h) // 2
        self.root.geometry(f"{win_w}x{win_h}+{x}+{y}")

        self.root.attributes("-topmost", True)
        self.root.after(3000, lambda: self.root.attributes("-topmost", False))

        try:
            self.root.iconbitmap(self._resolve_icon_path())
        except Exception:
            pass

        # ── Drag-to-move state ────────────────────────────────────────
        self._drag_x = 0
        self._drag_y = 0

        # ── Outer wrapper (1px border) ────────────────────────────────
        outer = tk.Frame(self.root, bg=self.colors["panel_border"])
        outer.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)

        # Card frame (the visible window body)
        card = tk.Frame(outer, bg=self.colors["panel"], bd=0)
        card.pack(expand=True, fill=tk.BOTH)

        # ── Custom title bar ──────────────────────────────────────────
        title_bar = tk.Frame(card, bg=self.colors["bg"], height=32, bd=0)
        title_bar.pack(fill=tk.X, side=tk.TOP)
        title_bar.pack_propagate(False)

        # Drag bindings on the title bar
        title_bar.bind("<ButtonPress-1>", self._on_drag_start)
        title_bar.bind("<B1-Motion>", self._on_drag_motion)

        title_bar_label = tk.Label(
            title_bar,
            text="  DnDTools Updater",
            fg=self.colors["text_muted"],
            bg=self.colors["bg"],
            font=("Segoe UI", 9),
            anchor="w",
        )
        title_bar_label.pack(side=tk.LEFT, fill=tk.Y, padx=(4, 0))
        title_bar_label.bind("<ButtonPress-1>", self._on_drag_start)
        title_bar_label.bind("<B1-Motion>", self._on_drag_motion)

        # Close button (✕) on the right side of the title bar
        self._close_btn = tk.Label(
            title_bar,
            text=" ✕ ",
            fg=self.colors["text_muted"],
            bg=self.colors["bg"],
            font=("Segoe UI", 11),
            cursor="hand2",
        )
        self._close_btn.pack(side=tk.RIGHT, padx=(0, 2))
        self._close_btn.bind("<Enter>", lambda _e: self._close_btn.configure(fg=self.colors["error"], bg="#2a1515"))
        self._close_btn.bind("<Leave>", lambda _e: self._close_btn.configure(fg=self.colors["text_muted"], bg=self.colors["bg"]))
        self._close_btn.bind("<ButtonRelease-1>", lambda _e: self._on_close_attempt())

        # Accent strip below the title bar
        tk.Frame(card, bg=self.colors["accent"], height=2, bd=0).pack(fill=tk.X, side=tk.TOP)

        body = tk.Frame(card, bg=self.colors["panel"], bd=0)
        body.pack(fill=tk.BOTH, expand=True)

        # ── Left rail: step indicators ────────────────────────────────
        rail = tk.Frame(body, bg=self.colors["rail"], width=170, bd=0)
        rail.pack(side=tk.LEFT, fill=tk.Y)
        rail.pack_propagate(False)

        rail_pad = tk.Frame(rail, bg=self.colors["rail"], bd=0)
        rail_pad.pack(fill=tk.BOTH, expand=True, padx=16, pady=(24, 16))

        tk.Label(
            rail_pad,
            text="Update steps",
            fg=self.colors["text_muted"],
            bg=self.colors["rail"],
            font=("Segoe UI", 9),
            anchor="w",
        ).pack(anchor="w", pady=(0, 12))

        self._step_labels: dict[str, tk.Label] = {}
        self._step_icons: dict[str, tk.Label] = {}
        for step in _STEPS:
            row = tk.Frame(rail_pad, bg=self.colors["rail"], bd=0)
            row.pack(anchor="w", fill=tk.X, pady=3)
            icon_lbl = tk.Label(
                row,
                text=step.icon_pending,
                fg=self.colors["step_pending"],
                bg=self.colors["rail"],
                font=("Consolas", 11),
                width=2,
                anchor="w",
            )
            icon_lbl.pack(side=tk.LEFT)
            text_lbl = tk.Label(
                row,
                text=step.label,
                fg=self.colors["step_pending"],
                bg=self.colors["rail"],
                font=("Segoe UI", 10),
                anchor="w",
            )
            text_lbl.pack(side=tk.LEFT)
            self._step_icons[step.key] = icon_lbl
            self._step_labels[step.key] = text_lbl

        # Version tag at the bottom of the rail
        ver_text = f"→ v{self.manifest.version}" if self.manifest else ""
        tk.Label(
            rail_pad,
            text=ver_text,
            fg=self.colors["text_subtle"],
            bg=self.colors["rail"],
            font=("Segoe UI", 9),
            anchor="w",
        ).pack(side=tk.BOTTOM, anchor="w")

        # ── Right pane: status + progress ─────────────────────────────
        right = tk.Frame(body, bg=self.colors["panel"], bd=0)
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        content = tk.Frame(right, bg=self.colors["panel"], padx=24, pady=24, bd=0)
        content.pack(fill=tk.BOTH, expand=True)

        tk.Label(
            content,
            text="DnDTools Updater",
            fg=self.colors["accent"],
            bg=self.colors["panel"],
            font=("Segoe UI Semibold", 15),
            anchor="w",
        ).pack(anchor="w")

        self.status_var = tk.StringVar(value="Preparing update…")
        self.detail_var = tk.StringVar(value="")

        tk.Label(
            content,
            textvariable=self.status_var,
            fg=self.colors["text_primary"],
            bg=self.colors["panel"],
            font=("Segoe UI", 11),
            anchor="w",
        ).pack(anchor="w", pady=(16, 0))

        tk.Label(
            content,
            textvariable=self.detail_var,
            fg=self.colors["text_muted"],
            bg=self.colors["panel"],
            font=("Segoe UI", 9),
            wraplength=300,
            justify="left",
            anchor="w",
        ).pack(anchor="w", pady=(6, 16))

        # Progress bar
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
            thickness=10,
            troughrelief="flat",
            relief="flat",
        )
        self.progress = ttk.Progressbar(
            content,
            orient="horizontal",
            mode="determinate",
            style="Updater.Horizontal.TProgressbar",
        )
        self.progress.pack(fill=tk.X)
        self.progress.configure(value=0, maximum=100)

        # Buttons area
        self.buttons_frame = tk.Frame(content, bg=self.colors["panel"])
        self.buttons_frame.pack(anchor="e", fill=tk.X, pady=(20, 0))

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

    # ------------------------------------------------------------------
    # Icon path resolution
    # ------------------------------------------------------------------
    def _resolve_icon_path(self) -> str:
        base_dir = Path(__file__).resolve().parent
        icon_path = base_dir / "assets" / "logo.ico"
        if icon_path.exists():
            return str(icon_path)
        exe_dir = Path(sys.executable).resolve().parent
        for candidate in (exe_dir / "logo.ico", exe_dir / "assets" / "logo.ico"):
            if candidate.exists():
                return str(candidate)
        return str(base_dir / "logo.ico")

    # ------------------------------------------------------------------
    # Frameless window drag support
    # ------------------------------------------------------------------
    def _on_drag_start(self, event) -> None:
        self._drag_x = event.x
        self._drag_y = event.y

    def _on_drag_motion(self, event) -> None:
        x = self.root.winfo_x() + (event.x - self._drag_x)
        y = self.root.winfo_y() + (event.y - self._drag_y)
        self.root.geometry(f"+{x}+{y}")

    # ------------------------------------------------------------------
    # Run / close
    # ------------------------------------------------------------------
    def run(self) -> None:
        # Ensure the frameless window appears on the Windows taskbar.
        # overrideredirect hides it by default; re-adding the WS_EX_APPWINDOW
        # extended style forces the shell to show a taskbar button.
        if sys.platform.startswith("win"):
            try:
                import ctypes
                GWL_EXSTYLE = -20
                WS_EX_APPWINDOW = 0x00040000
                hwnd = int(self.root.frame(), 16)
                style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
                ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style | WS_EX_APPWINDOW)
                # Toggle visibility so the shell picks up the new style
                self.root.withdraw()
                self.root.after(10, self.root.deiconify)
            except Exception:
                pass

        self.root.after(200, self._workflow_thread.start)
        self.root.after(100, self._process_queue)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close_attempt)
        self.root.mainloop()

    def _on_close_attempt(self) -> None:
        if self._workflow_thread.is_alive():
            return
        self.root.destroy()

    # ------------------------------------------------------------------
    # Queue helpers (thread-safe UI updates)
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
                elif action == "step":
                    step_key, state = payload  # state: "active" | "done" | "pending"
                    self._update_step_indicator(step_key, state)
                elif action == "complete":
                    self._show_completion_buttons()
                elif action == "error":
                    message = str(payload or "Update failed")
                    self._show_error_buttons(message)
        except queue.Empty:
            pass
        finally:
            self.root.after(80, self._process_queue)

    # ------------------------------------------------------------------
    # Step-indicator helpers
    # ------------------------------------------------------------------
    def _update_step_indicator(self, key: str, state: str) -> None:
        icon_lbl = self._step_icons.get(key)
        text_lbl = self._step_labels.get(key)
        if not icon_lbl or not text_lbl:
            return

        step = next((s for s in _STEPS if s.key == key), None)
        if step is None:
            return

        if state == "done":
            icon_lbl.configure(text=step.icon_done, fg=self.colors["step_done"])
            text_lbl.configure(fg=self.colors["step_done"])
        elif state == "active":
            icon_lbl.configure(text=step.icon_active, fg=self.colors["step_active"])
            text_lbl.configure(fg=self.colors["text_primary"])
        else:
            icon_lbl.configure(text=step.icon_pending, fg=self.colors["step_pending"])
            text_lbl.configure(fg=self.colors["step_pending"])

    def _mark_step(self, key: str, state: str) -> None:
        """Thread-safe wrapper: enqueue a step-indicator change."""
        self._enqueue("step", (key, state))

    # ------------------------------------------------------------------
    # Button helpers
    # ------------------------------------------------------------------
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
                self.buttons_frame, "Launch DnDTools", self._launch_app, variant="accent",
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
            self.buttons_frame, "View logs", self._open_logs_directory, variant="accent",
        )
        open_logs_button.pack(side=self.tk.RIGHT, padx=(0, 8))

    # ------------------------------------------------------------------
    # Post-update actions
    # ------------------------------------------------------------------
    def _launch_app(self) -> None:
        executable = self.app_info.get("executable")
        if not executable:
            self.root.destroy()
            return

        exe_path = Path(executable)
        if not exe_path.exists():
            self.root.destroy()
            return

        self.status_var.set("Launching DnDTools…")
        self.detail_var.set("The updater will close shortly.")

        try:
            subprocess.Popen([str(exe_path)], cwd=str(exe_path.parent))
        except Exception as exc:
            self.logger.error("Failed to launch app after update: %s", exc)
            self.detail_var.set(f"Launch failed: {exc}")
            return

        # Give the app a moment to spin up, then close.
        self.root.after(3000, self.root.destroy)

    def _open_logs_directory(self) -> None:
        log_dir = self.options.get("log_directory") or self.app_info.get("log_directory")
        if not log_dir:
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

    # ==================================================================
    # Workflow
    # ==================================================================
    def _workflow(self) -> None:
        try:
            if self.options.get("demo"):
                self._run_demo_flow()
                return

            # Step 1 — Close DnDTools
            self._mark_step("close", "active")
            self._enqueue("status", ("Closing DnDTools…", "Waiting for the application to exit."))
            self._enqueue("progress", 5)
            self._ensure_application_closed()
            self._mark_step("close", "done")
            self._enqueue("progress", 15)

            # Step 2 — Download
            self._mark_step("download", "active")
            self._enqueue("status", ("Downloading update…", "Connecting to server…"))
            installer_path = self._download_installer()
            self._download_path = installer_path
            self._mark_step("download", "done")

            # Step 3 — Verify
            if self.manifest.sha256:
                self._mark_step("verify", "active")
                self._enqueue("status", ("Verifying download…", "Checking file integrity."))
                self._enqueue("progress", 78)
                self._verify_sha256(installer_path, self.manifest.sha256)
                self._mark_step("verify", "done")
            else:
                self._mark_step("verify", "done")
            self._enqueue("progress", 80)

            # Step 4 — Install (launch and monitor Inno Setup)
            self._mark_step("install", "active")
            self._enqueue("status", ("Installing update…", "This may take a moment."))
            self._run_installer(installer_path, wait=True)
            self._mark_step("install", "done")
            self._enqueue("progress", 98)

            # Step 5 — Done
            self._mark_step("done", "active")
            version = self.manifest.version if self.manifest else "latest"
            self._enqueue("status", ("Update complete!", f"DnDTools has been updated to v{version}."))
            self._enqueue("progress", 100)
            self._mark_step("done", "done")
            self._enqueue("complete")

        except Exception as exc:
            self.logger.error("Update workflow failed: %s", exc, exc_info=True)
            self._enqueue("status", ("Update failed", str(exc)))
            self._enqueue("error", str(exc))
        finally:
            self._cleanup_temp()

    def _run_demo_flow(self) -> None:
        steps = [
            ("close", "Closing DnDTools…", "Looking for running instances.", 15),
            ("download", "Downloading update…", "Simulated download (demo)", 65),
            ("verify", "Verifying download…", "Checking file integrity.", 78),
            ("install", "Installing update…", "Running installer (demo).", 95),
        ]
        progress = 0
        for key, status, detail, target in steps:
            self._mark_step(key, "active")
            self._enqueue("status", (status, detail))
            while progress < target:
                progress = min(target, progress + 3)
                self._enqueue("progress", progress)
                time.sleep(0.12)
            self._mark_step(key, "done")

        version = self.manifest.version if self.manifest else "demo"
        self._mark_step("done", "done")
        self._enqueue("status", ("Update complete!", f"DnDTools updated to v{version} (demo)."))
        self._enqueue("progress", 100)
        self._enqueue("complete")

    # ------------------------------------------------------------------
    # Step implementations
    # ------------------------------------------------------------------
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

        if not targets:
            return

        self._enqueue("status", ("Closing DnDTools…", f"Waiting for {len(targets)} process(es) to exit."))

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

        # Graceful termination
        for proc in polling_targets:
            try:
                proc.terminate()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        time.sleep(1.5)

        # Force-kill survivors
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

        with requests.get(url, stream=True, timeout=120) as response:
            response.raise_for_status()
            total = int(response.headers.get("Content-Length", "0") or 0)
            downloaded = 0
            chunk_size = 1024 * 256

            with target_path.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=chunk_size):
                    if not chunk:
                        continue
                    handle.write(chunk)
                    downloaded += len(chunk)
                    if total:
                        # Map download progress to 15 → 75 range
                        frac = downloaded / total
                        progress = 15 + frac * 60
                        self._enqueue("progress", progress)
                        dl_kb = downloaded // 1024
                        total_kb = total // 1024
                        if total_kb >= 1024:
                            self._enqueue(
                                "status",
                                ("Downloading update…", f"{dl_kb / 1024:.1f} MB / {total_kb / 1024:.1f} MB"),
                            )
                        else:
                            self._enqueue("status", ("Downloading update…", f"{dl_kb} KB / {total_kb} KB"))

        self._enqueue("progress", 75)
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
                # Do NOT pass /CLOSEAPPLICATIONS — we already closed
                # DnDTools ourselves.  That flag would also kill
                # update.exe if it were running from {app}.
                # Do NOT pass /RESTARTAPPLICATIONS — the updater UI
                # handles relaunching via the "Launch DnDTools" button.
            ])
        else:
            args.extend([
                "/SILENT",
                "/SUPPRESSMSGBOXES",
                "/NORESTART",
                "/SP-",
            ])

        self.logger.info("Launching installer: %s", " ".join(args))

        popen_kwargs: dict[str, Any] = {
            "cwd": str(installer_path.parent),
            "close_fds": False,
        }
        # Don't detach — we WANT to wait for the installer and keep our
        # window alive so the user sees progress.

        proc = subprocess.Popen(args, **popen_kwargs)
        self._installer_proc = proc

        if wait:
            # Poll so we can keep the UI responsive with status updates.
            elapsed = 0.0
            while proc.poll() is None:
                time.sleep(0.5)
                elapsed += 0.5
                # Animate install progress from 80 → 95
                install_progress = min(95, 80 + elapsed * 0.5)
                self._enqueue("progress", install_progress)
                if elapsed % 5 < 0.6:
                    self._enqueue("status", ("Installing update…", "Please wait, this may take a minute."))
            return_code = proc.returncode
            if return_code != 0:
                raise UpdateError(f"Installer exited with code {return_code}")

    def _cleanup_temp(self) -> None:
        """Best-effort cleanup of temp files after install finishes."""
        # At this point the installer has already exited (we waited), so
        # it is safe to remove the downloaded .exe.
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
