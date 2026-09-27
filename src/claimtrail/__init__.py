"""claimtrail - every claim in a report keeps a trail back to the computation that made it.

Public API:
    @tracked              decorate a function to record every call
    claim(...)            register a numerical claim linked to a computation
    find(...)             query computations by tags / function / dates
    get(id)               fetch a single computation by id
    export_latex(...)     render claims to a .tex file
    register_external(...) retroactively register pre-existing JSON outputs
    set_store_root(path)  override the default .claimtrail store location
"""
from .tracking import (
    tracked,
    ClaimtrailHashWarning,
    ClaimtrailHashError,
    ClaimtrailFileMissingError,
    ClaimtrailTraversalError,
    ClaimtrailPropertyWarning,
)
from .store import (
    Store,
    Computation,
    Claim,
    get_store,
    set_store_root,
    ClaimtrailCollisionError,
    PayloadTamperedError,
)
from .claims import claim, export_latex, UnbackedPaperClaimError
from .inputs import canonical_file, hash_file, path_of
from .query import find, get
from .external import register_external
from .audit_paper import (
    audit_paper,
    AuditEntry,
    AuditReport,
    ExtractedNumber,
    Mismatch,
)
from .properties import Property, PropertyResult, ClaimtrailPropertyError

__version__ = "0.5.0"

# Names from before the qprov -> claimtrail rename. Kept so existing
# scripts and `except QprovCollisionError:` blocks keep working.
QprovHashWarning = ClaimtrailHashWarning
QprovHashError = ClaimtrailHashError
QprovFileMissingError = ClaimtrailFileMissingError
QprovTraversalError = ClaimtrailTraversalError
QprovPropertyWarning = ClaimtrailPropertyWarning
QprovCollisionError = ClaimtrailCollisionError
QprovPropertyError = ClaimtrailPropertyError

__all__ = [
    "tracked",
    "claim",
    "find",
    "get",
    "export_latex",
    "register_external",
    "canonical_file",
    "hash_file",
    "path_of",
    "UnbackedPaperClaimError",
    "ClaimtrailHashWarning",
    "ClaimtrailHashError",
    "ClaimtrailFileMissingError",
    "ClaimtrailTraversalError",
    "ClaimtrailCollisionError",
    "ClaimtrailPropertyError",
    "ClaimtrailPropertyWarning",
    "PayloadTamperedError",
    "Property",
    "PropertyResult",
    "Store",
    "Computation",
    "Claim",
    "get_store",
    "set_store_root",
    "audit_paper",
    "AuditEntry",
    "AuditReport",
    "ExtractedNumber",
    "Mismatch",
    "__version__",
]
