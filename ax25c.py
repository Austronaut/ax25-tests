"""Exposes lib/ax25 from the OBC repo to Python through ctypes, via the
flat wrapper in shim/ax25_shim.c (built by ax25build.py).
"""

import ctypes as C
import enum
import re
from dataclasses import dataclass

import ax25build
import ax25ref
from ax25build import AX25_DIR, WARNINGS, library_sources  # noqa: F401 (re-exported)


def _load_status_enum():
    """Reads ax25_status_t from the header so values never drift."""
    text = (AX25_DIR / "include" / "ax25" / "ax25_status.h").read_text()
    body = re.search(r"typedef enum \{(.*?)\} ax25_status_t;", text, re.S).group(1)
    names = re.findall(r"^\s*(AX25_[A-Z_]+)", body, re.M)
    return enum.IntEnum("Status", {n.removeprefix("AX25_"): i for i, n in enumerate(names)})


Status = _load_status_enum()

ax25build.write_compile_commands()
_lib = C.CDLL(str(ax25build.build_shim()))

_size = C.c_size_t
_size_p = C.POINTER(C.c_size_t)
_u8p = C.c_char_p
_uint = C.c_uint
_uint_p = C.POINTER(C.c_uint)
_u32_p = C.POINTER(C.c_uint32)
_int_p = C.POINTER(C.c_int)


def _sig(name, restype, *argtypes):
    fn = getattr(_lib, name)
    fn.restype = restype
    fn.argtypes = argtypes
    return fn


_fcs = _sig("shim_fcs", _uint, _u8p, _size)
_fcs_check = _sig("shim_fcs_check", C.c_int, _u8p, _size)
_address_set = _sig("shim_address_set", C.c_int, C.c_char_p, _uint)
_ui_init_build = _sig("shim_ui_init_build", C.c_int, C.c_char_p, _uint, C.c_char_p, _uint,
                      _u8p, _size, C.c_void_p, _size, _size_p)
_ui_build_raw = _sig("shim_ui_build_raw", C.c_int, C.c_char_p, _uint, C.c_char_p, _uint,
                     C.c_int, _uint, _uint, _u8p, _size, C.c_void_p, _size, _size_p)
_ui_parse = _sig("shim_ui_parse", C.c_int, _u8p, _size, C.c_char_p, _uint_p, C.c_char_p,
                 _uint_p, _int_p, _uint_p, _uint_p, _size_p, _size_p)
_hdlc_encode = _sig("shim_hdlc_encode", C.c_int, _u8p, _size, _size, _size,
                    C.c_void_p, _size, _size_p)
_hdlc_sizeof = _sig("shim_hdlc_sizeof", _size)
_hdlc_init = _sig("shim_hdlc_init", None, C.c_void_p, _size)
_hdlc_feed = _sig("shim_hdlc_feed", None, C.c_void_p, _u8p, _size)
_hdlc_results = _sig("shim_hdlc_results", None, C.c_void_p, _int_p, _int_p, _int_p)
_hdlc_frame = _sig("shim_hdlc_frame", _size, C.c_void_p, C.c_int, C.c_void_p, _size)
_nrzi_encode = _sig("shim_nrzi_encode", None, C.c_void_p, _size, _uint_p)
_nrzi_decode = _sig("shim_nrzi_decode", None, C.c_void_p, _size, _uint_p)
_scramble = _sig("shim_scramble", None, C.c_void_p, _size, _u32_p)
_descramble = _sig("shim_descramble", None, C.c_void_p, _size, _u32_p)
_scrambler_period = _sig("shim_scrambler_period", C.c_uint32, C.c_uint32, C.c_uint32)
_tx_config_default = _sig("shim_tx_config_default", None, _size_p, _size_p, _int_p, _int_p)
_encoded_max_bits = _sig("shim_encoded_max_bits", _size, _size, _size, _size)
_encode = _sig("shim_encode", C.c_int, _u8p, _size, _size, _size, C.c_int, C.c_int,
               C.c_void_p, _size, _size_p)
_rx_sizeof = _sig("shim_rx_sizeof", _size)
_rx_init = _sig("shim_rx_init", None, C.c_void_p, C.c_int)
_rx_feed = _sig("shim_rx_feed", None, C.c_void_p, _u8p, _size, C.c_int)
_rx_stats = _sig("shim_rx_stats", None, C.c_void_p, _u32_p)
_rx_count = _sig("shim_rx_count", C.c_int, C.c_void_p)
_rx_frame = _sig("shim_rx_frame", _size, C.c_void_p, C.c_int, C.c_void_p, _size)
_count_flips = _sig("shim_count_deliveries_with_flips", C.c_int, _u8p, _size, _size, _size,
                    C.c_void_p, C.c_void_p)

pack = ax25ref.pack_lsb
unpack = ax25ref.unpack_lsb


# ---------------------------------------------------------------------- FCS

def fcs(data):
    return _fcs(bytes(data), len(data))


def fcs_check(frame):
    return bool(_fcs_check(None if frame is None else bytes(frame), len(frame or b"")))


# -------------------------------------------------------------------- frame

def address_set(callsign, ssid):
    return Status(_address_set(None if callsign is None else callsign.encode(), ssid))


def build_ui(dest, dest_ssid, src, src_ssid, info=b"", out_cap=ax25ref.FRAME_MAX_LEN):
    out = C.create_string_buffer(max(out_cap, 1))
    n = C.c_size_t(0)
    st = Status(_ui_init_build(dest.encode(), dest_ssid, src.encode(), src_ssid,
                               bytes(info), len(info), out, out_cap, C.byref(n)))
    return st, out.raw[: n.value] if st == Status.OK else b""


def build_ui_raw(dest, dest_ssid, src, src_ssid, info=b"", command=True,
                 control=ax25ref.CONTROL_UI, pid=ax25ref.PID_NO_LAYER3,
                 out_cap=ax25ref.FRAME_MAX_LEN + 1):
    out = C.create_string_buffer(max(out_cap, 1))
    n = C.c_size_t(0)
    st = Status(_ui_build_raw(dest.encode(), dest_ssid, src.encode(), src_ssid,
                              int(command), control, pid, bytes(info), len(info),
                              out, out_cap, C.byref(n)))
    return st, out.raw[: n.value] if st == Status.OK else b""


@dataclass
class Parsed:
    dest: str
    dest_ssid: int
    src: str
    src_ssid: int
    command: bool
    control: int
    pid: int
    info_offset: int
    info: bytes


def parse_ui(frame):
    frame = bytes(frame)
    dest, src = C.create_string_buffer(7), C.create_string_buffer(7)
    dssid, sssid, ctrl, pid = _uint(), _uint(), _uint(), _uint()
    cmd = C.c_int()
    off, ilen = C.c_size_t(), C.c_size_t()
    st = Status(_ui_parse(frame, len(frame), dest, C.byref(dssid), src, C.byref(sssid),
                          C.byref(cmd), C.byref(ctrl), C.byref(pid), C.byref(off),
                          C.byref(ilen)))
    if st != Status.OK:
        return st, None
    return st, Parsed(dest.value.decode(), dssid.value, src.value.decode(), sssid.value,
                      bool(cmd.value), ctrl.value, pid.value, off.value,
                      frame[off.value: off.value + ilen.value])


# --------------------------------------------------------------------- HDLC

def hdlc_encode(frame, lead_flags, trail_flags, prefix=(), cap_bits=1 << 16):
    """Encodes frame after the bits in prefix; returns (status, bits)."""
    packed = C.create_string_buffer(pack(list(prefix)), (cap_bits + 7) // 8)
    n = C.c_size_t(len(prefix))
    st = Status(_hdlc_encode(None if frame is None else bytes(frame), len(frame or b""),
                             lead_flags, trail_flags, packed, (cap_bits + 7) // 8,
                             C.byref(n)))
    return st, unpack(packed.raw, n.value)


@dataclass
class HdlcResult:
    frames: list
    aborts: int
    overflows: int


def hdlc_decode(bits, rx_capacity=ax25ref.FRAME_MAX_LEN):
    state = C.create_string_buffer(_hdlc_sizeof())
    _hdlc_init(state, rx_capacity)
    _hdlc_feed(state, pack(bits), len(bits))
    count, aborts, overflows = C.c_int(), C.c_int(), C.c_int()
    _hdlc_results(state, C.byref(count), C.byref(aborts), C.byref(overflows))
    frames = []
    for i in range(count.value):
        buf = C.create_string_buffer(ax25ref.FRAME_MAX_LEN + 64)
        n = _hdlc_frame(state, i, buf, len(buf))
        frames.append(buf.raw[:n])
    return HdlcResult(frames, aborts.value, overflows.value)


# --------------------------------------------------------------------- line

def _apply(fn, bits, state, ctype):
    packed = C.create_string_buffer(pack(bits), max(1, (len(bits) + 7) // 8))
    s = ctype(state)
    fn(packed, len(bits), C.byref(s))
    return unpack(packed.raw, len(bits)), s.value


def nrzi_encode(bits, level=0):
    return _apply(_nrzi_encode, bits, level, _uint)


def nrzi_decode(bits, level=0):
    return _apply(_nrzi_decode, bits, level, _uint)


def scramble(bits, reg=0):
    return _apply(_scramble, bits, reg, C.c_uint32)


def descramble(bits, reg=0):
    return _apply(_descramble, bits, reg, C.c_uint32)


def scrambler_period(reg, limit):
    return _scrambler_period(reg, limit)


# ---------------------------------------------------------------- pipelines

def tx_config_default():
    lead, trail = C.c_size_t(), C.c_size_t()
    scr, pad = C.c_int(), C.c_int()
    _tx_config_default(C.byref(lead), C.byref(trail), C.byref(scr), C.byref(pad))
    return {"lead_flags": lead.value, "trail_flags": trail.value,
            "scramble": bool(scr.value), "pad_to_byte": bool(pad.value)}


def encoded_max_bits(frame_len, lead, trail):
    return _encoded_max_bits(frame_len, lead, trail)


def encode(frame, lead=ax25ref.DEFAULT_LEAD_FLAGS, trail=ax25ref.DEFAULT_TRAIL_FLAGS,
           scrambled=True, pad=True, cap_bytes=4096):
    packed = C.create_string_buffer(max(cap_bytes, 1))
    n = C.c_size_t(0)
    st = Status(_encode(None if frame is None else bytes(frame), len(frame or b""),
                        lead, trail, int(scrambled), int(pad), packed, cap_bytes,
                        C.byref(n)))
    return st, unpack(packed.raw, n.value)


class Receiver:
    """Streaming C receiver: descramble -> NRZI -> deframe -> FCS."""

    def __init__(self, descrambled=True):
        self._state = C.create_string_buffer(_rx_sizeof())
        _rx_init(self._state, int(descrambled))

    def feed(self, bits, invert=False):
        _rx_feed(self._state, pack(bits), len(bits), int(invert))

    @property
    def frames(self):
        out = []
        for i in range(_rx_count(self._state)):
            buf = C.create_string_buffer(ax25ref.FRAME_MAX_LEN)
            n = _rx_frame(self._state, i, buf, len(buf))
            out.append(buf.raw[:n])
        return out

    @property
    def stats(self):
        arr = (C.c_uint32 * 4)()
        _rx_stats(self._state, arr)
        return dict(zip(("frames_ok", "fcs_errors", "aborts", "overflows"), arr))


def count_deliveries_with_flips(bits, start, end):
    scratch = C.create_string_buffer(max(1, (len(bits) + 7) // 8))
    state = C.create_string_buffer(_rx_sizeof())
    return _count_flips(pack(bits), len(bits), start, end, scratch, state)
