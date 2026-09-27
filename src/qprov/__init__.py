"""Compatibility alias: `qprov` was renamed to `claimtrail`.

`import qprov` and `from qprov.store import Store` keep working. New code
should import `claimtrail`.
"""
import importlib
import sys

import claimtrail
from claimtrail import *  # noqa: F401,F403
from claimtrail import __all__, __version__  # noqa: F401
from claimtrail import (  # noqa: F401
    QprovCollisionError, QprovFileMissingError, QprovHashError, QprovHashWarning,
    QprovPropertyError, QprovPropertyWarning, QprovTraversalError,
)

for _name in (
    "audit_paper", "claims", "cli", "external", "gitinfo", "hardware", "inputs",
    "properties", "properties_qnumbers", "query", "serialize", "store",
    "tracking", "verify",
):
    sys.modules[f"qprov.{_name}"] = importlib.import_module(f"claimtrail.{_name}")
