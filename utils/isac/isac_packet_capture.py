# SPDX-FileCopyrightText: Copyright (C) 2026 OCUDU ISAC fork contributors
# SPDX-License-Identifier: BSD-3-Clause-Open-MPI
"""Lossless recorder for the OCUDU ISAC ZeroMQ packet stream.

The ISAC publisher sends one complete binary frame per ZeroMQ message.  This
utility deliberately does not decode, validate, filter, or transform those
frames.  It writes each received message unchanged to a framed capture file so
the complete subcarrier grid can be reconstructed later.

Example::

    python3 utils/isac/isac_packet_capture.py \
        --endpoint tcp://127.0.0.1:5556 \
        --output isac_capture_fdd_run_01.bin

Capture file format (little-endian):

    File header: 8-byte magic, uint16 version, uint16 record-header size,
                 uint32 reserved (16 bytes total).
    Record:      uint64 wall-clock arrival time in ns,
                 uint64 monotonic arrival time in ns,
                 uint64 record index,
                 uint32 payload length,
                 payload bytes exactly as received from ZeroMQ.

The two arrival clocks are stored so later analysis can use a stable monotonic
timeline while retaining a wall-clock reference.  The record index preserves
the receive order independently of the ISAC slot field inside the payload.
"""

from __future__ import annotations

import argparse
from datetime import datetime
import os
import queue
import signal
import struct
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

try:
    import zmq
except ImportError as exc:  # pragma: no cover - depends on the host environment
    raise SystemExit("pyzmq is required: pip install pyzmq") from exc


_FILE_MAGIC = b"ISACRAW\0"
_FILE_VERSION = 1
_FILE_HEADER = struct.Struct("<8sHHI")
_RECORD_HEADER = struct.Struct("<QQQI")


@dataclass(frozen=True)
class _Packet:
    """One received ZeroMQ message plus capture-time metadata."""

    wall_time_ns: int
    monotonic_time_ns: int
    index: int
    payload: bytes


_STOP = object()


def _write_all(stream: BinaryIO, data: bytes | memoryview) -> None:
    """Write all bytes, protecting the capture from short writes."""

    view = memoryview(data)
    while view:
        written = stream.write(view)
        if written is None or written <= 0:
            raise OSError("capture file write made no progress")
        view = view[written:]


def _write_packet(stream: BinaryIO, packet: _Packet) -> None:
    payload = packet.payload
    header = _RECORD_HEADER.pack(
        packet.wall_time_ns,
        packet.monotonic_time_ns,
        packet.index,
        len(payload),
    )
    _write_all(stream, header)
    _write_all(stream, payload)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Record complete OCUDU ISAC ZeroMQ messages without decoding them."
    )
    parser.add_argument(
        "--endpoint",
        default="tcp://127.0.0.1:5556",
        help="ZeroMQ PUB endpoint to connect to (default: %(default)s)",
    )
    parser.add_argument(
        "--output",
        default=None,
        type=Path,
        help=(
            "new binary capture file to create; if omitted, use "
            "isac_capture_<local-date-time>.bin"
        ),
    )
    parser.add_argument(
        "--queue-size",
        type=int,
        default=4096,
        help=(
            "maximum number of received packets waiting for disk I/O; a full queue "
            "blocks the receiver instead of dropping packets (default: %(default)s)"
        ),
    )
    parser.add_argument(
        "--rcv-hwm",
        type=int,
        default=10000,
        help="ZeroMQ receive high-water mark (default: %(default)s)",
    )
    parser.add_argument(
        "--rcvbuf",
        type=int,
        default=64 * 1024 * 1024,
        help="ZeroMQ socket receive buffer in bytes (default: %(default)s)",
    )
    parser.add_argument(
        "--fsync-every",
        type=int,
        default=0,
        metavar="N",
        help="call fsync after every N packets; 0 disables fsync (default: %(default)s)",
    )
    args = parser.parse_args()

    if args.queue_size < 1:
        parser.error("--queue-size must be positive")
    if args.rcv_hwm < 1:
        parser.error("--rcv-hwm must be positive")
    if args.rcvbuf < 0:
        parser.error("--rcvbuf cannot be negative")
    if args.fsync_every < 0:
        parser.error("--fsync-every cannot be negative")
    return args


def main() -> int:
    args = _parse_args()
    if args.output is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        args.output = Path(f"isac_capture_{timestamp}.bin")

    try:
        output = args.output.open("xb", buffering=1024 * 1024)
    except FileExistsError:
        print(f"error: capture file already exists: {args.output}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"error: cannot create {args.output}: {exc}", file=sys.stderr)
        return 2

    _write_all(
        output,
        _FILE_HEADER.pack(_FILE_MAGIC, _FILE_VERSION, _RECORD_HEADER.size, 0),
    )
    output.flush()

    packets: queue.Queue[_Packet | object] = queue.Queue(maxsize=args.queue_size)
    stop_event = threading.Event()
    writer_error: list[BaseException] = []
    stats_lock = threading.Lock()
    stats = {"received": 0, "written": 0, "bytes": 0}

    def write_packets() -> None:
        try:
            packet_count = 0
            while True:
                item = packets.get()
                try:
                    if item is _STOP:
                        return
                    assert isinstance(item, _Packet)
                    _write_packet(output, item)
                    packet_count += 1
                    # Flush each complete record so a process interruption does
                    # not leave a packet stranded in Python's buffer.  fsync is
                    # optional because it is substantially more expensive.
                    output.flush()
                    if args.fsync_every and packet_count % args.fsync_every == 0:
                        os.fsync(output.fileno())
                    with stats_lock:
                        stats["written"] += 1
                finally:
                    packets.task_done()
        except BaseException as exc:  # propagate writer failures to the main thread
            writer_error.append(exc)
            stop_event.set()

    writer = threading.Thread(target=write_packets, name="isac-capture-writer", daemon=True)
    writer.start()

    def request_stop(_signum: int, _frame: object) -> None:
        stop_event.set()

    previous_sigint = signal.signal(signal.SIGINT, request_stop)
    previous_sigterm = signal.signal(signal.SIGTERM, request_stop)

    context = zmq.Context()
    socket = context.socket(zmq.SUB)
    socket.setsockopt(zmq.RCVHWM, args.rcv_hwm)
    if args.rcvbuf:
        socket.setsockopt(zmq.RCVBUF, args.rcvbuf)
    socket.setsockopt(zmq.SUBSCRIBE, b"")
    socket.connect(str(args.endpoint))

    def enqueue(packet: _Packet) -> bool:
        """Queue a packet without silently dropping it if the queue is full."""

        while True:
            if writer_error:
                return False
            try:
                packets.put(packet, timeout=0.25)
                return True
            except queue.Full:
                continue

    print(f"Recording ISAC messages from {args.endpoint} to {args.output}", file=sys.stderr)
    print("Press Ctrl-C to stop and flush the capture.", file=sys.stderr)

    try:
        index = 0
        poller = zmq.Poller()
        poller.register(socket, zmq.POLLIN)

        while not stop_event.is_set():
            if writer_error:
                break
            events = dict(poller.poll(timeout=250))
            if socket not in events:
                continue

            # One recv is one complete ZMQ message.  Do not inspect or alter it.
            payload = socket.recv()
            packet = _Packet(
                wall_time_ns=time.time_ns(),
                monotonic_time_ns=time.monotonic_ns(),
                index=index,
                payload=payload,
            )
            index += 1
            if not enqueue(packet):
                break
            with stats_lock:
                stats["received"] += 1
                stats["bytes"] += len(payload)
    except KeyboardInterrupt:
        stop_event.set()
    except BaseException as exc:
        print(f"error: capture receiver failed: {exc}", file=sys.stderr)
        stop_event.set()
    finally:
        socket.close(0)
        context.term()

        # The writer drains every packet already accepted by the application
        # queue before stopping.  This keeps shutdown lossless at this layer.
        if writer.is_alive():
            while writer.is_alive():
                try:
                    packets.put(_STOP, timeout=0.25)
                    break
                except queue.Full:
                    continue
        writer.join()
        output.flush()
        output.close()
        signal.signal(signal.SIGINT, previous_sigint)
        signal.signal(signal.SIGTERM, previous_sigterm)

    if writer_error:
        print(f"error: capture writer failed: {writer_error[0]}", file=sys.stderr)
        return 1

    with stats_lock:
        received = stats["received"]
        written = stats["written"]
        byte_count = stats["bytes"]
    print(
        f"Captured {written} packets ({byte_count} payload bytes) to {args.output}",
        file=sys.stderr,
    )
    if received != written:
        print(f"warning: receiver accepted {received} packets but writer stored {written}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
