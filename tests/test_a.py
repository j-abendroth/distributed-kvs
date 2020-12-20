import os
import sys
import time
import random
import json
from vector_clock import VectorClock, VectorClockDecoder, VectorClockEncoder
from history import History, HistoryDecoder, HistoryEncoder
import requests


LINE = "\n---------------------------------------------------------\n"

class TestValues():
    node_addrs = ["10.10.0.2:13800", "10.10.0.3:13800",
                  "10.10.0.4:13800", "10.10.0.5:13800",
                  "10.10.0.6:13800", "10.10.0.7:13800"]
    repl_factor = 2

    random_keys = ["oh_fuck", "yeeted", "yikes", "pedro",
                   "hello_there", "dog", "cat", "pig", "oink",
                   "aloha", "zion", "old_man_jenkins",
                   "a_key_that_is_much_too_long_for_our_server_to_handle_kind_of_like_my_peen",
                   "he_was_number_one!", "how_much_wood_could_a_wood_chuck_chuck?",
                   "wait i dont need underscores...", "this is string",
                   "i am string", "words words", "sombrero", "[:)]", "abrakadabra",
                   "cars boats trains", "CSE138","a key", "another key", "and another key",
                   "sample key"]
    random_values = [str(random.randint(0, 500)) for i in range(50)]

    default_request = {"causal-context": None}


def send_get(node, key, context):
    response = requests.get(
        "http://{}/kvs/keys/{}".format(node, key), json={"causal-context": context})
    if response.json() is not None:
        resp = response.json()
        resp["status_code"] = response.status_code
        return resp
    return {"status_code": response.status_code}


def send_put(node, key, value, context):
    response = requests.put("http://{}/kvs/keys/{}".format(node, key),
                            json={"value": value, "causal-context": context})
    if response.json() is not None:
        resp = response.json()
        resp["status_code"] = response.status_code
        return resp
    return {"status_code": response.status_code}

def send_view_change(node, view, repl_factor):
    response = requests.put("http://{}/kvs/view-change".format(node),
                            json={"view": view, "repl-factor": repl_factor})
    if response.json() is not None:
        resp = response.json()
        resp["status_code"] = response.status_code
        return resp
    return {"status_code": response.status_code}

def send_get_key_count(node):
    response = requests.get("http://{}/kvs/key-count".format(node))
    if response.json() is not None:
        resp = response.json()
        resp["status_code"] = response.status_code
        return resp
    return {"status_code": response.status_code}

def send_get_shard_IDs(node):
    response = requests.get("http://{}/kvs/shards".format(node))
    if response.json() is not None:
        resp = response.json()
        resp["status_code"] = response.status_code
        return resp
    return {"status_code": response.status_code}

def send_get_shard_info(node, shard):
    response = requests.get("http://{}/kvs/shards/{}".format(node, shard))
    if response.json() is not None:
        resp = response.json()
        resp["status_code"] = response.status_code
        return resp
    return {"status_code": response.status_code}

def main():

    nodes_to_test = [TestValues.node_addrs[0], TestValues.node_addrs[2]]
    for node in nodes_to_test:
        context = {}
        keys = []
        values = []
        num_sends = 10
        context_list = []
        # Test basic GET/PUT from same client _______________________________________
        # testing PUTS
        print("\n\nTESTING PUTS\n\n", file=sys.stderr)
        for i in range(num_sends):
            keys.append(TestValues.random_keys[random.randint(
                0, len(TestValues.random_keys)-1)])
            values.append(TestValues.random_values[random.randint(
                0, len(TestValues.random_values)-1)])
            response = send_put(node, keys[i], values[i], context)
            if "causal-context" in response:
                context = response["causal-context"]
                context_list.append(context)
            for key, val in response.items():
                print(key, ":", val)
            print(LINE)

        print("I tried to add these keys:")
        for i in range(num_sends):
            print("key: {}\t\tval: {}".format(keys[i], values[i]))
        print(LINE)

        time.sleep(3)

        # testing GETS
        print("\n\nTESTING GETS\n\n", file=sys.stderr)
        for i in range(num_sends):
            context = context_list[random.randint(0, len(context_list) - 1)]
            print("causal-context before GET({},{}): {}\n".format(node,keys[i],context), file=sys.stderr)
            response = send_get(node, keys[i], context)
            for key, val in response.items():
                print(key, ":", val)
            print(LINE)


        time.sleep(3)

        # test causal consistency _______________________________________________
        # testing GETS to a different replica
        print("\n\nTESTING GETS FROM A DIFFERENT NODE\n\n", file=sys.stderr)
        node = TestValues.node_addrs[2]
        for i in range(num_sends):
            context = context_list[random.randint(0, len(context_list) - 1)]
            print("causal-context before GET({},{}): {}\n".format(node,keys[i],context), file=sys.stderr)
            response = send_get(node, keys[i], context)
            for key, val in response.items():
                print(key, ":", val)
            print(LINE)

    
    print("\n\nTESTING ADMIN ENDPOINTS\n\n", file=sys.stderr)
    command = "./docker_run_new_nodes.sh"
    os.system(command)
    time.sleep(8)
    # trigger a view change
    new_view = ",".join(TestValues.node_addrs)
    repl_factor = 3
    response = send_view_change(node, new_view, repl_factor)
    print(response)
    print(LINE)

    # test administrative endpoints
    node = TestValues.node_addrs[3]
    response = send_get_key_count(node)
    for key, val in response.items():
            print(key, ":", val)
    print(LINE)

    node = TestValues.node_addrs[0]
    response = send_get_shard_IDs(node)
    for key, val in response.items():
            print(key, ":", val)
    print(LINE)

    node = TestValues.node_addrs[2]
    shard = 0
    response = send_get_shard_info(node, shard)
    for key, val in response.items():
            print(key, ":", val)
    print(LINE)

    node = TestValues.node_addrs[2]
    shard = 1
    response = send_get_shard_info(node, shard)
    for key, val in response.items():
            print(key, ":", val)
    print(LINE)


    # send GET requests to the new view
    # testing GETS
    print("\n\nTESTING GETS\n\n", file=sys.stderr)
    for i in range(num_sends):
        context = context_list[random.randint(0, len(context_list) - 1)]
        response = send_get(node, keys[i], context)
        for key, val in response.items():
            print(key, ":", val)
        print(LINE)


    time.sleep(3)

    # test causal consistency _______________________________________________
    # testing GETS to a different replica
    print("\n\nTESTING GETS FROM A DIFFERENT NODE\n\n", file=sys.stderr)
    node = TestValues.node_addrs[2]
    for i in range(num_sends):
        response = send_get(node, keys[i], context)
        for key, val in response.items():
            print(key, ":", val)
        print(LINE)


    # # test gossip 1
    # # write sampleKey1 = value1 to replica1
    # response = send_put(TestValues.node_addrs[0], "favnumber", "420", {})
    # print("Sleeping...")
    # time.sleep(2)
    # context = response["causal-context"]
    # # read sampleKey1 from replica2 to make sure it"s correct
    # response = send_get(TestValues.node_addrs[1], "favnumber", context)


main()
