#! /usr/bin/env python3

'''
The Sorna API Server

It routes the API requests to kernel agents in VMs and manages the VM instance pool.
'''

from sorna.proto.manager_pb2 import ManagerRequest, ManagerResponse
from sorna.proto.manager_pb2 import PING, PONG, CREATE, DESTROY, SUCCESS, INVALID_INPUT, FAILURE
from sorna.proto.agent_pb2 import AgentRequest, AgentResponse
from sorna.proto.agent_pb2 import HEARTBEAT, SOCKET_INFO
from .utils.protobuf import read_message, write_message
import argparse
import asyncio, aiozmq, zmq
from abc import ABCMeta, abstractmethod
import docker
from enum import Enum
import json
from namedlist import namedtuple, namedlist
import signal
import struct
import subprocess
from urllib.parse import urlparse
import uuid

KernelDriverTypes = Enum('KernelDriverTypes', 'local docker')

AgentPortRange = tuple(range(5002, 5010))

Instance = namedlist('Instance', [
    ('ip', None),
    ('docker_port', 2375), # standard docker daemon port
    ('tag', ''),
    ('max_kernels', 2),
    ('cur_kernels', 0),
    ('used_ports', None),
])
# VM instances should run a docker daemon using "-H tcp://0.0.0.0:2375" in DOCKER_OPTS.

Kernel = namedlist('Kernel', [
    ('instance', None),
    ('kernel_id', None),
    ('spec', 'python34'),  # later, extend this to multiple languages and setups
    ('agent_sock', None),
    ('stdin_sock', None),
    ('stdout_sock', None),
    ('stderr_sock', None),
    ('priv', None),
])

class KernelDriver(metaclass=ABCMeta):

    def __init__(self, loop=None):
        self.loop = loop if loop else asyncio.get_event_loop()

    @asyncio.coroutine
    @abstractmethod
    def find_avail_instance(self):
        raise NotImplementedError()

    @asyncio.coroutine
    @abstractmethod
    def create_kernel(self, instance):
        '''
        Launches the kernel and return its ID.
        '''
        raise NotImplementedError()

    @asyncio.coroutine
    @abstractmethod
    def destroy_kernel(self, kernel_id):
        raise NotImplementedError()

    @asyncio.coroutine
    def ping_kernel(self, kernel_id):
        kernel = kernel_registry[kernel_id]
        sock = yield from aiozmq.create_zmq_stream(zmq.REQ, connect=kernel.agent_sock, loop=self.loop)
        req_id = str(uuid.uuid4())
        req = AgentRequest()
        req.req_type = HEARTBEAT
        req.body = req_id
        sock.write([req.SerializeToString()])
        try:
            resp_data = yield from asyncio.wait_for(sock.read(), timeout=2.0, loop=self.loop)
            resp = AgentResponse()
            resp.ParseFromString(resp_data[0])
            return (resp.body == req_id)
        except asyncio.TimeoutError:
            return False
        finally:
            sock.close()

    @asyncio.coroutine
    def get_socket_info(self, kernel_id):
        kernel = kernel_registry[kernel_id]
        sock = yield from aiozmq.create_zmq_stream(zmq.REQ, connect=kernel.agent_sock, loop=self.loop)
        req = AgentRequest()
        req.req_type = SOCKET_INFO
        req.body = ''
        sock.write([req.SerializeToString()])
        resp_data = yield from sock.read()
        resp = AgentResponse()
        resp.ParseFromString(resp_data[0])
        sock_info = json.loads(resp.body)
        kernel.stdin_sock = sock_info['stdin']
        kernel.stdout_sock = sock_info['stdout']
        kernel.stderr_sock = sock_info['stderr']
        sock.close()

class DockerKernelDriver(KernelDriver):

    @asyncio.coroutine
    def find_avail_instance(self):
        for instance in instance_registry.values():
            if instance.cur_kernels < instance.max_kernels:
                instance.cur_kernels += 1
                return instance
        return None

    @asyncio.coroutine
    def create_kernel(self, instance):
        # TODO: refactor instance as a separate class
        if instance.used_ports is None: instance.used_ports = set()
        agent_port = 0
        assert instance.max_kernels <= len(AgentPortRange)
        for p in AgentPortRange:
            if p not in instance.used_ports:
                instance.used_ports.add(p)
                agent_port = p
                break
        assert agent_port != 0

        cli = docker.Client(
            base_url='tcp://{0}:{1}'.format(instance.ip, instance.docker_port),
            timeout=5, version='auto'
        )
        # TODO: create the container image
        # TODO: change the command to "python3 -m sorna.kernel_agent"
        # TODO: pass agent_port
        container = cli.create_container(image='lablup-python-kernel:latest',
                                         command='/usr/bin/python3')
        kernel = Kernel(instance=instance, kernel_id=container.id)
        kernel.priv = container.id
        kernel.kernel_id = 'docker-{0}/{1}'.format(instance.ip, kernel.priv)
        kernel.agent_sock = 'tcp://{0}:{1}'.format(instance.ip, agent_port)
        # TODO: run the container and set the port mappings
        kernel_registry[kernel.kernel_id] = kernel
        return kernel_id

    @asyncio.coroutine
    def destroy_kernel(self, kernel_id):
        kernel = kernel_registry[kernel_id]
        kernel.instance.cur_kernels -= 1
        assert(kernel.instance.cur_kernels >= 0)
        agent_url = urlparse(kernel.agent_sock)
        kernel.instance.used_ports.remove(agent_url.port)
        # TODO: destroy the container
        del kernel_registry[kernel_id]
        raise NotImplementedError()


class LocalKernelDriver(KernelDriver):

    @asyncio.coroutine
    def find_avail_instance(self):
        for instance in instance_registry.values():
            if instance.ip != '127.0.0.1':
                continue
            if instance.cur_kernels < instance.max_kernels:
                instance.cur_kernels += 1
                return instance
        return None

    @asyncio.coroutine
    def create_kernel(self, instance):
        if instance.used_ports is None: instance.used_ports = set()
        agent_port = 0
        assert instance.max_kernels < len(AgentPortRange)
        for p in AgentPortRange:
            if p not in instance.used_ports:
                instance.used_ports.add(p)
                agent_port = p
                break
        assert agent_port != 0

        unique_id = str(uuid.uuid4())
        kernel_id = 'local/{0}'.format(unique_id)
        kernel = Kernel(instance=instance, kernel_id=unique_id)
        cmdargs = ('/usr/bin/python3', '-m', 'sorna.kernel_agent',
                   '--kernel-id', kernel_id, '--agent-port', str(agent_port))
        proc = yield from asyncio.create_subprocess_exec(*cmdargs, loop=self.loop)
        kernel.kernel_id = kernel_id
        kernel.agent_sock = 'tcp://{0}:{1}'.format(instance.ip, agent_port)
        kernel.priv = proc
        kernel_registry[kernel_id] = kernel
        return kernel_id

    @asyncio.coroutine
    def destroy_kernel(self, kernel_id):
        kernel = kernel_registry[kernel_id]
        kernel.instance.cur_kernels -= 1
        assert(kernel.instance.cur_kernels >= 0)
        proc = kernel.priv
        proc.terminate()
        yield from proc.wait()
        agent_url = urlparse(kernel.agent_sock)
        kernel.instance.used_ports.remove(agent_url.port)
        del kernel_registry[kernel_id]


# Module states

kernel_driver = KernelDriverTypes.docker
instance_registry = {
    'test': Instance(ip='127.0.0.1')
}
kernel_registry = dict()


# Module functions

@asyncio.coroutine
def handle_api(loop, server):
    while True:
        req_data = yield from server.read()
        req = ManagerRequest()
        req.ParseFromString(req_data[0])
        resp = ManagerResponse()

        if kernel_driver == KernelDriverTypes.docker:
            driver = DockerKernelDriver(loop)
        elif kernel_driver == KernelDriverTypes.local:
            driver = LocalKernelDriver(loop)
        else:
            assert False, 'Should not reach here.'

        if req.action == PING:

            resp.reply     = PONG
            resp.kernel_id = ''
            resp.body      = req.body

        elif req.action == CREATE:

            instance = yield from driver.find_avail_instance()
            if instance is None:
                resp.reply     = FAILURE
                resp.kernel_id = ''
                resp.body      = 'No instance is available to launch a new kernel.'
                server.write([resp.SerializeToString()])
                return
            kernel_id = yield from driver.create_kernel(instance)

            yield from asyncio.sleep(0.2, loop=loop)
            tries = 0
            print('Checking if the kernel is up...')
            while tries < 5:
                success = yield from driver.ping_kernel(kernel_id)
                if success:
                    break
                else:
                    print('  retrying after 1 sec...')
                    yield from asyncio.sleep(1, loop=loop)
                    tries += 1
            else:
                resp.reply     = FAILURE
                resp.kernel_id = ''
                resp.body      = 'The created kernel did not respond!'
                server.write([resp.SerializeToString()])
                return

            yield from driver.get_socket_info(kernel_id)

            # TODO: restore the user module state?

            kernel = kernel_registry[kernel_id]
            resp.reply     = SUCCESS
            resp.kernel_id = kernel_id
            resp.body      = json.dumps({
                'agent_sock': kernel.agent_sock,
                'stdin_sock': None,
                'stdout_sock': kernel.stdout_sock,
                'stderr_sock': kernel.stderr_sock,
            })

        elif req.action == DESTROY:

            if req.kernel_id in kernel_registry:
                yield from driver.destroy_kernel(req.kernel_id)
                resp.reply = SUCCESS
                resp.kernel_id = req.kernel_id
                resp.body = ''
            else:
                resp.reply = INVALID_INPUT
                resp.kernel_id = ''
                resp.body = 'No such kernel.'

        server.write([resp.SerializeToString()])

def handle_exit():
    loop.stop()

def main():
    global kernel_driver, instance_registry, kernel_registry
    argparser = argparse.ArgumentParser()
    argparser.add_argument('--kernel-driver', default='docker', choices=('docker', 'local'))
    args = argparser.parse_args()

    kernel_driver = KernelDriverTypes[args.kernel_driver]

    asyncio.set_event_loop_policy(aiozmq.ZmqEventLoopPolicy())
    loop = asyncio.get_event_loop()
    server = loop.run_until_complete(aiozmq.create_zmq_stream(zmq.REP, bind='tcp://*:5001', loop=loop))
    print('Started serving... (driver: {0})'.format(args.kernel_driver))
    loop.add_signal_handler(signal.SIGTERM, handle_exit)
    # TODO: add a timer loop to check heartbeats and reclaim kernels unused for long time.
    try:
        asyncio.async(handle_api(loop, server), loop=loop)
        loop.run_forever()
    except KeyboardInterrupt:
        print()
        pass
    server.close()
    loop.close()
    print('Exit.')

if __name__ == '__main__':
    main()
