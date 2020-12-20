"""
History Class will be used to store causal histories
"""
import copy
import json
from vector_clock import VectorClock, VectorClockDecoder, VectorClockEncoder


class HistoryEncoder(json.JSONEncoder):
    def default(self, hist):
        if isinstance(hist, History):
            dct = {}
            for key, clock in hist.items():
                dct[key] = VectorClockEncoder().encode(clock)
            return dct
        return json.JSONEncoder.default(self, hist)


class HistoryDecoder(json.JSONDecoder):
    def __init__(self, *args, **kwargs):
        json.JSONDecoder.__init__(
            self, object_hook=self.object_hook, *args, **kwargs)

    def object_hook(self, dct):
        hist = History()
        for key, clock in dct.items():
            hist.insert(key, json.loads(clock, cls=VectorClockDecoder))
        return hist


class History:
    """
        A History is a collection of events, stored as a dictionary and represented as:
                {"key1":VectorClock, "key2":VectorClock,...}

    Purpose:
        This class will be used to hold the casual history of each data item:
                Track per-item history

                Furthermore, it will be used to hold a temporary between-gossip history:
                        When new events occur on a replica, they are added to two
                        locations: the total history and the between-gossip history. This
                        way, we can just send the between-gossip history during gossip and
                        use merge() to merge incoming between-gossip-histories with ours.
                        Then, delete between-gossip history (only if all replicas received
                        them though)

        API:
                insert(key,clock):		inserts new events into the history

                merge(foreign_hist):	merge foreign history into local history
                                                                return list of updated keys

        Access individual clocks with "hist[key]"

        Iterate through clocks with "for key,clock in hist.items()"

    """

    def __init__(self):
        self.hist = {}

    def __str__(self):
        """
            what to do if print(History) is called
        """
        ret = "{"
        for key, clock in self.hist.items():
            ret += str(key) + ": " + str(clock) + "\t"
        ret += "}"
        return ret

    # iterator functions _______________________
    def __getitem__(self, key):
        if key in self.hist:
            return self.hist[key]
        return None

    def items(self):
        return self.hist.items()

    def __iter__(self):
        return iter(self.hist.keys())
    # __________________________________________

    def insert(self, key, clock):
        """
        purpose:	insert a new event to the history

        Input: 		a key and a VectorClock

        return:		true if it was inserted
        false if it was rejected
        """
        if key not in self.hist or VectorClock.compare(clock, self.hist[key]) == VectorClock.GREATER_THAN:
            # not sure if deepcopy is actually necessary here - I've just been worried
            # about accidently deleting referenced objects in other parts of the program
            self.hist.update({key: copy.deepcopy(clock)})
            return True
        return False

    def merge(self, foreign_hist):
        """
        Purpose:	merges foreign hist with self.hist

        Input: 		a History object

        Return:		a list of updated keys
        """
        updated_keys = []

        # check if incoming clock is more recent
        # if yes --> replace ours
        for f_key, f_clock in foreign_hist.hist.items():
            if f_key not in self.hist:
                self.insert(f_key, f_clock)
                updated_keys.append(f_key)
                continue
            l_clock = self.hist[f_key]
            if l_clock is None or VectorClock.compare(f_clock, l_clock) == VectorClock.GREATER_THAN:
                self.hist[f_key] = copy.deepcopy(f_clock)
                updated_keys.append(f_key)

        return updated_keys
