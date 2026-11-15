"""Planificador: llena huecos, publica lo que vence y reintenta lo que falló.

Un *tick* hace dos cosas por cada canal activo:

1. **Promoción**: toma los clips ``accepted`` más prometedores y les asigna el
   primer hueco libre (pasan a ``scheduled``). Es lo que hace que un "aceptar sin
   fecha" termine en el calendario sin que nadie abra la app de nuevo.
2. **Publicación**: todo ``scheduled`` cuya hora ya pasó se entrega al
   ``Publisher``. Éxito → ``published`` con sus URLs; error → ``failed`` con el
   motivo, y el siguiente tick lo reintenta (``failed → scheduled``).

Con el publicador simulado esto corre sin red, que es lo que permite probar el
e2e completo hoy.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select, update

from . import publisher as pub
from . import state as st
from . import store

# Un fallo no se reintenta de inmediato: el siguiente tick puede caer un segundo
# después y sólo repetiría el mismo error.
RETRY_AFTER = timedelta(minutes=5)


@dataclass
class TickReport:
    channels: int = 0
    promoted: int = 0
    published: int = 0
    failed: int = 0
    details: list = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"channels": self.channels, "promoted": self.promoted,
                "published": self.published, "failed": self.failed,
                "details": self.details}


def _now() -> datetime:
    return datetime.now(UTC)


def _as_utc(value) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _active_channels(conn) -> list:
    rows = conn.execute(select(store.channels)
                        .where(store.channels.c.active.is_(True))
                        .order_by(store.channels.c.slug)).mappings()
    return [dict(r) for r in rows]


def _accepted(conn, channel_id: str, limit: int) -> list:
    return [dict(r) for r in conn.execute(
        select(store.clips)
        .where(store.clips.c.state == st.ACCEPTED,
               or_(store.clips.c.channel_id == channel_id,
                   store.clips.c.channel_id.is_(None)))
        .order_by(store.clips.c.predicted_score.desc().nullslast(),
                  store.clips.c.created_at.asc())
        .limit(limit)
    ).mappings()]


def _due(conn, channel_id: str, now: datetime) -> list:
    rows = conn.execute(
        select(store.clips).where(
            store.clips.c.channel_id == channel_id,
            store.clips.c.state.in_((st.SCHEDULED, st.FAILED)),
            store.clips.c.scheduled_for.is_not(None))
    ).mappings()
    due = []
    for row in rows:
        clip = dict(row)
        when = _as_utc(clip["scheduled_for"])
        if clip["state"] == st.FAILED:
            # Tras un fallo se espera: reintentar en el mismo tick no aporta.
            last = _as_utc(clip["decided_at"]) if clip["decided_at"] else when
            if now - last < RETRY_AFTER:
                continue
        if when <= now:
            due.append(clip)
    return due


def promote(conn, channel: dict, *, limit: int = 50, now: datetime | None = None) -> list:
    """Mueve ``accepted`` a ``scheduled`` en los huecos libres del canal."""
    now = now or _now()
    promoted = []
    for _ in range(limit):
        pending = _accepted(conn, channel["id"], 1)
        if not pending:
            break
        slot = store.next_free_slot(conn, channel)
        if slot is None:
            break
        clip = pending[0]
        conn.execute(update(store.clips)
                     .where(store.clips.c.id == clip["id"])
                     .values(state=st.SCHEDULED, channel_id=channel["id"],
                             scheduled_for=store.to_utc(slot), error=None))
        promoted.append({"clip_id": clip["id"], "scheduled_for": slot.isoformat()})
    return promoted


def publish_due(conn, channel: dict, publisher: pub.Publisher, *,
                now: datetime | None = None) -> tuple[list, list]:
    """Entrega al publicador lo que ya venció. Devuelve (publicados, fallidos)."""
    now = now or _now()
    published, failed = [], []
    for clip in _due(conn, channel["id"], now):
        # Un fallo anterior vuelve a `scheduled` antes de reintentar, así el
        # estado nunca miente sobre "está pendiente de publicar".
        if clip["state"] == st.FAILED:
            conn.execute(update(store.clips).where(store.clips.c.id == clip["id"])
                         .values(state=st.SCHEDULED))
        result = publisher.publish(clip=clip, channel=channel)
        if result.ok:
            conn.execute(update(store.clips).where(store.clips.c.id == clip["id"]).values(
                state=st.mark_published(st.SCHEDULED),
                published_at=now,
                publish_job_id=result.publish_job_id,
                external_urls=str(result.external_urls),
                error=None))
            published.append({"clip_id": clip["id"],
                              "publish_job_id": result.publish_job_id})
        else:
            conn.execute(update(store.clips).where(store.clips.c.id == clip["id"]).values(
                state=st.mark_failed(st.SCHEDULED, result.error or ""),
                decided_at=now, error=result.error or "publish failed"))
            failed.append({"clip_id": clip["id"], "error": result.error})
    return published, failed


def tick(conn, publisher: pub.Publisher | None = None, *,
         now: datetime | None = None, limit: int = 50) -> TickReport:
    """Un ciclo completo. Idempotente: repetirlo con el mismo reloj no duplica."""
    publisher = publisher or pub.from_env()
    now = now or _now()
    report = TickReport()
    for channel in _active_channels(conn):
        report.channels += 1
        promoted = promote(conn, channel, limit=limit, now=now)
        published, failed = publish_due(conn, channel, publisher, now=now)
        report.promoted += len(promoted)
        report.published += len(published)
        report.failed += len(failed)
        if promoted or published or failed:
            report.details.append({
                "channel": channel["slug"],
                "promoted": len(promoted),
                "published": len(published),
                "failed": len(failed),
                "publisher": publisher.name,
            })
    return report
