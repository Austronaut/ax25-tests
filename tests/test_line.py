"""NRZI and G3RUH scrambler (ax25_line.c)."""

import ax25c
import ax25ref

N_BITS = 4000


def random_bits(rng, n=N_BITS):
    return [rng.randrange(2) for _ in range(n)]


def direwolf_scramble(bits):
    """Transcription of Direwolf src/gen_tone.c lines 451-457
    (wb2osz/direwolf @ eda1383)."""
    lfsr, out = 0, []
    for dat in bits:
        x = (dat ^ (lfsr >> 16) ^ (lfsr >> 11)) & 1
        lfsr = (lfsr << 1) | (x & 1)
        out.append(x)
    return out


def direwolf_descramble(bits):
    """Transcription of Direwolf descramble(), src/demod_9600.h lines 18-25."""
    state, out = 0, []
    for bit in bits:
        out.append((bit ^ (state >> 16) ^ (state >> 11)) & 1)
        state = (state << 1) | (bit & 1)
    return out


def test_nrzi_known_sequence():
    # level starts at 0; a 0 toggles, a 1 holds
    assert ax25c.nrzi_encode([0, 1, 1, 0, 0, 1, 0])[0] == [1, 1, 1, 0, 1, 1, 0]


def test_nrzi_matches_reference_and_roundtrips(rng):
    data = random_bits(rng)
    line, _ = ax25c.nrzi_encode(data)
    assert line == ax25ref.nrzi_encode(data)
    assert ax25c.nrzi_decode(line)[0] == data


def test_nrzi_decode_ignores_polarity(rng):
    data = random_bits(rng)
    line, _ = ax25c.nrzi_encode(data)
    inverted = [b ^ 1 for b in line]
    assert ax25c.nrzi_decode(inverted)[0][1:] == data[1:]  # only bit 0 may differ


def test_scrambler_matches_direwolf_and_reference(rng):
    data = random_bits(rng)
    scrambled, _ = ax25c.scramble(data)
    assert scrambled == direwolf_scramble(data)
    assert scrambled == ax25ref.scramble(data)
    assert ax25c.descramble(scrambled)[0] == direwolf_descramble(scrambled) == data


def test_scrambler_is_maximal_length():
    # x^17 + x^12 + 1 is primitive: a non-zero register fed zeros visits all
    # 2^17 - 1 states before repeating. A wrong tap breaks this.
    assert ax25c.scrambler_period(1, 200_000) == 2**17 - 1


def test_descrambler_self_synchronises(rng):
    data = random_bits(rng)
    scrambled, _ = ax25c.scramble(data)
    recovered, _ = ax25c.descramble(scrambled, reg=0x15A5A)  # wrong start state
    assert recovered[17:] == data[17:]


def test_state_carries_across_calls(rng):
    data = random_bits(rng)
    whole, _ = ax25c.scramble(data)
    first, reg = ax25c.scramble(data[:1234])
    second, _ = ax25c.scramble(data[1234:], reg)
    assert first + second == whole

    whole, _ = ax25c.nrzi_encode(data)
    first, level = ax25c.nrzi_encode(data[:777])
    second, _ = ax25c.nrzi_encode(data[777:], level)
    assert first + second == whole


def test_receive_orders_equivalent(rng):
    """Direwolf (descramble -> NRZI) and gr-satellites (NRZI -> descramble)
    recover the same data, with either polarity, after the 18-bit start-up."""
    data = random_bits(rng)
    line, _ = ax25c.scramble(ax25c.nrzi_encode(data)[0])

    direwolf_order = ax25c.nrzi_decode(ax25c.descramble(line)[0])[0]
    gr_order = ax25c.descramble(ax25c.nrzi_decode(line)[0])[0]
    inverted = ax25c.nrzi_decode(ax25c.descramble([b ^ 1 for b in line])[0])[0]

    assert direwolf_order == data
    assert gr_order[18:] == data[18:]
    assert inverted[18:] == data[18:]
