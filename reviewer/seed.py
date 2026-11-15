"""Alta de canales de prueba: ``python -m reviewer.seed [n]``.

Un canal = un calendario = un perfil de publicación. El seed existe para poder
probar multi-canal y multi-calendario hoy, sin Upload-Post: crea ``channel-1``..
``channel-N`` con plataformas y husos distintos, y es idempotente (repetirlo no
duplica ni pisa lo que ya existe).

    DATABASE_URL=sqlite:///./reviewer.db python -m reviewer.seed 3
"""
from __future__ import annotations

import sys

from sqlalchemy import select

from . import store

# Husos distintos a propósito: si dos canales comparten calendario por error, el
# seed lo hace visible (los huecos caen en horas locales diferentes).
PRESETS = [
    ("channel-1", "tiktok,instagram", "America/Argentina/Buenos_Aires", 2),
    ("channel-2", "tiktok", "Europe/Madrid", 1),
    ("channel-3", "instagram,youtube", "America/Bogota", 3),
    ("channel-4", "tiktok", "America/Mexico_City", 1),
    ("channel-5", "youtube", "America/Santiago", 2),
]


def seed(conn, count: int = 3) -> list:
    created = []
    for index in range(max(1, min(count, len(PRESETS)))):
        slug, platforms, tz, slots = PRESETS[index]
        if store.get_channel(conn, slug) is not None:
            continue
        created.append(store.create_channel(
            conn, slug=slug, uploadpost_profile=f"perfil-{slug}",
            platforms=platforms.split(","), timezone_name=tz,
            slots_per_day=slots))
    return created


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    count = int(argv[0]) if argv else 3
    engine = store.engine_from_env()
    store.create_schema(engine)
    with engine.connect() as conn, conn.begin():
        created = seed(conn, count)
        existing = [c["slug"] for c in conn.execute(
            select(store.channels).order_by(store.channels.c.slug)).mappings()]
    if created:
        for channel in created:
            print(f"created {channel['slug']}: {channel['platforms']} "
                  f"· {channel['timezone']} · {channel['slots_per_day']}/día")
    else:
        print("nothing to create")
    print("channels now:", ", ".join(existing))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
