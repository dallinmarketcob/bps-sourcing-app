"""Lead-source attribution engine for Brooks Pest Control.

Pulls sold subscriptions from PestRoutes, gathers lead touches across Meta,
Gmail, and Genesys, applies a deterministic credit rule, and writes the
winning source back. See plan: eventual-finding-seal.md.
"""

__version__ = "0.1.0"
