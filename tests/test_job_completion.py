"""A job is finished when the pipeline says it is, not when a file shows up.

The metadata JSON is written after the analysis pass and BEFORE the first clip
renders, so "has metadata" only means "the analysis finished". Treating it as
"the job finished" cost six of seven clips on 2026-11-15: a host reboot killed
the render, the restart recovered the job as completed and deleted its resume
manifest, so nothing ever retried it.

The pipeline now stamps `<job>/.job_done` when it reaches the end, and both the
recovery and the resume path read that marker instead of guessing.
"""
import json

import pytest

app = pytest.importorskip("app")


def _make_job(root, *,
              clips=3,
              rendered=3,
              marker=False,
              manifest=True,
              job_id="job-1",
              base="video"):
    job = root / job_id
    job.mkdir(parents=True)
    meta = {"shorts": [{"start": i * 10.0, "end": i * 10.0 + 9.0} for i in range(clips)]}
    (job / f"{base}_metadata.json").write_text(json.dumps(meta))
    for i in range(rendered):
        (job / f"{base}_clip_{i + 1}.mp4").write_bytes(b"\x00")
    if marker:
        (job / app.JOB_DONE_FILE).write_text(json.dumps({"clips": clips}))
    if manifest:
        (job / app._RESUME_FILE).write_text(json.dumps(
            {"cmd": ["python", "main.py"], "priority": 0, "attempts": 0, "watermark": False}))
    return job


@pytest.fixture
def outdir(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "OUTPUT_DIR", str(tmp_path))
    app.jobs.clear()
    yield tmp_path
    app.jobs.clear()


def test_recovery_is_not_completed_while_clips_are_missing(outdir):
    _make_job(outdir, clips=7, rendered=1, marker=False)
    app._recover_jobs_from_disk()
    status = app.jobs["job-1"]["status"]
    assert status != "completed", "an interrupted render must not read as completed"


def test_recovery_is_completed_when_the_marker_exists(outdir):
    _make_job(outdir, clips=3, rendered=3, marker=True)
    app._recover_jobs_from_disk()
    assert app.jobs["job-1"]["status"] == "completed"


def test_recovery_serves_only_the_clips_that_exist(outdir):
    """An interrupted render must not hand the UI six URLs that answer 404."""
    _make_job(outdir, clips=7, rendered=2, marker=False)
    app._recover_jobs_from_disk()
    clips = (app.jobs["job-1"].get("result") or {}).get("clips") or []
    assert len(clips) == 2


def test_a_finished_job_lists_every_clip_even_if_one_failed_to_render(outdir):
    """A clip that failed inside a finished run stays listed: the user asked
    for it, and the marker says the pipeline did reach the end."""
    _make_job(outdir, clips=3, rendered=2, marker=True)
    app._recover_jobs_from_disk()
    clips = (app.jobs["job-1"].get("result") or {}).get("clips") or []
    assert len(clips) == 3


def test_resume_keeps_the_manifest_of_a_job_with_metadata_but_no_marker(outdir):
    job = _make_job(outdir, clips=7, rendered=1, marker=False)
    app._resume_interrupted_jobs()
    assert (job / app._RESUME_FILE).exists(), "the job must stay queued for a retry"
    assert app.jobs["job-1"]["status"] == "queued"


def test_resume_clears_the_manifest_when_the_marker_exists(outdir):
    job = _make_job(outdir, clips=3, rendered=3, marker=True)
    app._resume_interrupted_jobs()
    assert not (job / app._RESUME_FILE).exists()


def test_resume_still_resumes_a_job_whose_analysis_never_finished(outdir):
    """No metadata at all: nothing changed for that case."""
    job = outdir / "job-2"
    job.mkdir()
    (job / app._RESUME_FILE).write_text(json.dumps(
        {"cmd": ["python", "main.py"], "priority": 0, "attempts": 0, "watermark": False}))
    app._resume_interrupted_jobs()
    assert app.jobs["job-2"]["status"] == "queued"


def test_the_marker_name_is_the_same_in_both_processes():
    """app.py writes nothing; main.py stamps. One literal, two importers."""
    main = pytest.importorskip("main")
    assert main.JOB_DONE_FILE == app.JOB_DONE_FILE
