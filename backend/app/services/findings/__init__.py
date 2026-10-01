"""Shadow finding emission. Findings never participate in a credit decision."""

from .emitter import emit_findings
from .version import EMITTER_VERSION, HASH_EXTRAS

__all__ = ["EMITTER_VERSION", "HASH_EXTRAS", "emit_findings"]
