/*
 * Flat C API over lib/ax25 for Python ctypes.
 *
 * Only plain integers, byte buffers and opaque state blobs cross the
 * boundary, so Python never has to mirror the library's struct layouts.
 * Bit buffers are packed LSB-first, exactly as ax25_bitbuf_t stores them.
 */
#include <string.h>

#include "ax25/ax25_codec.h"

#define SHIM_MAX_FRAMES 16

/* ------------------------------------------------------------------ fcs */

unsigned
shim_fcs(const uint8_t *data, size_t len)
{
	return ax25_fcs(data, len);
}

int
shim_fcs_check(const uint8_t *frame, size_t len)
{
	return ax25_fcs_check(frame, len) ? 1 : 0;
}

/* ---------------------------------------------------------------- frame */

int
shim_address_set(const char *callsign, unsigned ssid)
{
	ax25_address_t addr;
	return (int)ax25_address_set(&addr, callsign, (uint8_t)ssid);
}

int
shim_ui_init_build(const char *dest, unsigned dest_ssid, const char *src,
    unsigned src_ssid, const uint8_t *info, size_t info_len, uint8_t *out,
    size_t out_cap, size_t *out_len)
{
	ax25_ui_frame_t f;
	ax25_status_t st = ax25_ui_frame_init(&f, dest, (uint8_t)dest_ssid, src,
	    (uint8_t)src_ssid, info, info_len);
	if (st != AX25_OK) {
		return (int)st;
	}
	return (int)ax25_ui_frame_build(&f, out, out_cap, out_len);
}

/* Fills the struct by hand (no validation) to test build's own checks.
 * Callsigns longer than 6 characters are truncated to fit the struct. */
int
shim_ui_build_raw(const char *dest, unsigned dest_ssid, const char *src,
    unsigned src_ssid, int is_command, unsigned control, unsigned pid,
    const uint8_t *info, size_t info_len, uint8_t *out, size_t out_cap,
    size_t *out_len)
{
	ax25_ui_frame_t f;
	memset(&f, 0, sizeof f);
	strncpy(f.dest.callsign, dest, AX25_CALLSIGN_MAX_LEN);
	strncpy(f.src.callsign, src, AX25_CALLSIGN_MAX_LEN);
	f.dest.ssid = (uint8_t)dest_ssid;
	f.src.ssid = (uint8_t)src_ssid;
	f.is_command = is_command != 0;
	f.control = (uint8_t)control;
	f.pid = (uint8_t)pid;
	f.info = info;
	f.info_len = info_len;
	return (int)ax25_ui_frame_build(&f, out, out_cap, out_len);
}

/* dest_call and src_call must hold AX25_CALLSIGN_MAX_LEN + 1 bytes. */
int
shim_ui_parse(const uint8_t *data, size_t len, char *dest_call,
    unsigned *dest_ssid, char *src_call, unsigned *src_ssid, int *is_command,
    unsigned *control, unsigned *pid, size_t *info_offset, size_t *info_len)
{
	ax25_ui_frame_t f;
	ax25_status_t st = ax25_ui_frame_parse(data, len, &f);
	if (st != AX25_OK) {
		return (int)st;
	}
	memcpy(dest_call, f.dest.callsign, sizeof f.dest.callsign);
	memcpy(src_call, f.src.callsign, sizeof f.src.callsign);
	*dest_ssid = f.dest.ssid;
	*src_ssid = f.src.ssid;
	*is_command = f.is_command ? 1 : 0;
	*control = f.control;
	*pid = f.pid;
	*info_offset = (size_t)(f.info - data);
	*info_len = f.info_len;
	return (int)AX25_OK;
}

/* ----------------------------------------------------------------- hdlc */

/* Appends to an existing packed buffer holding *nbits bits. */
int
shim_hdlc_encode(const uint8_t *frame, size_t len, size_t lead_flags,
    size_t trail_flags, uint8_t *packed, size_t packed_cap, size_t *nbits)
{
	ax25_bitbuf_t b;
	ax25_bitbuf_init(&b, packed, packed_cap);
	b.length_bits = *nbits;
	ax25_status_t st =
	    ax25_hdlc_encode(frame, len, lead_flags, trail_flags, &b);
	*nbits = b.length_bits;
	return (int)st;
}

/* Collects deframer output for one run over a packed NRZ bitstream. */
typedef struct {
	ax25_hdlc_rx_t rx;
	uint8_t buf[AX25_FRAME_MAX_LEN + 64u];
	uint8_t frames[SHIM_MAX_FRAMES][AX25_FRAME_MAX_LEN + 64u];
	size_t lens[SHIM_MAX_FRAMES];
	int count;
	int aborts;
	int overflows;
} shim_hdlc_t;

size_t
shim_hdlc_sizeof(void)
{
	return sizeof(shim_hdlc_t);
}

void
shim_hdlc_init(shim_hdlc_t *s, size_t rx_capacity)
{
	memset(s, 0, sizeof *s);
	if (rx_capacity > sizeof s->buf) {
		rx_capacity = sizeof s->buf;
	}
	ax25_hdlc_rx_init(&s->rx, s->buf, rx_capacity);
}

void
shim_hdlc_feed(shim_hdlc_t *s, const uint8_t *packed, size_t nbits)
{
	ax25_bitbuf_t b;
	ax25_bitbuf_init(&b, (uint8_t *)(uintptr_t)packed, (nbits + 7u) / 8u);
	b.length_bits = nbits;
	for (size_t i = 0u; i < nbits; i++) {
		size_t len = 0u;
		switch (
		    ax25_hdlc_rx_bit(&s->rx, ax25_bitbuf_get(&b, i), &len)) {
		case AX25_HDLC_RX_FRAME:
			if (s->count < SHIM_MAX_FRAMES) {
				memcpy(s->frames[s->count], s->buf, len);
				s->lens[s->count] = len;
			}
			s->count++;
			break;
		case AX25_HDLC_RX_ABORT:
			s->aborts++;
			break;
		case AX25_HDLC_RX_OVERFLOW:
			s->overflows++;
			break;
		case AX25_HDLC_RX_NONE:
		default:
			break;
		}
	}
}

void
shim_hdlc_results(const shim_hdlc_t *s, int *count, int *aborts, int *overflows)
{
	*count = s->count;
	*aborts = s->aborts;
	*overflows = s->overflows;
}

/* Copies frame i to dst; returns its length (0 if i is out of range). */
size_t
shim_hdlc_frame(const shim_hdlc_t *s, int i, uint8_t *dst, size_t cap)
{
	if (i < 0 || i >= s->count || i >= SHIM_MAX_FRAMES ||
	    s->lens[i] > cap) {
		return 0u;
	}
	memcpy(dst, s->frames[i], s->lens[i]);
	return s->lens[i];
}

/* ----------------------------------------------------------------- line */

static void
wrap(ax25_bitbuf_t *b, uint8_t *packed, size_t nbits)
{
	ax25_bitbuf_init(b, packed, (nbits + 7u) / 8u);
	b->length_bits = nbits;
}

void
shim_nrzi_encode(uint8_t *packed, size_t nbits, unsigned *level)
{
	ax25_bitbuf_t b;
	ax25_nrzi_t s = {(uint8_t)*level};
	wrap(&b, packed, nbits);
	ax25_nrzi_encode_buf(&s, &b);
	*level = s.level;
}

void
shim_nrzi_decode(uint8_t *packed, size_t nbits, unsigned *level)
{
	ax25_bitbuf_t b;
	ax25_nrzi_t s = {(uint8_t)*level};
	wrap(&b, packed, nbits);
	ax25_nrzi_decode_buf(&s, &b);
	*level = s.level;
}

void
shim_scramble(uint8_t *packed, size_t nbits, uint32_t *reg)
{
	ax25_bitbuf_t b;
	ax25_g3ruh_t s = {*reg};
	wrap(&b, packed, nbits);
	ax25_g3ruh_scramble_buf(&s, &b);
	*reg = s.reg;
}

void
shim_descramble(uint8_t *packed, size_t nbits, uint32_t *reg)
{
	ax25_bitbuf_t b;
	ax25_g3ruh_t s = {*reg};
	wrap(&b, packed, nbits);
	ax25_g3ruh_descramble_buf(&s, &b);
	*reg = s.reg;
}

/* Feeds zeros through the scrambler from reg until it returns to reg. */
uint32_t
shim_scrambler_period(uint32_t reg, uint32_t limit)
{
	ax25_g3ruh_t s = {reg};
	uint32_t steps = 0u;
	do {
		(void)ax25_g3ruh_scramble_bit(&s, 0u);
		steps++;
	} while (s.reg != reg && steps < limit);
	return steps;
}

/* ---------------------------------------------------------------- codec */

void
shim_tx_config_default(size_t *lead, size_t *trail, int *scramble, int *pad)
{
	const ax25_tx_config_t cfg = ax25_tx_config_default();
	*lead = cfg.lead_flags;
	*trail = cfg.trail_flags;
	*scramble = cfg.scramble ? 1 : 0;
	*pad = cfg.pad_to_byte ? 1 : 0;
}

size_t
shim_encoded_max_bits(size_t frame_len, size_t lead, size_t trail)
{
	return AX25_ENCODED_MAX_BITS(frame_len, lead, trail);
}

int
shim_encode(const uint8_t *frame, size_t len, size_t lead, size_t trail,
    int scramble, int pad, uint8_t *packed, size_t packed_cap, size_t *nbits)
{
	ax25_tx_config_t cfg;
	cfg.lead_flags = lead;
	cfg.trail_flags = trail;
	cfg.scramble = scramble != 0;
	cfg.pad_to_byte = pad != 0;
	ax25_bitbuf_t b;
	ax25_bitbuf_init(&b, packed, packed_cap);
	ax25_status_t st = ax25_encode(frame, len, &cfg, &b);
	*nbits = b.length_bits;
	return (int)st;
}

/* A streaming receiver plus storage for the frames it delivers. */
typedef struct {
	ax25_rx_t rx;
	uint8_t buf[AX25_FRAME_MAX_LEN];
	uint8_t frames[SHIM_MAX_FRAMES][AX25_FRAME_MAX_LEN];
	size_t lens[SHIM_MAX_FRAMES];
	int count;
} shim_rx_t;

static void
on_frame(void *user, const uint8_t *data, size_t len)
{
	shim_rx_t *s = user;
	if (s->count < SHIM_MAX_FRAMES) {
		memcpy(s->frames[s->count], data, len);
		s->lens[s->count] = len;
	}
	s->count++;
}

size_t
shim_rx_sizeof(void)
{
	return sizeof(shim_rx_t);
}

void
shim_rx_init(shim_rx_t *s, int descramble)
{
	memset(s, 0, sizeof *s);
	ax25_rx_init(
	    &s->rx, s->buf, sizeof s->buf, descramble != 0, on_frame, s);
}

void
shim_rx_feed(shim_rx_t *s, const uint8_t *packed, size_t nbits, int invert)
{
	ax25_bitbuf_t b;
	ax25_bitbuf_init(&b, (uint8_t *)(uintptr_t)packed, (nbits + 7u) / 8u);
	b.length_bits = nbits;
	const uint8_t flip = invert ? 1u : 0u;
	for (size_t i = 0u; i < nbits; i++) {
		ax25_rx_bit(&s->rx, (uint8_t)(ax25_bitbuf_get(&b, i) ^ flip));
	}
}

void
shim_rx_stats(const shim_rx_t *s, uint32_t out[4])
{
	out[0] = s->rx.stats.frames_ok;
	out[1] = s->rx.stats.fcs_errors;
	out[2] = s->rx.stats.aborts;
	out[3] = s->rx.stats.overflows;
}

int
shim_rx_count(const shim_rx_t *s)
{
	return s->count;
}

size_t
shim_rx_frame(const shim_rx_t *s, int i, uint8_t *dst, size_t cap)
{
	if (i < 0 || i >= s->count || i >= SHIM_MAX_FRAMES ||
	    s->lens[i] > cap) {
		return 0u;
	}
	memcpy(dst, s->frames[i], s->lens[i]);
	return s->lens[i];
}

/* Encode once, then for every line bit in [start, end) flip it, decode,
 * and count frames delivered. One C call instead of millions of ctypes
 * calls. */
int
shim_count_deliveries_with_flips(const uint8_t *packed, size_t nbits,
    size_t start, size_t end, uint8_t *scratch, shim_rx_t *s)
{
	int delivered = 0;
	const size_t nbytes = (nbits + 7u) / 8u;
	for (size_t pos = start; pos < end && pos < nbits; pos++) {
		memcpy(scratch, packed, nbytes);
		scratch[pos / 8u] ^= (uint8_t)(1u << (pos % 8u));
		shim_rx_init(s, 1);
		shim_rx_feed(s, scratch, nbits, 0);
		delivered += s->count;
	}
	return delivered;
}
