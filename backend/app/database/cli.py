"""Schema management entry point: ``python -m app.database.cli migrate``.

Runs the additive migrations in :mod:`app.database.migrations` against the
configured PostgreSQL database. Safe to run repeatedly; also executed at
backend startup when ``DATABASE_MIGRATE_ON_STARTUP`` is true.
"""

from __future__ import annotations

import sys


def migrate() -> str:
    from app.database.connection import _engine
    from app.database.migrations import migrate as run

    return run(_engine)


def main(argv: list[str]) -> int:
    command = argv[1] if len(argv) > 1 else "migrate"
    if command != "migrate":
        print(f"unknown command {command!r}; usage: python -m app.database.cli migrate", file=sys.stderr)
        return 2
    version = migrate()
    print(f"schema at {version}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
