# CSE138 Assignment 4 - Causally consistent and available KVS
### John Abendroth, Richard Thai, Matt Ngo, Austin Seyboldt

- [JamBoard](https://jamboard.google.com/d/1ls0JhLuJl_EugUaINRULNFQV4LlfDyMsMmUSJL6VQ34/edit?usp=sharing)  
  
## **Notes/Updates/Feedback**
### Please use this section to make note of issues, suggest fixes, mark items for discussion
```
Possible problems:
  [if these don't make sense - don't worry - mostly just notes for myself later if there are bugs (Austin)]
      - what happens if we set the History of an item from another replica to be our History of that item?
          - it should work but comparing vector clocks could potentially become non-deterministic and result
          - in two replicas agreeing on different final values of an item because we copied the history (including
          - all vector clocks in it and the vectorclocks have a data member corresponding to the replica id) --
          - so essentially by copying we could end up with a replica holding a vector clock and being confused about
          - who it belongs to
              - not an issue because we're comparing only most recent histories ==> will always have correct address

  -
  -
  -
  -
  -
  -
  -
```
  
  
# Requirements
## Features / Functionalities
- Each node is capable of handling client requests
- Each key on only one shard, each shard stored by m nodes, m = replication factor
- Perform view changes when requested, which may change replication factor
- Insert, update, get, delete (optional)
- Nodes can respond to key-count requests
- Failures can happen (use timeouts)
- System is available - if even one replica is alive, it should respond (if it can)

## Endpoints
- /kvs/keys/<key>
- /kvs/key-count
- /kvs/shards
- /kvs/shards/<shard-id>
- /kvs/view-change
- /kvs/gossip
- /kvs/liveness
  
## Constraints
### Nodes
- A node is an individual instance of the program, corresponding to a logical shard and one of possibly many replicas
- The number of nodes in the view = shards * replication factor  
  
**Ex for shards = 2, replication factor = 2:**  
```
+-----------------------+   +-----------------------+   +------------+   +------------+
|   node1               |   |   node2               |   |   node3    |   |   node4    |
| --------------------- |   | --------------------- |   | ---------- |   | ---------- |
|  shard1               |   |  shard1               |   |  shard2    |   |  shard2    |
|  replica1             |   |  replica2             |   |  replica1  |   |  replica2  |
| --------------------- |   | --------------------- |   | ---------- |   | ---------- |
|  - key1               |   |  - key1               |   |  - key2    |   |  - key2    |
|  - key3               |   |  - key3               |   |  - key4    |   |  - key4    |
|  - key5               |   |  - key5               |   |  - key6    |   |  - key6    |
|  - attending_class    |   |  - attending_class    |   |  - key8    |   |  - key8    |
+-----------------------+   +-----------------------+   +------------+   +------------+
```
  
### Causality / Happens-Before
- A message send *happens before* its corresponding receive
- There is a total order to all local processses
- If A → B, and B → C, then A → C; if no such order exists between A and C then the events can happen in any order
- A causal context must be included in all reponses to clients
- Nodes gossip to guarantee eventual consistency and will do so **within 5 seconds of any key-value operation (preferably *much sooner*)**
- Causal consistency must be preserved between any 2 sequential view changes
  
## Additional Notes
- If we assign shard IDs starting at 0, must remember to subtract 1 from requested shard (TA spec gives first shard_ID as 1)

  
# Design
## Assigning shard / replication IDs to nodes
- Nodes will have two IDs: shard_ID, node_ID
- node_ID = same as asgn3 implementation (sort addresses)
- shard_id = int(node_ID / repl_factor)
- a node will know the addresses of everyone in his shard because they will be all node_IDs in range [(local_shard_ID * repl_factor):(local_shard_ID * repl_factor) + repl_factor]

## Causal Consistency Mechanics
#### How do we define causality?
We are not concerned with a total ordering of events and if two events occur one after another on the same replica, we can know that by looking at their vector clocks, 
but it doesn't necessarily matter for consistency.  
  
For example, client 1 writes *x* to replica 1. Then, client 2 writes *y* to replica 1. In some sense, *y* was written 
after *x* (at least replica 1 received the PUT after, but that doesn't mean the second client really wrote it after the first client (ex: message from client 2 could have 
been sent before client 1's message but just took a long time to deliver). Thus, we say the two events are concurrent and x *is not* a dependency of y.  
  
Now, let's say client 2 writes a new value *z* to replica 2. Now *z* *is dependent* on *y* because client 2 was aware of *y* and *z* was meaningfully written after *y*.  
  
Thus, our definition of causality is defined entirely around what events a client witnesses.
  
![](ex1.png)
  
#### Casuality basics:
1. A vector clock V = {(r1, t1),(r2,t2),(r3,t3),...}
    - Let's define our vector clocks:
    - Only 'put' operations count as events
    - Merging, comparing, adding new events like in class: monotonically increasing, ordering over ints, piece-wise max merge
2. Each data item on a replica has:
    - The current value associated with the key
    - Vector clock timestamp of most recent update / 'put' operation
    - A causal history H = {h1, h2, h3,...} where each h(i) = (k, v)
        - k = a key of a data item
        - v = the timestamp of an update associated with that key; timestamp is a vector of the form described in (1)
3. Client Context:
    - VectorClock high_time ==> replicas can use clients to piggback the current time and update accordingly
    - History context
  
**Example of replicas piggbacking current_time on clients:**
  
![](piggy.png "By piggbacking the highest known Vclock on a GET request from client 2 to replica 3, replica 3 can update its Vclock -- 
                Pretty much, it helps us break ties between events that would be considered concurrent")  
   
#### Causality and consistency (updated and more specific) ==> see classes below for implementation details:
1. Each replica stores the current time as a VectorClock, this is the most basic unit we need so that we know when events are occuring
2. Each replica has a dictionary with all of its key:value pairs (exactly the same as asgn2)
3. Each replica has another dictionary with the causal histories of all the keys:
   - Indexing by key
   - The value associated with the key is a History object
   - includes most recent timestamp of the key itself
   - Dictionary ==> makes it very easy to access elements, make comparisons, updates, etc
4. Each replica has a dictionary 'between-gossip-hist', which stores the history of events since the last gossip
   - don't necessarily need to store this -> could just store list of updated keys and then grab the histories
     from the dictionary in (3)
   - Makes it easy and more efficient to compare and merge histories during gossip
   - Can be deleted after gossip if **all replicas** received it
   - Indexed by key
   - Value associated with key is a History object
5. When keys are PUT:
   - the replicas vectorClock must be updated
   - the key must be assigned a causal history equal to the replica's history
   - the event must be added to the between-gossip-history
   - context updated and returned to client 
6. The causal context returned to the client is dictionary of Key:History pairs
   - causal context returned is the incoming client context + new PUT event
7. When keys are read:
   - NACK if client_History has a more recent x than the replica has
   - otherwise, client_History.merge(x History)
   - return client_History
  
**Concurrency example**

![](concurrent_ex.png)  
   
#### Comparing Vector Clocks
Let a VectorClock V = {(r1,t1),(v2,t2)...}  
Let A and B be two Vector Clocks
1. A < B if every t(i) in A is less than or equal to every t(i) in B
2. A > B if every t(i) in A is greater than or equal to every t(i) in B
3. A & B are concurrent if neither (1) nor (2) are satisifed
    - Example: Some t(i) in A are greater than B and some t(i) in A are less than B
   
#### Matt's Question: How to compare per-item histories?
Honestly, it's been a while and I don't fully remember the context or meaning of the question but I'm assuming the question
is referring to comparisons during gossip:
1. Comparing during gossip:
    - it seems like we can just compare the time of the most recent update for a key and set the history equal to that
    of the most recent key (history should be fully expressed in history of most recent key) --> 
    so no history-comparisons necessary --> am i wrong??
  
#### Returning values to clients:
Values can be returned to a client if the timestamp associated with a key is >= the timestamp associated with that item in the client context.  
Clients are not permitted to read concurrent writes!
  
#### Putting values:
It is always safe for clients to PUT values
  
#### Client Context objects
{"high_clock":VectorClock,"history":History, "current_view":*int*}
1. GET/PUTs must check value of current_view
    - if current_view < self.current_view:
        - clear the client's context and set there new current_view value equal to ours
        - GET/PUT is safe because the client's history is dependent on a previous view

#### Visual Intuition / Example run:
  
![](cons.png)  
  
#### High Level overview
```
class VectorClock {
    __init__(self, [address1, address2,...]):
        /* 
          initialization requires addresses of replicas (passed in as list)
        */
        self.clock = {"address":time,...}   # one entry for every replica (in local shard)
        
    compare(VectorClock v1, VectorClock v2):
        /* Return 1 if v1 > v2
           Return 0 if v1 & v2 are concurrent
           Return -1 if v1 < v2 */
        
    merge(self, VectorClock):
        /* Do piece-wise-max merge of the vectors,
           update self.clock to that */
           
    increment(self, address):
        /* increment the time associated with repl_ID */
        if address not in time_vector:
            reject update
        self.clock[address] += 1
        
}
```

```
class History {
    /* 
        A history is a collection of events, stored as a dictionary and represented as:
            {"key1":VectorClock, "key2":VectorClock,...}
        
        Purpose:
                 This class will be used to hold the casual history of each data item:
                      Track per-item history
                      
                 Furthermore, it will be used to hold a temporary between-gossip history:
                      When new events occur on a replica, they are added to 
                      the between-gossip history. This
                      way, we can just send the between-gossip history during gossip and
                      use merge() to merge incoming between-gossip-histories with ours.
                      Then, delete between-gossip history (only if all replicas received
                      them though)
    */
    
    __init__(self):
        self.hist = {(key, vector_clock),...}
        
    insert(self, key, VectorClock):
        /* insert a new key and its VectorClock timestamp 
           if the key already exists -> override it with the new value
        */
        
    merge(self, foreign_hist):
        /* 
        Desc: merge the incoming causal history with our local one
        
        Parameter: a History object
        
        What it Does:
            For every event in foreign_hist, update the corresponding key 
            in self.hist if the key in foreign_hist has a more recent timestamp.
            HINT: you can call VectorClock.compare() to figure this out.
            
        Return Value:
            Return a list of all keys that were replaced
                # This is important bc it will tell us which keys in kvs must be updated as well
        */
}
```

```
class Replica {
    /*
        This class isn't really new, but an extension of the node class (new things we need)
    */
    __init__(self, nodes[], repl_factor):

        self.view = [addr1,addr2...]  # list of all nodes in system -- sorted

        self.shards[i][j] = addr of replica j in shard i

        self.this_shard = id of the shard this node is in (an integer)

        self.local_kvs = {}         # the kvs dict of this node

        self.per_item_history = {key:History,key2:History....}  # {key : History} -- History contains all
                                                                # dependencies of 'key' (including most 
                                                                # recent timestamp of 'key')

        self.between_gossip_updates = History()   # keys and their most recent updates (since last gossip)

        self.cur_time = VectorClock               # time of last event of this replica

        self.replica_alive = []   # boolean values corresponding to whether a replica is alive
                                  # a self.shard[i] will always have replica shards in sorted
                                  # order, so you can use those indexes to also index this list

}
```
  
```
    handle PUT requests for item x on replica r:
        1. update r.cur_time with merge of r.cur_time and highest timestamp in client_context
        2. update(x), set time(x) to cur_time, update x's dependencies with client_context
        3. return SUCCESS, client_history.insert(x, time(x))
        
        
    handle GET requests for item x on replica r:
        1. update r.cur_time with merge of r.cur_time and highest timestamp in client_context
        2. if there is no timestamp(x) in client_context > r.timestamp(x):
              a. return x, client_history.merge(history(x))
        3. else: return NACK
        
        
    handle GET key-count for a node:
        1. return len(kvs), shard-id ==> how do we do this though? --> replicas may not be consistent yet / have diff # keys
        
        
    handle GET ID for a shard:
        /* what is a shard ID in the spec? essentially just asking shard count? */
        
        
    handle GET info for a specific shard:
        /* how to return an accurate key count if 
           one replica has more items than another?
           Should we make them consistent first?   */
           
           
    handle view change:
        /* alterations need to be made to asgn2 algorithm -
           it is guaranteed that all nodes are alive when a 
           view change request is issued, so we can temporarily
           elect a 'shard leader' and it is trivially guaranteed
           that all nodes will agree on who the leaders are
           based on our method of assigning shard and replica IDs
           -- by using a shard leader, the shard leaders can communicate
           and exchange keys on behalf of the entire shard */
        
        0. If the receiving node is not the leader of his shard, he forwards
           the request to his shard leader and acts as a proxy for the response
        1. view change leader tells other shard leaders to collect all
           their keys
           a. shard leaders get keys from all other replicas to assemble the
              full kvs of the shard
        2. view change leader gives the new view to every node in the system
        3. view change leader tells the shard leaders to rehash their keys
        4. view change leader rehashes his own keys and broadcasts them
        5. view change leader tells other shards to broadcast their data
           and waits for all shard leaders to ack him
        6. view change leader tells new shard leaders to distribute their
           keys to all replicas in their shard
        7. view change leader gives his keys to other replicas in his shard
        8. view change leader acks the client
```
   
## Replication Mechanics
--- 
### Nitty Gritty
#### Data Structures
- Vector clocks
  - ``` {address1: int, address2: int}```
- Shards
  - relevant with view change and other per-node view awareness
  - PUT request for vc now contains data:
    - new view (list of addresses)
    - replication factor
  - Seems like it's our own call how to split up these addresses into shards
    - --data      '{"view":"10.10.0.2:13800,10.10.0.3:13800,10.10.0.4:13800,10.10.0.5:13800","repl-factor":2}'
    - Most likely [2,3,4,5] --> [2,3],[4,5]
  - Configure view during 'prime' phase
    - configure_view()
      - chop up the view into shards, determine my shard + my node
        - (populate global vars accordingly)
      - populate shards
      - init replica status dead/alive
      - init vector clock if none exists (new node in view)
  - old view was just a list of addresses
    - = [address1, address2, ...]
  - new view should probably be a **list of lists**
      ```
      shards = [
              [address1, address2], # nodes on shard 0
              [address3, address4], # nodes on shard 1
              ...                   # nodes on shard n -1
          ]

      self.this_shard = 1
      # access replica address list ~ shards[self.this_shard]
      ```

### Gossip:
 **Summary**
1. Compile a list of keys that were updated since last gossip + their values (items_to_update)
2. Init dictionaries to store responses from gossip + throw our own data in there to compare later
   ```
   history_responses = { 
       address: {
           key : timestamp of last update (vc),
           ...
       },
       ...
    }

   items_responses = { 
       address: {
           key : value from last update,
           ...
       },
       ...
    }

   vc_responses = { 
       address: vector clock
       ...
    }
   ```
3. For every address in my shard except mine, send a gossip message
   - contains items_to_update, my per-item history, & my vector clock
4. If i get no response, mark the replica as dead
5. Else, add their response (contains *their* items, history, & vector clock) to the dicts we init'd earlier (2)
6. Take history_responses and items_responses and find the most current values and their timestamps
7. Use ther return from (6) to update our own kvs and per-item-history
8. Update our'general' vector clock by looping through  vc_responses
9. IF WE DARE...
   -   we can clear out current_events []
   -   this was a global list of all the keys that have been modified since last gossip
       -   we track this so we dont send our **entire** kvs during gossip, only relevant items

