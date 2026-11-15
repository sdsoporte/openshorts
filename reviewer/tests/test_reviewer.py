"""Tests del Reviewer: estados, almacenamiento y los cuatro endpoints.

Se corren dentro del contenedor del backend (que ya trae fastapi, sqlalchemy,
httpx y pytest):

    docker compose run --rm --no-deps --user root backend \\
      sh -c "pip install -q pytest && python -m pytest reviewer/tests -q"
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from reviewer import api, store
from reviewer import state as st

# --- máquina de estados ----------------------------------------------------

def test_accept_moves_a_pending_clip_to_accepted():
    assert st.apply_decision(st.PENDING, "accept") == st.ACCEPTED


def test_reject_is_terminal():
    assert st.apply_decision(st.PENDING, "reject") == st.REJECTED
    with pytest.raises(st.AlreadyDecided):
        st.apply_decision(st.REJECTED, "accept")


def test_repeating_the_same_decision_is_not_a_conflict():
    """Un swipe repetido por red lenta no puede devolver un error."""
    assert st.apply_decision(st.ACCEPTED, "accept") == st.ACCEPTED
    assert st.apply_decision(st.REJECTED, "reject") == st.REJECTED


def test_an_unknown_decision_is_rejected():
    with pytest.raises(st.TransitionError):
        st.apply_decision(st.PENDING, "maybe")


def test_publishing_requires_a_scheduled_clip():
    with pytest.raises(st.TransitionError):
        st.mark_published(st.ACCEPTED)
    assert st.mark_published(st.SCHEDULED) == st.PUBLISHED


def test_a_failed_publication_keeps_the_clip_approved():
    assert st.mark_failed(st.SCHEDULED, "boom") == st.FAILED
    assert st.schedule(st.FAILED, datetime.now(UTC)) == st.SCHEDULED


# --- almacenamiento --------------------------------------------------------

@pytest.fixture
def engine(tmp_path):
    eng = store.engine_from_env(f"sqlite:///{tmp_path/'reviewer.db'}")
    store.create_schema(eng)
    return eng


@pytest.fixture
def channel(engine):
    with engine.connect() as c, c.begin():
        return store.create_channel(c, slug="reels", uploadpost_profile="perfil-1",
                                   platforms=["tiktok", "instagram"],
                                   timezone_name="America/Argentina/Buenos_Aires")


def _items(n=3):
    return [{"clip_index": i, "video_url": f"/videos/j/{i}.mp4",
             "predicted_score": 90 - i * 10, "viral_hook_text": f"hook {i}",
             "video_title_for_youtube_short": f"titulo {i}", "duration_s": 40}
            for i in range(n)]


def test_ingest_is_idempotent(engine, channel):
    with engine.connect() as c, c.begin():
        first = store.ingest(c, job_id="job-1", items=_items(3), channel_id=channel["id"])
    with engine.connect() as c, c.begin():
        again = store.ingest(c, job_id="job-1", items=_items(3), channel_id=channel["id"])
    assert first == {"ingested": 3, "skipped": 0}
    assert again == {"ingested": 0, "skipped": 3}


def test_queue_is_ordered_by_score(engine, channel):
    with engine.connect() as c, c.begin():
        store.ingest(c, job_id="job-1", items=_items(3), channel_id=channel["id"])
    with engine.connect() as c:
        rows = store.queue(c, channel_slug="reels")
    assert [r["clip_index"] for r in rows] == [0, 1, 2]
    assert rows[0]["predicted_score"] == 90


def test_queue_skips_decided_clips(engine, channel):
    with engine.connect() as c, c.begin():
        store.ingest(c, job_id="job-1", items=_items(2), channel_id=channel["id"])
    with engine.connect() as c:
        first = store.queue(c, channel_slug="reels")[0]
    with engine.connect() as c, c.begin():
        store.decide(c, clip_id=first["id"], decision="reject")
    with engine.connect() as c:
        assert len(store.queue(c, channel_slug="reels")) == 1


def test_scheduling_moves_the_clip_out_of_the_queue(engine, channel):
    with engine.connect() as c, c.begin():
        store.ingest(c, job_id="job-1", items=_items(1), channel_id=channel["id"])
    with engine.connect() as c:
        clip = store.queue(c, channel_slug="reels")[0]
        slot = store.next_free_slot(c, channel)
    assert slot is not None
    with engine.connect() as c, c.begin():
        row = store.decide(c, clip_id=clip["id"], decision="accept",
                           channel_id=channel["id"], scheduled_for=slot)
    assert row["state"] == st.SCHEDULED
    with engine.connect() as c:
        assert store.queue(c, channel_slug="reels") == []


def test_next_free_slot_avoids_the_day_already_taken(engine, channel):
    with engine.connect() as c, c.begin():
        store.ingest(c, job_id="job-1", items=_items(1), channel_id=channel["id"])
    with engine.connect() as c:
        first = store.next_free_slot(c, channel)
        clip = store.queue(c, channel_slug="reels")[0]
    assert first is not None
    with engine.connect() as c, c.begin():
        store.decide(c, clip_id=clip["id"], decision="accept",
                     channel_id=channel["id"], scheduled_for=first)
    with engine.connect() as c:
        second = store.next_free_slot(c, channel)
    assert second is not None
    assert second.date() > first.date()


# --- HTTP ------------------------------------------------------------------

@pytest.fixture
def client(engine):
    return TestClient(api.create_app(engine))


def test_full_swipe_flow_over_http(client):
    created = client.post("/api/channels", json={
        "slug": "reels", "uploadpost_profile": "perfil-1",
        "platforms": ["tiktok"], "timezone": "UTC"})
    assert created.status_code == 201
    channel_id = created.json()["id"]

    r = client.post("/api/ingest", json={"job_id": "job-1", "clips": _items(2)})
    assert r.json() == {"ingested": 2, "skipped": 0}

    queue = client.get("/api/queue", params={"channel": "reels"}).json()
    assert len(queue["clips"]) == 2
    assert queue["suggested_slot"]

    clip = queue["clips"][0]
    accepted = client.post("/api/decisions", json={
        "clip_id": clip["id"], "decision": "accept", "channel_id": channel_id,
        "scheduled_for": queue["suggested_slot"]})
    assert accepted.status_code == 200
    assert accepted.json()["state"] == st.SCHEDULED

    calendar = client.get("/api/calendar", params={
        "channel": "reels", "start": str(date.today()),
        "end": str(date.today() + timedelta(days=30))}).json()
    assert len(calendar["posts"]) == 1


def test_deciding_twice_the_other_way_is_a_conflict(client):
    client.post("/api/channels", json={"slug": "c", "uploadpost_profile": "p",
                                      "platforms": ["tiktok"]})
    client.post("/api/ingest", json={"job_id": "j", "clips": _items(1)})
    clip = client.get("/api/queue").json()["clips"][0]
    assert client.post("/api/decisions", json={"clip_id": clip["id"],
                                               "decision": "reject"}).status_code == 200
    conflict = client.post("/api/decisions", json={"clip_id": clip["id"],
                                                   "decision": "accept"})
    assert conflict.status_code == 409


def test_scheduling_without_a_channel_is_rejected(client):
    """Un clip sin canal no puede programarse: no habría calendario donde ponerlo."""
    client.post("/api/channels", json={"slug": "c2", "uploadpost_profile": "p",
                                      "platforms": ["tiktok"]})
    client.post("/api/ingest", json={"job_id": "j2", "clips": _items(1)})
    clip = client.get("/api/queue").json()["clips"][0]
    r = client.post("/api/decisions", json={
        "clip_id": clip["id"], "decision": "accept",
        "scheduled_for": "2026-12-01T18:00:00+00:00"})
    assert r.status_code == 400
    assert "channel" in r.json()["detail"]


def test_an_unknown_clip_is_404(client):
    r = client.post("/api/decisions", json={"clip_id": "nope", "decision": "accept"})
    assert r.status_code == 404


def test_a_duplicate_channel_slug_is_409(client):
    body = {"slug": "dup", "uploadpost_profile": "p", "platforms": ["tiktok"]}
    assert client.post("/api/channels", json=body).status_code == 201
    assert client.post("/api/channels", json=body).status_code == 409
