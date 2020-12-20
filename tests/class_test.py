import sys
import random
import json
from vector_clock import VectorClock, VectorClockDecoder, VectorClockEncoder
from history import History, HistoryDecoder, HistoryEncoder

GREATER = " is greater than "
LESS = " is less than "


def main():
    hist = History()
    addrs = [1, 2, 3]

    # make 10 random keys and default clocks -- put in our history
    keys = []
    i = 0
    temp_clock = VectorClock(i % 3 + 1, addrs)
    for i in range(10):
        key = random.randint(0, 100)
        keys.append(key)

        # change clock
        temp_clock = VectorClock(i % 3 + 1, addrs, temp_clock)
        temp_clock.clock[random.randint(1, 3)] += 1
        temp_clock.clock[random.randint(1, 3)] += 1

        clock = VectorClock(i % 3 + 1, addrs, temp_clock)
        was_inserted = hist.insert(key, clock)
        print(was_inserted, file=sys.stderr)
        print("key: ", key, "\tclock: ", clock)

    for key, clock in hist.items():
        print("key: ", key, "\tclock: ", clock)

    for key, clock in hist.items():
        print("key: ", key, "\tclock: ", hist[key])

    print("\n\n Compare clocks.....\n\n")
    for key, clock in hist.items():
        for key2, clock2 in hist.items():
            if VectorClock.compare(clock, clock2) == VectorClock.GREATER_THAN:
                print(clock, GREATER, clock2,
                      "\t(", clock.addr, " ", clock2.addr, ")")
            else:
                print(clock, LESS, clock2,
                      "\t\t(", clock.addr, " ", clock2.addr, ")")

    print("\n\nTest clock merge....\n\n")
    for key, clock in hist.items():
        for key2, clock2 in hist.items():
            print(clock, " merged with ", clock2, " is", end=" ")
            clock.merge(clock2)
            print(clock)

    # ______________________________________________________________________
    print("\n\nTesting history merge...\n\n")
    hist = History()
    addrs = [1, 2, 3]

    # make 10 random keys and default clocks -- put in our history
    keys = []
    i = 0
    temp_clock = VectorClock(i % 3 + 1, addrs)
    for i in range(10):
        key = random.randint(0, 10)
        keys.append(key)

        # change clock
        temp_clock = VectorClock(i % 3 + 1, addrs, temp_clock)
        temp_clock.clock[random.randint(1, 3)] += 1
        temp_clock.clock[random.randint(1, 3)] += 1

        clock = VectorClock(i % 3 + 1, addrs, temp_clock)
        hist.insert(key, clock)

    # make smaller histories from big one --> then merge them
    hist1 = History()
    hist2 = History()

    i = 0
    for key, clock in hist.items():
        if i % 3 == 0:
            hist1.insert(key, clock)
        elif i % 2 == 0:
            hist2.insert(key, clock)
        else:
            hist1.insert(key, clock)
            hist2.insert(key, clock)
            hist2[key].clock[random.randint(1, 3)] += 1
        i += 1

    print(hist1, "\nmerged with\n", hist2, "\nis\n")
    hist1.merge(hist2)
    print(hist1)

    # another test to make sure vector clocks have the right
    # replica addrs associated with them after a merge ___________________________

    # ____________________________________________________________________________

    # test class json encoding / decoding
    print("history before json:\n", hist)
    json_obj = json.dumps(hist, cls=HistoryEncoder)
    new_hist = json.loads(json_obj, cls=HistoryDecoder)
    print("history after json:\n", new_hist)

    # test vector clock encoding / decoding
    for key, clock in hist.items():
        print("clock before json:\n", clock)
        json_obj = json.dumps(clock, cls=VectorClockEncoder)
        new_clock = json.loads(json_obj, cls=VectorClockDecoder)
        print("history after json:\n", new_clock)

    # test vector clock encoding / decoding
    for key, clock in hist.items():
        print("clock before json:\n", clock)
        json_obj = json.dumps(VectorClockEncoder().encode(clock))
        new_clock = json.loads(json_obj)
        new_clock = json.loads(new_clock, cls=VectorClockDecoder)
        print("history after json:\n", new_clock)

    for key in hist:
        print(key)


main()
