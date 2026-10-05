"""End to end over TCP:

    C sender (lib/ax25 from the OBC repo)  --line bits-->  Python server (ax25ref decoder)

The test decides what to send, the C program encodes and sends it, and the
server's independent decoder must recover exactly that. A simulated channel
on the server side adds noise, bit errors and polarity inversion.
"""

import subprocess

import pytest

import ax25ref
from ax25server import Ax25Server, Channel


def send(sender, server, *args, check=True):
    result = subprocess.run([str(sender), "--port", str(server.port), *args],
                            capture_output=True, text=True, timeout=10)
    if check:
        assert result.returncode == 0, result.stderr
    return result


def run_session(sender, *args, **server_kwargs):
    """Start a server, send once, return the decoded session."""
    with Ax25Server(**server_kwargs) as server:
        send(sender, server, *args)
        return server.wait_for_sessions(1)[0]


def expected_frame(info, dest=("CQ", 0), src=("N0CALL", 1)):
    """What the frame must be, built by the independent Python implementation."""
    return ax25ref.build_ui(*dest, *src, info)


def test_single_frame(sender):
    session = run_session(sender, "--dest", "CQ-0", "--src", "VK2ABC-7",
                          "--info", "hello from the OBC library")
    assert len(session.frames) == 1
    decoded = session.frames[0]
    assert decoded.error is None
    assert decoded.frame == expected_frame(b"hello from the OBC library", src=("VK2ABC", 7))
    p = decoded.parsed
    assert (p["dest"], p["dest_ssid"], p["src"], p["src_ssid"]) == ("CQ", 0, "VK2ABC", 7)
    assert p["command"] and p["control"] == 0x03 and p["pid"] == 0xF0
    assert session.stats == {"frames_ok": 1, "fcs_errors": 0}


def test_many_frames_arrive_in_order(sender):
    infos = [f"beacon {i:03d} batt=7.{i % 10}V temp={20 + i}C".encode() for i in range(30)]
    args = [a for info in infos for a in ("--info", info.decode())]
    session = run_session(sender, *args)
    assert [d.frame for d in session.frames] == [expected_frame(i) for i in infos]


@pytest.mark.parametrize("info", [b"", bytes(range(256)), b"\xff" * 256, b"\x7e" * 256],
                         ids=["empty", "all-byte-values", "all-ones", "all-flags"])
def test_binary_and_worst_case_payloads(sender, info):
    session = run_session(sender, "--info-hex", info.hex())
    assert [d.frame for d in session.frames] == [expected_frame(info)]


def test_random_payloads(sender, rng):
    infos = [rng.randbytes(rng.randrange(0, 257)) for _ in range(20)]
    args = [a for info in infos for a in ("--info-hex", info.hex())]
    session = run_session(sender, *args)
    assert [d.frame for d in session.frames] == [expected_frame(i) for i in infos]


@pytest.mark.parametrize("recv_size", [1, 3, 7])
def test_tcp_fragmentation_does_not_matter(sender, recv_size):
    # Server reads tiny pieces, so frames and flags are split at odd points.
    session = run_session(sender, "--info", "first", "--info", "second",
                          recv_size=recv_size)
    assert [d.frame for d in session.frames] == [expected_frame(b"first"),
                                                 expected_frame(b"second")]


def test_plain_nrzi_without_scrambler(sender):
    session = run_session(sender, "--no-scramble", "--info", "no G3RUH", descrambled=False)
    assert [d.frame for d in session.frames] == [expected_frame(b"no G3RUH")]


def test_minimum_flags(sender):
    session = run_session(sender, "--lead", "1", "--trail", "1", "--info", "tight")
    assert [d.frame for d in session.frames] == [expected_frame(b"tight")]


# ------------------------------------------------------------ channel model

def test_inverted_polarity(sender):
    session = run_session(sender, "--info", "upside down", channel=Channel(invert=True))
    assert [d.frame for d in session.frames] == [expected_frame(b"upside down")]


def test_noise_before_transmission(sender, rng):
    noise = [rng.randrange(2) for _ in range(500)]
    session = run_session(sender, "--info", "after noise", channel=Channel(noise_prefix=noise))
    assert [d.frame for d in session.frames] == [expected_frame(b"after noise")]


def frame_body_bits(info, lead=ax25ref.DEFAULT_LEAD_FLAGS, trail=ax25ref.DEFAULT_TRAIL_FLAGS):
    """Line-bit range [start, end) of one encoded frame's body, so a flip
    there hits the frame rather than its flags."""
    total = len(ax25ref.encode(expected_frame(info), lead, trail))
    return lead * 8, total - trail * 8, total


def test_corrupted_frame_rejected_neighbours_survive(sender):
    # Three frames back to back; flip one bit in the middle of the second.
    _, _, first_len = frame_body_bits(b"one")
    start, end, _ = frame_body_bits(b"two")
    target = first_len + (start + end) // 2
    session = run_session(sender, "--info", "one", "--info", "two", "--info", "three",
                          channel=Channel(flip_bits=frozenset({target})))
    assert [d.frame for d in session.frames] == [expected_frame(b"one"),
                                                 expected_frame(b"three")]
    assert session.stats["frames_ok"] == 2


@pytest.mark.parametrize("fraction", [0.1, 0.3, 0.5, 0.7, 0.9])
def test_single_bit_error_never_delivered(sender, fraction):
    start, end, _ = frame_body_bits(b"corrupt me")
    target = start + int((end - start) * fraction)
    session = run_session(sender, "--info", "corrupt me",
                          channel=Channel(flip_bits=frozenset({target})))
    assert session.frames == []


# ---------------------------------------------------------- sender errors

@pytest.mark.parametrize("args, message", [
    (["--src", "n0call", "--info", "x"], "invalid callsign"),
    (["--dest", "CQ-16", "--info", "x"], "invalid SSID"),
    (["--info-hex", "00" * 257], "info field too long"),  # rejected by the library
    (["--info", "x" * 257], "info field too long"),
    (["--info-hex", "zz"], "bad value for --info-hex"),
    ([], "no --info"),
])
def test_sender_rejects_bad_input_and_sends_nothing(sender, args, message):
    with Ax25Server() as server:
        result = send(sender, server, *args, check=False)
        assert result.returncode == 1
        assert message in result.stderr
        with pytest.raises(TimeoutError):
            server.wait_for_sessions(1, timeout=0.3)  # never connected
