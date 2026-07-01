# SPDX-FileCopyrightText: Copyright (C) 2026 OCUDU ISAC fork contributors
# SPDX-License-Identifier: BSD-3-Clause-Open-MPI
"""
GNURadio Embedded Python Block for the OCUDU ISAC ZMQ tap.

Subscribes to the gNB's ISAC PUB stream and emits ONE complex64 stream, selected by `kind`:
  - kind = 0 (CSI): one channel-estimate coefficient per slot at subcarrier index `sc`
                    -> feed a QT GUI Frequency Sink (Doppler) and/or Time Sink.
  - kind = 1 (EQ):  every equalized symbol of each captured OFDM symbol
                    -> feed a QT GUI Constellation Sink.

USAGE (GNURadio Companion):
  Add TWO "Python Block" instances pointing at this class, same `endpoint`, one with kind=0 and one
  with kind=1. Set `samp_rate` on the sinks to the slot rate: 1000 (15 kHz SCS) or 2000 (30 kHz SCS).
  Start this flowgraph BEFORE the gNB (PUB/SUB drops anything sent before a subscriber connects).
  Use endpoint tcp://127.0.0.1:5556 if co-located, else tcp://<gnb-LAN-IP>:5556.

Wire frame (v2, little-endian, tightly packed): struct "<IHBHIHBBBHH" (22 bytes) =
  magic u32 (0x49534143 "ISAC"), version u16, kind u8, scs_khz u16, slot u32, rnti u16,
  symbol u8, rx_port u8, tx_layer u8, rb_start u16, count u16
followed by `count` * complex64.
"""

import struct

import numpy as np
from gnuradio import gr

try:
    import zmq
except ImportError as exc:  # pragma: no cover
    raise ImportError("pyzmq is required for the ISAC block: pip install pyzmq") from exc

_HDR = struct.Struct("<IHBHIHBBBHH")  # 22-byte ISAC v2 header
_MAGIC = 0x49534143                   # "ISAC"


class blk(gr.sync_block):
    """ISAC ZMQ subscriber source: emits CSI subcarrier (kind=0) or EQ symbols (kind=1)."""

    def __init__(self, endpoint="tcp://127.0.0.1:5556", kind=0, sc=0, rnti=-1):
        gr.sync_block.__init__(self, name="ISAC tap", in_sig=None, out_sig=[np.complex64])
        self.kind = int(kind)
        self.sc = int(sc)          # subcarrier index to emit when kind == 0
        self.rnti = int(rnti)      # -1 = accept any RNTI; else filter to this C-RNTI
        self._buf = np.empty(0, dtype=np.complex64)

        self.ctx = zmq.Context.instance()
        self.sock = self.ctx.socket(zmq.SUB)
        self.sock.setsockopt(zmq.RCVHWM, 2000)
        self.sock.connect(str(endpoint))
        self.sock.setsockopt(zmq.SUBSCRIBE, b"")

    def _parse(self, msg):
        """Return the samples to emit for this frame, or None if it is not ours."""
        if len(msg) < _HDR.size:
            return None
        magic, _ver, kind, _scs, _slot, rnti, _sym, _rxp, _lyr, _rb0, count = _HDR.unpack_from(msg, 0)
        if magic != _MAGIC or kind != self.kind:
            return None
        if self.rnti >= 0 and rnti != self.rnti:
            return None
        if count == 0 or len(msg) < _HDR.size + count * 8:
            return None
        iq = np.frombuffer(msg, dtype=np.complex64, count=count, offset=_HDR.size)
        if self.kind == 0:                       # CSI: one subcarrier
            if self.sc >= count:
                return None
            return iq[self.sc:self.sc + 1]
        return iq                                 # EQ: all constellation points

    def work(self, input_items, output_items):
        out = output_items[0]

        # Avoid busy-spinning the scheduler when idle and nothing is buffered.
        if self._buf.size == 0 and self.sock.poll(timeout=50) == 0:
            return 0

        # Drain everything currently queued so the socket never backs up (keeps the plot smooth).
        chunks = []
        while True:
            try:
                msg = self.sock.recv(flags=zmq.NOBLOCK)
            except zmq.Again:
                break
            samples = self._parse(msg)
            if samples is not None and samples.size:
                chunks.append(samples)
        if chunks:
            chunks.insert(0, self._buf)
            self._buf = np.concatenate(chunks)

        if self._buf.size == 0:
            return 0

        n = min(self._buf.size, len(out))
        out[:n] = self._buf[:n]
        self._buf = self._buf[n:].copy()
        return int(n)

    def stop(self):
        try:
            self.sock.close(0)
        except Exception:
            pass
        return True
