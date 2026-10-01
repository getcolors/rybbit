"""Scoped public SSH identity selection; encrypted authority is library-owned."""
from .access import identity_args
from .compute import planning as rendered_only


def with_machine_key(opts):
    return opts
