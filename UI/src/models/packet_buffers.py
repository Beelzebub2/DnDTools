"""Bounded packet history and TCP stream framing helpers.

The capture loop receives individual TCP segments.  Segments from separate
connections must never share framing state, and retransmitted bytes must not be
fed into the protobuf decoder twice.  This module keeps those concerns small,
deterministic, and independent from pyshark so they can be regression-tested.
"""

from __future__ import annotations

import json
import struct
import threading
import time
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from typing import Any, Callable, Deque, Dict, Iterable, Iterator, List, Optional, Tuple


DEFAULT_STREAM_ID = "__default__"


def estimate_json_size(value: Any, max_bytes: Optional[int] = None) -> int:
    """Return the UTF-8 JSON size without building one large JSON string.

    When ``max_bytes`` is supplied, iteration stops as soon as the value is
    known to exceed the limit.  The returned value is then a lower bound, which
    is sufficient for deciding whether a packet-viewer payload should be kept.
    """

    encoder = json.JSONEncoder(
        ensure_ascii=False,
        separators=(",", ":"),
        check_circular=True,
    )
    total = 0
    for chunk in encoder.iterencode(value):
        total += len(chunk.encode("utf-8"))
        if max_bytes is not None and total > max_bytes:
            break
    return total


class BoundedPacketHistory:
    """Thread-safe packet history bounded by both record count and JSON bytes."""

    def __init__(self, max_packets: int = 1000, max_bytes: int = 16 * 1024 * 1024):
        if max_packets <= 0:
            raise ValueError("max_packets must be positive")
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        self.max_packets = int(max_packets)
        self.max_bytes = int(max_bytes)
        self._entries: Deque[Tuple[Dict[str, Any], int]] = deque()
        self._total_bytes = 0
        self._lock = threading.RLock()

    @property
    def maxlen(self) -> int:
        """Compatibility with the deque previously exposed by PacketCapture."""

        return self.max_packets

    @property
    def total_bytes(self) -> int:
        with self._lock:
            return self._total_bytes

    def append(self, packet: Dict[str, Any], size_bytes: Optional[int] = None) -> bool:
        """Append a packet, evicting oldest entries until both limits hold.

        A single entry larger than the full history budget is rejected.  The
        capture layer normally replaces oversized JSON with a small diagnostic
        marker before calling this method.
        """

        if size_bytes is None:
            size_bytes = estimate_json_size(packet, max_bytes=self.max_bytes)
        size = max(1, int(size_bytes))
        if size > self.max_bytes:
            return False

        with self._lock:
            self._entries.append((packet, size))
            self._total_bytes += size
            while (
                len(self._entries) > self.max_packets
                or self._total_bytes > self.max_bytes
            ):
                _discarded, discarded_size = self._entries.popleft()
                self._total_bytes -= discarded_size
        return True

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._total_bytes = 0

    def snapshot(
        self,
        *,
        after_id: Optional[int] = None,
        packet_types: Optional[Iterable[str]] = None,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Return a stable snapshot suitable for Flask/JSON serialization.

        Initial reads return the newest ``limit`` records. Incremental reads
        return the oldest ``limit`` records after ``after_id`` so no ids are
        skipped when more than one page arrived between polls.
        """

        allowed = set(packet_types) if packet_types is not None else None
        with self._lock:
            records = [
                packet
                for packet, _size in self._entries
                if (after_id is None or int(packet.get("id", 0)) > after_id)
                and (allowed is None or packet.get("type") in allowed)
            ]

        if limit is not None:
            bounded_limit = max(0, int(limit))
            if bounded_limit == 0:
                return []
            if after_id is None:
                records = records[-bounded_limit:]
            else:
                records = records[:bounded_limit]
        return records

    def packet_types(self) -> List[str]:
        with self._lock:
            return sorted(
                {
                    packet.get("type")
                    for packet, _size in self._entries
                    if packet.get("type")
                }
            )

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def __iter__(self) -> Iterator[Dict[str, Any]]:
        return iter(self.snapshot())


@dataclass
class _TCPStreamState:
    frame_buffer: bytearray = field(default_factory=bytearray)
    next_sequence: Optional[int] = None
    sequence_anchor: Optional[int] = None
    pending_segments: Dict[int, bytes] = field(default_factory=dict)
    pending_bytes: int = 0
    last_seen: float = field(default_factory=time.monotonic)


class FramedPacketStreams:
    """Reassemble sequence-aware TCP streams into length-prefixed packets."""

    def __init__(
        self,
        validate_header: Callable[[int, int, int], bool],
        on_packet: Callable[[bytes, int], None],
        *,
        max_packet_size: int = 2 * 1024 * 1024,
        max_pending_bytes: int = 2 * 1024 * 1024,
        max_total_buffered_bytes: int = 32 * 1024 * 1024,
        max_streams: int = 64,
        idle_timeout: float = 300.0,
        max_frames_per_feed: int = 4096,
        on_desync: Optional[Callable[[str, int, str], None]] = None,
    ):
        self._validate_header = validate_header
        self._on_packet = on_packet
        self.max_packet_size = int(max_packet_size)
        self.max_pending_bytes = int(max_pending_bytes)
        self.max_total_buffered_bytes = int(max_total_buffered_bytes)
        self.max_streams = int(max_streams)
        self.idle_timeout = float(idle_timeout)
        self.max_frames_per_feed = int(max_frames_per_feed)
        if min(
            self.max_packet_size,
            self.max_pending_bytes,
            self.max_total_buffered_bytes,
            self.max_streams,
            self.max_frames_per_feed,
        ) <= 0:
            raise ValueError("packet stream limits must be positive")
        if self.idle_timeout <= 0:
            raise ValueError("idle_timeout must be positive")
        self._on_desync = on_desync
        self._streams: "OrderedDict[str, _TCPStreamState]" = OrderedDict()
        self._feed_count = 0

    def feed(
        self,
        stream_id: Optional[Any],
        data: bytes,
        sequence: Optional[int] = None,
        *,
        out_of_order: bool = False,
    ) -> int:
        payload = bytes(data or b"")
        if not payload:
            return 0

        key = DEFAULT_STREAM_ID if stream_id is None else str(stream_id)
        state = self._get_state(key)
        state.last_seen = time.monotonic()

        self._feed_count += 1
        if self._feed_count % 128 == 0:
            self._expire_idle_streams(state.last_seen)

        if sequence is None:
            return self._finish_feed(self._feed_contiguous(key, state, payload))

        try:
            segment_start = int(sequence)
        except (TypeError, ValueError):
            return self._finish_feed(self._feed_contiguous(key, state, payload))

        segment_start = self._unwrap_sequence(state, segment_start)

        if state.next_sequence is None and out_of_order:
            # tshark can deliver a later segment first and explicitly marks it
            # as out-of-order.  Do not make that segment the stream baseline:
            # doing so would classify the missing, earlier bytes as a stale
            # retransmission when they arrive.  Hold it under the same bounded
            # gap budget used after a baseline has been established.
            # The segment that fills the earlier gap can itself carry an
            # out-of-order analysis flag.  Once a lower sequence arrives, it
            # is still a safer baseline than any later segment already held.
            if (
                state.pending_segments
                and segment_start < min(state.pending_segments)
            ):
                state.next_sequence = segment_start + len(payload)
                state.sequence_anchor = state.next_sequence
                emitted = self._feed_contiguous(key, state, payload)
                return self._finish_feed(
                    emitted + self._drain_pending(key, state)
                )
            return self._finish_feed(
                self._store_pending(key, state, segment_start, payload)
            )

        if state.next_sequence is None:
            state.next_sequence = segment_start + len(payload)
            state.sequence_anchor = state.next_sequence
            emitted = self._feed_contiguous(key, state, payload)
            return self._finish_feed(emitted + self._drain_pending(key, state))

        if segment_start < state.next_sequence:
            overlap = state.next_sequence - segment_start
            if overlap >= len(payload):
                return self._finish_feed(0)
            payload = payload[overlap:]
            segment_start = state.next_sequence

        if segment_start == state.next_sequence:
            state.next_sequence += len(payload)
            state.sequence_anchor = state.next_sequence
            emitted = self._feed_contiguous(key, state, payload)
            return self._finish_feed(emitted + self._drain_pending(key, state))

        # A gap remains. Keep the segment until the missing sequence arrives.
        return self._finish_feed(
            self._store_pending(key, state, segment_start, payload)
        )

    def clear(self, stream_id: Optional[Any] = None) -> None:
        if stream_id is None:
            self._streams.clear()
            return
        key = str(stream_id)
        self._streams.pop(key, None)

    def get_buffer(self, stream_id: Optional[Any] = None) -> bytes:
        key = DEFAULT_STREAM_ID if stream_id is None else str(stream_id)
        state = self._streams.get(key)
        return bytes(state.frame_buffer) if state else b""

    @property
    def buffered_bytes(self) -> int:
        return sum(
            len(state.frame_buffer) + state.pending_bytes
            for state in self._streams.values()
        )

    @property
    def stream_count(self) -> int:
        return len(self._streams)

    def _get_state(self, key: str) -> _TCPStreamState:
        state = self._streams.pop(key, None)
        if state is None:
            state = _TCPStreamState()
        self._streams[key] = state

        while len(self._streams) > self.max_streams:
            evicted_key, evicted = self._streams.popitem(last=False)
            self._notify_desync(
                evicted_key,
                len(evicted.frame_buffer) + evicted.pending_bytes,
                "too many active TCP streams",
            )
        return state

    def _expire_idle_streams(self, now: float) -> None:
        stale = [
            key
            for key, state in self._streams.items()
            if now - state.last_seen > self.idle_timeout
        ]
        for key in stale:
            self._streams.pop(key, None)

    def _drain_pending(self, key: str, state: _TCPStreamState) -> int:
        emitted = 0
        while state.pending_segments and state.next_sequence is not None:
            progressed = False
            for start in sorted(state.pending_segments):
                if start > state.next_sequence:
                    break
                segment = state.pending_segments.pop(start)
                state.pending_bytes -= len(segment)
                end = start + len(segment)
                if end <= state.next_sequence:
                    progressed = True
                    break
                offset = state.next_sequence - start
                contiguous = segment[offset:]
                state.next_sequence += len(contiguous)
                state.sequence_anchor = state.next_sequence
                emitted += self._feed_contiguous(key, state, contiguous)
                progressed = True
                break
            if not progressed:
                break
        return emitted

    def _store_pending(
        self,
        key: str,
        state: _TCPStreamState,
        segment_start: int,
        payload: bytes,
    ) -> int:
        previous = state.pending_segments.get(segment_start)
        if previous is None or len(payload) > len(previous):
            if previous is not None:
                state.pending_bytes -= len(previous)
            state.pending_segments[segment_start] = payload
            state.pending_bytes += len(payload)
            if state.sequence_anchor is None:
                state.sequence_anchor = segment_start

        if state.pending_bytes <= self.max_pending_bytes:
            return 0

        # Do not let a permanently missing TCP segment grow memory without
        # bound. Resume from the newest segment and let framing resynchronize.
        self._notify_desync(
            key,
            state.pending_bytes,
            "pending TCP gap exceeded memory limit",
        )
        newest_start = max(state.pending_segments)
        newest = state.pending_segments[newest_start]
        state.pending_segments.clear()
        state.pending_bytes = 0
        state.frame_buffer.clear()
        state.next_sequence = newest_start + len(newest)
        state.sequence_anchor = state.next_sequence
        return self._feed_contiguous(key, state, newest)

    @staticmethod
    def _unwrap_sequence(state: _TCPStreamState, sequence: int) -> int:
        """Map a 32-bit TCP sequence to the nearest position on a 64-bit line."""
        modulus = 1 << 32
        if sequence < 0 or sequence >= modulus:
            return sequence

        reference = state.next_sequence
        if reference is None:
            reference = state.sequence_anchor
        if reference is None:
            state.sequence_anchor = sequence
            return sequence

        cycle = (reference // modulus) * modulus
        candidates = (
            sequence + cycle - modulus,
            sequence + cycle,
            sequence + cycle + modulus,
        )
        return min(candidates, key=lambda candidate: abs(candidate - reference))

    def _finish_feed(self, emitted: int) -> int:
        self._enforce_total_buffer_limit()
        return emitted

    def _enforce_total_buffer_limit(self) -> None:
        total = self.buffered_bytes
        while self._streams and total > self.max_total_buffered_bytes:
            evicted_key, evicted = self._streams.popitem(last=False)
            evicted_bytes = len(evicted.frame_buffer) + evicted.pending_bytes
            total -= evicted_bytes
            self._notify_desync(
                evicted_key,
                evicted_bytes,
                "global TCP reassembly memory limit exceeded",
            )

    def _feed_contiguous(
        self,
        key: str,
        state: _TCPStreamState,
        data: bytes,
    ) -> int:
        state.frame_buffer.extend(data)
        emitted = 0

        while emitted < self.max_frames_per_feed:
            if len(state.frame_buffer) < 8:
                break

            packet_length, proto_type, padding = struct.unpack_from(
                "<IHH", state.frame_buffer, 0
            )
            if (
                packet_length > self.max_packet_size
                or not self._validate_header(packet_length, proto_type, padding)
            ):
                offset = self._find_next_header(state.frame_buffer)
                if offset is None:
                    dropped = max(0, len(state.frame_buffer) - 7)
                    if dropped:
                        del state.frame_buffer[:dropped]
                        self._notify_desync(key, dropped, "invalid frame header")
                    break
                del state.frame_buffer[:offset]
                self._notify_desync(key, offset, "resynchronized frame header")
                continue

            if len(state.frame_buffer) < packet_length:
                break

            packet = bytes(state.frame_buffer[:packet_length])
            del state.frame_buffer[:packet_length]
            self._on_packet(packet, proto_type)
            emitted += 1

        return emitted

    def _find_next_header(self, data: bytearray) -> Optional[int]:
        last_start = len(data) - 8
        for offset in range(1, last_start + 1):
            length, proto_type, padding = struct.unpack_from("<IHH", data, offset)
            if length <= self.max_packet_size and self._validate_header(
                length, proto_type, padding
            ):
                return offset
        return None

    def _notify_desync(self, stream_id: str, dropped: int, reason: str) -> None:
        if self._on_desync and dropped:
            self._on_desync(stream_id, dropped, reason)
