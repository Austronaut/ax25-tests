"""Independent pure-Python AX.25 reference implementation.

Written from the AX.25 v2.2 spec and Direwolf's behaviour, deliberately
structured differently from the C library (string-based deframing,
history-list scrambler) so the two are unlikely to share a bug.

Bits are lists of 0/1 ints in on-air order.
"""

FLAG = 0x7E
FLAG_BITS = "01111110"  # 0x7E LSB-first
INFO_MAX_LEN = 256
HEADER_LEN = 16  # two addresses + control + PID
FRAME_MAX_LEN = HEADER_LEN + INFO_MAX_LEN + 2
MIN_FRAME_LEN = 17  # two addresses + control + FCS
CONTROL_UI = 0x03
PID_NO_LAYER3 = 0xF0
DEFAULT_LEAD_FLAGS = 16
DEFAULT_TRAIL_FLAGS = 4


# ------------------------------------------------------------------ helpers

def byte_to_bits(byte):
    """LSB-first."""
    return [(byte >> i) & 1 for i in range(8)]


def bytes_to_bits(data):
    return [bit for byte in data for bit in byte_to_bits(byte)]


def pack_lsb(bits):
    """Packs bits like ax25_bitbuf_t: bit i -> byte i//8, position i%8."""
    out = bytearray((len(bits) + 7) // 8)
    for i, bit in enumerate(bits):
        if bit:
            out[i // 8] |= 1 << (i % 8)
    return bytes(out)


def unpack_lsb(data, nbits):
    return [(data[i // 8] >> (i % 8)) & 1 for i in range(nbits)]


def bits_to_str(bits):
    return "".join(str(b) for b in bits)


# ---------------------------------------------------------------------- FCS

def fcs(data):
    """CRC-16/X.25: reflected poly 0x8408, init 0xFFFF, xorout 0xFFFF."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0x8408 if crc & 1 else crc >> 1
    return crc ^ 0xFFFF


def fcs_bytes(data):
    value = fcs(data)
    return bytes([value & 0xFF, value >> 8])  # low byte first


# -------------------------------------------------------------------- frame

def valid_callsign(call):
    return 1 <= len(call) <= 6 and all(c.isascii() and (c.isupper() or c.isdigit()) for c in call)


def encode_address(call, ssid, c_bit, last):
    if not valid_callsign(call):
        raise ValueError(f"bad callsign {call!r}")
    if not 0 <= ssid <= 15:
        raise ValueError(f"bad SSID {ssid}")
    padded = call.ljust(6)
    out = bytes(ord(c) << 1 for c in padded)
    ssid_byte = 0x60 | (ssid << 1) | (0x80 if c_bit else 0) | (0x01 if last else 0)
    return out + bytes([ssid_byte])


def build_ui(dest, dest_ssid, src, src_ssid, info=b"", command=True,
             control=CONTROL_UI, pid=PID_NO_LAYER3):
    if len(info) > INFO_MAX_LEN:
        raise ValueError("info too long")
    body = (encode_address(dest, dest_ssid, c_bit=command, last=False)
            + encode_address(src, src_ssid, c_bit=not command, last=True)
            + bytes([control, pid]) + bytes(info))
    return body + fcs_bytes(body)


def decode_address(field):
    call = "".join(chr(b >> 1) for b in field[:6]).rstrip(" ")
    ssid_byte = field[6]
    return {
        "callsign": call,
        "ssid": (ssid_byte >> 1) & 0x0F,
        "c_bit": bool(ssid_byte & 0x80),
        "last": bool(ssid_byte & 0x01),
    }


def parse_ui(frame):
    """Returns a dict; raises ValueError on anything malformed."""
    if len(frame) < HEADER_LEN + 2:
        raise ValueError("too short")
    if fcs_bytes(frame[:-2]) != bytes(frame[-2:]):
        raise ValueError("bad FCS")
    dest = decode_address(frame[0:7])
    src = decode_address(frame[7:14])
    if dest["last"] or not src["last"]:
        raise ValueError("unsupported address field")
    return {
        "dest": dest["callsign"], "dest_ssid": dest["ssid"],
        "src": src["callsign"], "src_ssid": src["ssid"],
        "command": dest["c_bit"] and not src["c_bit"],
        "control": frame[14], "pid": frame[15],
        "info": bytes(frame[16:-2]),
    }


# --------------------------------------------------------------------- HDLC

def stuff(bits):
    out, ones = [], 0
    for bit in bits:
        out.append(bit)
        ones = ones + 1 if bit else 0
        if ones == 5:
            out.append(0)
            ones = 0
    return out


def hdlc_encode(frame, lead_flags, trail_flags):
    flag = byte_to_bits(FLAG)
    return flag * lead_flags + stuff(bytes_to_bits(frame)) + flag * trail_flags


def segment_to_frame(seg):
    """NRZ bits found between two flags (as a '0'/'1' string) -> frame bytes,
    or None for an abort, unaligned or too-short segment."""
    if "1111111" in seg:
        return None  # abort
    data = seg.replace("111110", "11111")  # unstuff
    if len(data) % 8 or len(data) // 8 < MIN_FRAME_LEN:
        return None
    return bytes(int(data[i:i + 8][::-1], 2) for i in range(0, len(data), 8))


def hdlc_decode(bits):
    """Splits on flags and keeps segments that form whole frames."""
    segments = bits_to_str(bits).split(FLAG_BITS)
    frames = (segment_to_frame(seg) for seg in segments[1:-1])  # flag on both sides
    return [f for f in frames if f is not None]


# --------------------------------------------------------------------- line

def nrzi_encode(bits, level=0):
    out = []
    for bit in bits:
        if bit == 0:
            level ^= 1
        out.append(level)
    return out


def nrzi_decode(bits, level=0):
    out = []
    for bit in bits:
        out.append(1 if bit == level else 0)
        level = bit
    return out


def scramble(bits):
    """out[n] = in[n] ^ out[n-12] ^ out[n-17], history starts at zero."""
    out = []
    for n, bit in enumerate(bits):
        o12 = out[n - 12] if n >= 12 else 0
        o17 = out[n - 17] if n >= 17 else 0
        out.append(bit ^ o12 ^ o17)
    return out


def descramble(bits):
    """out[n] = in[n] ^ in[n-12] ^ in[n-17], history starts at zero."""
    return [bits[n] ^ (bits[n - 12] if n >= 12 else 0) ^ (bits[n - 17] if n >= 17 else 0)
            for n in range(len(bits))]


# ---------------------------------------------------------------- pipelines

def encode(frame, lead_flags=DEFAULT_LEAD_FLAGS, trail_flags=DEFAULT_TRAIL_FLAGS,
           scrambled=True, pad=True):
    bits = hdlc_encode(frame, lead_flags, trail_flags)
    if pad:
        bits += byte_to_bits(FLAG)[: (-len(bits)) % 8]
    bits = nrzi_encode(bits)
    return scramble(bits) if scrambled else bits


def decode(bits, descrambled=True):
    if descrambled:
        bits = descramble(bits)
    frames = hdlc_decode(nrzi_decode(bits))
    return [f for f in frames if fcs_bytes(f[:-2]) == f[-2:]]


class StreamDecoder:
    """Line bits in arbitrary chunks -> FCS-valid frames, as a ground station
    receives them. Same algorithm as decode(), but keeps state between feeds."""

    MAX_PENDING_BITS = FRAME_MAX_LEN * 8 * 6 // 5 + 16  # longest stuffed frame + slack

    def __init__(self, descrambled=True):
        self.descrambled = descrambled
        self._history = [0] * 17  # last 17 line bits, newest last
        self._level = 0
        self._pending = ""        # NRZ bits after the last flag seen
        self._after_flag = False  # has a flag opened the pending segment?
        self.stats = {"frames_ok": 0, "fcs_errors": 0}

    def feed(self, line_bits):
        nrz = []
        for bit in line_bits:
            if self.descrambled:
                data = bit ^ self._history[-12] ^ self._history[-17]
                self._history = self._history[1:] + [bit]
            else:
                data = bit
            nrz.append(1 if data == self._level else 0)
            self._level = data

        parts = (self._pending + bits_to_str(nrz)).split(FLAG_BITS)
        complete = parts[:-1] if self._after_flag else parts[1:-1]
        if len(parts) > 1:
            self._after_flag = True
        self._pending = parts[-1]
        if len(self._pending) > self.MAX_PENDING_BITS:
            self._pending = self._pending[-7:]  # too long for a frame: resync
            self._after_flag = False

        frames = []
        for seg in complete:
            frame = segment_to_frame(seg)
            if frame is None:
                continue
            if fcs_bytes(frame[:-2]) == frame[-2:]:
                self.stats["frames_ok"] += 1
                frames.append(frame)
            else:
                self.stats["fcs_errors"] += 1
        return frames
