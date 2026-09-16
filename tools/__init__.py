"""Operational tools: UI audits, BMS diagnostics, the Firestore migration.

This file makes ``tools`` a real package rather than a namespace one. Without
it, an unrelated ``tools`` distribution in site-packages wins the import and
``tools.migration_plan`` cannot be found — a regular package on the front of
``sys.path`` takes precedence, a namespace portion does not.

Nothing is imported here: each tool is a script, and the migration modules are
imported explicitly by the ones that need them.
"""
