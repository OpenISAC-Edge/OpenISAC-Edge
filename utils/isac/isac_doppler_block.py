# SPDX-FileCopyrightText: Copyright (C) 2026 OCUDU ISAC fork contributors
# SPDX-License-Identifier: BSD-3-Clause-Open-MPI
"""
GNURadio Embedded Python Block: near-real-time Doppler spectrum from the ISAC CSI stream.

Subscribes to the gNB ISAC PUB stream, tracks channel-estimate subcarrier `sc` over slots (the
slow-time signal at the slot rate), and emits a magnitude spectrum VECTOR:
  - keeps a rolling window of `nfft` samples,
  - subtracts the window mean (removes the strong 0 Hz static-clutter return),
  - emits |FFT| in dB, fftshift'd so DC is centered, every `hop` new samples.

This decouples refresh from FFT fill: full `nfft` resolution AND ~ (slot_rate / hop) updates/sec
(e.g. 1000/128 ≈ 8 Hz) via an overlapping/sliding window — good for slow 2–10 Hz targets.

USAGE (GNURadio Companion):
  Add a "Python Block" pointing at this class. Output is a float32 vector of length `nfft`.
  Wire it to a QT GUI Vector Sink:
    - vlen = nfft
    - X-axis: start = -samp_rate/2, step = samp_rate/nfft   (Hz; samp_rate = slot rate 1000 or 2000)
  Start this flowgraph BEFORE the gNB; need continuous UL traffic for slot-rate frames.

Recommended: nfft = 1024 (≈1 Hz resolution — needed to separate 2–10 Hz from 0 Hz clutter),
hop = 128 (≈8 updates/sec). Reduce hop for smoother/faster redraw (more overlap).

Frame header (v2, LE, 22 bytes): struct "<IHBHIHBBBHH" then count * complex64.
"""

import struct

import numpy as np
from gnuradio import gr

try:
    import zmq
except ImportError as exc:  # pragma: no cover
    raise ImportError("pyzmq is required for the ISAC Doppler block: pip install pyzmq") from exc

_HDR = struct.Struct("<IHBHIHBBBHH")  # 22-byte ISAC v2 header
_MAGIC = 0x49534143                   # "ISAC"
_KIND_CSI = 0
_EPS = 1e-12


class blk(gr.sync_block):
    """Sliding-window Doppler spectrum (dB, DC-centered) of a chosen CSI subcarrier."""

    def __init__(self, endpoint="tcp://127.0.0.1:5556", sc=0, nfft=1024, hop=128, rnti=-1, remove_dc=True):
        self.nfft = int(nfft)
        gr.sync_block.__init__(self, name="ISAC Doppler", in_sig=None, out_sig=[(np.float32, self.nfft)])
        self.sc = int(sc)
        self.hop = max(1, int(hop))
        self.rnti = int(rnti)
        self.remove_dc = bool(remove_dc)

        self._win = np.zeros(self.nfft, dtype=np.complex64)  # rolling slow-time window
        self._filled = 0                                     # samples seen (until >= nfft)
        self._since = 0                                      # new samples since last emitted FFT

        self.ctx = zmq.Context.instance()
        self.sock = self.ctx.socket(zmq.SUB)
        self.sock.setsockopt(zmq.RCVHWM, 4000)
        self.sock.connect(str(endpoint))
        self.sock.setsockopt(zmq.SUBSCRIBE, b"")

    def _csi_sc(self, msg):
        """Return subcarrier `sc` of a CSI frame, or None."""
        if len(msg) < _HDR.size:
            return None
        magic, _v, kind, _scs, _slot, rnti, _sym, _rx, _ly, _rb, count = _HDR.unpack_from(msg, 0)
        if magic != _MAGIC or kind != _KIND_CSI:
            return None
        if self.rnti >= 0 and rnti != self.rnti:
            return None
        if self.sc >= count or len(msg) < _HDR.size + count * 8:
            return None
        return np.frombuffer(msg, dtype=np.complex64, count=count, offset=_HDR.size)[self.sc]

    def _push(self, x):
        # Roll the window left by one and append the newest sample.
        self._win[:-1] = self._win[1:]
        self._win[-1] = x
        if self._filled < self.nfft:
            self._filled += 1
        self._since += 1

    def work(self, input_items, output_items):
        out = output_items[0]

        # Avoid busy-spin when idle and not yet ready to emit.
        if self._since < self.hop and self.sock.poll(timeout=50) == 0:
            return 0

        # Drain all queued CSI frames into the rolling window.
        while True:
            try:
                msg = self.sock.recv(flags=zmq.NOBLOCK)
            except zmq.Again:
                break
            x = self._csi_sc(msg)
            if x is not None:
                self._push(x)

        # Emit one fresh spectrum if the window is full and we advanced by >= hop.
        if self._filled < self.nfft or self._since < self.hop:
            return 0

        win = self._win
        if self.remove_dc:
            win = win - win.mean()
        spec = np.fft.fftshift(np.abs(np.fft.fft(win)))
        out[0][:] = (20.0 * np.log10(spec + _EPS)).astype(np.float32)
        self._since = 0
        return 1

    def stop(self):
        try:
            self.sock.close(0)
        except Exception:
            pass
        return True
