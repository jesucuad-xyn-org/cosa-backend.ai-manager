from ipaddress import ip_address

from ai.backend.common.argparse import HostPortPair, host_port_pair, port_no
import configargparse


def load_config(argv=None, extra_args_funcs=()):
    parser = configargparse.ArgumentParser()
    parser.add('--namespace', env_var='BACKEND_NAMESPACE',
               type=str, default='local',
               help='The namespace of this Backend.AI cluster. (default: local)')
    parser.add('--etcd-addr', env_var='BACKEND_ETCD_ADDR',
               type=host_port_pair, metavar='HOST:PORT',
               default=HostPortPair(ip_address('127.0.0.1'), 2379),
               help='The host:port pair of the etcd cluster or its proxy.')
    parser.add('--docker-registry', env_var='BACKEND_DOCKER_REGISTRY',
               type=str, metavar='URL', default=None,
               help='The host:port pair of the private docker registry '
                    'that caches the kernel images')
    parser.add('--agent-port', env_var='BACKEND_AGENT_PORT',
               type=port_no, default=6001,
               help='The TCP port number where the agent instances are listening on.'
                    ' (default: 6001)')
    parser.add('--redis-addr', env_var='BACKEND_REDIS_ADDR', type=host_port_pair,
               default=HostPortPair(ip_address('127.0.0.1'), 6379),
               help='The hostname-port pair of a redis server. '
                    '(default: localhost:6379)')
    parser.add('--db-addr', env_var='BACKEND_DB_ADDR', type=host_port_pair,
               default=HostPortPair(ip_address('127.0.0.1'), 5432),
               help='The hostname-port pair of a database server. '
                    '(default: localhost:5432)')
    parser.add('--db-name', env_var='BACKEND_DB_NAME',
               type=str, default='sorna',
               help='The database name. (default: sorna)')
    parser.add('--db-user', env_var='BACKEND_DB_USER',
               type=str, default='postgres',
               help='The username to authenticate to the database server. '
                    '(default: postgres)')
    parser.add('--db-password', env_var='BACKEND_DB_PASSWORD',
               type=str, default='develove',
               help='The password to authenticate to the database server. '
                    '(default: develove)')
    for func in extra_args_funcs:
        func(parser)
    args = parser.parse_args(args=argv)
    return args
