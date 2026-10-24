int oversized(int seed) {
    int acc = seed;
    if (acc > 0) {
        acc += 1;
        acc *= 2;
    }
    acc += 1;
    acc += 2;
    acc += 3;
    acc += 4;
    acc += 5;
    acc += 6;
    acc += 7;
    acc += 8;
    acc += 9;
    acc += 10;
    acc += 11;
    acc += 12;
    acc += 13;
    acc += 14;
    acc += 15;
    acc += 16;
    acc += 17;
    acc += 18;
    acc += 19;
    acc += 20;
    acc += 21;
    acc += 22;
    return acc;
}
