"""UI frame build/parse (ax25_frame.c)."""

import pytest

import ax25c
import ax25ref
from ax25c import Status

# CQ-0 <- N0CALL-1, info "hi", derived by hand:
#   'C'=0x43<<1=0x86  'Q'=0x51<<1=0xA2  ' '=0x20<<1=0x40
#   dest SSID: C=1 | reserved 0x60 | ssid 0       = 0xE0
#   'N'=0x9C '0'=0x60 'C'=0x86 'A'=0x82 'L'=0x98 'L'=0x98
#   src SSID:  C=0 | reserved 0x60 | 1<<1 | ext 1 = 0x63
EXPECTED_HEADER = bytes([
    0x86, 0xA2, 0x40, 0x40, 0x40, 0x40, 0xE0,
    0x9C, 0x60, 0x86, 0x82, 0x98, 0x98, 0x63,
    0x03, 0xF0,
])


def reference_frame():
    st, frame = ax25c.build_ui("CQ", 0, "N0CALL", 1, b"hi")
    assert st == Status.OK
    return bytearray(frame)


def refresh_fcs(frame):
    """Re-sign a tampered frame so parse gets past the FCS check."""
    frame[-2:] = ax25ref.fcs_bytes(bytes(frame[:-2]))
    return frame


def test_build_known_frame():
    frame = reference_frame()
    assert frame[:16] == EXPECTED_HEADER
    assert frame[16:18] == b"hi"
    fcs = ax25c.fcs(frame[:18])
    assert frame[18] == fcs & 0xFF  # low byte first
    assert frame[19] == fcs >> 8
    assert len(frame) == 20


def test_response_frame_c_bits():
    st, frame = ax25c.build_ui_raw("CQ", 0, "N0CALL", 1, command=False)
    assert st == Status.OK
    assert frame[6] == 0x60   # dest C=0
    assert frame[13] == 0xE3  # src C=1, ext=1


def test_max_ssid_and_full_callsigns():
    st, frame = ax25c.build_ui("ABCDEF", 15, "Z9Z9Z9", 15)
    assert st == Status.OK
    assert frame[6] == 0xFE   # 0x80 | 0x60 | 15<<1
    assert frame[13] == 0x7F  # 0x60 | 15<<1 | 1


@pytest.mark.parametrize("info_len", [0, 1, 2, 100, 256])
def test_roundtrip_and_matches_reference(rng, info_len):
    info = rng.randbytes(info_len)
    st, frame = ax25c.build_ui("VK2ABC", 7, "AX25", 0, info)
    assert st == Status.OK
    assert frame == ax25ref.build_ui("VK2ABC", 7, "AX25", 0, info)

    st, p = ax25c.parse_ui(frame)
    assert st == Status.OK
    assert (p.dest, p.dest_ssid, p.src, p.src_ssid) == ("VK2ABC", 7, "AX25", 0)
    assert p.command and p.control == 0x03 and p.pid == 0xF0
    assert p.info_offset == 16 and p.info == info


@pytest.mark.parametrize("call, ssid, expected", [
    ("", 0, Status.ERR_CALLSIGN),
    ("ABCDEFG", 0, Status.ERR_CALLSIGN),
    ("n0call", 0, Status.ERR_CALLSIGN),
    ("N0-CAL", 0, Status.ERR_CALLSIGN),
    ("N0 CAL", 0, Status.ERR_CALLSIGN),
    ("N0CALL", 16, Status.ERR_SSID),
    (None, 0, Status.ERR_ARG),
    ("N0CALL", 15, Status.OK),
    ("A", 0, Status.OK),
])
def test_address_validation(call, ssid, expected):
    assert ax25c.address_set(call, ssid) == expected


def test_build_errors():
    assert ax25c.build_ui("CQ", 0, "N0CALL", 0, bytes(257))[0] == Status.ERR_INFO_TOO_LONG
    assert ax25c.build_ui("CQ", 0, "N0CALL", 0, bytes(4), out_cap=16 + 4 + 1)[0] == Status.ERR_NO_SPACE
    assert ax25c.build_ui("CQ", 0, "N0CALL", 0, bytes(4), out_cap=16 + 4 + 2)[0] == Status.OK
    assert ax25c.build_ui_raw("CQ", 0, "N0CALL", 0, control=0x00)[0] == Status.ERR_UNSUPPORTED  # I-frame
    assert ax25c.build_ui_raw("CQ", 0, "N0CALL", 0, control=0x13)[0] == Status.OK  # UI with P bit
    assert ax25c.build_ui_raw("CQ", 0, "N0CALL", 16)[0] == Status.ERR_SSID  # re-validated
    assert ax25c.build_ui_raw("CQ", 0, "n0call", 0)[0] == Status.ERR_CALLSIGN


def test_parse_too_short_and_bad_fcs():
    frame = reference_frame()
    assert ax25c.parse_ui(frame[:17])[0] == Status.ERR_TOO_SHORT
    frame[16] ^= 0x01
    assert ax25c.parse_ui(frame)[0] == Status.ERR_FCS


@pytest.mark.parametrize("index, mutate, expected", [
    (13, lambda b: b & ~0x01, Status.ERR_UNSUPPORTED),  # src not last: digipeater path
    (6, lambda b: b | 0x01, Status.ERR_ADDRESS),        # address field ends after dest
    (14, lambda b: 0x00, Status.ERR_UNSUPPORTED),       # I-frame control
    (2, lambda b: ord("a") << 1, Status.ERR_ADDRESS),   # lowercase in callsign
    (0, lambda b: 0x40, Status.ERR_ADDRESS),            # leading space
    (3, lambda b: b | 0x01, Status.ERR_ADDRESS),        # extension bit on a callsign byte
])
def test_parse_rejects_malformed(index, mutate, expected):
    frame = reference_frame()
    frame[index] = mutate(frame[index]) & 0xFF
    assert ax25c.parse_ui(refresh_fcs(frame))[0] == expected


def test_parse_tolerates_reserved_bits_clear():
    frame = reference_frame()
    frame[6] &= ~0x60
    frame[13] &= ~0x60
    st, p = ax25c.parse_ui(refresh_fcs(frame))
    assert st == Status.OK and p.src_ssid == 1


def test_parse_accepts_reference_frames(rng):
    for _ in range(50):
        info = rng.randbytes(rng.randrange(0, 257))
        frame = ax25ref.build_ui("VK2XYZ", rng.randrange(16), "N0CALL", rng.randrange(16), info)
        st, p = ax25c.parse_ui(frame)
        assert st == Status.OK and p.info == info
