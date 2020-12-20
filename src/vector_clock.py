"""
    VectorClock class
"""
import sys
import copy
import json


class VectorClockEncoder(json.JSONEncoder):
    def default(self, clock):
        if clock is None:
            return json.dumps(None)
        if isinstance(clock, VectorClock):
            dct = {}
            for addr, time in clock.items():
                dct[addr] = time
            dct["addr"] = clock.addr
            return dct
        return json.JSONEncoder.default(self, clock)


class VectorClockDecoder(json.JSONDecoder):
    def __init__(self, *args, **kwargs):
        json.JSONDecoder.__init__(
            self, object_hook=self.object_hook, *args, **kwargs)

    def object_hook(self, dct):
        if dct is None:
            return None
        clock = VectorClock(None, None, None)
        for addr, time in dct.items():
            if addr == "addr":
                clock.addr = dct["addr"]
                continue
            clock.clock[addr] = time
        return clock


class VectorClock:
    """
    API:
        init(addr, addr_list, v_clock = none): make a new vector, initialize every value to 0 or use
                                                provided vector, set 'home' address to addr

        compare(vClock1, vClock2)
            return GREATER_THAN if v1 > v2
            return LESS_THAN if v1 < v2
            return EQUAL if v1 == v2
            else they are CONCURRENT, so break tie by making
                    smaller addr the winner

        merge(self, v2)
            merge vclock v2 into self.clock

        increment(self, address)
                time(address) += 1

        update(self, VectorClock2)
            self.clock = vectorclock2
            # this sets clock without changing self.addr

        You can iterate through a vector clock with "for addr, time in v_clock.items()"

        You can access individual times with v_clock[addr]
        """

    # constants
    GREATER_THAN = 1
    LESS_THAN = -1
    CONCURRENT = 0
    EQUAL = 2

    def __init__(self, this_addr, addr_list, v_clock=None):
        self.clock = {}
        self.addr = None
        if this_addr == None and addr_list == None:
            return
        if v_clock is None:
            self.clock = dict.fromkeys(addr_list, 0)
        else:
            self.clock = copy.deepcopy(v_clock.clock)
        self.addr = this_addr

    def __str__(self):
        """
            what to do it print(VectorClock) is called
        """
        ret = "{"
        for addr, time in self.clock.items():
            ret += str(time) + ", "
        ret += "}"
        return ret

    # Iterator functions ________________________________
    def __getitem__(self, addr):
        return self.clock[addr]

    def items(self):
        return self.clock.items()

    def __iter__(self):
        return iter(self.clock.keys())
    # ___________________________________________________

    def update(self, v_clock):
        """
            set self.clock to equal v_clock
        """
        for addr in self.clock:
            self.clock[addr] = v_clock[addr]

    @staticmethod
    def compare(clock_1, clock_2):
        """
        Input:  2 vector clocks
        Return:
                return GREATER_THAN if v1 > v2
                return LESS_THAN if v1 < v2
                else they are CONCURRENT, so break tie by making
                    smaller addr the winner
        """

        if clock_1 is None:
            return VectorClock.LESS_THAN
        if clock_2 is None:
            return VectorClock.GREATER_THAN

        # see if all values in one clock are either >= or < than other clock
        bigger, smaller = False, False
        try:
            for addr, time in clock_1.clock.items():
                if time > clock_2.clock[addr]:
                    bigger = True
                elif time < clock_2.clock[addr]:
                    smaller = True
        except:
            print("\n\nWooooaaah, hole up -- somethin real fucked up occurred\n\n", file=sys.stderr)
            for i, j in clock_1.items():
                print("clock_1[{}]: {}:{}".format(clock_1.addr,i, j), file=sys.stderr)
            for i, j in clock_2.items():
                print("clock_2[{}]: {}:{}".format(clock_2.addr,i, j), file=sys.stderr)

        if (bigger and smaller):
            # they are concurrent, so break tie with vector clock addresses
            if clock_1.addr <= clock_2.addr:
                return VectorClock.GREATER_THAN
            return VectorClock.LESS_THAN
        if not (bigger or smaller):
            return VectorClock.EQUAL
        if bigger:
            return VectorClock.GREATER_THAN
        return VectorClock.LESS_THAN

    def merge(self, clock_2):
        """
        Input:  a Vclock
        result: the local clock is updated to max
                of itself and v2
        """
        if clock_2 is None:
            return
        for addr, time in self.clock.items():
            try:
                self.clock[addr] = max(time, clock_2.clock[addr])
            except:
                print("\nAttempted to compare incompatible vector clocks\n", file=sys.stderr)
                return

    def increment(self):
        """
        input:  the address of a replica
        result: add 1 to time(address)
        """
        self.clock[self.addr] += 1
