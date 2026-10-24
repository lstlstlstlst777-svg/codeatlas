#ifdef HAVE_TLS
#define TLS_ENABLED 1

int tlsHandshake(int fd) {
    return fd + 1;
}
#else
int tlsHandshake(int fd) {
    return fd;
}
#endif

#ifndef HAVE_TLS
int plainHandshake(int fd) {
    return fd;
}
#endif
