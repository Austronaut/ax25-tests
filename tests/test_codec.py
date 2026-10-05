"""Full encode/decode pipelines (ax25_codec.c)."""

import pytest

import ax25c
import ax25ref
from ax25c import Status


def make_frame(text=b"AUStronauts AX.25 test 0123456789"):
    st, frame = ax25c.build_ui("CQ", 0, "N0CALL", 1, text)
    assert st == Status.OK
    return frame


def test_default_config():
    assert ax25c.tx_config_default() == {
        "lead_flags": ax25ref.DEFAULT_LEAD_FLAGS,
        "trail_flags": ax25ref.DEFAULT_TRAIL_FLAGS,
        "scramble": True,
        "pad_to_byte": True,
    }


def test_first_flag_line_bits():
    # flag 0,1,1,1,1,1,1,0 from NRZI level 0 -> 1,1,1,1,1,1,1,0
    st, line = ax25c.encode(make_frame(b"hi"), lead=1, scrambled=False)
    assert st == Status.OK
    assert line[:8] == [1, 1, 1, 1, 1, 1, 1, 0]


@pytest.mark.parametrize("scrambled", [True, False])
@pytest.mark.parametrize("pad", [True, False])
def test_matches_reference_and_roundtrips(scrambled, pad):
    frame = make_frame()
    st, line = ax25c.encode(frame, scrambled=scrambled, pad=pad)
    assert st == Status.OK
    assert line == ax25ref.encode(frame, scrambled=scrambled, pad=pad)
    if pad:
        assert len(line) % 8 == 0
    assert len(line) <= ax25c.encoded_max_bits(len(frame), 16, 4)

    rx = ax25c.Receiver(descrambled=scrambled)
    rx.feed(line)
    assert rx.frames == [frame]
    assert rx.stats == {"frames_ok": 1, "fcs_errors": 0, "aborts": 0, "overflows": 0}
    assert ax25ref.decode(line, descrambled=scrambled) == [frame]


def test_padding_is_a_partial_flag():
    for n in range(1, 20):  # find a frame that isn't already byte aligned
        frame = make_frame(b"x" * n)
        _, unpadded = ax25c.encode(frame, scrambled=False, pad=False)
        if len(unpadded) % 8:
            break
    _, padded = ax25c.encode(frame, scrambled=False, pad=True)
    extra = len(padded) - len(unpadded)
    assert 0 < extra < 8
    nrz = ax25ref.nrzi_decode(padded)
    assert nrz[-extra:] == [0, 1, 1, 1, 1, 1, 1][:extra]


def test_max_size_worst_case_frame():
    st, frame = ax25c.build_ui("CQ", 0, "N0CALL", 1, b"\xff" * 256)
    assert st == Status.OK
    st, line = ax25c.encode(frame)
    assert st == Status.OK
    assert len(line) <= ax25c.encoded_max_bits(len(frame), 16, 4)
    rx = ax25c.Receiver()
    rx.feed(line)
    assert rx.frames == [frame]


def test_random_frames_c_and_reference_agree(rng):
    for _ in range(40):
        frame = make_frame(rng.randbytes(rng.randrange(0, 257)))
        _, line = ax25c.encode(frame)
        assert line == ax25ref.encode(frame)
        rx = ax25c.Receiver()
        rx.feed(line)
        assert rx.frames == [frame]


def test_inverted_polarity():
    frame = make_frame(b"inverted")
    _, line = ax25c.encode(frame)
    rx = ax25c.Receiver()
    rx.feed(line, invert=True)
    assert rx.frames == [frame]


def test_noise_then_back_to_back_bursts(rng):
    a, b = make_frame(b"first"), make_frame(b"second")
    rx = ax25c.Receiver()
    rx.feed([rng.randrange(2) for _ in range(200)])
    rx.feed(ax25c.encode(a)[1])
    rx.feed(ax25c.encode(b)[1])  # new burst: transmitter state reset
    assert rx.frames == [a, b]


def test_single_line_bit_error_never_delivered():
    # One line error becomes 1+x+x^12+x^13+x^17+x^18 after descrambling and
    # NRZI decoding; the CRC-16 generator doesn't divide it, so the FCS
    # catches it whenever framing survives.
    _, line = ax25c.encode(make_frame(b"corrupt me"))
    start, end = 16 * 8, len(line) - 4 * 8
    assert ax25c.count_deliveries_with_flips(line, start, end) == 0


def test_encode_errors():
    frame = make_frame(b"x")
    assert ax25c.encode(frame, lead=0)[0] == Status.ERR_ARG
    assert ax25c.encode(frame, trail=0)[0] == Status.ERR_ARG
    assert ax25c.encode(None)[0] == Status.ERR_ARG
    assert ax25c.encode(frame, cap_bytes=20)[0] == Status.ERR_NO_SPACE
