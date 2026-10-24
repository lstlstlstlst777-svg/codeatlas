/* Command helpers used by the server. */
#include <stdio.h>

#define MAX_CLIENTS 1024
#define ZMALLOC_USED_MEMORY() zmalloc_used_memory()

typedef struct clientState {
    int fd;
    char *name;
} clientState;

enum connType {
    CONN_TYPE_SOCKET,
    CONN_TYPE_TLS
};

/* Initialize the server event loop and databases. */
void initServer(void) {
    int fd = 0;
    /* bind and listen */
    fd = 1;
}

static int addNumbers(int a, int b) {
    return a + b;
}
