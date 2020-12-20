import sys
import hashlib
import json
from os import environ
import threading
from flask_restful import reqparse
from flask import request
import requests
import grequests
import schedule
import time
import math
from vector_clock import VectorClock, VectorClockEncoder, VectorClockDecoder
from history import History, HistoryDecoder, HistoryEncoder
from urllib.parse import urlparse, parse_qs

DEBUG = False


# lets use a consistent timeout for everything
TIMEOUT_LENGTH = 0.5


def timeout_handler(req, exception):
    if DEBUG: 
        print("Request failed: {}".format(exception.__class__.__name__), file=sys.stderr)
    return None


class Node:
    def __init__(self, new_view, repl_factor):

        # view change & node IDs ##############################################
        self.fragments = []         # view change fragments (usually empty)

        # create and then set our view/shard vars
        self.view = []                  # current view
        self.__old_view = []            # needed during view change
        self.repl_factor = None         # current view repl_factor
        self.__old_repl_factor = None   # old view repl_factor
        self.shards = []                # functionally equivalent to our view from asgn2
        # 2d array where shards[i][j] = addr of shard i, replica j
        self.__old_shards = []          # needed for view change
        self.this_shard = None          # ID of this replica"s shard
        self.__old_this_shard = None    # needed for view change
        # view ID -- used to work around stale client-context assoc w/ old view
        self.current_view = 0
        self.cur_time = None

        self.set_shards_and_view(new_view, int(repl_factor))

        ######################################################################

        # kvs / causality stuff ##############################################
        self.local_kvs = {}         # the kvs dict of this node

        self.per_item_history = {}  # {key : History} -- History contains all
        # dependencies of "key", including

        # the clock associated w/ each of our keys
        self.local_key_versions = History()
        # I decided to store this separately
        # from the history because it makes
        # resharding simpler

        # keys/clocks that have been changed
        self.between_gossip_updates = History()
        # since the last fully successful
        # gossip (when all replicas
        # received my updates)

        ######################################################################

        # gossip stuff #######################################################
        self.replica_alive = {}
        if self.this_shard is not None:
            self.replica_alive = {replica:True for replica in self.shards[self.this_shard]}
        self.gossip_thread = threading.Thread(
            target=self.timed_gossip)
        #self.liveness_thread = threading.Thread(
        #    target=self.timed_liveness_check)
        self.gossip_thread.start()
        #self.liveness_thread.start()

        self.wait_time = 2
        ######################################################################

    def get_shard_id(self, addr):
        return self.view.index(addr) // self.repl_factor

    def hash(self, key):
        """
        Params:
            Key
        Returns:
            ID of server that the key should be stored on (need ID instead
            of address to facilitate placing keys in buckets during reshard)
        """
        # encode(utf8) -> unicode objects must be encoded before hashing
        # hexdigest() -> hex string (no binary data)
        hash_val = int(hashlib.md5(key.encode("utf-8")).hexdigest(), 16)
        return hash_val % len(self.shards)

    def set_shards_and_view(self, new_view, repl_factor, current_view=0):
        """
        Params:
            - A string corresponding to the new_view
            - An int corresponding to replication factor
        Result:
            Updates all current view information for the node
        Returns:
            Nothing
        """
        # save previous view variables
        self.old_view = self.view
        self.__old_shards = self.shards
        self.__old_repl_factor = self.repl_factor
        self.__old_this_shard = self.this_shard

        # Converts passed-in VIEW string to an array then sorts
        self.view = new_view.split(",")
        self.view.sort()
        self.repl_factor = repl_factor

        self.current_view = int(current_view)

        # update shards to reflect the view change
        self.shards = [[None for x in range(repl_factor)]
                       for y in range(len(self.view)//repl_factor)]

        # populate shards
        for addr in self.view:
            shard_id = self.view.index(addr) // self.repl_factor
            repl_id = self.view.index(addr) % self.repl_factor
            self.shards[shard_id][repl_id] = addr

        # save my shard_id
        if environ["ADDRESS"] not in self.view:
            self.this_shard = None
            return

        self.this_shard = self.view.index(environ["ADDRESS"]) // repl_factor
        # reset vector clocks at the beginning of view changes
        self.cur_time = VectorClock(
            environ["ADDRESS"], self.shards[self.this_shard])

    def reset_histories(self):
        # resests histories between view changes
        self.per_item_history = {}
        self.local_key_versions = History()
        self.between_gossip_updates = History()

    def try_reshard(self, new_view, repl_factor):
        """
            - if I"m the shard leader, call initiate_reshard,
              otherwise I"m a proxy for my shard leader
        """
        # I'm not the leader -- be proxy
        # Case 1: request sent to a completely new node
        if environ["ADDRESS"] not in self.view:
            response = requests.put("http://{}/kvs/view-change".format(
                self.shards[0][0]),
                json={"view": new_view, "repl-factor": repl_factor},
                headers=dict(request.headers), timeout=TIMEOUT_LENGTH)
            return response.json(), 200
        # Case 2: request sent to a current node, but not the shard-leader
        if self.shards[self.this_shard][0] != environ["ADDRESS"]:
            response = requests.put("http://{}/kvs/view-change".format(
                self.shards[self.this_shard][0]),
                json={"view": new_view, "repl-factor": repl_factor},
                headers=dict(request.headers), timeout=TIMEOUT_LENGTH)
            return response.json(), 200

        return self.initiate_reshard(new_view, repl_factor)

    def initiate_reshard(self, new_view, repl_factor):
        """
        Input: a comma-separated string of node addresses and a replication factor (int)

        Note: Important --> this is the ACTUAL reshard orchestration function
                - We need a separate function to be used as an endpoint for
                  incoming view-change requests that checks if its the
                  shard leader, and if not, act as a proxy for the real leader,
                  forward to this function"s endpoint
        """

        self.set_shards_and_view(new_view, repl_factor)

        # Generates lists of nodes (addresses)
        all_nodes = list(set().union([replica for replica in self.view if replica != environ["ADDRESS"]],
                                     [replica for replica in self.__old_view if replica != environ["ADDRESS"]]))

        new_leaders = [self.shards[i][0] for i in range(len(self.shards)) 
                        if self.shards[i][0] != environ["ADDRESS"]]

        old_leaders = [self.__old_shards[i][0] for i in range(len(self.__old_shards)) 
                        if self.__old_shards[i][0] != environ["ADDRESS"]]

        
        # Step 0: Tell shard leaders to collect all their keys -------------------------------------
        # please note this is completely different from "prime" in asgn3
        # return the value of current_view
        rs = [grequests.put("http://{}/kvs/reshard/prime".format(leader))
              for leader in old_leaders]
        responses = grequests.map(rs)

        # I get all my keys too
        self.leader_prime()

        # find the max of (current_view IDs) -- because reshard leader could be brand new node
        all_view_IDs = []
        for response in responses:
            all_view_IDs.append(response.json()["current_view"])
        all_view_IDs.append(self.current_view)
        self.current_view = max(all_view_IDs) + 1
        # ------------------------------------------------------------------------------------------

        # Step 0.5: Give new view to all nodes in the system ---------------------------------------
        rs = [grequests.put("http://{}/kvs/reshard/set_new_view".format(node), 
                            json={"view": ",".join(self.view),
                           "repl_factor": self.repl_factor,
                           "current_view": self.current_view}) for node in all_nodes]
        responses = grequests.map(rs)
        # ------------------------------------------------------------------------------------------

        # Step 1: Tell old shard leaders to rehash keys  -------------------------------------------
        # contact all old shard leaders at endpoint: "/kvs/reshard/rehash"
        # Rehash: Nodes rehash their keys into "fragments" (dictionaries for
        #        every other shard leader)
        rs = [grequests.put("http://{}/kvs/reshard/rehash".format(follower),
                                json={"view": ",".join(self.view),
                             "repl_factor": self.repl_factor}) for follower in old_leaders]
        responses = grequests.map(rs)
        # Check 200 response for all responses
        # CODE HERE

        # Rehashes myself and assembles fragments for other shards
        self.rehash()
        self.reset_histories()
        # ------------------------------------------------------------------------------------------

        # Step 2: Send our fragments to all shard leaders in new_view  -----------------------------
        # shard_ID ==> self.view.index(shard_leader) // self.repl_factor
        rs = [grequests.put("http://{}/kvs/reshard/put_payload".format(shard_leader),
                            json={"payload": self.fragments[self.get_shard_id(shard_leader)]})
                            for shard_leader in new_leaders]
        responses = grequests.map(rs)

        # Make sure all fragments were received
        """
        for response in responses:
            if response.status_code != 200:
                # need to handle error somehow - this is just placeholder code rn
                return 500
        """
        # delete our fragments (done with them)
        self.fragments.clear()
        # ------------------------------------------------------------------------------------------

        # Step 3: Tell old shard leaders to send their keys to new shard leaders -------------------
        rs = [grequests.get("http://{}/kvs/reshard/reshard".format(follower))
              for follower in old_leaders]
        # WIP: We may want to consider throttling
        responses = grequests.map(rs, exception_handler=timeout_handler)
        # Check 200 response from all nodes
        # CODE HERE
        # ------------------------------------------------------------------------------------------

        # Step 4: Tell new shard leaders to send their keys to the other replicas in their shard ---
        rs = [grequests.get("http://{}/kvs/reshard/send_keys_to_replicas".format(leader))
              for leader in new_leaders]
        responses = grequests.map(rs)
        # ------------------------------------------------------------------------------------------

        # Step 5: send out my keys to other replicas in my shard ------------
        if environ["ADDRESS"] in self.view and environ["ADDRESS"] == self.shards[self.this_shard][0]:
            others = [replica for replica in self.shards[self.this_shard]
                      if replica != environ["ADDRESS"]]
            rs = [grequests.put("http://{}/kvs/reshard/put_payload".format(other),
                                json={"payload": self.local_kvs}) for other in others]
            grequests.map(rs)
        # ------------------------------------------------------------------------------------------

        # Step 6: construct client response --------------------------------------------------------
        # the format of responses in response is:
        #   {"address": node_addr, "key-count": node_count}
        shard_resp = []
        for response in responses:
            # only add response to client response if it"s not empty
            if response.json()["shard-id"] is not None:
                shard_resp.append(response.json())

        if environ["ADDRESS"] in self.view and environ["ADDRESS"] == self.shards[self.this_shard][0]:
            shard_resp.append({"shard-id": self.this_shard,
                                "key-count": len(self.local_kvs),
                                "replicas": self.shards[self.this_shard]})

        return {"message": "View change successful", "shards": shard_resp}, 200

    def prime(self):
        """
        Input: A comma-separated string representing the view
        Outline:
            - the shard leader tells his replicas to give him their keys and delete their own keys
            - shard leader acks caller (the view-change leader)
        """
        other_replicas = [replica for replica in self.shards[self.this_shard]
                          if replica != environ["ADDRESS"]]

        # send key request message --> receiving nodes send their keys and then clear their kvs
        res = [grequests.get("http://{}/kvs/reshard/get_keys".format(replica))
                     for replica in other_replicas]
        responses = grequests.map(res)

        # leader puts all keys in his kvs if they"re more recent
        for response in responses:
            new_keys = response.json()["keys"]
            new_hist = json.loads(response.json()["history"], cls=HistoryDecoder)

            updated_keys = self.local_key_versions.merge(new_hist)
            # update our keys if we need to
            for key in updated_keys:
                self.local_kvs[key] = new_keys[key]

        # leader no longer needs histories
        self.reset_histories()

        return {"current_view": self.current_view}, 200

    def leader_prime(self):
        """
        Input: A comma-separated string representing the view
        Outline:
            - the shard leader tells his replicas to give him their keys and delete their own keys
            - shard leader acks caller (the view-change leader)
        """
        # return if I am brand new
        if self.__old_this_shard is None:
            return
        other_replicas = [replica for replica in self.__old_shards[self.__old_this_shard]
                          if replica != environ["ADDRESS"]]

        # send key request message --> receiving nodes send their keys and then clear their kvs
        res = [grequests.get("http://{}/kvs/reshard/get_keys".format(replica))
                     for replica in other_replicas]
        responses = grequests.map(res)

        # leader puts all keys in his kvs if they"re more recent
        for response in responses:
            new_keys = response.json()["keys"]
            new_hist = json.loads(response.json()["history"], cls=HistoryDecoder)

            updated_keys = self.local_key_versions.merge(new_hist)
            # update our keys if we need to
            for key in updated_keys:
                self.local_kvs[key] = new_keys[key]

        # leader no longer needs histories
        self.reset_histories()

    def get_keys(self):
        """
        - Receiving node responds to caller(shard leader) with all of its keys and their most recent updates
        - then removes its kvs and all histories
        """
        kvs = self.local_kvs
        versions = self.local_key_versions
        self.local_kvs = {}

        self.reset_histories()

        return {"keys": kvs, "history": HistoryEncoder().encode(versions)}, 200

    def rehash(self):
        """
        Places all local key/value pairs into their appropriate temporary dictionaries.
        """

        # Replaces the fragments with empty dictionaries corresponding to the # of shards
        self.fragments = [{} for _ in range(len(self.shards))]

        # Populates the fragments with their corresponding key-value pairs based on each key"s hash
        for key, value in self.local_kvs.items():
            self.fragments[self.hash(key)][key] = value

        # Replaces the local KVS with its new key-value pairs
        if environ["ADDRESS"] in self.view:
            self.local_kvs = self.fragments[self.this_shard]
        else:
            self.local_kvs = {}

    def put_payload(self, fragment):
        """
        Note: the shard leader is the one using this function

        Input: A JSON payload which is just the full key/value list sent from another node.
        Function: Adds every item in the JSON payload to local_kvs
        """

        # Incorporate all incoming keys into my local kvs
        self.local_kvs.update(fragment)

        return {}, 200

    def distribute_keys(self):
        """
        shard leader sends his keys to the other replicas in his shard
        """
        other_replicas = [replica for replica in self.shards[self.this_shard]
                          if replica != environ["ADDRESS"]]

        # send keys
        responses = [grequests.put("http://{}/kvs/reshard/put_payload".format(replica),
                                   json={"payload": self.local_kvs}) for replica in other_replicas]
        grequests.map(responses)

        return {"shard-id": self.this_shard, "key-count": len(self.local_kvs),
                "replicas": self.shards[self.this_shard]}, 200

    def reshard(self):
        """
        desc:   old shard leaders send out their keys to the new shard leaders
        """
        # send data to other shard leaders in the new view
        others = [self.shards[i][0] for i in range(len(self.shards))
                        if self.shards[i][0] != environ["ADDRESS"]]
        responses = [grequests.put("http://{}/kvs/reshard/put_payload".format(other),
                                   json={"payload": self.fragments[self.get_shard_id(other)]}) 
                                    for other in others]
        grequests.map(responses)

        # clear our temp fragments - we are done with them
        self.fragments.clear()

        # If I"m not part of the new view, send empty response
        if environ["ADDRESS"] not in self.view:
            return {}, 200

        # return our address and number of keys to the leader

        return {"shard-id": self.this_shard, "key-count": len(self.local_kvs),
                "replicas": self.shards[self.this_shard]}, 200


########## GOSSIP FUNCTIONS ########################################################################

    # Timers schedule gossip and liveness check every x seconds


    def timed_gossip(self):
        schedule.every(1).seconds.do(self.gossip)
        while True:
            schedule.run_pending()
            time.sleep(1)

    def timed_liveness_check(self):
        schedule.every(1).seconds.do(self.liveness_check)
        while True:
            schedule.run_pending()
            time.sleep(1)
    """
     send out items that have changed since last gossip,
     determine by looking through current_events[], list of items that have changed since the last gossip
    """

    def gossip(self):
        """
            - protocol for 'gossiping' our keys between replicas to achieve eventual and causal consistency
        """
        if self.this_shard is None:
            return
        # populate dict to send out values to be updated
        changed_keys = {}                           # dicitonary of keys and their values
        per_item_history_for_changed_keys = {}      # dictionary of History objects

        # get the last modified items + their respective histories
        for key in self.between_gossip_updates:
            changed_keys[key] = self.local_kvs[key]
            per_item_history_for_changed_keys[key] = self.per_item_history[key]

        # need to send a few things:
        # 1. self.per_item_history_for_changed_keys ==> replaces per_item_history on replica node
        # 2. changed_keys  ==> so receiving node can update their keys/values
        # 3. self.between_gossip_updates ==> used to compare how recent an item is
        # 4. send self.cur_time

        # for each replica send a gossip msg
        # using grequests better then sending them one-by-one:
        #       - if several replicas are down, waiting for timeout would be lengthy
        #       - grequests consistent with our design philosophy from asgn3
        replicas = [replica for replica in self.shards[self.this_shard]
                    if replica != environ["ADDRESS"]]
        
        msgs = [grequests.get("http://" + replica + "/kvs/gossip",
                              json={"items": {key:value for (key, value) in changed_keys.items()},
                                    "item-history": {key: HistoryEncoder().encode(per_item_history_for_changed_keys[key])
                                                     for key in changed_keys},
                                    "updated-key-times": HistoryEncoder().encode(self.between_gossip_updates),
                                    "vector-clock": VectorClockEncoder().encode(self.cur_time),
                                    "address": environ["ADDRESS"]
                                    },
                              timeout=TIMEOUT_LENGTH) for replica in replicas]
        responses = grequests.map(msgs, exception_handler=timeout_handler)
        

        safe_to_delete = True
        # go through each response and updates my values accordingly

        for i, response in enumerate(responses):
            if response is None or response.status_code != 200:
                # mark replica as down
                self.replica_alive[replicas[i]] = False
                # don"t delete between_gossip_history later
                safe_to_delete = False
                continue

            items = response.json()["items"]
            history_responses_temp = response.json()["item-history"]
            history_responses = {}

            for key in history_responses_temp:
                history_responses[key] = json.loads(
                    history_responses_temp[key], cls=HistoryDecoder)

            other_clock = json.loads(response.json()["vector-clock"], cls=VectorClockDecoder)
            updated_key_times = json.loads(
                response.json()["updated-key-times"], cls=HistoryDecoder)

            # merge foreign update times with mine ==> returns list of my out-of-date keys
            keys_to_replace = self.local_key_versions.merge(updated_key_times)
            # replace keys and update necessary variables
            for key in keys_to_replace:
                self.local_kvs[key] = items[key]
                self.per_item_history[key] = history_responses[key]

            # update my vector clock
            self.cur_time.merge(other_clock)

        # if i heard from all replicas --> delete between_gossip_updates
        # THIS IS CURRENTLY BROKEN **************************************************************
        # if safe_to_delete:
        #     print("deleting update log", file=sys.stderr)
        #     self.between_gossip_updates = History()

        # just for testing --> let's see what we have
        if DEBUG:
            print("key-count: {}".format(len(self.local_kvs)), file=sys.stderr)
            print("local_kvs:\n", self.local_kvs, file=sys.stderr)
        # for key, version in self.local_key_versions.items():
        #     print("key: {}\nversions: {}".format(key,version), file=sys.stderr)
        # for key, history in self.per_item_history.items():
        #     print("key: {}\nhistory: {}".format(key,history), file=sys.stderr)

    def gossip_ack(self, sender_items, item_hist, updated_key_times, vector_clock, address):
        # if the sender address is in my view
        sender_address = address
        if self.this_shard is not None and sender_address in self.shards[self.this_shard]:

            # retrieve what items that have been updated on my end
            changed_keys = {}                           # dict of keys and their values
            per_item_history_for_changed_keys = {}      # dict of History objects
            for key in self.between_gossip_updates:
                changed_keys[key] = self.local_kvs[key]
                per_item_history_for_changed_keys[key] = self.per_item_history[key]

            # sender_items = request.json()["items"]
            # sender_item_hist = request.json()["item-history"]

            history_responses = item_hist

            # # populate histery responses dict with decoded sender per item history
            # for key in item_hist:
            #     history_responses[key] = json.loads(
            #         item_hist[key], cls=HistoryDecoder)

            sender_clock = vector_clock
            sender_updated_key_times = updated_key_times

            # merge sender update times with mine ==> returns list of my out-of-date keys
            keys_to_replace = self.local_key_versions.merge(
                sender_updated_key_times)

            # replace keys and update necessary variables
            for key in keys_to_replace:
                self.local_kvs[key] = sender_items[key]
                self.per_item_history[key] = history_responses[key]

            # update my vector clock
            self.cur_time.merge(sender_clock)

            # return ack
            return {
                "items": {key:value for (key, value) in changed_keys.items()},
                                    "item-history": {key: HistoryEncoder().encode(per_item_history_for_changed_keys[key])
                                                     for key in changed_keys},
                                    "updated-key-times": HistoryEncoder().encode(self.between_gossip_updates),
                                    "vector-clock": VectorClockEncoder().encode(self.cur_time)
            }, 200

        # alternative: return some meaningful response that makes sender update the view,
        # idk if worth the effort rn, could potentially affect view change
        #   let it get handled during reshard
        else:
            if DEBUG:
                print("\n\nSender address not in shard", file=sys.stderr)
                print("Sender address: {}\nMy address: {}\nThe view: {}\n".format(sender_address,environ["ADDRESS"],",".join(self.view)), file=sys.stderr)
            # return a dummy response
            return {
                "items": {},
                "item-history": {},
                "updated-key-times": {},
                "vector-clock": {}
            }, 404

# ------------------------------------------------------------------------------------------------------
# Liveness threads currently disabled (unneccesarry )and also broken --> request.remote_addr
# does not give the correct address (doesn't have port number during regular operation and
# during a network partition the address might not be even similar, if we want this thread
# to work, we would need to pass in an address, like we did with gossip)
# ------------------------------------------------------------------------------------------------------

    def liveness_check(self):
        # check our ded replicas, mark them as alive if they respond
        for address in self.replica_alive:
            if self.replica_alive[address] == False:
                try:
                    requests.get("http://{}/kvs/liveness".format(address), timeout=TIMEOUT_LENGTH)
                except:
                    None
                else:
                    self.replica_alive[address] = True

    def liveness_ack(self):
        # if someone asked if we"re alive, ack them and mark them as alive locally
        if self.replica_alive[request.remote_addr] == False:
            self.replica_alive[request.remote_addr] = True
        print(request.remote_addr, file=sys.stderr)
        return 200


# GET/PUT/DELETE request handlers *******************************************


    def put(self, key):
        """
            ASGN4:
                    requests will include the client context ====================
                        client context:
                            {"high_clock":VectorClock,"history":History}
                            - high_clock is the highest (most recent) vectorclock
                                the client knows about
                            - history is a History object containging time stamps
                                of all keys the client is aware of
                    putting keys:
                        - add the key to local_kvs
                        - merge self.cur_time with high_clock, then increment it
                        - set high_clock = self.cur_time
                        - set self.per_item_history[key] = client_history
                        - set self.local_key_versions[key] = high_clock
                        - add key and clock to between_gossip_updates
                        - do client_history.insert(key,local_key_versions[key])
                        - return client_history and high_clock to the client
                    proxy:
                        - these will be useful:
                            - self.shards[self.this_shard] is the list of all node addresses
                                in this shard
                            - self.shards[i] is a list of all nodes in shard i
                            - self.shards[i][j] is replica j in shard i
        """
        
        # Richard"s Update --- START ---
        # NOTE TO SELF: Returning causal context w/o json.dumps

        # Gets the data
        args = request.json
        # Takes the client"s context and converts it to a dictionary
        # NOTE: Using args vs. request.args
        causal_context = args["causal-context"]

        # Determines which shard the key should be in
        shard_id = self.hash(key)

        # Determines if I am a replica of the shard the key is supposed to be in
        if environ["ADDRESS"] in self.shards[shard_id]:
            # Checks if cleint"s causal context includes current view
            if "current_view" in causal_context:
                # Checks if the client"s current view is the same as ours
                if int(causal_context["current_view"]) != self.current_view:
                    # If not, delete client"s history and high clock and treat as fresh
                    # NOTE: If we get a KeyError it would probs come from here but I"m thinking if we have a current_view, should have everything else
                    causal_context.pop("high_clock_list", None)
                    causal_context.pop("history", None)
                    # del causal_context["high_clock_list"]
                    # del causal_context["history"]
                    # Give the client our current view
                    causal_context["current_view"] = self.current_view
            else:
                causal_context["current_view"] = self.current_view

            # Verifies key
            # Question: Would we need to do anything with the client"s causal context if it fails (two checks below), currently sends back whatever came in?
            if len(key) > 50:
                return {"error": "Key is too long", "message": "Error in PUT", "causal-context": causal_context}, 400

            # Checks if the value is present
            if args["value"] is None:
                return {"error": "Value is missing", "message": "Error in PUT", "causal-context": causal_context}, 400

            adding = False
            # Determines if we are ADDING or UPDATING
            if key not in self.local_kvs:
                adding = True

            # Adds/Updates the key to our KVS
            self.local_kvs[key] = args["value"]

            # Checks if the client"s causal context includes high clock
            if "high_clock_list" in causal_context and "history" in causal_context:
                # Decodes History and VectorClock
                history = json.loads(
                    causal_context["history"], cls=HistoryDecoder)
                # Question: I"m not sure if this part is necessary considering we already parsed the JSON so it should"ve converted it to a list for us already?
                high_clock_list = causal_context["high_clock_list"]
                # Decodes each high clock in the high clock list
                for index, high_clock in enumerate(high_clock_list):
                    high_clock_list[index] = json.loads(
                        high_clock, cls=VectorClockDecoder)

                # Merges our current time with the high clock
                self.cur_time.merge(high_clock_list[self.this_shard])

                # Increments our current time
                self.cur_time.increment()

                # Sets the high clock to our current time
                high_clock_list[self.this_shard] = self.cur_time

                # Updates the per item history
                self.per_item_history[key] = history

                # Tracks updates
                self.per_item_history[key].insert(
                    key, high_clock_list[self.this_shard])

                # Inserts the updated per item history into the client"s history
                history.merge(self.per_item_history[key])

                # Encodes History and VectorClock before sending back
                causal_context["history"] = HistoryEncoder().encode(history)
                causal_context["high_clock_list"] = [VectorClockEncoder().encode(high_clock)
                                    for high_clock in high_clock_list]
            else:
                # Increments our current time
                self.cur_time.increment()

                # Creates a new high clock list
                high_clock_list = [None for _ in range(
                    len(self.view) // self.repl_factor)]

                # Sets the new high clock to our current time
                high_clock_list[self.this_shard] = self.cur_time

                # Creates a new history
                history = History()

                # Creates the per item history
                self.per_item_history[key] = history

                # Tracks updates
                self.per_item_history[key].insert(
                    key, high_clock_list[self.this_shard])

                # Inserts the updated per item history into the client"s history
                history.merge(self.per_item_history[key])

                # Encodes History and VectorClock before sending back
                causal_context["history"] = HistoryEncoder().encode(history)
                causal_context["high_clock_list"] = [VectorClockEncoder().encode(high_clock)
                                    for high_clock in high_clock_list]

            
            # Adds key and clock to local key versions
            self.local_key_versions.insert(key, self.cur_time)

            # Adds key and clock to between gossip updates
            self.between_gossip_updates.insert(key, self.cur_time)

            # Replies to the client
            if adding:
                return {"message": "Added successfully", "replaced": False, "causal-context": causal_context}, 201
            else:
                return {"message": "Updated successfully", "replaced": True, "causal-context": causal_context}, 200
        else:
            # Proxies
            # Question: Client will always send causal-context right? Not checking for empty body currently
            resp = [grequests.put("http://{}/kvs/keys/{}".format(addr, key),
                                  json=request.json,
                                  headers=dict(request.headers),
                                  timeout=TIMEOUT_LENGTH)
                    for addr in self.shards[shard_id]]
            responses = grequests.map(resp, exception_handler=timeout_handler)

            bad_response = None
            # Checks for the first valid response
            for i, response in enumerate(responses):
                # Skips if timed out
                if response is None:
                    continue
                if response.status_code != 200:
                    bad_response = response
                    bad_response.json().update({"address": self.shards[shard_id][i]})

                # Gets json
                resp = response.json()
                resp.update({"address": self.shards[shard_id][i]})

                return resp, response.status_code

            # if put couldn't be fulfilled, return why not
            if bad_response is not None:
                return bad_response.json(), bad_response.status_code
            # All requests failed
            # Question: Should we send back the causal context?
            return {"error": "Unable to satisfy request", "message": "Error in PUT", "causal-context": causal_context}, 503
        # Richard"s Update --- END ---

        
    # -----------------------------------------------------------------------------
    def get(self, key):
        """
            ASGN4:
                    requests will include the client context ====================
                        client context:
                            {"high_clock":VectorClock,"history":History}
                            - high_clock is the highest (most recent) vectorclock
                                the client knows about
                            - history is a History object containging time stamps
                                of all keys the client is aware of

                    getting keys:
                        - safe to return local_kvs[key] if:
                            - self.local_key_versions[key] >= client_history[key]
                        - if not safe:
                            - set our cur_time = cur_time merged with client_high_clock
                            - NACK the client
                        - if safe:
                            - merge self.cur_time with high_clock
                        - set high_clock = self.cur_time
                        - do client_history.merge(per_item_history[key])
                        - return client_history and high_clock to the client

                    proxy:
                        - these will be useful:
                            - self.shards[self.this_shard] is the list of all node addresses
                                in this shard
                            - self.shards[i] is a list of all nodes in shard i
                            - self.shards[i][j] is replica j in shard i

        """
        # Forward request if I don"t have the key
        # Parse client"s causal context from request
        ######
        # not sure if the request object will be here or if I need to pass it into the function
        ######

        # we need to take into account that a fresh client will not have any causal-context ==> must check
        # for this to avoid errors being thrown

        args = request.json

        # if no provided causal context, lets make a default one

        context = args["causal-context"]

        # if context is empty -> initialize with empty values
        if "history" not in context:
            context = {"high_clock_list": [None for i in range(len(self.view) // self.repl_factor)],
                       "history": History(),
                       "current_view": self.current_view}       # might fuck up
            client_history = context["history"]
            client_high_clock_list = context["high_clock_list"]
        else:
            client_history = json.loads(context["history"], cls=HistoryDecoder)
            # high_clock must be a list equal to the number of shards in the view
            client_high_clock_list = context["high_clock_list"]
            for index, clock in enumerate(client_high_clock_list):
                client_high_clock_list[index] = json.loads(
                    clock, cls=VectorClockDecoder)

        client_view_id = int(context["current_view"])

        node_id = self.hash(key)
        if environ["ADDRESS"] not in self.shards[node_id]:
            res = [grequests.get("http://{}/kvs/keys/{}".format(addr, key),
                                 json=request.json,
                                 headers=dict(request.headers),
                                 timeout=TIMEOUT_LENGTH)
                   for addr in self.shards[node_id]]
            responses = grequests.map(res, exception_handler=timeout_handler)

            bad_response = None
            # Checks for the first valid response
            for i, response in enumerate(responses):
                # Skips if timed out
                if response is None:
                    continue
                # response was received, but node didn't have the key
                if response.status_code != 200:
                    bad_response = response
                    bad_response.json().update({"address": self.shards[node_id][i]})
                    continue

                # Gets json
                resp = response.json()
                resp.update({"address": self.shards[node_id][i]})

                return resp, response.status_code

            # if no node had the key, but we still heard from them, return message
            if bad_response is not None:
                return bad_response.json(), bad_response.status_code

            # All requests failed
            context = {"high_clock_list": [VectorClockEncoder().encode(clock) for clock in client_high_clock_list],
                       "history": HistoryEncoder().encode(client_history),
                       "current_view": client_view_id}
            return {"error": "Unable to satisfy request", "message": "Error in GET", "causal-context": context}, 503

        # Determine if the history of the key is consistent with the client
        # Compare client"s vc for the key they"re trying to access with ours
        # Safe to return if VC compare is not -1
        try:
            local_vc = self.local_key_versions[key]
        except:
            local_vc = None
        client_vc = client_history[key]
        compare_val = VectorClock.compare(local_vc, client_vc)

        #
        # isn"t it safe to return whatever we have if the clocks are concurrent?
        # pretty sure VectorClock no longer returns CONCURRENT, it has a built-in tie breaker
        #
        is_safe = (compare_val == VectorClock.GREATER_THAN or compare_val == VectorClock.EQUAL) or local_vc is None

        new_causal_context = False
        if client_view_id < self.current_view:
            # automatically safe to return whatever we have
            # override comparison from before, and signal to return fresh context
            is_safe = True
            new_causal_context = True

        if (is_safe):
            if new_causal_context:
                # Client was behind on view change, so give fresh context
                # return client whatever we"ve seen to this point
                client_view_id = self.current_view
                # if we have the key, give them the history of the key
                # otherwise give blank history object since they"re starting fresh
                if key in self.local_kvs:
                    try:
                        fresh_history = self.per_item_history[key]
                    except:
                        fresh_history = History()
                else:
                    fresh_history = History()
                new_context = {"high_clock_list": [VectorClockEncoder().encode(clock) for clock in client_high_clock_list],
                               "history": HistoryEncoder().encode(fresh_history),
                               "current_view": client_view_id}
            else:
                # know client is up to date with all causal dependencies
                # Merge our local vc with client"s just to make sure we"re up to date
                self.cur_time.merge(client_high_clock_list[self.this_shard])
                client_high_clock_list[self.this_shard] = self.cur_time

                # set up client"s context object to be up to date with what we know
                if key in self.local_kvs:
                    # per_item_history won't exist after view change --> key error
                    try:
                        client_history.merge(self.per_item_history[key])
                    except:
                        pass

                # finally return the key the client asked for, with updated clock and history for client
                #
                # changed to json.dumps() because had error for no fp with json.dump()
                new_context = {"high_clock_list": [VectorClockEncoder().encode(clock) for clock in client_high_clock_list],
                               "history": HistoryEncoder().encode(client_history),
                               "current_view": client_view_id}

            if key in self.local_kvs:
                return {"message": "Retrieved successfully", "doesExist": True, "value": self.local_kvs[key],
                        "causal-context": new_context}, 200
            else:
                # Because we checked it"s safe to ack client, we can be sure the key hasn"t been inserted yet
                return {"message": "Error in GET", "error": "Key does not exist", "doesExist": False,
                        "causal-context": new_context}, 404

        else:
            # NACK
            # Possible to attempt gossip here
            # Want to make sure regular get works first, then can add gossip optimization
            #
            # Austin requested we update our high_clock on failed requests just to keep most current time
            self.cur_time.merge(client_high_clock_list[self.this_shard])
            return {"error": "Unable to satisfy request", "message": "Error in GET"}, 400

    # ------------------------------------------------------------------------------
    """
    def delete(self, key):
        # Forward request if I don"t have the key
        node_id = self.hash(key)
        if node_id != self.view.index(environ["ADDRESS"]):
            try:
                r = requests.delete(
                    "http://{}/kvs/keys/{}".format(self.view[node_id], key),
                    headers=dict(request.headers), timeout=TIMEOUT_LENGTH)
            except:
                return {"error": "Key store is down", "message": "Error in DELETE"}, 503
            else:
                resp = r.json()
                # proxy adds forwarding address
                resp.update({"address": self.view[node_id]})
                return resp, r.status_code

        if key in self.local_kvs:
            del self.local_kvs[key]
            return {"doesExist": True, "message": "Deleted successfully"}

        return {"doesExist": False, "error": "Key does not exist", "message": "Error in DELETE"}, 404
    """
    # ------------------------------------------------------------------------------

    def key_count(self):
        """
        Returns: length of KVS
        """
        return {"message": "Key count retrieved successfully",
                "key-count": len(self.local_kvs),
                "shard-id": self.this_shard}, 200

    def get_shard_info(self, shard_id):
        # forward message to the appropriate node
        if shard_id != self.this_shard:
            msgs = [grequests.get("http://{}/kvs/shards/{}".format(addr, shard_id),
                                  headers=dict(request.headers), timeout=TIMEOUT_LENGTH)
                    for addr in self.shards[shard_id]]
            responses = grequests.map(msgs, exception_handler=timeout_handler)

            # return the first valid response
            for response in responses:
                if response is None or response.status_code != 200:
                    continue

                return response.json(), response.status_code
            return {"error": "Unable to satisfy request", "message": "Error in GET"}, 503

        return {"message": "Shard information retrieved successfully",
                "shard-id": self.this_shard,
                "key-count": len(self.local_kvs),
                "replicas": self.shards[self.this_shard]}, 200

    def get_all_shard_IDs(self):
        return {"message": "Shard membership retrieved successfully",
                "shards": [shard_id for shard_id in range(len(self.shards))]}, 200


################ Additional utility functions ######################################################


    def __str__(self):
        """
            what to do if print(Node) is called
        """
        ret = "{"
        for key, value in self.local_kvs.items():
            ret += str(key) + ": " + str(value) + ",\t"
        ret += "}"
        return ret
