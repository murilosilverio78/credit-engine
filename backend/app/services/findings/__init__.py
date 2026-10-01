"""Shadow finding emission. Findings never participate in a credit decision."""

from .emitter import emit_findings
from .version import EMITTER_VERSION

__all__ = ["EMITTER_VERSION", "emit_findings"]
