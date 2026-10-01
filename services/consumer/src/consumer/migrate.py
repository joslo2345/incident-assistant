"""Applies deploy/db/migrations/*.sql in order, each once, each in its own transaction.

Usage: python -m consumer.migrate [--dir deploy/db/migrations]
"""

import argparse
import asyncio
import os
from pathlib import Path

import asyncpg

from consumer.settings import Settings

DEFAULT_DIR = Path(os.environ.get("MIGRATIONS_DIR", "deploy/db/migrations"))


async def migrate(database_url: str, migrations_dir: Path) -> list[str]:
    conn = await asyncpg.connect(database_url)
    try:
        await conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            " name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
        )
        # Serialize concurrent runners (e.g. two replicas starting at once).
        await conn.execute("SELECT pg_advisory_lock(hashtext('schema_migrations'))")
        done = {r["name"] for r in await conn.fetch("SELECT name FROM schema_migrations")}
        applied = []
        for path in sorted(migrations_dir.glob("*.sql")):
            if path.name in done:
                continue
            async with conn.transaction():
                await conn.execute(path.read_text())
                await conn.execute("INSERT INTO schema_migrations (name) VALUES ($1)", path.name)
            applied.append(path.name)
        return applied
    finally:
        await conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply database migrations.")
    parser.add_argument("--dir", type=Path, default=DEFAULT_DIR)
    args = parser.parse_args()
    applied = asyncio.run(migrate(Settings.from_env().database_url, args.dir))
    print(f"applied {len(applied)} migration(s): {', '.join(applied) or 'none'}")


if __name__ == "__main__":
    main()
