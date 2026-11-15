"""API del Reviewer: ingesta, cola de swipe, decisiones y calendario.

Cuatro endpoints y nada más, según ``docs/reviewer-contract.md``. La lógica de
estado vive en ``state.py`` y el acceso a datos en ``store.py``; acá sólo se
traduce HTTP a esas dos cosas, para que los tests puedan ejercitar la lógica sin
levantar el servidor.

El planificador y la llamada a OpenShorts/Upload-Post no están en esta rebanada:
este servicio decide y guarda fechas, no publica.
"""
from __future__ import annotations

from datetime import date, datetime

import sqlalchemy as sa
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from . import publisher as pub
from . import scheduler, store
from . import state as st


class ClipIn(BaseModel):
    clip_index: int
    storage_url: str | None = None
    video_url: str | None = None
    duration_s: float = 0
    predicted_score: int | None = None
    hook_text: str | None = None
    viral_hook_text: str | None = None
    title: str | None = None
    video_title_for_youtube_short: str | None = None


class IngestIn(BaseModel):
    job_id: str
    clips: list[ClipIn] = Field(default_factory=list)
    channel_id: str | None = None


class DecisionIn(BaseModel):
    clip_id: str
    decision: str
    channel_id: str | None = None
    scheduled_for: datetime | None = None
    decided_by: str | None = None


class ChannelIn(BaseModel):
    slug: str
    uploadpost_profile: str
    platforms: list[str]
    timezone: str = "UTC"
    slots_per_day: int = 1


def create_app(engine: sa.Engine | None = None) -> FastAPI:
    # Variable propia: `engine` puede ser None y pyright pierde el angostamiento
    # dentro del closure.
    active_engine = engine or store.engine_from_env()
    store.create_schema(active_engine)
    app = FastAPI(title="OpenShorts Reviewer", version="0.1.0")

    def conn():
        return active_engine.connect()

    @app.post("/api/channels", status_code=201)
    def create_channel(payload: ChannelIn):
        """Alta de un canal. Un canal = un calendario = un perfil de publicación."""
        with conn() as c, c.begin():
            if store.get_channel(c, payload.slug) is not None:
                raise HTTPException(409, f"channel {payload.slug!r} already exists")
            return store.create_channel(
                c, slug=payload.slug, uploadpost_profile=payload.uploadpost_profile,
                platforms=payload.platforms, timezone_name=payload.timezone,
                slots_per_day=payload.slots_per_day)

    @app.post("/api/ingest")
    def ingest(payload: IngestIn):
        """Alta idempotente de los clips de un job (la llama n8n)."""
        with conn() as c, c.begin():
            return store.ingest(
                c, job_id=payload.job_id,
                items=[item.model_dump() for item in payload.clips],
                channel_id=payload.channel_id)

    @app.get("/api/queue")
    def queue(channel: str | None = None,
              limit: int = Query(default=20, ge=1, le=100)):
        """Los clips pendientes de decisión, con el primer hueco libre del canal."""
        with conn() as c:
            rows = store.queue(c, channel_slug=channel, limit=limit)
            suggested = None
            if channel:
                channel_row = store.get_channel(c, channel)
                if channel_row is not None:
                    slot = store.next_free_slot(c, channel_row)
                    suggested = slot.isoformat() if slot else None
        return {"channel": channel, "suggested_slot": suggested, "clips": rows}

    @app.post("/api/decisions")
    def decide(payload: DecisionIn):
        """Aceptar o rechazar. Idempotente; 409 si ya estaba decidido distinto."""
        try:
            with conn() as c, c.begin():
                return store.decide(
                    c, clip_id=payload.clip_id, decision=payload.decision,
                    channel_id=payload.channel_id,
                    scheduled_for=payload.scheduled_for,
                    decided_by=payload.decided_by)
        except LookupError:
            raise HTTPException(404, "clip not found") from None
        except st.AlreadyDecided as e:
            raise HTTPException(409, str(e)) from e
        except st.TransitionError as e:
            raise HTTPException(400, str(e)) from e

    @app.get("/api/calendar")
    def get_calendar(channel: str, start: date | None = None,
                     end: date | None = None):
        """Lo programado y publicado en el rango, más próximo primero."""
        today = date.today()
        first_day = start or today
        last_day = end or date.fromordinal(today.toordinal() + 30)
        if last_day < first_day:
            raise HTTPException(400, "end must be after start")
        with conn() as c:
            return {"channel": channel, "from": first_day, "to": last_day,
                    "posts": store.calendar(c, channel_slug=channel,
                                            start=first_day, end=last_day)}

    @app.post("/api/scheduler/tick")
    def scheduler_tick(simulate: bool = True):
        """Un ciclo del planificador: llena huecos y publica lo que venció.

        ``simulate=true`` (default) usa el publicador simulado: no toca la red y
        deja URLs bajo ``simulated.invalid``. Es lo que permite probar el e2e sin
        publicar de verdad. Con ``simulate=false`` se usa el publicador del
        entorno (``PUBLISH_MODE``), que hoy todavía no publica.
        """
        chosen = pub.SimulatedPublisher() if simulate else pub.from_env()
        with conn() as c, c.begin():
            return scheduler.tick(c, chosen).as_dict()

    @app.get("/health")
    def health():
        return {"ok": True}

    return app
