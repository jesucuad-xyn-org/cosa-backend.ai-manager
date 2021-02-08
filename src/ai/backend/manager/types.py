import enum
from typing import (
    Protocol,
)

import sqlalchemy as sa
from aiopg.sa.connection import SAConnection


class SessionGetter(Protocol):

    def __call__(self, *, db_connection: SAConnection) -> sa.engine.RowProxy:
        ...


# Sentinel is a special object that indicates a special status instead of a value
# where the user expects a value.
# According to the discussion in https://github.com/python/typing/issues/236,
# we define our Sentinel type as an enum with only one special value.
# This enables passing of type checks by "value is sentinel" (or "value is Sentinel.token")
# instead of more expensive "isinstance(value, Sentinel)" because we can assure type checkers
# to think there is no other possible instances of the Sentinel type.

class Sentinel(enum.Enum):
    token = 0
