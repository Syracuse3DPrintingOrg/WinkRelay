/* zbrouter: keep the Wink Relay's Zigbee radio on its network as a router.
 *
 *   zbrouter [-v] [/dev/ttymxc1]
 *
 * The Relay's Zigbee chip is a Silicon Labs Ember network coprocessor
 * (EmberZNet 5.1.2, EZSP protocol 4) on UART2 at 57600 baud with XON/XOFF.
 * It is joined to a network once, from a computer, with join_router.py; it
 * keeps that network in its own flash. After every reset (a reboot, a power cut)
 * it stays off the network until its host tells it to resume, and this is
 * that host. It:
 *
 *  1. resets the chip over ASH (Silicon Labs' serial framing) and checks the
 *     EZSP version;
 *  2. sets the stack profile and security level, and registers endpoint 1 as
 *     a Home Automation range extender with the Basic cluster, so the
 *     coordinator's interview learns what this device is;
 *  3. resumes the stored network (networkInit);
 *  4. stays connected: acknowledges everything the chip sends, answers Basic
 *     cluster reads (manufacturer, model, power source and so on), checks the
 *     network state every 30 seconds and resumes it if it has dropped.
 *
 * Any link failure (no answer, an ASH error frame) starts again from 1. It
 * never exits on its own; the boot hook (device/panel-boot.sh) starts it and restarts it if it dies.
 * Log: /data/local/tmp/zbrouter.log (capped at 64 KB). -v also logs frames.
 */
#include <errno.h>
#include <fcntl.h>
#include <stdarg.h>
#include <stdio.h>
#include <signal.h>
#include <stdlib.h>
#include <string.h>
#include <sys/select.h>
#include <sys/stat.h>
#include <sys/time.h>
#include <termios.h>
#include <time.h>
#include <unistd.h>

#define LOG_PATH "/data/local/tmp/zbrouter.log"
#define LOG_MAX 65536

/* What the device says about itself (Basic cluster). */
#define MANUFACTURER "Wink"
#define MODEL "Wink Relay"
#define SW_BUILD "zbrouter 1.0 (EmberZNet 5.1.2)"
#define DATE_CODE "20261010"

/* EZSP v4 frame ids. */
#define EZSP_VERSION 0x00
#define EZSP_ADD_ENDPOINT 0x02
#define EZSP_NOP 0x05
#define EZSP_NETWORK_INIT 0x17
#define EZSP_NETWORK_STATE 0x18
#define EZSP_STACK_STATUS_HANDLER 0x19
#define EZSP_GET_NODE_ID 0x27
#define EZSP_SEND_UNICAST 0x34
#define EZSP_INCOMING_MESSAGE_HANDLER 0x45
#define EZSP_SET_CONFIGURATION_VALUE 0x53

#define CONFIG_STACK_PROFILE 0x0C
#define CONFIG_SECURITY_LEVEL 0x0D

#define EMBER_SUCCESS 0x00
#define EMBER_NETWORK_UP 0x90
#define EMBER_NETWORK_DOWN 0x91
#define EMBER_JOINED_NETWORK 0x02
#define EMBER_NO_NETWORK 0x00

static int fd = -1, verbose = 0;

static void logf_(const char *fmt, ...) {
    struct stat st;
    if (stat(LOG_PATH, &st) == 0 && st.st_size > LOG_MAX) rename(LOG_PATH, LOG_PATH ".old");
    FILE *f = fopen(LOG_PATH, "a");
    if (!f) return;
    time_t now = time(0);
    char ts[32];
    strftime(ts, sizeof ts, "%Y-%m-%d %H:%M:%S", localtime(&now));
    fprintf(f, "%s ", ts);
    va_list ap;
    va_start(ap, fmt);
    vfprintf(f, fmt, ap);
    va_end(ap);
    fputc('\n', f);
    fclose(f);
}

static long long now_ms(void) {
    struct timeval tv;
    gettimeofday(&tv, 0);
    return (long long)tv.tv_sec * 1000 + tv.tv_usec / 1000;
}

/* ---- ASH ---- */

static unsigned short crc16(const unsigned char *p, int n) {
    unsigned short c = 0xFFFF;
    for (int i = 0; i < n; i++) {
        c ^= (unsigned short)p[i] << 8;
        for (int b = 0; b < 8; b++) c = (c & 0x8000) ? (c << 1) ^ 0x1021 : c << 1;
    }
    return c;
}

static void randomize(unsigned char *p, int n) {
    unsigned char r = 0x42;
    for (int i = 0; i < n; i++) {
        p[i] ^= r;
        r = (r & 1) ? ((r >> 1) ^ 0xB8) : (r >> 1);
    }
}

static int reserved(unsigned char b) {
    return b == 0x7E || b == 0x7D || b == 0x11 || b == 0x13 || b == 0x18 || b == 0x1A;
}

static void dump(const char *what, const unsigned char *p, int n) {
    if (!verbose) return;
    char s[3 * 200 + 1];
    int o = 0;
    for (int i = 0; i < n && i < 200; i++) o += sprintf(s + o, " %02X", p[i]);
    s[o] = 0;
    logf_("%s%s", what, s);
}

/* Send one frame: control byte, data (already randomized for DATA frames). */
static int send_frame(unsigned char control, const unsigned char *data, int n) {
    unsigned char raw[260], out[540];
    int len = 0, o = 0;
    if (n > 250) return -1;
    raw[len++] = control;
    if (n) memcpy(raw + len, data, n);
    len += n;
    unsigned short c = crc16(raw, len);
    raw[len++] = c >> 8;
    raw[len++] = c & 0xFF;
    for (int i = 0; i < len; i++) {
        if (reserved(raw[i])) { out[o++] = 0x7D; out[o++] = raw[i] ^ 0x20; }
        else out[o++] = raw[i];
    }
    out[o++] = 0x7E;
    dump("tx", raw, len);
    return write(fd, out, o) == o ? 0 : -1;
}

/* Receive state: raw bytes read but not yet parsed, and the frame being built. */
static unsigned char inbuf[512];
static int in_pos = 0, in_len = 0;
static unsigned char rx[300];
static int rx_n = 0, rx_esc = 0, rx_bad = 0;

/* Parse buffered bytes; returns the frame length (control byte and data, no
 * CRC) in f when a good frame ends, 0 when the buffer runs out first. */
static int parse_buffered(unsigned char *f) {
    while (in_pos < in_len) {
        unsigned char b = inbuf[in_pos++];
        if (b == 0x11 || b == 0x13) continue;                  /* XON / XOFF */
        if (b == 0x18 || b == 0x1A) { rx_n = 0; rx_esc = 0; rx_bad = 0; continue; } /* substitute, cancel */
        if (b == 0x7E) {
            int len = rx_n, bad = rx_bad;
            rx_n = 0; rx_esc = 0; rx_bad = 0;
            if (len < 3 || bad) continue;
            if (crc16(rx, len - 2) != ((rx[len - 2] << 8) | rx[len - 1])) {
                if (verbose) logf_("rx crc error");
                continue;
            }
            memcpy(f, rx, len - 2);
            dump("rx", f, len - 2);
            return len - 2;
        }
        if (b == 0x7D) { rx_esc = 1; continue; }
        if (rx_esc) { b ^= 0x20; rx_esc = 0; }
        if (rx_n < (int)sizeof rx) rx[rx_n++] = b; else rx_bad = 1;
    }
    return 0;
}

/* Read one complete, CRC-checked frame within ms. Returns its length in f,
 * 0 on timeout, -1 on a read error. */
static int read_frame(unsigned char *f, int ms) {
    long long until = now_ms() + ms;
    for (;;) {
        int n = parse_buffered(f);
        if (n > 0) return n;
        long long left = until - now_ms();
        if (left < 0) return 0;
        fd_set s;
        FD_ZERO(&s);
        FD_SET(fd, &s);
        struct timeval tv = { left / 1000, (left % 1000) * 1000 };
        int r = select(fd + 1, &s, 0, 0, &tv);
        if (r < 0 && errno == EINTR) continue;
        if (r < 0) return -1;
        if (r == 0) return 0;
        n = read(fd, inbuf, sizeof inbuf);
        if (n <= 0) return -1;
        in_pos = 0;
        in_len = n;
    }
}

/* Sequence state. */
static int our_frm = 0;      /* next frmNum we send */
static int ncp_next = 0;     /* next frmNum we expect from the NCP (our ackNum) */
static int ezsp_seq = 0;

static int ash_reset(void) {
    unsigned char f[300];
    for (int attempt = 0; attempt < 5; attempt++) {
        tcflush(fd, TCIOFLUSH);
        in_pos = in_len = 0;
        unsigned char cancel = 0x1A;
        write(fd, &cancel, 1);
        send_frame(0xC0, 0, 0);
        long long until = now_ms() + 5000;
        while (now_ms() < until) {
            int n = read_frame(f, 1000);
            if (n < 0) return -1;
            if (n >= 3 && f[0] == 0xC1) {
                logf_("radio reset (ash version %d, reset code 0x%02X)", f[1], f[2]);
                our_frm = 0;
                ncp_next = 0;
                ezsp_seq = 0;
                return 0;
            }
        }
    }
    return -1;
}

/* ---- incoming messages, answered after the current command ---- */

struct pending { unsigned short sender; unsigned char aps[11]; unsigned char msg[128]; int len; };
static struct pending queue[8];
static int queued = 0;

static void on_callback(unsigned char id, const unsigned char *p, int n) {
    if (id == EZSP_STACK_STATUS_HANDLER && n >= 1) {
        logf_("stack status 0x%02X%s", p[0],
              p[0] == EMBER_NETWORK_UP ? " (network up)" : p[0] == EMBER_NETWORK_DOWN ? " (network down)" : "");
    } else if (id == EZSP_INCOMING_MESSAGE_HANDLER && n >= 19) {
        /* type, apsFrame(11), lqi, rssi, sender(2), bindingIndex, addressIndex, len, msg */
        const unsigned char *aps = p + 1;
        unsigned short profile = aps[0] | aps[1] << 8, cluster = aps[2] | aps[3] << 8;
        unsigned char dst_ep = aps[5];
        int mlen = p[18];
        if (19 + mlen > n) return;
        if (profile == 0x0104 && cluster == 0x0000 && dst_ep == 1 && queued < 8 && mlen <= 128) {
            struct pending *q = &queue[queued++];
            q->sender = p[14] | p[15] << 8;
            memcpy(q->aps, aps, 11);
            memcpy(q->msg, p + 19, mlen);
            q->len = mlen;
        } else if (verbose) {
            logf_("message profile 0x%04X cluster 0x%04X ep %d ignored", profile, cluster, dst_ep);
        }
    }
}

/* Handle one received frame: ACK data frames, pass EZSP frames to the caller.
 * Returns 1 and fills ezsp/elen for a new DATA frame, 0 otherwise, -1 on an
 * ASH error frame. *acked gets the ackNum of any frame carrying one. */
static int handle_frame(unsigned char *f, int n, unsigned char *ezsp, int *elen, int *acked) {
    unsigned char c = f[0];
    if ((c & 0x80) == 0) {                     /* DATA */
        int frm = (c >> 4) & 7;
        *acked = c & 7;
        if (frm != ncp_next) {                 /* duplicate or out of order: re-ACK */
            send_frame(0x80 | ncp_next, 0, 0);
            return 0;
        }
        ncp_next = (ncp_next + 1) & 7;
        send_frame(0x80 | ncp_next, 0, 0);
        *elen = n - 1;
        memcpy(ezsp, f + 1, n - 1);
        randomize(ezsp, *elen);
        return 1;
    }
    if ((c & 0xE0) == 0x80 || (c & 0xE0) == 0xA0) { /* ACK or NAK */
        *acked = c & 7;
        return 0;
    }
    if (c == 0xC2) {
        logf_("ash error frame (code 0x%02X)", n > 2 ? f[2] : 0);
        return -1;
    }
    if (c == 0xC1) {
        logf_("radio reset unexpectedly");
        return -1;
    }
    return 0;
}

/* Send an EZSP command and wait for its response. Callbacks that arrive
 * meanwhile go to on_callback. Returns the response parameter length in out,
 * or -1 on failure (the link should be reset). */
static int ezsp_command(unsigned char id, const unsigned char *params, int plen, unsigned char *out, int max) {
    unsigned char frame[256], data[256], f[300], ezsp[300];
    int seq = ezsp_seq;
    ezsp_seq = (ezsp_seq + 1) & 0xFF;
    frame[0] = seq;
    frame[1] = 0x00;
    frame[2] = id;
    if (plen) memcpy(frame + 3, params, plen);
    int dlen = 3 + plen;
    int frm = our_frm;
    our_frm = (our_frm + 1) & 7;
    int got_ack = 0;

    for (int tx = 0; tx < 5; tx++) {
        memcpy(data, frame, dlen);
        randomize(data, dlen);
        send_frame((frm << 4) | (tx ? 0x08 : 0) | ncp_next, data, dlen);
        long long until = now_ms() + 2000;
        while (now_ms() < until) {
            int n = read_frame(f, (int)(until - now_ms()));
            if (n < 0) return -1;
            if (n == 0) break;
            int elen = 0, acked = -1;
            int r = handle_frame(f, n, ezsp, &elen, &acked);
            if (r < 0) return -1;
            if (acked == ((frm + 1) & 7)) got_ack = 1;
            if (r == 1 && elen >= 3) {
                unsigned char fc = ezsp[1], fid = ezsp[2];
                int is_callback = (fc & 0x18) != 0;
                if (!is_callback && ezsp[0] == seq && fid == id) {
                    int pl = elen - 3;
                    if (pl > max) pl = max;
                    memcpy(out, ezsp + 3, pl);
                    return pl;
                }
                on_callback(fid, ezsp + 3, elen - 3);
            }
        }
        if (got_ack) {
            /* The NCP has the command; give the response a little longer. */
            until = now_ms() + 3000;
            while (now_ms() < until) {
                int n = read_frame(f, (int)(until - now_ms()));
                if (n <= 0) break;
                int elen = 0, acked = -1;
                int r = handle_frame(f, n, ezsp, &elen, &acked);
                if (r < 0) return -1;
                if (r == 1 && elen >= 3) {
                    unsigned char fc = ezsp[1], fid = ezsp[2];
                    if ((fc & 0x18) == 0 && ezsp[0] == seq && fid == id) {
                        int pl = elen - 3;
                        if (pl > max) pl = max;
                        memcpy(out, ezsp + 3, pl);
                        return pl;
                    }
                    on_callback(fid, ezsp + 3, elen - 3);
                }
            }
            break;
        }
    }
    logf_("no answer to command 0x%02X", id);
    return -1;
}

/* Read frames for up to ms, handling callbacks (nothing outstanding). */
static int idle(int ms) {
    unsigned char f[300], ezsp[300];
    long long until = now_ms() + ms;
    while (now_ms() < until) {
        int n = read_frame(f, (int)(until - now_ms()));
        if (n < 0) return -1;
        if (n == 0) return 0;
        int elen = 0, acked = -1;
        int r = handle_frame(f, n, ezsp, &elen, &acked);
        if (r < 0) return -1;
        if (r == 1 && elen >= 3) on_callback(ezsp[2], ezsp + 3, elen - 3);
        if (queued) return 0;
    }
    return 0;
}

/* ---- the Basic cluster ---- */

static int put_string(unsigned char *p, const char *s) {
    int n = strlen(s);
    p[0] = 0x42;          /* character string */
    p[1] = n;
    memcpy(p + 2, s, n);
    return 2 + n;
}

/* One read attribute record for attribute a; returns its length. */
static int basic_attribute(unsigned char *p, unsigned short a) {
    p[0] = a & 0xFF;
    p[1] = a >> 8;
    p[2] = 0x00;          /* success */
    switch (a) {
    case 0x0000: p[3] = 0x20; p[4] = 0x03; return 5;          /* ZCL version */
    case 0x0001: p[3] = 0x20; p[4] = 0x01; return 5;          /* application version */
    case 0x0002: p[3] = 0x20; p[4] = 0x51; return 5;          /* stack version */
    case 0x0003: p[3] = 0x20; p[4] = 0x01; return 5;          /* hardware version */
    case 0x0004: return 3 + put_string(p + 3, MANUFACTURER);
    case 0x0005: return 3 + put_string(p + 3, MODEL);
    case 0x0006: return 3 + put_string(p + 3, DATE_CODE);
    case 0x0007: p[3] = 0x30; p[4] = 0x01; return 5;          /* mains, single phase */
    case 0x4000: return 3 + put_string(p + 3, SW_BUILD);
    }
    p[2] = 0x86;          /* unsupported attribute */
    return 3;
}

static int answer(struct pending *q) {
    const unsigned char *m = q->msg;
    int n = q->len;
    if (n < 3) return 0;
    unsigned char fc = m[0];
    int hdr = (fc & 0x04) ? 5 : 3;            /* manufacturer specific adds a code */
    if ((fc & 0x03) != 0 || (fc & 0x04) || n < hdr) return 0;  /* only plain profile-wide commands */
    unsigned char zseq = m[hdr - 2], cmd = m[hdr - 1];
    if (cmd != 0x00) return 0;                /* read attributes only */

    unsigned char zcl[120];
    int o = 0;
    zcl[o++] = 0x18;                          /* profile-wide, server to client, no default response */
    zcl[o++] = zseq;
    zcl[o++] = 0x01;                          /* read attributes response */
    for (int i = hdr; i + 1 < n && o < 80; i += 2)
        o += basic_attribute(zcl + o, m[i] | m[i + 1] << 8);

    unsigned char p[160];
    int k = 0;
    p[k++] = 0x00;                            /* direct unicast */
    p[k++] = q->sender & 0xFF;
    p[k++] = q->sender >> 8;
    p[k++] = 0x04; p[k++] = 0x01;             /* profile 0x0104 */
    p[k++] = 0x00; p[k++] = 0x00;             /* cluster Basic */
    p[k++] = 0x01;                            /* source endpoint 1 */
    p[k++] = q->aps[4];                       /* back to the sender's endpoint */
    p[k++] = 0x40; p[k++] = 0x01;             /* retry, route discovery */
    p[k++] = 0x00; p[k++] = 0x00;             /* group */
    p[k++] = 0x00;                            /* aps sequence, set by the stack */
    p[k++] = 0x01;                            /* message tag */
    p[k++] = o;
    memcpy(p + k, zcl, o);
    k += o;
    unsigned char r[8];
    int rn = ezsp_command(EZSP_SEND_UNICAST, p, k, r, sizeof r);
    if (rn < 0) return -1;
    logf_("answered a Basic read from 0x%04X (%d attributes, status 0x%02X)", q->sender, (n - hdr) / 2, rn ? r[0] : 0xFF);
    return 0;
}

static int flush_queue(void) {
    while (queued) {
        struct pending q = queue[0];
        memmove(queue, queue + 1, (queued - 1) * sizeof queue[0]);
        queued--;
        if (answer(&q) < 0) return -1;
    }
    return 0;
}

/* ---- the session ---- */

static int set_config(unsigned char id, unsigned short value) {
    unsigned char p[3] = { id, value & 0xFF, value >> 8 }, r[4];
    int n = ezsp_command(EZSP_SET_CONFIGURATION_VALUE, p, 3, r, sizeof r);
    if (n < 1) return -1;
    if (r[0] != 0) logf_("config 0x%02X = %d: status 0x%02X", id, value, r[0]);
    return 0;
}

static int network_state(void) {
    unsigned char r[4];
    int n = ezsp_command(EZSP_NETWORK_STATE, 0, 0, r, sizeof r);
    return n < 1 ? -1 : r[0];
}

static int session(void) {
    unsigned char r[16];
    if (ash_reset() < 0) { logf_("radio does not answer a reset"); return -1; }

    unsigned char v = 4;
    int n = ezsp_command(EZSP_VERSION, &v, 1, r, sizeof r);
    if (n < 4) return -1;
    logf_("EZSP protocol %d, stack type %d, stack version 0x%02X%02X", r[0], r[1], r[3], r[2]);
    if (r[0] != 4) { logf_("unexpected EZSP protocol version"); return -1; }

    if (set_config(CONFIG_STACK_PROFILE, 2) < 0 || set_config(CONFIG_SECURITY_LEVEL, 5) < 0) return -1;

    /* Endpoint 1: Home Automation, Range Extender (0x0008), Basic in. */
    unsigned char ep[] = { 0x01, 0x04, 0x01, 0x08, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00 };
    n = ezsp_command(EZSP_ADD_ENDPOINT, ep, sizeof ep, r, sizeof r);
    if (n < 1) return -1;
    if (r[0] != 0) logf_("addEndpoint status 0x%02X", r[0]);

    n = ezsp_command(EZSP_NETWORK_INIT, 0, 0, r, sizeof r);
    if (n < 1) return -1;
    if (r[0] != EMBER_SUCCESS) {
        logf_("networkInit status 0x%02X: the radio has no stored network to resume", r[0]);
        return -2;
    }

    int down = 0;
    long long next_check = now_ms() + 5000;
    for (;;) {
        if (idle(1000) < 0) return -1;
        if (flush_queue() < 0) return -1;
        if (now_ms() < next_check) continue;
        next_check = now_ms() + 30000;
        int st = network_state();
        if (st < 0) return -1;
        if (st == EMBER_JOINED_NETWORK) {
            if (down) logf_("back on the network");
            if (down || verbose) {
                n = ezsp_command(EZSP_GET_NODE_ID, 0, 0, r, sizeof r);
                if (n >= 2) logf_("on the network as node 0x%04X", r[0] | r[1] << 8);
            }
            down = 0;
            continue;
        }
        down++;
        logf_("network state %d (check %d)", st, down);
        if (st == EMBER_NO_NETWORK && down >= 2) {
            n = ezsp_command(EZSP_NETWORK_INIT, 0, 0, r, sizeof r);
            if (n < 1) return -1;
            logf_("networkInit again: status 0x%02X", r[0]);
        }
    }
}

int main(int argc, char **argv) {
    const char *dev = "/dev/ttymxc1";
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "-v")) verbose = 1;
        else dev = argv[i];
    }
    /* Started from an adb shell or a boot script: outlive both. */
    signal(SIGHUP, SIG_IGN);
    signal(SIGPIPE, SIG_IGN);
    logf_("zbrouter starting on %s", dev);
    int backoff = 5;
    for (;;) {
        fd = open(dev, O_RDWR | O_NOCTTY);
        if (fd < 0) { logf_("cannot open %s: %s", dev, strerror(errno)); sleep(30); continue; }
        struct termios t;
        tcgetattr(fd, &t);
        cfmakeraw(&t);
        cfsetispeed(&t, B57600);
        cfsetospeed(&t, B57600);
        t.c_cflag |= CLOCAL | CREAD;
        t.c_cflag &= ~(CRTSCTS | CSTOPB);
        t.c_iflag &= ~(IXON | IXOFF | IXANY);
        t.c_cc[VMIN] = 0;
        t.c_cc[VTIME] = 0;
        tcsetattr(fd, TCSANOW, &t);
        rx_n = 0; rx_esc = 0; rx_bad = 0; queued = 0; in_pos = in_len = 0;
        int r = session();
        close(fd);
        fd = -1;
        /* No stored network: nothing to do but wait; check rarely. */
        int wait = r == -2 ? 600 : backoff;
        logf_("link lost, starting again in %d s", wait);
        sleep(wait);
        if (r != -2 && backoff < 120) backoff *= 2;
        if (r == -2) backoff = 5;
    }
}
