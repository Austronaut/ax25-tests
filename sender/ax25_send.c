/*
 * ax25_send: encodes AX.25 UI frames with lib/ax25 from the OBC repo and
 * sends the resulting line bitstream to a TCP server.
 *
 *   ax25_send [--host H] [--port P] [--dest CALL[-SSID]] [--src CALL[-SSID]]
 *             [--lead N] [--trail N] [--no-scramble]
 *             (--info TEXT | --info-hex HEX)...
 *
 * Each --info / --info-hex becomes one frame, encoded as its own
 * transmission (ax25_encode), all sent over one connection.
 *
 * Wire format: the raw line bits, packed exactly as ax25_bitbuf_t stores
 * them (bit i in byte i/8, position i%8). Closing the connection ends the
 * session.
 *
 * Exit codes: 0 sent, 1 bad arguments or frame, 2 network error.
 */
#define _POSIX_C_SOURCE 200112L

#include <netdb.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <unistd.h>

#include "ax25/ax25_codec.h"

#define MAX_FRAMES 64
#define MAX_FLAGS  255u
#define LINE_BYTES                                                             \
	AX25_BITS_TO_BYTES(                                                    \
	    AX25_ENCODED_MAX_BITS(AX25_FRAME_MAX_LEN, MAX_FLAGS, MAX_FLAGS))

typedef struct {
	const char *text; /* --info: bytes of the string */
	uint8_t bytes[AX25_INFO_MAX_LEN + 1u];
	size_t len;
} info_arg_t;

typedef struct {
	const char *host;
	const char *port;
	char dest[AX25_CALLSIGN_MAX_LEN + 2u];
	uint8_t dest_ssid;
	char src[AX25_CALLSIGN_MAX_LEN + 2u];
	uint8_t src_ssid;
	ax25_tx_config_t tx;
	info_arg_t infos[MAX_FRAMES];
	size_t n_infos;
} options_t;

static const char *
status_name(ax25_status_t st)
{
	switch (st) {
	case AX25_OK:
		return "OK";
	case AX25_ERR_ARG:
		return "invalid argument";
	case AX25_ERR_CALLSIGN:
		return "invalid callsign";
	case AX25_ERR_SSID:
		return "invalid SSID";
	case AX25_ERR_INFO_TOO_LONG:
		return "info field too long";
	case AX25_ERR_NO_SPACE:
		return "buffer too small";
	case AX25_ERR_TOO_SHORT:
		return "frame too short";
	case AX25_ERR_FCS:
		return "bad FCS";
	case AX25_ERR_ADDRESS:
		return "malformed address";
	case AX25_ERR_UNSUPPORTED:
		return "unsupported";
	default:
		return "unknown status";
	}
}

static void
usage(void)
{
	fprintf(stderr,
	    "usage: ax25_send [--host H] [--port P] [--dest CALL[-SSID]] "
	    "[--src CALL[-SSID]]\n"
	    "                 [--lead N] [--trail N] [--no-scramble]\n"
	    "                 (--info TEXT | --info-hex HEX)...\n");
}

/* Parses "CALL" or "CALL-SSID". Callsign characters are validated later by
 * the library itself. */
static int
parse_address(const char *arg, char *call, size_t call_size, uint8_t *ssid)
{
	const char *dash = strchr(arg, '-');
	const size_t len = dash ? (size_t)(dash - arg) : strlen(arg);
	if (len >= call_size) {
		return -1;
	}
	memcpy(call, arg, len);
	call[len] = '\0';
	*ssid = 0u;
	if (dash != NULL) {
		char *end = NULL;
		const long v = strtol(dash + 1, &end, 10);
		if (dash[1] == '\0' || *end != '\0' || v < 0 || v > 255) {
			return -1;
		}
		*ssid = (uint8_t)v; /* range 0..15 is checked by the library */
	}
	return 0;
}

static int
parse_count(const char *arg, size_t *out)
{
	char *end = NULL;
	const long v = strtol(arg, &end, 10);
	if (arg[0] == '\0' || *end != '\0' || v < 1 || v > (long)MAX_FLAGS) {
		return -1;
	}
	*out = (size_t)v;
	return 0;
}

static int
hex_value(char c)
{
	if (c >= '0' && c <= '9') {
		return c - '0';
	}
	if (c >= 'a' && c <= 'f') {
		return c - 'a' + 10;
	}
	if (c >= 'A' && c <= 'F') {
		return c - 'A' + 10;
	}
	return -1;
}

static int
parse_hex(const char *arg, info_arg_t *info)
{
	const size_t n = strlen(arg);
	if (n % 2u != 0u || n / 2u > sizeof info->bytes) {
		return -1;
	}
	for (size_t i = 0u; i < n / 2u; i++) {
		const int hi = hex_value(arg[2u * i]);
		const int lo = hex_value(arg[2u * i + 1u]);
		if (hi < 0 || lo < 0) {
			return -1;
		}
		info->bytes[i] = (uint8_t)((hi << 4) | lo);
	}
	info->text = NULL;
	info->len = n / 2u;
	return 0;
}

static int
parse_options(int argc, char **argv, options_t *o)
{
	memset(o, 0, sizeof *o);
	o->host = "127.0.0.1";
	o->port = "8001";
	strcpy(o->dest, "CQ");
	strcpy(o->src, "N0CALL");
	o->src_ssid = 1u;
	o->tx = ax25_tx_config_default();

	for (int i = 1; i < argc; i++) {
		const char *opt = argv[i];
		const char *val = (i + 1 < argc) ? argv[i + 1] : NULL;
		int bad = 0;
		if (strcmp(opt, "--no-scramble") == 0) {
			o->tx.scramble = false;
			continue;
		}
		if (val == NULL) {
			fprintf(stderr, "ax25_send: %s needs a value\n", opt);
			return -1;
		}
		i++;
		if (strcmp(opt, "--host") == 0) {
			o->host = val;
		}
		else if (strcmp(opt, "--port") == 0) {
			o->port = val;
		}
		else if (strcmp(opt, "--dest") == 0) {
			bad = parse_address(
			    val, o->dest, sizeof o->dest, &o->dest_ssid);
		}
		else if (strcmp(opt, "--src") == 0) {
			bad = parse_address(
			    val, o->src, sizeof o->src, &o->src_ssid);
		}
		else if (strcmp(opt, "--lead") == 0) {
			bad = parse_count(val, &o->tx.lead_flags);
		}
		else if (strcmp(opt, "--trail") == 0) {
			bad = parse_count(val, &o->tx.trail_flags);
		}
		else if (strcmp(opt, "--info") == 0 ||
			 strcmp(opt, "--info-hex") == 0) {
			if (o->n_infos == MAX_FRAMES) {
				fprintf(stderr,
				    "ax25_send: at most %d frames\n",
				    MAX_FRAMES);
				return -1;
			}
			info_arg_t *info = &o->infos[o->n_infos++];
			if (opt[6] == '-') {
				bad = parse_hex(val, info);
			}
			else {
				info->text = val;
				info->len = strlen(val);
			}
		}
		else {
			fprintf(stderr, "ax25_send: unknown option %s\n", opt);
			return -1;
		}
		if (bad) {
			fprintf(stderr, "ax25_send: bad value for %s: %s\n",
			    opt, val);
			return -1;
		}
	}
	if (o->n_infos == 0u) {
		fprintf(stderr, "ax25_send: no --info or --info-hex given\n");
		return -1;
	}
	return 0;
}

static int
connect_to(const char *host, const char *port)
{
	struct addrinfo hints;
	struct addrinfo *res = NULL;
	memset(&hints, 0, sizeof hints);
	hints.ai_family = AF_UNSPEC;
	hints.ai_socktype = SOCK_STREAM;
	const int rc = getaddrinfo(host, port, &hints, &res);
	if (rc != 0) {
		fprintf(stderr, "ax25_send: %s:%s: %s\n", host, port,
		    gai_strerror(rc));
		return -1;
	}
	int fd = -1;
	for (struct addrinfo *ai = res; ai != NULL; ai = ai->ai_next) {
		fd = socket(ai->ai_family, ai->ai_socktype, ai->ai_protocol);
		if (fd < 0) {
			continue;
		}
		if (connect(fd, ai->ai_addr, ai->ai_addrlen) == 0) {
			break;
		}
		close(fd);
		fd = -1;
	}
	freeaddrinfo(res);
	if (fd < 0) {
		fprintf(
		    stderr, "ax25_send: cannot connect to %s:%s\n", host, port);
	}
	return fd;
}

static int
send_all(int fd, const uint8_t *data, size_t len)
{
	while (len > 0u) {
		const ssize_t n = send(fd, data, len, 0);
		if (n <= 0) {
			return -1;
		}
		data += n;
		len -= (size_t)n;
	}
	return 0;
}

int
main(int argc, char **argv)
{
	static options_t opts;
	static uint8_t frame[AX25_FRAME_MAX_LEN];
	static uint8_t line_storage[LINE_BYTES];

	if (parse_options(argc, argv, &opts) != 0) {
		usage();
		return 1;
	}

	/* Encode everything before connecting, so a bad frame sends nothing. */
	static uint8_t wire[MAX_FRAMES][LINE_BYTES];
	static size_t wire_len[MAX_FRAMES];
	for (size_t i = 0u; i < opts.n_infos; i++) {
		const info_arg_t *info = &opts.infos[i];
		const uint8_t *bytes =
		    info->text ? (const uint8_t *)info->text : info->bytes;
		ax25_ui_frame_t f;
		size_t frame_len = 0u;
		ax25_status_t st = ax25_ui_frame_init(&f, opts.dest,
		    opts.dest_ssid, opts.src, opts.src_ssid, bytes, info->len);
		if (st == AX25_OK) {
			st = ax25_ui_frame_build(
			    &f, frame, sizeof frame, &frame_len);
		}
		ax25_bitbuf_t line;
		ax25_bitbuf_init(&line, line_storage, sizeof line_storage);
		if (st == AX25_OK) {
			st = ax25_encode(frame, frame_len, &opts.tx, &line);
		}
		if (st != AX25_OK) {
			fprintf(stderr, "ax25_send: frame %zu: %s\n", i + 1u,
			    status_name(st));
			return 1;
		}
		wire_len[i] = AX25_BITS_TO_BYTES(line.length_bits);
		memcpy(wire[i], line.data, wire_len[i]);
	}

	const int fd = connect_to(opts.host, opts.port);
	if (fd < 0) {
		return 2;
	}
	size_t total = 0u;
	for (size_t i = 0u; i < opts.n_infos; i++) {
		if (send_all(fd, wire[i], wire_len[i]) != 0) {
			fprintf(stderr, "ax25_send: send failed\n");
			close(fd);
			return 2;
		}
		total += wire_len[i];
	}
	close(fd);
	fprintf(stderr, "ax25_send: sent %zu frame(s), %zu bytes\n",
	    opts.n_infos, total);
	return 0;
}
