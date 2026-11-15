"""Tests del publicador simulado, del planificador y del seed de canales.

Lo que se prueba acá es lo que permite el e2e sin publicar de verdad: que un
clip aprobado llegue solo al calendario, que se "publique" cuando vence, que un
fallo no pierda el clip, y que dos canales no se pisen.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from reviewer import publisher as pub
from reviewer import scheduler, seed, store
from reviewer import state as st


@pytest.fixture
def engine(tmp_path):
    eng = store.engine_from_env(f"sqlite:///{tmp_path/'scheduler.db'}")
    store.create_schema(eng)
    return eng


@pytest.fixture
def channel(engine):
    with engine.connect() as c, c.begin():
        return store.create_channel(c, slug="reels", uploadpost_profile="perfil-1",
                                    platforms=["tiktok", "instagram"],
                                    timezone_name="America/Argentina/Buenos_Aires")


def _ingest(engine, channel, n=1, job="job-1"):
    items = [{"clip_index": i, "video_url": f"/v/{i}.mp4",
              "predicted_score": 90 - i, "viral_hook_text": f"h{i}"} for i in range(n)]
    with engine.connect() as c, c.begin():
        store.ingest(c, job_id=job, items=items, channel_id=channel["id"])


def _accept_all(engine, channel):
    with engine.connect() as c:
        clips = store.queue(c, channel_slug=channel["slug"])
    with engine.connect() as c, c.begin():
        for clip in clips:
            store.decide(c, clip_id=clip["id"], decision="accept",
                         channel_id=channel["id"])
    return len(clips)


# --- publicador simulado ---------------------------------------------------

def test_simulated_publish_never_touches_the_network(channel):
    result = pub.SimulatedPublisher().publish(
        clip={"id": "c1"}, channel=channel)
    assert result.ok
    assert result.publish_job_id is not None
    assert result.publish_job_id.startswith("sim-")
    assert all("simulated.invalid" in url for url in result.external_urls.values())
    assert set(result.external_urls) == {"tiktok", "instagram"}


def test_the_simulated_publisher_can_be_told_to_fail(channel):
    result = pub.SimulatedPublisher(should_fail=True).publish(
        clip={"id": "c1"}, channel=channel)
    assert not result.ok
    assert result.error


def test_publish_mode_defaults_to_simulate(monkeypatch):
    monkeypatch.delenv("PUBLISH_MODE", raising=False)
    assert pub.from_env().name == "simulate"


# --- planificador ----------------------------------------------------------

def test_promote_puts_an_accepted_clip_on_the_calendar(engine, channel):
    _ingest(engine, channel)
    _accept_all(engine, channel)
    with engine.connect() as c, c.begin():
        promoted = scheduler.promote(c, channel)
    assert len(promoted) == 1
    with engine.connect() as c:
        rows = store.queue(c, channel_slug="reels")
    assert rows == []  # ya no está pendiente


def test_promote_respects_slots_per_day(engine):
    with engine.connect() as c, c.begin():
        one_slot = store.create_channel(c, slug="uno", uploadpost_profile="p",
                                        platforms=["tiktok"], slots_per_day=1)
    _ingest(engine, one_slot, n=3)
    _accept_all(engine, one_slot)
    with engine.connect() as c, c.begin():
        scheduler.promote(c, one_slot)
    with engine.connect() as c:
        scheduled = c.execute(
            select(store.clips).where(store.clips.c.state == st.SCHEDULED)
        ).mappings().all()
    # 1 hueco por día no limita el total: reparte los tres en tres días distintos
    assert len(scheduled) == 3
    days = {row["scheduled_for"].date() for row in scheduled}
    assert len(days) == 3


def test_two_slots_per_day_fit_two_clips_on_the_same_day(engine):
    """2/día tiene que usar las dos horas, no dos días."""
    with engine.connect() as c, c.begin():
        dos = store.create_channel(c, slug="dos-slots", uploadpost_profile="p",
                                   platforms=["tiktok"], timezone_name="UTC",
                                   slots_per_day=2)
    _ingest(engine, dos, n=2)
    _accept_all(engine, dos)
    with engine.connect() as c, c.begin():
        scheduler.promote(c, dos)
    with engine.connect() as c:
        rows = c.execute(select(store.clips)).mappings().all()
    hours = sorted(r["scheduled_for"].hour for r in rows)
    assert len(rows) == 2
    assert len({r["scheduled_for"].date() for r in rows}) == 1, "deben caer el mismo día"
    assert hours == [12, 18]


def test_a_non_utc_channel_keeps_its_two_slots(engine):
    """SQLite descarta el offset: sin normalizar, el segundo hueco se pierde."""
    with engine.connect() as c, c.begin():
        ba = store.create_channel(c, slug="ba", uploadpost_profile="p",
                                  platforms=["tiktok"],
                                  timezone_name="America/Argentina/Buenos_Aires",
                                  slots_per_day=2)
    _ingest(engine, ba, n=2)
    _accept_all(engine, ba)
    with engine.connect() as c, c.begin():
        scheduler.promote(c, ba)
    with engine.connect() as c:
        rows = c.execute(select(store.clips)).mappings().all()
    from zoneinfo import ZoneInfo
    local = sorted(store.to_utc(r["scheduled_for"] if r["scheduled_for"].tzinfo
                                else r["scheduled_for"].replace(tzinfo=UTC))
                   .astimezone(ZoneInfo("America/Argentina/Buenos_Aires")).hour
                   for r in rows)
    assert local == [12, 18], f"esperaba 12 y 18 local, obtuve {local}"


def test_tick_publishes_what_is_due(engine, channel):
    _ingest(engine, channel)
    _accept_all(engine, channel)
    later = datetime.now(UTC) + timedelta(days=30)
    with engine.connect() as c, c.begin():
        scheduler.promote(c, channel)
    with engine.connect() as c, c.begin():
        report = scheduler.tick(c, pub.SimulatedPublisher(), now=later)
    assert report.published == 1
    assert report.failed == 0
    assert report.details[0]["publisher"] == "simulate"


def test_a_published_clip_keeps_its_urls_and_id(engine, channel):
    _ingest(engine, channel)
    _accept_all(engine, channel)
    when = datetime.now(UTC) + timedelta(days=30)
    with engine.connect() as c, c.begin():
        scheduler.promote(c, channel)
        scheduler.tick(c, pub.SimulatedPublisher(), now=when)
    with engine.connect() as c:
        row = c.execute(select(store.clips)).mappings().one()
    assert row["state"] == st.PUBLISHED
    assert row["publish_job_id"].startswith("sim-")
    assert "simulated.invalid" in row["external_urls"]
    assert row["published_at"] is not None


def test_a_failure_is_recorded_and_the_clip_is_not_lost(engine, channel):
    _ingest(engine, channel)
    _accept_all(engine, channel)
    when = datetime.now(UTC) + timedelta(days=30)
    with engine.connect() as c, c.begin():
        scheduler.promote(c, channel)
        report = scheduler.tick(c, pub.SimulatedPublisher(should_fail=True), now=when)
    assert report.failed == 1
    with engine.connect() as c:
        row = c.execute(select(store.clips)).mappings().one()
    assert row["state"] == st.FAILED
    assert row["error"] == "simulated failure"


def test_a_failure_is_not_retried_in_the_same_minute(engine, channel):
    _ingest(engine, channel)
    _accept_all(engine, channel)
    when = datetime.now(UTC) + timedelta(days=30)
    with engine.connect() as c, c.begin():
        scheduler.promote(c, channel)
        scheduler.tick(c, pub.SimulatedPublisher(should_fail=True), now=when)
        again = scheduler.tick(c, pub.SimulatedPublisher(), now=when + timedelta(seconds=30))
    assert again.published == 0 and again.failed == 0


def test_a_failure_is_retried_after_the_backoff(engine, channel):
    _ingest(engine, channel)
    _accept_all(engine, channel)
    when = datetime.now(UTC) + timedelta(days=30)
    with engine.connect() as c, c.begin():
        scheduler.promote(c, channel)
        scheduler.tick(c, pub.SimulatedPublisher(should_fail=True), now=when)
        retried = scheduler.tick(c, pub.SimulatedPublisher(),
                                 now=when + scheduler.RETRY_AFTER + timedelta(seconds=1))
    assert retried.published == 1
    with engine.connect() as c:
        assert c.execute(select(store.clips)).mappings().one()["state"] == st.PUBLISHED


def test_two_channels_do_not_publish_each_other(engine):
    with engine.connect() as c, c.begin():
        uno = store.create_channel(c, slug="uno", uploadpost_profile="p1",
                                   platforms=["tiktok"], timezone_name="UTC")
        dos = store.create_channel(c, slug="dos", uploadpost_profile="p2",
                                   platforms=["tiktok"], timezone_name="UTC")
    _ingest(engine, uno, n=1, job="job-uno")
    _accept_all(engine, uno)
    with engine.connect() as c, c.begin():
        scheduler.promote(c, uno)
        report = scheduler.tick(c, pub.SimulatedPublisher(),
                                now=datetime.now(UTC) + timedelta(days=60))
    assert report.channels == 2          # el tick recorre los dos
    assert report.published == 1         # pero sólo publica lo de `uno`
    with engine.connect() as c:
        dos_clips = c.execute(select(store.clips)
                              .where(store.clips.c.channel_id == dos["id"])).mappings().all()
    assert dos_clips == []


def test_tick_is_idempotent(engine, channel):
    _ingest(engine, channel)
    _accept_all(engine, channel)
    when = datetime.now(UTC) + timedelta(days=30)
    with engine.connect() as c, c.begin():
        scheduler.promote(c, channel)
        first = scheduler.tick(c, pub.SimulatedPublisher(), now=when)
        second = scheduler.tick(c, pub.SimulatedPublisher(), now=when)
    assert first.published == 1
    assert second.published == 0


# --- seed ------------------------------------------------------------------

def test_seed_creates_the_asked_channels(engine):
    with engine.connect() as c, c.begin():
        created = seed.seed(c, 3)
    assert [ch["slug"] for ch in created] == ["channel-1", "channel-2", "channel-3"]
    assert created[0]["timezone"] != created[1]["timezone"]


def test_seed_is_idempotent(engine):
    with engine.connect() as c, c.begin():
        seed.seed(c, 2)
    with engine.connect() as c, c.begin():
        again = seed.seed(c, 2)
    assert again == []


def test_seeded_channels_come_up_active(engine):
    with engine.connect() as c, c.begin():
        seed.seed(c, 3)
        rows = c.execute(select(store.channels)).mappings().all()
    assert len(rows) == 3
    assert all(r["active"] for r in rows)
