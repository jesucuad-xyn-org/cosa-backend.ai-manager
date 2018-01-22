from ai.backend.gateway.config import load_config
from ai.backend.gateway.logging import Logger
from ai.backend.manager import cli

resolved_command_classes = {}


def init_app_args(parser):
    cli.global_argparser = parser

    import ai.backend.manager.cli.fixture   # noqa
    import ai.backend.manager.cli.dbschema  # noqa
    import ai.backend.manager.cli.shell     # noqa
    import ai.backend.manager.cli.etcd      # noqa


config = load_config(extra_args_funcs=(init_app_args, Logger.update_log_args))
logger = Logger(config)
with logger:
    config.function(config)
