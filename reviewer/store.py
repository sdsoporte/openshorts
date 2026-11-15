"""Esquema y operaciones del Reviewer sobre SQLAlchemy Core.

SQLite en desarrollo y Postgres en producción con el mismo código
(``DATABASE_URL``). Las dos garantías que importan viven acá y no en la API:

* **Ingesta idempotente** por ``unique(job_id, clip_index)``: el callback de n8n
  se puede reintentar sin duplicar tarjetas.
* **Una sola decisión por clip**: la decisión se aplica con un ``UPDATE``
  condicionado al estado leído, así dos swipes concurrentes no ganan los dos.
"""
from __future__ import annotations

import os
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from sqlalchemy import insert, or_, select, update
from sqlalchemy.exc import IntegrityError

metadata = sa.MetaData()

channels = sa.Table(
    "channels", metadata,
    sa.Column("id", sa.String(36), primary_key=True),
    sa.Column("slug", sa.String(64), nullable=False, unique=True),
    sa.Column("uploadpost_profile", sa.String(128), nullable=False),
    sa.Column("platforms", sa.String(255), nullable=False),  # csv: tiktok,instagram
    sa.Column("timezone", sa.String(64), nullable=False, server_default="UTC"),
    sa.Column("slots_per_day", sa.Integer, nullable=False, server_default="1"),
    sa.Column("active", sa.Boolean, nullable=False, server_default=sa.true()),
)

clips = sa.Table(
    "reviewer_clips", metadata,
    sa.Column("id", sa.String(36), primary_key=True),
    sa.Column("job_id", sa.String(64), nullable=False),
    sa.Column("clip_index", sa.Integer, nullable=False),
    sa.Column("storage_url", sa.Text),
    sa.Column("duration_s", sa.Numeric(6, 2), nullable=False, server_default="0"),
    sa.Column("predicted_score", sa.Integer),
    sa.Column("hook_text", sa.Text),
    sa.Column("title", sa.Text),
    sa.Column("state", sa.String(16), nullable=False, server_default="pending"),
    sa.Column("channel_id", sa.String(36), sa.ForeignKey("channels.id")),
    sa.Column("scheduled_for", sa.DateTime(timezone=True)),
    sa.Column("decided_at", sa.DateTime(timezone=True)),
    sa.Column("decided_by", sa.String(128)),
    sa.Column("publish_job_id", sa.String(64)),
    sa.Column("published_at", sa.DateTime(timezone=True)),
    sa.Column("external_urls", sa.Text),  # JSON serializado
    sa.Column("error", sa.Text),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.UniqueConstraint("job_id", "clip_index", name="uq_clip_per_job"),
)

sa.Index("ix_clips_state_scheduled", clips.c.state, clips.c.scheduled_for)
sa.Index("ix_clips_channel_scheduled", clips.c.channel_id, clips.c.scheduled_for)

# Horas locales en las que se programan los clips de un canal. Una lista fija en
# vez de un cálculo: el planificador de la rebanada 2 puede elegir entre estos
# huecos y quien quiera otra cosa los configura acá.
SLOT_HOURS = (12, 18, 21)


def engine_from_env(url: str | None = None) -> sa.Engine:
    url = url or os.environ.get("DATABASE_URL") or "sqlite:///./reviewer.db"
    # check_same_thread=False: FastAPI atiende en varios hilos con SQLite.
    kwargs: dict = {"future": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    return sa.create_engine(url, **kwargs)


def create_schema(engine: sa.Engine) -> None:
    metadata.create_all(engine)


def _now() -> datetime:
    return datetime.now(UTC)


def to_utc(value: datetime) -> datetime:
    """Normaliza antes de guardar.

    SQLite descarta el offset de un ``DateTime``: un ``18:00-03`` vuelve como
    18:00 *naive* y se lee como UTC, o sea 15:00 local — y el hueco de las 18
    parece libre otra vez. Persistir en UTC hace que el valor que vuelve sea el
    mismo que se guardó, en los dos motores.
    """
    return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)


def create_channel(conn, *, slug: str, uploadpost_profile: str, platforms,
                   timezone_name: str = "UTC", slots_per_day: int = 1) -> dict:
    import uuid

    row = {
        "id": str(uuid.uuid4()),
        "slug": slug,
        "uploadpost_profile": uploadpost_profile,
        "platforms": ",".join(platforms),
        "timezone": timezone_name,
        "slots_per_day": max(1, min(int(slots_per_day), len(SLOT_HOURS))),
        "active": True,
    }
    conn.execute(insert(channels).values(**row))
    return row


def get_channel(conn, slug: str):
    return conn.execute(select(channels).where(channels.c.slug == slug)).mappings().first()


def ingest(conn, *, job_id: str, items: list, channel_id: str | None = None) -> dict:
    """Alta idempotente de los clips de un job.

    ``items`` es lo que n8n saca de ``result.clips`` de OpenShorts. Repetir la
    llamada no duplica: la clave única lo impide y el conflicto se cuenta como
    ``skipped``.
    """
    import uuid

    added = skipped = 0
    for item in items:
        row = {
            "id": str(uuid.uuid4()),
            "job_id": job_id,
            "clip_index": int(item["clip_index"]),
            "storage_url": item.get("storage_url") or item.get("video_url"),
            "duration_s": item.get("duration_s", 0) or 0,
            "predicted_score": item.get("predicted_score"),
            "hook_text": item.get("hook_text") or item.get("viral_hook_text"),
            "title": item.get("title") or item.get("video_title_for_youtube_short"),
            "state": "pending",
            "channel_id": channel_id,
            "created_at": _now(),
        }
        try:
            with conn.begin_nested():
                conn.execute(insert(clips).values(**row))
            added += 1
        except IntegrityError:
            skipped += 1
    return {"ingested": added, "skipped": skipped}


def _row_to_dict(row) -> dict:
    out = dict(row)
    for key in ("scheduled_for", "decided_at", "published_at", "created_at"):
        value = out.get(key)
        if isinstance(value, datetime):
            out[key] = value.isoformat()
    return out


def queue(conn, *, channel_slug: str | None = None, limit: int = 20) -> list:
    """Los ``pending`` del canal, primero lo que más promete."""
    top = max(1, min(limit, 100))
    order = (clips.c.predicted_score.desc().nullslast(), clips.c.created_at.asc())
    if channel_slug:
        channel = get_channel(conn, channel_slug)
        if channel is None:
            return []
        rows = conn.execute(
            select(clips)
            .where(clips.c.state == "pending",
                   or_(clips.c.channel_id == channel["id"],
                       clips.c.channel_id.is_(None)))
            .order_by(*order).limit(top)).mappings()
    else:
        rows = conn.execute(
            select(clips).where(clips.c.state == "pending")
            .order_by(*order).limit(top)).mappings()
    return [_row_to_dict(r) for r in rows]


def next_free_slot(conn, channel: dict, *, days: int = 14) -> datetime | None:
    """Primer hueco libre del canal, en su zona horaria.

    Mira hacia adelante desde hoy y devuelve el primer hueco de ``slots_per_day``
    que ningún clip programado o publicado ocupa.
    """
    tz = ZoneInfo(channel["timezone"] or "UTC")
    # Un hueco es (día, hora), no el día entero: con slots_per_day=2 tienen que
    # caber dos clips el mismo día. Antes se marcaba el día completo y el segundo
    # hueco quedaba inalcanzable, así que 2/día se comportaba como 1/día.
    taken_slots = set()
    for row in conn.execute(
        select(clips.c.scheduled_for).where(
            clips.c.channel_id == channel["id"],
            clips.c.state.in_(("scheduled", "published", "failed")),
            clips.c.scheduled_for.is_not(None))).mappings():
        value = row["scheduled_for"]
        if isinstance(value, str):
            value = datetime.fromisoformat(value)
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        local = value.astimezone(tz)
        taken_slots.add((local.date(), local.hour))

    hours = SLOT_HOURS[: int(channel["slots_per_day"] or 1)]
    now = datetime.now(tz)
    for offset in range(days):
        day = now.date() + timedelta(days=offset)
        for hour in hours:
            if (day, hour) in taken_slots:
                continue
            candidate = datetime.combine(day, time(hour), tzinfo=tz)
            if candidate > now:
                return candidate
    return None


def decide(conn, *, clip_id: str, decision: str, channel_id: str | None = None,
           scheduled_for: datetime | None = None, decided_by: str | None = None) -> dict:
    """Aplica una decisión. Idempotente; conflicto si ya estaba decidido distinto."""
    from . import state as st

    row = conn.execute(select(clips).where(clips.c.id == clip_id)).mappings().first()
    if row is None:
        raise LookupError("clip not found")
    new_state = st.apply_decision(row["state"], decision)
    values = {
        "state": new_state,
        "decided_at": row["decided_at"] or _now(),
        "decided_by": decided_by or row["decided_by"],
    }
    if channel_id:
        values["channel_id"] = channel_id
    if new_state == st.ACCEPTED and scheduled_for is not None:
        # Un clip sin canal no tiene calendario: programarlo lo dejaría invisible
        # para el planificador y para la vista de calendario.
        target_channel = values.get("channel_id") or row["channel_id"]
        if not target_channel:
            raise st.TransitionError("scheduling needs a channel")
        values["channel_id"] = target_channel
        values["state"] = st.schedule(st.ACCEPTED, scheduled_for)
        values["scheduled_for"] = to_utc(scheduled_for)
    conn.execute(update(clips).where(clips.c.id == clip_id).values(**values))
    return _row_to_dict(
        conn.execute(select(clips).where(clips.c.id == clip_id)).mappings().one())


def calendar(conn, *, channel_slug: str, start: date, end: date) -> list:
    channel = get_channel(conn, channel_slug)
    if channel is None:
        return []
    rows = [_row_to_dict(r) for r in conn.execute(
        select(clips)
        .where(clips.c.channel_id == channel["id"],
               clips.c.state.in_(("scheduled", "published", "failed")),
               clips.c.scheduled_for.is_not(None))
        .order_by(clips.c.scheduled_for.asc())).mappings()]
    out = []
    for r in rows:
        when = datetime.fromisoformat(r["scheduled_for"])
        if start <= when.date() <= end:
            out.append(r)
    return out
