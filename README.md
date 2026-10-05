# ax25-tests

Test bench for the AX.25 library in the OBC repo (`lib/ax25`). Kept outside
the OBC repo so no test code ships with flight software. Every run compiles
the library straight from the OBC repo, and the test header records which
OBC commit was tested.

## Run

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/pytest -v
```

The OBC repo is expected at `../UNSW-Austronauts-OBC`; point elsewhere with
`OBC_REPO=/path/to/repo .venv/bin/pytest`.

## Socket loopback (end to end)

```
pytest ── starts ──▶ ax25server.py (Python, TCP)       decoder: ax25ref.py (independent)
   │                        ▲  optional channel: noise, bit flips, inversion
   └── runs ──▶ build/ax25_send (C, lib/ax25 from the OBC repo) ── line bits over TCP
```

The test chooses callsigns and payloads, the C sender encodes them with the
OBC library and sends the raw line bitstream, and the server must decode
exactly the frames the test expects (built independently by `ax25ref`).

By hand, in two terminals:

```sh
.venv/bin/python ax25build.py                       # builds build/ax25_send
.venv/bin/python ax25server.py --port 8001          # prints one JSON line per frame
build/ax25_send --port 8001 --src VK2ABC-7 --info "hello" --info "second frame"
```

Running `ax25_send` with no arguments prints its usage. Wire format: the raw
line bits, packed bit i -> byte i/8, position i%8 (as `ax25_bitbuf_t`).

## Files

| File | Role |
|---|---|
| `sender/ax25_send.c` | C program: build frames + `ax25_encode` with the OBC library, send over TCP |
| `ax25server.py` | TCP server: unpack bits, simulated channel, decode, parse, JSON output |
| `ax25ref.py` | Independent pure-Python AX.25 (encoder, batch + streaming decoder) |
| `ax25build.py` | Compiles the C code against the OBC repo; writes `build/compile_commands.json` for clangd |
| `ax25c.py`, `shim/ax25_shim.c` | ctypes access to individual library layers, for the layer tests |

## Tests

| File | Covers |
|---|---|
| `test_socket_loopback.py` | C sender → TCP → Python decoder: payloads, ordering, TCP fragmentation, noise, inversion, bit errors, bad input |
| `test_fcs.py` | CRC-16/X.25 check value, crcmod agreement, byte order, error detection |
| `test_frame.py` | Hand-derived frame bytes, C/H/extension bits, validation, parse errors |
| `test_hdlc.py` | Flags, stuffing patterns, deframer (abort, overflow, shared flags, noise) |
| `test_line.py` | NRZI, G3RUH vs Direwolf, maximal length, self-sync, RX order equivalence |
| `test_codec.py` | Full pipelines, C vs reference bit-for-bit, polarity, bit errors |
| `test_msp430.py` | Library compiles warning-free for MSP430 (16-bit int); skipped without Homebrew LLVM |

C code is compiled with `-Werror -Wconversion` and UBSan in trap mode.
