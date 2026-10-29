"""
GNURadio Embedded Python Block for the OCUDU ISAC ZMQ tap.

Subscribes to the gNB's ISAC PUB stream and emits ONE complex64 stream, selected by `kind`:
  - kind = 0 (CSI): one coefficient per captured PUSCH at vector index `sc`,
                    selected by `rx_port` and `tx_layer` (both default to zero)
                    -> feed a QT GUI Frequency Sink (Doppler) and/or Time Sink.
  - kind = 1 (EQ):  every equalized symbol of each captured OFDM symbol
                    -> feed a QT GUI Constellation Sink.

USAGE (GNURadio Companion):
  Add TWO "Python Block" instances pointing at this class, same `endpoint`, one with kind=0 and one
  with kind=1. Set `samp_rate` on the sinks to the slot rate: 1000 (15 kHz SCS) or 2000 (30 kHz SCS).
  The subscriber can start at any time; PUB/SUB cannot replay earlier messages.
  Slot-rate interpretation requires one captured PUSCH per slot for the selected UE/path.
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

    def __init__(self, endpoint="tcp://127.0.0.1:5556", kind=0, sc=0, rnti=-1, rx_port=0, tx_layer=0):
        gr.sync_block.__init__(self, name="ISAC tap", in_sig=None, out_sig=[np.complex64])
        self.kind = int(kind)
        self.sc = int(sc)          # subcarrier index to emit when kind == 0
        self.rnti = int(rnti)      # -1 = accept any RNTI; else filter to this C-RNTI
        self.rx_port = int(rx_port)
        self.tx_layer = int(tx_layer)
        if self.sc < 0 or not 0 <= self.rx_port <= 255 or not 0 <= self.tx_layer <= 255:
            raise ValueError("sc must be non-negative and rx_port/tx_layer must be in 0..255")
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
        magic, version, kind, _scs, _slot, rnti, _sym, rxp, lyr, _rb0, count = _HDR.unpack_from(msg, 0)
        if magic != _MAGIC or version != 2 or kind != self.kind:
            return None
        if self.rnti >= 0 and rnti != self.rnti:
            return None
        if count == 0 or len(msg) < _HDR.size + count * 8:
            return None
        iq = np.frombuffer(msg, dtype=np.complex64, count=count, offset=_HDR.size)
        if self.kind == 0:                       # CSI: one subcarrier
            if rxp != self.rx_port or lyr != self.tx_layer:
                return None
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
