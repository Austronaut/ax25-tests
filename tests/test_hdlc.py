"""HDLC flags, bit stuffing and deframing (ax25_hdlc.c)."""

import pytest

import ax25c
import ax25ref
from ax25c import Status

FLAG = [0, 1, 1, 1, 1, 1, 1, 0]  # 0x7E LSB-first


def bits(s):
    return [int(c) for c in s.replace(" ", "")]


def test_flags_are_lsb_first_and_unstuffed():
    assert ax25c.hdlc_encode(None, 1, 1) == (Status.OK, FLAG + FLAG)


@pytest.mark.parametrize("frame, expected", [
    (b"\xff\x00", "11111 0 111  00000000"),   # stuffed 0 after five 1s
    (b"\x1f", "11111 0 000"),                 # stuffed even when a 0 follows anyway
    (b"\xf0\x01", "0000 1111 1 0 0000000"),   # run spans a byte boundary
    (b"\x7e", "0 11111 0 10"),                # flag-valued data byte gets stuffed
])
def test_stuffing_patterns(frame, expected):
    assert ax25c.hdlc_encode(frame, 0, 0) == (Status.OK, bits(expected))


def test_matches_reference_and_never_six_ones_inside(rng):
    for i in range(60):
        frame = (b"\xff" * 64 if i == 0 else b"\x7e" * 64 if i == 1
                 else rng.randbytes(rng.randrange(1, 275)))
        st, out = ax25c.hdlc_encode(frame, 1, 1)
        assert st == Status.OK
        assert out == ax25ref.hdlc_encode(frame, 1, 1)
        assert "111111" not in ax25ref.bits_to_str(out[8:-8])


def test_worst_case_fits_max_bits_bound():
    frame = b"\xff" * ax25ref.FRAME_MAX_LEN
    st, out = ax25c.hdlc_encode(frame, 3, 2)
    assert st == Status.OK
    assert len(out) <= (3 + 2) * 8 + len(frame) * 8 + len(frame) * 8 // 5


def test_no_space():
    assert ax25c.hdlc_encode(b"\x00", 2, 1, cap_bits=16)[0] == Status.ERR_NO_SPACE


def test_appends_after_existing_bits():
    prefix = [1, 0, 1]
    st, out = ax25c.hdlc_encode(b"\x00", 1, 0, prefix=prefix)
    assert st == Status.OK and out == prefix + FLAG + [0] * 8


@pytest.mark.parametrize("length", [17, 18, 20, 100, ax25ref.FRAME_MAX_LEN])
@pytest.mark.parametrize("fill", ["random", "ones", "flags"])
def test_roundtrip(rng, length, fill):
    frame = {"random": rng.randbytes(length), "ones": b"\xff" * length,
             "flags": b"\x7e" * length}[fill]
    _, line = ax25c.hdlc_encode(frame, 2, 2)
    result = ax25c.hdlc_decode(line)
    assert result.frames == [frame]
    assert ax25ref.hdlc_decode(line) == [frame]  # reference agrees on C output


def test_decodes_reference_output(rng):
    for _ in range(30):
        frame = rng.randbytes(rng.randrange(17, 275))
        assert ax25c.hdlc_decode(ax25ref.hdlc_encode(frame, 1, 1)).frames == [frame]


def test_garbage_before_first_flag(rng):
    frame = rng.randbytes(20)
    noise = [rng.randrange(2) for _ in range(37)]
    _, line = ax25c.hdlc_encode(frame, 1, 1, prefix=noise)
    assert ax25c.hdlc_decode(line).frames == [frame]


def test_frames_sharing_one_flag(rng):
    a, b = rng.randbytes(20), rng.randbytes(30)
    _, line = ax25c.hdlc_encode(a, 1, 1)
    _, line = ax25c.hdlc_encode(b, 0, 1, prefix=line)
    assert ax25c.hdlc_decode(line).frames == [a, b]


def test_abort_discards_frame_in_progress(rng):
    a, b = rng.randbytes(20), rng.randbytes(20)
    _, line = ax25c.hdlc_encode(a[:10], 1, 0)
    _, line = ax25c.hdlc_encode(b, 1, 1, prefix=line + [1] * 8)
    result = ax25c.hdlc_decode(line)
    assert result.aborts == 1
    assert result.frames == [b]


def test_overflow_then_recovery(rng):
    big, small = rng.randbytes(60), rng.randbytes(20)
    _, line = ax25c.hdlc_encode(big, 1, 1)
    _, line = ax25c.hdlc_encode(small, 1, 1, prefix=line)
    result = ax25c.hdlc_decode(line, rx_capacity=40)
    assert result.overflows == 1
    assert result.frames == [small]


def test_short_frame_ignored(rng):
    _, line = ax25c.hdlc_encode(rng.randbytes(16), 1, 1)
    assert ax25c.hdlc_decode(line).frames == []


def test_unaligned_frame_ignored(rng):
    _, line = ax25c.hdlc_encode(rng.randbytes(20), 1, 0)
    _, line = ax25c.hdlc_encode(None, 1, 0, prefix=line + [0])  # one stray bit
    assert ax25c.hdlc_decode(line).frames == []
