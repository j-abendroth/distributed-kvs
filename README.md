# Distributed, Replicated, & Causally Consistent Key-Value Store
### John Abendroth, Matt Ngo, Austin Seyboldt, Richard Thai

## About
As a team, this was our final project for our capstone class Distributed Systems. It is a key-value store database which uses a weak-consistency model of eventual consistency, targetting availability and partition tolerance. It partitions keys using sharding, and replicates each shard with multiple nodes to achieve fault tolerance. To achieve eventual consistency, the system uses an anti-entropy "gossip" protocol to exchange causual data between replicas, so that the key-value store can accurately respond to requests with updated causal data. The system rejects requests that violate causal consistency, or requests which a replica can't answer due to not having updated causal history available. The causal history utilizes vector clocks to track consistency, and is piggybacked during requests so that replicas can stay updated, and reject as few requests as possible.

## How to Build
Provided is a dockerfile to create the docker image for the system. From there, you can easily create multiple instances to run multiple nodes in the store. The instances take several container options and environment variables:
* ADDRESS - environment variable for network address of the instance
* VIEW - environment variable which provides a comma separated list of all other instances in the store
* REPL_FACTOR = environment variable for the replication factor of the store, how many replicas (nodes) for each shard
* ip - Container option for Docker subnet IP address of container
* net - Container option for Docker subnet to use
* name - Container option for name of container to use

#### Example:
Create 4 nodes and a Docker subnet named 'kv_subnet':

- Create subnet, **kv_subnet**, with IP range **10.10.0.0/16**:

    ```bash
    $ docker network create --subnet=10.10.0.0/16 kv_subnet
    ```

    - Build Docker image containing the key-value store implementation:

    ```bash
    $ docker build -t kvs:4.0 ./src/
    ```
- Run four instances at IP's 10.10.0.2, 10.10.0.3, 10.10.0.4 and 10.10.0.5, and listening to port 13800:  

    ```bash
    $ docker run -p 13800:13800                                                            \
                 --net=kv_subnet --ip=10.10.0.2 --name="node1"                             \
                 -e ADDRESS="10.10.0.2:13800"                                              \
                 -e VIEW="10.10.0.2:13800,10.10.0.3:13800,10.10.0.4:13800,10.10.0.5:13800" \
                 -e REPL_FACTOR=2                                                          \
                 kvs:4.0
    ```
    
    ```bash
    $ docker run -p 13801:13800                                                            \
                 --net=kv_subnet --ip=10.10.0.3 --name="node2"                             \
                 -e ADDRESS="10.10.0.3:13800"                                              \
                 -e VIEW="10.10.0.2:13800,10.10.0.3:13800,10.10.0.4:13800,10.10.0.5:13800" \
                 -e REPL_FACTOR=2                                                          \
                 kvs:4.0
    ```
    
    ```bash
    $ docker run -p 13802:13800                                                            \
                 --net=kv_subnet --ip=10.10.0.4 --name="node3"                             \
                 -e ADDRESS="10.10.0.4:13800"                                              \
                 -e VIEW="10.10.0.2:13800,10.10.0.3:13800,10.10.0.4:13800,10.10.0.5:13800" \
                 -e REPL_FACTOR=2                                                          \
                 kvs:4.0
    ```
    
    ```bash
    $ docker run -p 13803:13800                                                            \
                 --net=kv_subnet --ip=10.10.0.5 --name="node4"                             \
                 -e ADDRESS="10.10.0.5:13800"                                              \
                 -e VIEW="10.10.0.2:13800,10.10.0.3:13800,10.10.0.4:13800,10.10.0.5:13800" \
                 -e REPL_FACTOR=2                                                          \
                 kvs:4.0
    ```
- Stop and remove containers:
  
    ```bash
    $ docker stop node1 node2 node3 node4 && docker rm node1 node2 node3 node4
    ```

## How to Use:
The causal context object is stored as JSON, and must be sent and received with every request as `Content-Type: application/json`

#### Endpoins:
The key-value store supports the following endpoints:
| Endpoint URI       | accepted request types    |
| ------------------ | ------------------------- |
| /kvs/keys/\<key\>  | GET, PUT                  |
| /kvs/key-count     | GET                       |
| /kvs/shards        | GET                       |
| /kvs/shards/\<id\> | GET                       |
| /kvs/view-change   | PUT                       |
| /kvs/keys/\<key\>  | DELETE (not implemented)  |

#### Example Usage:
#### Insert new key

- To insert a key named sampleKey, send a PUT request to **/kvs/keys/sampleKey** and include the causal context object as JSON.

    - If no value is provided for the new key, the key-value store should respond with status code 400. This example sends the request to node3.

    ```bash
    $ curl --request   PUT                                        \
           --header    "Content-Type: application/json"           \
           --write-out "%{http_code}\n"                           \
           --data      '{"causal-context":causal-context-object}' \
           http://127.0.0.1:13802/kvs/keys/sampleKey

           {
               "message"       : "Error in PUT",
               "error"         : "Value is missing",
               "causal-context": new-causal-context-object,
           }
           400
    ```

    - If the key has length greater than 50, the key-value store should respond with status code 400. This example sends the request to node1. Assume that shard2 stores the key **loooooooooooooooooooooooooooooooooooooooooooooooong**, according to the key-partition mechanism. However, node3 from shard2 returns an error message saying the key is too long. 
    
    ```bash
    $ curl --request   PUT                                                              \
           --header    "Content-Type: application/json"                                 \
           --write-out "%{http_code}\n"                                                 \
           --data      '{"value":"sampleValue","causal-context":causal-context-object}' \
           http://127.0.0.1:13800/kvs/keys/loooooooooooooooooooooooooooooooooooooooooooooooong

           {
               "message"       : "Error in PUT",
               "error"         : "Key is too long",
               "address"       : "10.10.0.4:13800",
               "causal-context": new-causal-context-object,
           }
           400
    ```

    - On success, the key-value store should respond with status code 201. This example sends the request to node1.

    ```bash
    $ curl --request   PUT                                                              \
           --header    "Content-Type: application/json"                                 \
           --write-out "%{http_code}\n"                                                 \
           --data      '{"value":"sampleValue","causal-context":causal-context-object}' \
           http://127.0.0.1:13800/kvs/keys/sampleKey

           {
               "message"       : "Added successfully",
               "replaced"      : false,
               "address"       : "10.10.0.4:13800",
               "causal-context": new-causal-context-object,
           }
           201
    ```

#### Update existing key

- To update an existing key named sampleKey, send a PUT request to /kvs/keys/sampleKey and include the causal context object as JSON.
  
    - If no updated value is provided for the key, the key-value store should respond with status code 400. This example sends the request to node1.

    ```bash
    $ curl --request   PUT                                        \
           --header    "Content-Type: application/json"           \
           --write-out "%{http_code}\n"                           \
           --data      '{"causal-context":causal-context-object}' \
           http://127.0.0.1:13800/kvs/keys/sampleKey

           {
               "message"       : "Error in PUT",
               "error"         : "Value is missing",
               "address"       : "10.10.0.4:13800",
               "causal-context": new-causal-context-object,
           }
           400
    ```
    
    - The key-value store should respond with status code 200. This example sends the request to node3.

    ```bash
    $ curl --request   PUT                                                              \
           --header    "Content-Type: application/json"                                 \
           --write-out "%{http_code}\n"                                                 \
           --data      '{"value":"sampleValue","causal-context":causal-context-object}' \
           http://127.0.0.1:13802/kvs/keys/sampleKey

           {
               "message"       : "Updated successfully",
               "replaced"      : true,
               "causal-context": new-causal-context-object,
           }
           200
    ```

#### Read an existing key

- To get an existing key named sampleKey, send a GET request to /kvs/keys/sampleKey and include the causal context object as JSON.
  
    - If the key, sampleKey, does not exist, the key-value store should respond with status code 404. The example sends the request to node1.

    ```bash
    $ curl --request   GET                                        \
           --header    "Content-Type: application/json"           \
           --write-out "%{http_code}\n"                           \
           --data      '{"causal-context":causal-context-object}' \
           http://127.0.0.1:13800/kvs/keys/sampleKey

           {
               "message"       : "Error in GET",
               "error"         : "Key does not exist",
               "doesExist"     : false,
               "address"       : "10.10.0.4:13800",
               "causal-context": new-causal-context-object,
           }
           404
    ```

    - On success, assuming the current value of sampleKey is sampleValue, the key-value store should respond with status code 200. This example sends the request to node1.

    ```bash
    $ curl --request   GET                                        \
           --header    "Content-Type: application/json"           \
           --write-out "%{http_code}\n"                           \
           --data      '{"causal-context":causal-context-object}' \
           http://127.0.0.1:13800/kvs/keys/sampleKey

           {
               "message"       : "Retrieved successfully",
               "doesExist"     : true,
               "value"         : "sampleValue",
               "address"       : "10.10.0.4:13800",
               "causal-context": new-causal-context-object,
           }
           200
    ```