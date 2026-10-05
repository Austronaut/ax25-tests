"""CRC-16/X.25 frame check sequence (ax25_fcs.c)."""

import crcmod.predefined
import pytest

import ax25c
import ax25ref

crc_x25 = crcmod.predefined.mkCrcFun("x-25")  # independent implementation


def test_check_value():
    assert ax25c.fcs(b"123456789") == 0x906E


def test_empty_input():
    assert ax25c.fcs(b"") == 0x0000


@pytest.mark.parametrize("data", [b"A", b"\x00", b"\xff" * 40, bytes(range(256))])
def test_matches_crcmod(data):
    assert ax25c.fcs(data) == crc_x25(data)


def test_random_data_matches_crcmod_and_reference(rng):
    for _ in range(200):
        data = rng.randbytes(rng.randrange(0, 300))
        assert ax25c.fcs(data) == crc_x25(data) == ax25ref.fcs(data)


def test_check_expects_low_byte_first():
    assert ax25c.fcs_check(b"123456789\x6e\x90")
    assert not ax25c.fcs_check(b"123456789\x90\x6e")


def test_check_detects_every_single_bit_error():
    frame = bytearray(b"123456789\x6e\x90")
    for i in range(len(frame) * 8):
        frame[i // 8] ^= 1 << (i % 8)
        assert not ax25c.fcs_check(frame), f"bit {i} flip went undetected"
        frame[i // 8] ^= 1 << (i % 8)


def test_check_rejects_short_and_null():
    assert not ax25c.fcs_check(b"\x00")
    assert not ax25c.fcs_check(None)
