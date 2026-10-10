/* One-client TCP <-> serial bridge, for the one-time join only.
 *
 *   tcpser /dev/ttymxc1 57600 6638
 *
 * Listens on the Relay's own 127.0.0.1, never the network: reach it from your
 * computer with `adb forward tcp:16638 tcp:6638`. Raw bytes, no flow control
 * (the radio's XON/XOFF bytes pass through to bellows, which drops them). */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <fcntl.h>
#include <unistd.h>
#include <termios.h>
#include <signal.h>
#include <sys/select.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <arpa/inet.h>
int main(int argc, char **argv) {
    if (argc < 4) return 2;
    signal(SIGHUP, SIG_IGN); signal(SIGPIPE, SIG_IGN);
    int tty = open(argv[1], O_RDWR | O_NOCTTY);
    if (tty < 0) { perror("tty"); return 1; }
    struct termios t; tcgetattr(tty, &t); cfmakeraw(&t);
    speed_t sp = atoi(argv[2]) == 115200 ? B115200 : B57600;
    cfsetispeed(&t, sp); cfsetospeed(&t, sp);
    t.c_cflag |= CLOCAL | CREAD; t.c_cflag &= ~(CRTSCTS | CSTOPB);
    t.c_iflag &= ~(IXON | IXOFF | IXANY); t.c_cc[VMIN] = 0; t.c_cc[VTIME] = 0;
    tcsetattr(tty, TCSANOW, &t);
    int ls = socket(AF_INET, SOCK_STREAM, 0), one = 1;
    setsockopt(ls, SOL_SOCKET, SO_REUSEADDR, &one, sizeof one);
    struct sockaddr_in a; memset(&a, 0, sizeof a);
    a.sin_family = AF_INET; a.sin_port = htons(atoi(argv[3])); a.sin_addr.s_addr = inet_addr("127.0.0.1");
    if (bind(ls, (struct sockaddr *)&a, sizeof a) || listen(ls, 1)) { perror("listen"); return 1; }
    for (;;) {
        int c = accept(ls, 0, 0);
        if (c < 0) continue;
        setsockopt(c, IPPROTO_TCP, TCP_NODELAY, &one, sizeof one);
        tcflush(tty, TCIOFLUSH);
        char buf[512];
        for (;;) {
            fd_set s; FD_ZERO(&s); FD_SET(tty, &s); FD_SET(c, &s);
            if (select((tty > c ? tty : c) + 1, &s, 0, 0, 0) < 0) break;
            if (FD_ISSET(tty, &s)) { int n = read(tty, buf, sizeof buf); if (n > 0 && write(c, buf, n) != n) break; }
            if (FD_ISSET(c, &s)) { int n = read(c, buf, sizeof buf); if (n <= 0) break; write(tty, buf, n); }
        }
        close(c);
    }
}
