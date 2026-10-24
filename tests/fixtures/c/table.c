struct commandEntry {
    char *name;
    int arity;
    void (*proc)(void);
};

struct commandEntry commandTable[] = {
    {"get", 2, getCommand},
    {"set", 3, setCommand},
    {"del", 2, delCommand},
    {"exists", 2, existsCommand},
    {"expire", 3, expireCommand},
    {"ttl", 2, ttlCommand},
    {"incr", 2, incrCommand},
    {"decr", 2, decrCommand},
    {"lpush", 3, lpushCommand},
    {"rpush", 3, rpushCommand},
    {"hset", 4, hsetCommand},
    {"hget", 3, hgetCommand},
};

int main(void) {
    return 0;
}
