from __future__ import annotations

import ctypes
from ctypes import wintypes
import itertools
import logging
import sys
import threading
from dataclasses import dataclass
from typing import Callable, Dict, Optional, Tuple

try:
    import keyboard as _keyboard
except ImportError:  # pragma: no cover - optional dependency fallback
    _keyboard = None

__all__ = [
    "HotkeyError",
    "HotkeyParseError",
    "HotkeyRegistrationError",
    "HotkeyConflictError",
    "GlobalHotkeyManager",
    "canonicalize_hotkey",
]


class HotkeyError(RuntimeError):
    """Base exception for hotkey-related errors."""


class HotkeyParseError(HotkeyError):
    """Raised when a hotkey string cannot be parsed."""


class HotkeyRegistrationError(HotkeyError):
    """Raised when a hotkey cannot be registered with the system."""


class HotkeyConflictError(HotkeyRegistrationError):
    """Raised when two bindings would use the same hotkey."""


@dataclass(frozen=True)
class _ParsedHotkey:
    canonical: str
    modifiers: Tuple[str, ...]
    key: str
    modifier_mask: int
    vk_code: int
    keyboard_sequence: str


_MODIFIER_ALIASES = {
    "ctrl": "ctrl",
    "control": "ctrl",
    "alt": "alt",
    "shift": "shift",
    "win": "win",
    "windows": "win",
    "super": "win",
    "meta": "win",
    "cmd": "win",
}

_MODIFIER_ORDER = ("ctrl", "alt", "shift", "win")

_KEY_ALIASES = {
    "escape": "esc",
    "return": "enter",
    "spacebar": "space",
    "pgup": "pageup",
    "pgdn": "pagedown",
    "del": "delete",
    "ins": "insert",
    "bksp": "backspace",
    "caps": "capslock",
    "esc": "esc",
    "enter": "enter",
    "space": "space",
    "tab": "tab",
    "plus": "+",
    "+": "+",
}

_SPECIAL_VK = {
    "backspace": 0x08,
    "tab": 0x09,
    "enter": 0x0D,
    "capslock": 0x14,
    "esc": 0x1B,
    "space": 0x20,
    "pageup": 0x21,
    "pagedown": 0x22,
    "end": 0x23,
    "home": 0x24,
    "left": 0x25,
    "up": 0x26,
    "right": 0x27,
    "down": 0x28,
    "insert": 0x2D,
    "delete": 0x2E,
    "printscreen": 0x2C,
    "scrolllock": 0x91,
    "pause": 0x13,
    "numlock": 0x90,
    "apps": 0x5D,
    "volumeup": 0xAF,
    "volumedown": 0xAE,
    "volumemute": 0xAD,
    "mediaplaypause": 0xB3,
    "mediastop": 0xB2,
    "medianext": 0xB0,
    "mediaprevious": 0xB1,
    "+": 0xBB,
}

_KEYBOARD_MODIFIER_NAMES = {
    "ctrl": "ctrl",
    "alt": "alt",
    "shift": "shift",
    "win": "windows",
}

_KEYBOARD_KEY_ALIASES = {
    "esc": "esc",
    "pageup": "page up",
    "pagedown": "page down",
    "left": "left",
    "right": "right",
    "up": "up",
    "down": "down",
    "+": "+",
}

_WINDOWS_MODIFIER_BITS = {
    "alt": 0x0001,
    "ctrl": 0x0002,
    "shift": 0x0004,
    "win": 0x0008,
}


def canonicalize_hotkey(raw: str) -> str:
    """Return the canonical form of a hotkey string."""
    return _parse_hotkey(raw).canonical


def _parse_hotkey(raw: str) -> _ParsedHotkey:
    if raw is None:
        raise HotkeyParseError("Hotkey cannot be empty")

    tokens = [segment.strip() for segment in raw.replace("-", "+").split("+") if segment.strip()]
    if not tokens:
        raise HotkeyParseError("Hotkey cannot be empty")

    modifiers: list[str] = []
    key_token: Optional[str] = None

    for token in tokens:
        lowered = token.lower()
        if lowered in _MODIFIER_ALIASES:
            canonical_mod = _MODIFIER_ALIASES[lowered]
            if canonical_mod not in modifiers:
                modifiers.append(canonical_mod)
            continue

        if key_token is not None:
            raise HotkeyParseError("Hotkey may only specify one non-modifier key")

        mapped_key = _KEY_ALIASES.get(lowered, lowered)
        key_token = mapped_key

    if key_token is None:
        raise HotkeyParseError("Hotkey must include a non-modifier key")

    ordered_modifiers = tuple(sorted(modifiers, key=_MODIFIER_ORDER.index))
    canonical = "+".join(ordered_modifiers + (key_token,)) if ordered_modifiers else key_token

    modifier_mask = 0
    for modifier in ordered_modifiers:
        modifier_mask |= _WINDOWS_MODIFIER_BITS[modifier]

    vk_code = _key_to_vk(key_token)
    keyboard_modifiers = [_KEYBOARD_MODIFIER_NAMES[m] for m in ordered_modifiers]
    keyboard_key = _KEYBOARD_KEY_ALIASES.get(key_token, key_token)
    keyboard_sequence = "+".join(keyboard_modifiers + [keyboard_key]) if keyboard_modifiers else keyboard_key

    return _ParsedHotkey(
        canonical=canonical,
        modifiers=ordered_modifiers,
        key=key_token,
        modifier_mask=modifier_mask,
        vk_code=vk_code,
        keyboard_sequence=keyboard_sequence,
    )


def _key_to_vk(key: str) -> int:
    if len(key) == 1:
        upper = key.upper()
        if "A" <= upper <= "Z" or "0" <= upper <= "9":
            return ord(upper)

    if key.startswith("f") and key[1:].isdigit():
        index = int(key[1:])
        if 1 <= index <= 24:
            return 0x6F + index

    if key.startswith("numpad") and key[6:].isdigit():
        index = int(key[6:])
        if 0 <= index <= 9:
            return 0x60 + index

    if key == "multiply":
        return 0x6A
    if key == "add":
        return 0x6B
    if key == "subtract":
        return 0x6D
    if key == "decimal":
        return 0x6E
    if key == "divide":
        return 0x6F

    if key in _SPECIAL_VK:
        return _SPECIAL_VK[key]

    raise HotkeyParseError(f"Unsupported hotkey key: {key}")


@dataclass
class _WindowsBinding:
    native_id: int
    parsed: _ParsedHotkey
    callback: Callable[[], None]


@dataclass
class _KeyboardBinding:
    handle: int
    parsed: _ParsedHotkey
    callback: Callable[[], None]


class _WindowsHotkeyBackend:
    WM_HOTKEY = 0x0312
    WM_QUIT = 0x0012
    WM_APP = 0x8000
    COMMAND_APPLY = WM_APP + 1
    COMMAND_SHUTDOWN = WM_APP + 2

    def __init__(self, logger: logging.Logger) -> None:
        if not sys.platform.startswith("win"):
            raise HotkeyRegistrationError("Windows hotkey backend is only available on Windows")

        self._logger = logger
        self._user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        self._kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        self._bindings: Dict[str, _WindowsBinding] = {}
        self._id_lookup: Dict[int, str] = {}
        self._lock = threading.RLock()
        self._thread: Optional[threading.Thread] = None
        self._thread_id: Optional[int] = None
        self._ready = threading.Event()
        self._pending_commands: Dict[int, Dict[str, object]] = {}
        self._hotkey_id_source = itertools.count(1)
        self._command_id_source = itertools.count(1)

    def apply(self, parsed_bindings: Dict[str, Tuple[Optional[_ParsedHotkey], Callable[[], None]]]) -> Dict[str, str]:
        return self._submit_command(self.COMMAND_APPLY, parsed_bindings)

    def _submit_command(self, message: int, payload: Optional[object]) -> Dict[str, str]:
        self._ensure_thread()

        if self._thread_id is None:
            raise HotkeyRegistrationError("Hotkey listener thread is not available")

        command_id = next(self._command_id_source)
        completion = threading.Event()

        with self._lock:
            self._pending_commands[command_id] = {
                "payload": payload,
                "event": completion,
                "result": None,
                "error": None,
            }

        if not self._user32.PostThreadMessageW(self._thread_id, message, command_id, 0):
            with self._lock:
                self._pending_commands.pop(command_id, None)
            raise HotkeyRegistrationError("Failed to communicate with hotkey listener thread")

        if not completion.wait(timeout=5.0):
            with self._lock:
                entry = self._pending_commands.pop(command_id, None)
            raise HotkeyRegistrationError("Timed out while applying global hotkey changes")

        with self._lock:
            entry = self._pending_commands.pop(command_id, None)

        if entry is None:
            raise HotkeyRegistrationError("Lost response from hotkey listener thread")

        if entry["error"]:
            raise entry["error"]  # type: ignore[misc]

        result = entry["result"]
        return result if isinstance(result, dict) else {}

    def shutdown(self) -> None:
        thread: Optional[threading.Thread]
        with self._lock:
            thread = self._thread

        if not thread:
            return

        if not thread.is_alive():
            with self._lock:
                self._thread = None
                self._thread_id = None
                self._ready.clear()
                self._id_lookup.clear()
                self._bindings.clear()
                self._pending_commands.clear()
            return

        try:
            self._submit_command(self.COMMAND_SHUTDOWN, None)
        except HotkeyError:
            raise
        except Exception as exc:  # pragma: no cover - defensive
            raise HotkeyRegistrationError(str(exc)) from exc
        finally:
            thread.join(timeout=2.0)
            with self._lock:
                self._thread = None
                self._thread_id = None
                self._ready.clear()
                self._id_lookup.clear()
                self._bindings.clear()
                self._pending_commands.clear()

    def _register_hotkey(self, parsed: _ParsedHotkey) -> int:
        native_id = next(self._hotkey_id_source)
        if not self._user32.RegisterHotKey(None, native_id, parsed.modifier_mask, parsed.vk_code):
            error = ctypes.WinError()
            raise HotkeyRegistrationError(
                f"Unable to register hotkey '{parsed.canonical}': {error.strerror} (0x{error.winerror:08X})"
            )
        return native_id

    def _unregister_locked(self, label: str) -> None:
        binding = self._bindings.pop(label, None)
        if not binding:
            return
        self._user32.UnregisterHotKey(None, binding.native_id)
        self._id_lookup.pop(binding.native_id, None)

    def _ensure_thread(self) -> None:
        if self._thread and self._thread.is_alive():
            return

        self._ready.clear()
        self._thread = threading.Thread(target=self._message_loop, name="HotkeyMessageLoop", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout=2.0):
            raise HotkeyRegistrationError("Hotkey listener thread failed to start")

    def _message_loop(self) -> None:
        self._thread_id = int(self._kernel32.GetCurrentThreadId())
        msg = wintypes.MSG()
        # Ensure the thread has a message queue before signaling readiness
        self._user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)
        self._ready.set()

        try:
            while True:
                result = self._user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if result == 0:
                    break
                if result == -1:
                    self._logger.error("Hotkey message loop encountered an error: %s", ctypes.WinError())
                    break

                if msg.message == self.WM_HOTKEY:
                    hotkey_id = int(msg.wParam)
                    callback: Optional[Callable[[], None]] = None
                    with self._lock:
                        label = self._id_lookup.get(hotkey_id)
                        if label:
                            binding = self._bindings.get(label)
                            if binding:
                                callback = binding.callback
                    if callback:
                        try:
                            callback()
                        except Exception:  # pragma: no cover - defensive
                            self._logger.exception("Unhandled exception in hotkey callback")
                elif msg.message == self.COMMAND_APPLY:
                    command_id = int(msg.wParam)
                    entry = self._get_pending_command(command_id)
                    if entry is None:
                        continue

                    try:
                        parsed_bindings = entry["payload"]
                        if not isinstance(parsed_bindings, dict):
                            raise HotkeyError("Invalid hotkey registration payload")
                        entry["result"] = self._apply_bindings_internal(parsed_bindings)
                    except Exception as exc:  # pragma: no cover - defensive
                        entry["error"] = exc
                    finally:
                        entry["event"].set()
                elif msg.message == self.COMMAND_SHUTDOWN:
                    command_id = int(msg.wParam)
                    entry = self._get_pending_command(command_id)
                    try:
                        self._teardown_bindings()
                    except Exception as exc:  # pragma: no cover - defensive
                        if entry is not None:
                            entry["error"] = exc
                    finally:
                        if entry is not None:
                            entry["result"] = {}
                            entry["event"].set()
                    break
                else:
                    self._user32.TranslateMessage(ctypes.byref(msg))
                    self._user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            self._thread_id = None

    def _get_pending_command(self, command_id: int) -> Optional[Dict[str, object]]:
        with self._lock:
            entry = self._pending_commands.get(command_id)
            return entry

    def _apply_bindings_internal(
        self,
        parsed_bindings: Dict[str, Tuple[Optional[_ParsedHotkey], Callable[[], None]]],
    ) -> Dict[str, str]:
        canonical_map: Dict[str, str] = {}

        with self._lock:
            current_labels = set(self._bindings.keys())
            new_labels = set(parsed_bindings.keys())

            for label in current_labels - new_labels:
                self._unregister_locked(label)

            for label in new_labels:
                parsed, _ = parsed_bindings[label]
                if parsed is None:
                    self._unregister_locked(label)

            for label, (parsed, callback) in parsed_bindings.items():
                if parsed is None:
                    continue

                existing = self._bindings.get(label)
                if existing and existing.parsed.canonical == parsed.canonical:
                    existing.callback = callback
                    canonical_map[label] = parsed.canonical
                    continue

                for other_label, binding in self._bindings.items():
                    if other_label != label and binding.parsed.canonical == parsed.canonical:
                        raise HotkeyConflictError(
                            f"Hotkey '{parsed.canonical}' is already assigned to '{other_label}'"
                        )

                native_id = self._register_hotkey(parsed)
                try:
                    if existing:
                        self._user32.UnregisterHotKey(None, existing.native_id)
                        self._id_lookup.pop(existing.native_id, None)

                    self._bindings[label] = _WindowsBinding(native_id, parsed, callback)
                    self._id_lookup[native_id] = label
                    canonical_map[label] = parsed.canonical
                except Exception:
                    self._user32.UnregisterHotKey(None, native_id)
                    raise

        return canonical_map

    def _teardown_bindings(self) -> None:
        with self._lock:
            for label in list(self._bindings.keys()):
                self._unregister_locked(label)
            self._bindings.clear()
            self._id_lookup.clear()


class _KeyboardBackend:
    def __init__(self, logger: logging.Logger) -> None:
        if _keyboard is None:
            raise HotkeyRegistrationError("keyboard module is required for non-Windows hotkeys")
        self._logger = logger
        self._bindings: Dict[str, _KeyboardBinding] = {}
        self._lock = threading.RLock()

    def apply(self, parsed_bindings: Dict[str, Tuple[Optional[_ParsedHotkey], Callable[[], None]]]) -> Dict[str, str]:
        canonical_map: Dict[str, str] = {}
        with self._lock:
            current_labels = set(self._bindings.keys())
            new_labels = set(parsed_bindings.keys())

            for label in current_labels - new_labels:
                self._remove_locked(label)

            for label in new_labels:
                parsed, _ = parsed_bindings[label]
                if parsed is None:
                    self._remove_locked(label)

            for label, (parsed, callback) in parsed_bindings.items():
                if parsed is None:
                    continue

                existing = self._bindings.get(label)
                if existing and existing.parsed.canonical == parsed.canonical:
                    existing.callback = callback
                    canonical_map[label] = parsed.canonical
                    continue

                for other_label, binding in self._bindings.items():
                    if other_label != label and binding.parsed.canonical == parsed.canonical:
                        raise HotkeyConflictError(
                            f"Hotkey '{parsed.canonical}' is already assigned to '{other_label}'"
                        )

                handle = _keyboard.add_hotkey(parsed.keyboard_sequence, callback, suppress=False)
                try:
                    if existing:
                        self._remove_locked(label)
                    self._bindings[label] = _KeyboardBinding(handle, parsed, callback)
                    canonical_map[label] = parsed.canonical
                except Exception:
                    _keyboard.remove_hotkey(handle)
                    raise

        return canonical_map

    def shutdown(self) -> None:
        with self._lock:
            for label in list(self._bindings.keys()):
                self._remove_locked(label)

    def _remove_locked(self, label: str) -> None:
        binding = self._bindings.pop(label, None)
        if not binding:
            return
        try:
            _keyboard.remove_hotkey(binding.handle)
        except Exception:  # pragma: no cover - defensive
            self._logger.debug("Failed to remove hotkey '%s'", binding.parsed.canonical)


class GlobalHotkeyManager:
    """Facade for registering and managing global hotkeys."""

    def __init__(self, logger: Optional[logging.Logger] = None) -> None:
        self._logger = logger or logging.getLogger(__name__)
        if sys.platform.startswith("win"):
            self._backend = _WindowsHotkeyBackend(self._logger)
        else:
            self._backend = _KeyboardBackend(self._logger)
        self._lock = threading.RLock()

    def apply_bindings(self, bindings: Dict[str, Tuple[str, Callable[[], None]]]) -> Dict[str, str]:
        if not isinstance(bindings, dict):
            raise HotkeyError("Bindings must be provided as a dictionary")

        parsed_bindings: Dict[str, Tuple[Optional[_ParsedHotkey], Callable[[], None]]] = {}
        seen: Dict[str, str] = {}

        for label, payload in bindings.items():
            if not isinstance(payload, tuple) or len(payload) != 2:
                raise HotkeyError(f"Binding payload for '{label}' must be a tuple of (hotkey, callback)")
            hotkey, callback = payload
            if not callable(callback):
                raise HotkeyError(f"Callback for hotkey '{label}' is not callable")

            hotkey_value = (hotkey or "").strip()
            if not hotkey_value:
                parsed_bindings[label] = (None, callback)
                continue

            parsed = _parse_hotkey(hotkey_value)
            duplicate_label = seen.get(parsed.canonical)
            if duplicate_label and duplicate_label != label:
                raise HotkeyConflictError(
                    f"Hotkey '{parsed.canonical}' is already assigned to '{duplicate_label}'"
                )
            seen[parsed.canonical] = label
            parsed_bindings[label] = (parsed, callback)

        with self._lock:
            return self._backend.apply(parsed_bindings)

    def shutdown(self) -> None:
        with self._lock:
            self._backend.shutdown()
