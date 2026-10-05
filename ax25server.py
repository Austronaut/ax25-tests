"""TCP server that decodes AX.25 line bitstreams (as sent by ax25_send).

Each connection is one receiving session: incoming bytes are unpacked into
line bits (bit i = byte i//8, position i%8), optionally passed through a
simulated channel, then decoded by the independent Python decoder in
ax25ref.py: descramble -> NRZI -> deframe -> FCS -> parse.

Standalone, it prints one JSON line per decoded frame:

    python ax25server.py --port 8001
    build/ax25_send --port 8001 --info "hello"
"""

import argparse
import json
import socketserver
import threading
from dataclasses import dataclass, field

import ax25ref


@dataclass
class Channel:
    """Impairments applied to line bits before decoding."""
    invert: bool = False                          # flip every bit (polarity)
    flip_bits: frozenset = frozenset()            # bit positions to corrupt
    noise_prefix: list = field(default_factory=list)  # bits "heard" before the sender


@dataclass
class Decoded:
    frame: bytes
    parsed: dict | None  # None if the frame passed FCS but isn't a UI frame we parse
    error: str | None

    def to_json(self):
        out = {"frame_hex": self.frame.hex(), "error": self.error}
        if self.parsed:
            p = self.parsed
            out.update(dest=f"{p['dest']}-{p['dest_ssid']}", src=f"{p['src']}-{p['src_ssid']}",
                       command=p["command"], control=p["control"], pid=p["pid"],
                       info_hex=p["info"].hex(),
                       info_text=p["info"].decode("ascii", errors="replace"))
        return out


@dataclass
class Session:
    """Everything one connection produced."""
    frames: list = field(default_factory=list)
    stats: dict = field(default_factory=dict)
    bits_received: int = 0


class Ax25Server:
    def __init__(self, host="127.0.0.1", port=0, descrambled=True, channel=None,
                 recv_size=4096, on_frame=None):
        self.descrambled = descrambled
        self.channel = channel or Channel()
        self.recv_size = recv_size
        self.on_frame = on_frame
        self.sessions = []
        self._done = threading.Condition()
        server = self

        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                server._handle(self.request)

        socketserver.ThreadingTCPServer.allow_reuse_address = True
        self._tcp = socketserver.ThreadingTCPServer((host, port), Handler)
        self._thread = None

    @property
    def port(self):
        return self._tcp.server_address[1]

    def start(self):
        self._thread = threading.Thread(target=self._tcp.serve_forever, args=(0.02,), daemon=True)
        self._thread.start()
        return self

    def stop(self):
        self._tcp.shutdown()
        self._tcp.server_close()

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()

    def wait_for_sessions(self, count, timeout=5.0):
        with self._done:
            if not self._done.wait_for(lambda: len(self.sessions) >= count, timeout):
                raise TimeoutError(f"expected {count} session(s), got {len(self.sessions)}")
            return list(self.sessions)

    def _handle(self, sock):
        decoder = ax25ref.StreamDecoder(descrambled=self.descrambled)
        session = Session()
        position = 0  # line-bit index within this session, for flip_bits

        def process(bits):
            nonlocal position
            channel = self.channel
            out = []
            for bit in bits:
                if channel.invert:
                    bit ^= 1
                if position in channel.flip_bits:
                    bit ^= 1
                position += 1
                out.append(bit)
            for frame in decoder.feed(out):
                self._record(session, frame)

        process(self.channel.noise_prefix)
        while chunk := sock.recv(self.recv_size):
            session.bits_received += len(chunk) * 8
            process(ax25ref.unpack_lsb(chunk, len(chunk) * 8))

        session.stats = dict(decoder.stats)
        with self._done:
            self.sessions.append(session)
            self._done.notify_all()

    def _record(self, session, frame):
        try:
            decoded = Decoded(frame, ax25ref.parse_ui(frame), None)
        except ValueError as exc:
            decoded = Decoded(frame, None, str(exc))
        session.frames.append(decoded)
        if self.on_frame:
            self.on_frame(decoded)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8001)
    ap.add_argument("--no-descramble", action="store_true", help="plain NRZI (no G3RUH)")
    ap.add_argument("--invert", action="store_true", help="simulate inverted polarity")
    args = ap.parse_args()

    server = Ax25Server(args.host, args.port, descrambled=not args.no_descramble,
                        channel=Channel(invert=args.invert),
                        on_frame=lambda d: print(json.dumps(d.to_json()), flush=True))
    print(f"listening on {args.host}:{server.port}", flush=True)
    try:
        server._tcp.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server._tcp.server_close()


if __name__ == "__main__":
    main()
