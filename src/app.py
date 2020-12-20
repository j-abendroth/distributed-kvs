import sys
import json
from node import Node
from os import environ
from flask_restful import Api, Resource, reqparse
from flask import Flask, request
from gevent import monkey
from vector_clock import VectorClockDecoder
from history import HistoryDecoder
monkey.patch_all()

app = Flask(__name__)
api = Api(app)

"""
    Changes required: _____________________________________________________

        update endpoints:
            - add support for client context

"""


class KVS(Resource):
    def put(self, key):
        return instance.put(key)

    def get(self, key):
        return instance.get(key)

    """
    def delete(self, key):
        return instance.delete(key)
    """


class ViewChange(Resource):
    def put(self):
        # get the view from the request to pass as argument
        parser = reqparse.RequestParser()
        parser.add_argument("view")
        parser.add_argument("repl-factor")
        args = parser.parse_args()
        return instance.try_reshard(args["view"], int(args["repl-factor"]))


class KeyCount(Resource):
    def get(self):
        return instance.key_count()


# Internal endpoints for resharding
class Reshard(Resource):
    # perform a check to reject requests not from a node
    def put(self, command):
        # Handle prime here
        if command == "prime":
            return instance.prime()

        # shard leader rehashes his keys
        if command == "rehash":
            return instance.rehash()

        # Handle incoming dictionaries here (if shard leader)
        elif command == "put_payload":
            json_data = request.json
            payload = json_data["payload"]
            return instance.put_payload(payload)

        # # handle incoming dictionaries here (if shard follower)
        # elif command == "put_shard_keys":
        #     json_data = request.json
        #     payload = json_data["payload"]
        #     return instance.put_payload(payload)

        # update the view and shards
        elif command == "set_new_view":
            parser = reqparse.RequestParser()
            parser.add_argument("view")
            parser.add_argument("repl_factor")
            parser.add_argument("current_view")
            args = parser.parse_args()
            return instance.set_shards_and_view(args["view"], int(args["repl_factor"]),
                            args["current_view"])

    def get(self, command):
        # Handle Reshard here
        if command == "reshard":
            return instance.reshard()

        # call function for a replica to respond with its entire kvs
        if command == "get_keys":
            return instance.get_keys()

        # shard leaders distribute keys
        elif command == "send_keys_to_replicas":
            return instance.distribute_keys()


class Gossip(Resource):
    def get(self):
        # get the view from the request to pass as argument
        json_data = request.json
        hist = json_data["item-history"]
        for key, val in hist.items():
            hist[key] = json.loads(hist[key], cls=HistoryDecoder)
        
        return instance.gossip_ack(json_data["items"], hist,
                    json.loads(json_data["updated-key-times"], cls=HistoryDecoder),
                    json.loads(json_data["vector-clock"], cls=VectorClockDecoder),
                    json_data["address"])


class Liveness(Resource):
    def get(self):
        return instance.liveness_ack()


class ShardsInfo(Resource):
    def get(self):
        return instance.get_all_shard_IDs()


class SingleShardInfo(Resource):
    def get(self, shard_id):
        return instance.get_shard_info(shard_id)


# /kvs/keys/<key>	GET, PUT, DELETE
api.add_resource(KVS, "/kvs/keys/<string:key>")
# /kvs/key-count	GET
api.add_resource(KeyCount, "/kvs/key-count")
# /kvs/view-change	PUT
api.add_resource(ViewChange, "/kvs/view-change")
# /kvs/reshard/<string:command>
api.add_resource(Reshard, "/kvs/reshard/<string:command>")
# /kvs/shards --> get IDs of all shards
api.add_resource(ShardsInfo, "/kvs/shards")
# /kvs/shards/<shard_id> ==> get info for a specific shard
api.add_resource(SingleShardInfo, "/kvs/shards/<int:shard_id>")


api.add_resource(Gossip, "/kvs/gossip")
api.add_resource(Liveness, "/kvs/liveness")

if __name__ == "__main__":
    if "VIEW" in environ and "REPL_FACTOR" in environ:
        instance = Node(environ["VIEW"], environ["REPL_FACTOR"])
    app.run(debug=True, host="0.0.0.0", port="13800", threaded=True)
