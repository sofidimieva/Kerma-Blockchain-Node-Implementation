from Peer import Peer
import constants as const
from message.msgexceptions import *
from jcs import canonicalize

import mempool
import objects
import peer_db
import object_db

import asyncio
import ipaddress
import json
import random
import re
import sqlite3
import sys

PEERS = set()
CONNECTIONS = dict()
BACKGROUND_TASKS = set()
BLOCK_VERIFY_TASKS = dict()
BLOCK_WAIT_LOCK = None
TX_WAIT_LOCK = None
MEMPOOL = mempool.Mempool(const.GENESIS_BLOCK_ID, {})
LISTEN_CFG = {
        "address": const.ADDRESS,
        "port": const.PORT
}

# Add peer to your list of peers
def add_peer(peer):
    # Do not add banned peer addresses
    if peer.host in const.BANNED_HOSTS:
        return

    # Do not add loopback or multicast addrs
    # TODO

    peer_db.store_peer(peer, PEERS)
    PEERS.add(peer)

# Add connection if not already open
def add_connection(peer, queue):
    ip, port = peer

    p = Peer(ip, port)
    if p in CONNECTIONS:
        raise Exception("Connection with {} already open!".format(peer))

    CONNECTIONS[p] = queue

# Delete connection
def del_connection(peer):
    ip, port = peer
    del CONNECTIONS[Peer(ip, port)]

# Make msg objects
def mk_error_msg(error_str, error_name):
    return {"type": "error", "name": error_name, "msg": error_str}

def mk_hello_msg():
    return {"type": "hello", "version": const.VERSION, "agent": const.AGENT}

def mk_getpeers_msg():
    return {"type": "getpeers"}

def mk_peers_msg():
    pl = [f'{peer}' for peer in PEERS]
    if len(pl) > 29:
        pl = [f'{LISTEN_CFG["address"]}:{LISTEN_CFG["port"]}'] + random.sample(pl, 29)
    return {"type": "peers", "peers": pl}

def mk_getobject_msg(objid):
    return {"type": "getobject", "objectid": objid}

def mk_object_msg(obj_dict):
    return {"type": "object", "object": obj_dict}

def mk_ihaveobject_msg(objid):
    return {"type": "ihaveobject", "objectid": objid}

def mk_chaintip_msg(blockid):
    pass # TODO

def mk_mempool_msg(txids):
    pass # TODO

def mk_getchaintip_msg():
    pass # TODO

def mk_getmempool_msg():
    pass # TODO

# parses a message as json. returns decoded message
def parse_msg(msg_str):
    try:
        msg = json.loads(msg_str)
    except Exception as e:
        raise ErrorInvalidFormat("JSON parse error: {}".format(str(e)))

    if not isinstance(msg, dict):
        raise ErrorInvalidFormat("Received message not a dictionary!")
    if not 'type' in msg:
        raise ErrorInvalidFormat("Key 'type' not set in message!")
    if not isinstance(msg['type'], str):
        raise ErrorInvalidFormat("Key 'type' is not a string!")

    return msg

# Send data over the network as a message
async def write_msg(writer, msg_dict):
    msg_bytes = canonicalize(msg_dict)
    writer.write(msg_bytes)
    writer.write(b'\n')
    await writer.drain()

# Check if message contains no invalid keys,
# raises a MalformedMsgException
def validate_allowed_keys(msg_dict, allowed_keys, msg_type):
    if len(set(msg_dict.keys()) - set(allowed_keys)) != 0:
        raise ErrorInvalidFormat(
            "Message malformed: {} message contains invalid keys!".format(msg_type))


# Validate the hello message
# raises an exception
def validate_hello_msg(msg_dict):
    if msg_dict['type'] != 'hello':
        raise ErrorInvalidHandshake("Message type is not 'hello'!")

    try:
        if 'version' not in msg_dict:
            raise ErrorInvalidFormat(
                "Message malformed: version is missing!")

        version = msg_dict['version']
        if not isinstance(version, str):
            raise ErrorInvalidFormat(
                "Message malformed: version is not a string!")

        if not re.compile('0\.10\.\d').fullmatch(version):
            raise ErrorInvalidFormat(
                "Version invalid")

        validate_allowed_keys(msg_dict, ['type', 'version', 'agent'], 'hello')
    except ErrorInvalidFormat as e:
        raise e

    except Exception as e:
        raise ErrorInvalidFormat("Message malformed: {}".format(str(e)))


# returns true iff host_str is a valid hostname
def validate_hostname(host_str):
    return re.match(r'^(?=.*[a-zA-Z])[a-zA-Z\d\.\-\_]{3,50}$', host_str) and '.' in host_str[1:-1]

# returns true iff host_str is a valid ipv4 address
def validate_ipv4addr(host_str):
    try:
        ipaddress.IPv4Address(host_str)
        return True
    except ipaddress.AddressValueError:
        return False

# returns true iff peer_str is a valid peer address
def validate_peer_str(peer_str):
    try:
        host, port = peer_str.split(':')
    except:
        return False
    if int(port)<1 or int(port)> 65535:
        return False
    return (validate_hostname(host) or validate_ipv4addr(host))

# raise an exception if not valid
def validate_peers_msg(msg_dict):
    try:
        if 'peers' not in msg_dict:
            raise ErrorInvalidFormat("Message malformed: peers is missing!")

        peers = msg_dict['peers']
        if not isinstance(peers, list):
            raise ErrorInvalidFormat(
                "Message malformed: peers is not a list!")

        validate_allowed_keys(msg_dict, ['type', 'peers'], 'peers')

        if len(msg_dict['peers']) > 30:
            raise ErrorInvalidFormat('Too many peers in peers msg')

        for p in peers:
            if not isinstance(p, str):
                raise ErrorInvalidFormat(
                    "Message malformed: peer is not a string!")

            if not validate_peer_str(p):
                raise ErrorInvalidFormat(
                    "Message malformed: peer does not have a valid format address:host!")

    except ErrorInvalidFormat as e:
        raise e
    except Exception as e:
        raise ErrorInvalidFormat("Message malformed: {}".format(str(e)))

# raise an exception if not valid
def validate_getpeers_msg(msg_dict):
    if msg_dict['type'] != 'getpeers':
        raise ErrorInvalidFormat("Message type is not 'getpeers'!")

    validate_allowed_keys(msg_dict, ['type'], 'getpeers')

# raise an exception if not valid
def validate_getchaintip_msg(msg_dict):
    pass # TODO

# raise an exception if not valid
def validate_getmempool_msg(msg_dict):
    pass # TODO

# raise an exception if not valid
def validate_error_msg(msg_dict):
    pass # TODO

# raise an exception if not valid
def validate_ihaveobject_msg(msg_dict):
    if sorted(list(msg_dict.keys())) != sorted(['type', 'objectid']):
        raise ErrorInvalidFormat("Message malformed: ihaveobject message contains invalid keys!")

# raise an exception if not valid
def validate_getobject_msg(msg_dict):
    if sorted(list(msg_dict.keys())) != sorted(['type', 'objectid']):
        raise ErrorInvalidFormat("Message malformed: getobject message contains invalid keys!")

# raise an exception if not valid
def validate_object_msg(msg_dict):
    if sorted(list(msg_dict.keys())) != sorted(['type', 'object']):
        raise ErrorInvalidFormat("Message malformed: object message contains invalid keys!")

# raise an exception if not valid
def validate_chaintip_msg(msg_dict):
    pass # todo
    
# raise an exception if not valid
def validate_mempool_msg(msg_dict):
    pass # todo
        
def validate_msg(msg_dict):
    msg_type = msg_dict['type']
    if msg_type == 'hello':
        validate_hello_msg(msg_dict)
    elif msg_type == 'getpeers':
        validate_getpeers_msg(msg_dict)
    elif msg_type == 'peers':
        validate_peers_msg(msg_dict)
    elif msg_type == 'getchaintip':
        validate_getchaintip_msg(msg_dict)
    elif msg_type == 'getmempool':
        validate_getmempool_msg(msg_dict)
    elif msg_type == 'error':
        validate_error_msg(msg_dict)
    elif msg_type == 'ihaveobject':
        validate_ihaveobject_msg(msg_dict)
    elif msg_type == 'getobject':
        validate_getobject_msg(msg_dict)
    elif msg_type == 'object':
        validate_object_msg(msg_dict)
    elif msg_type == 'chaintip':
        validate_chaintip_msg(msg_dict)
    elif msg_type == 'mempool':
        validate_mempool_msg(msg_dict)
    else:
        raise ErrorInvalidFormat("Message type {} not valid!".format(msg_type))


def handle_peers_msg(msg_dict):
    for p in msg_dict['peers']:
        peer_parts = p.rsplit(':', 1)

        host_str, port_str = peer_parts

        port = int(port_str, 10)

        peer = Peer(host_str, port)
        add_peer(peer)


def handle_error_msg(msg_dict, peer_self):
    print("{}: Received error of type {}: {}".format(peer_self, msg_dict['name'], msg_dict['msg']))


async def handle_ihaveobject_msg(msg_dict, writer):
    object_id = msg_dict['objectid']

    if not object_db.object_exists(object_id):
        await write_msg(writer, mk_getobject_msg(object_id))


async def handle_getobject_msg(msg_dict, writer):
    object_id = msg_dict['objectid']

    obj_dict = object_db.fetch_object(object_id)
    # if object exists, send it
    if obj_dict:
        await write_msg(writer, mk_object_msg(obj_dict))
    else:
        await write_msg(writer, mk_error_msg("Object with id {} not found".format(object_id), "OBJECT_NOT_FOUND"))

# return a list of transactions that tx_dict references
def gather_previous_txs(db_cur, tx_dict):
    # coinbase transaction
    if 'height' in tx_dict:
        return {}

    pass # TODO

# get the block, the current utxo and block height
def get_block_utxo_height(blockid):
    # TODO
    block = ''
    utxo = ''
    height = ''
    return (block, utxo, height)

# get all transactions as a dict txid -> tx from a list of ids
def get_block_txs(txids):
    pass # TODO


# Stores for a block its utxoset and height
def store_block_utxo_height(block, utxo, height: int):
    pass # TODO

# runs a task to verify a block
# raises blockverifyexception
async def verify_block_task(block_dict):
    pass # TODO

# adds a block verify task to queue and starting it
def add_verify_block_task(objid, block, queue):
    pass # TODO

# abort a block verify task
async def del_verify_block_task(task, objid):
    pass # TODO

# what to do when an object message arrives
async def handle_object_msg(msg_dict, peer_self, writer):
    print("Received Object message")
    object_dict = msg_dict['object']
    if not objects.validate_object(object_dict):
        raise ErrorInvalidFormat("Received object is not valid!")

    print('Get object id')
    object_id = objects.get_objid(object_dict)
    if object_db.object_exists(object_id):
        return
    
    print('Storing object with id {} in db'.format(object_id))
    # store object in db
    object_db.store_object(object_id, object_dict)

    # gossip the object to all connected peers
    for queue in CONNECTIONS.values():
        await queue.put(mk_ihaveobject_msg(object_id))
    

# returns the chaintip blockid
def get_chaintip_blockid():
    pass # TODO


async def handle_getchaintip_msg(msg_dict, writer):
    pass # TODO


async def handle_getmempool_msg(msg_dict, writer):
    pass # TODO


async def handle_chaintip_msg(msg_dict):
    pass # TODO


async def handle_mempool_msg(msg_dict):
    pass # TODO

# Helper function
async def handle_queue_msg(msg_dict, writer):
    await write_msg(writer, msg_dict)

# how to handle a connection
async def handle_connection(reader, writer):
    read_task = None
    queue_task = None

    peer = None
    queue = asyncio.Queue()
    try:
        peer = writer.get_extra_info('peername')
        if not peer:
            raise Exception("Failed to get peername!")
        
        add_connection(peer, queue)

        print("New connection with {}".format(peer))
    except Exception as e:
        print(str(e))
        try:
            writer.close()
        except:
            pass
        return

    try:
        # Send initial messages
        await write_msg(writer, mk_hello_msg())
        await write_msg(writer, mk_getpeers_msg())
        
        # Complete handshake
        firstmsg_str = await asyncio.wait_for(reader.readline(),
                timeout=const.HELLO_MSG_TIMEOUT)
        firstmsg = parse_msg(firstmsg_str)
        validate_hello_msg(firstmsg)

        msg_str = None
        while True:
            if read_task is None:
                read_task = asyncio.create_task(reader.readline())
            if queue_task is None:
                queue_task = asyncio.create_task(queue.get())

            # wait for network or queue messages
            done, pending = await asyncio.wait([read_task, queue_task],
                    return_when = asyncio.FIRST_COMPLETED)
            if read_task in done:
                msg_str = read_task.result()
                read_task = None
            # handle queue messages
            if queue_task in done:
                queue_msg = queue_task.result()
                queue_task = None
                await handle_queue_msg(queue_msg, writer)
                queue.task_done()

            # if no message was received over the network continue
            if read_task is not None:
                continue

            try:

                msg = parse_msg(msg_str)
                validate_msg(msg)

                msg_type = msg['type']
                if msg_type == 'hello':
                    raise ErrorInvalidHandshake("Additional handshake initiated by peer!")
                elif msg_type == 'getpeers':
                    await write_msg(writer, mk_peers_msg())
                elif msg_type == 'peers':
                    handle_peers_msg(msg)
                elif msg_type == 'error':
                    handle_error_msg(msg, peer)
                elif msg_type == 'ihaveobject':
                    await handle_ihaveobject_msg(msg, writer)
                elif msg_type == 'getobject':
                    await handle_getobject_msg(msg, writer)
                elif msg_type == 'object':
                    await handle_object_msg(msg, peer, writer)
                elif msg_type == 'getchaintip':
                    await handle_getchaintip_msg(msg, writer)
                elif msg_type == 'chaintip':
                    await handle_chaintip_msg(msg)
                elif msg_type == 'getmempool':
                    await handle_getmempool_msg(msg, writer)
                elif msg_type == 'mempool':
                    await handle_mempool_msg(msg)
                else:
                    pass # assert: false
            except NonfaultyNodeException as e:
                print("{}: An error occured: {}: {}".format(peer, e.error_name, e.message))
                await write_msg(writer, mk_error_msg(e.message, e.error_name))

    except asyncio.exceptions.TimeoutError:
        print("{}: Timeout".format(peer))
        try:
            await write_msg(writer, mk_error_msg("INVALID_HANDSHAKE", "Timeout"))
        except:
            pass
    except FaultyNodeException as e:
        peer_db.remove_peer(peer)
        print("{}: Detected Faulty Node: {}: {}".format(peer, e.error_name, e.message))
        try:
            await write_msg(writer, mk_error_msg(e.message, e.error_name))
        except:
            pass
    except Exception as e:
        print("{}: An error occured: {}".format(peer, str(e)))
    finally:
        print("Closing connection with {}".format(peer))
        writer.close()
        del_connection(peer)
        if read_task is not None and not read_task.done():
            read_task.cancel()
        if queue_task is not None and not queue_task.done():
            queue_task.cancel()


async def connect_to_node(peer: Peer):
    try:
        reader, writer = await asyncio.open_connection(peer.host, peer.port,
                limit=const.RECV_BUFFER_LIMIT)
    except Exception as e:
        print(f"failed to connect to peer {peer.host}:{peer.port}: {str(e)}")

        # remove this peer from your known peers, unless this is a bootstrap peer
        if not peer.isBootstrap:
            peer_db.remove_peer(peer)

        return

    await handle_connection(reader, writer)


async def listen():
    server = await asyncio.start_server(handle_connection, LISTEN_CFG['address'],
            LISTEN_CFG['port'], limit=const.RECV_BUFFER_LIMIT)

    print("Listening on {}:{}".format(LISTEN_CFG['address'], LISTEN_CFG['port']))

    async with server:
        await server.serve_forever()

# bootstrap peers. connect to hardcoded peers
async def bootstrap():
    for p in const.PRELOADED_PEERS:
        add_peer(p)
        t = asyncio.create_task(connect_to_node(p))
        BACKGROUND_TASKS.add(t)
        t.add_done_callback(BACKGROUND_TASKS.discard)

# connect to some peers
def resupply_connections():
    cons = set(CONNECTIONS.keys())

    if len(cons) >= const.LOW_CONNECTION_THRESHOLD:
        return

    npeers = const.LOW_CONNECTION_THRESHOLD - len(cons)
    available_peers = PEERS - cons

    if len(available_peers) == 0:
        print("Not enough peers available to reconnect.")
        return

    if len(available_peers) < npeers:
        npeers = len(available_peers)

    print("Connecting to {} new peers.".format(npeers))

    chosen_peers = random.sample(tuple(available_peers), npeers)
    for p in chosen_peers:
        t = asyncio.create_task(connect_to_node(p))
        BACKGROUND_TASKS.add(t)
        t.add_done_callback(BACKGROUND_TASKS.discard)


async def init():
    global BLOCK_WAIT_LOCK
    BLOCK_WAIT_LOCK = asyncio.Condition()
    global TX_WAIT_LOCK
    TX_WAIT_LOCK = asyncio.Condition()

    # PEERS.update(peer_db.load_peers())

    bootstrap_task = asyncio.create_task(bootstrap())
    listen_task = asyncio.create_task(listen())

    # Service loop
    while True:
        print("Service loop reporting in.")
        print("Open connections: {}".format(set(CONNECTIONS.keys())))

        # Open more connections if necessary
        resupply_connections()

        await asyncio.sleep(const.SERVICE_LOOP_DELAY)

    await bootstrap_task
    await listen_task


def main():
    object_db.create_db()
    asyncio.run(init())


if __name__ == "__main__":
    if len(sys.argv) == 3:
        LISTEN_CFG['address'] = sys.argv[1]
        LISTEN_CFG['port'] = sys.argv[2]

    main()
