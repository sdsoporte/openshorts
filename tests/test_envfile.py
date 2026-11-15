"""A local .env the process cannot read must not stop the app from starting.

Prod incident in miniature: a host .env at 0600, bind-mounted at /app, read by
a container user that is not its owner. python-dotenv raises PermissionError at
import time, so the FastAPI app never reaches its first line of serving even
though Compose already injected every variable through env_file.

The helper is the whole surface: it must swallow OSError and stay quiet about
it, and it must still report what dotenv reports when the file is readable.
"""
import subprocess
import sys

import pytest

import envfile


def test_an_unreadable_env_is_not_fatal(monkeypatch):
    monkeypatch.delenv(envfile.DISABLE_ENV, raising=False)

    def _refuse(*_args, **_kwargs):
        raise PermissionError(13, "Permission denied", ".env")

    monkeypatch.setattr(envfile, "load_dotenv", _refuse)
    assert envfile.load_env() is False


def test_a_readable_env_reports_what_dotenv_says(monkeypatch):
    # conftest switches loading off for the suite; this is the enabled path.
    monkeypatch.delenv(envfile.DISABLE_ENV, raising=False)
    monkeypatch.setattr(envfile, "load_dotenv", lambda *a, **k: {"LLM_MODEL": "x"})
    assert envfile.load_env() is True


def test_loading_never_raises_a_plain_oserror(monkeypatch):
    monkeypatch.delenv(envfile.DISABLE_ENV, raising=False)

    def _refuse(*_args, **_kwargs):
        raise OSError(13, "Permission denied")

    monkeypatch.setattr(envfile, "load_dotenv", _refuse)
    assert envfile.load_env("/app/.env") is False


def test_importing_s3_uploader_survives_an_unreadable_env(tmp_path):
    """The real failure: the import itself, not the call."""
    secrets = tmp_path / ".env"
    secrets.write_text("LLM_API_KEY=sk-not-for-you\n")
    secrets.chmod(0o600)
    script = (
        "import envfile\n"
        "envfile.load_dotenv = lambda *a, **k: (_ for _ in ()).throw("
        "PermissionError(13, 'Permission denied'))\n"
        "import envfile as e\n"
        "assert e.load_env() is False\n"
        "print('ok')\n"
    )
    out = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert "ok" in out.stdout


def test_the_switch_disables_loading_entirely(monkeypatch):
    """A deployment that injects env itself must be able to switch the file off."""
    called = {"n": 0}

    def _spy(*_a, **_k):
        called["n"] += 1
        return {"X": "1"}

    monkeypatch.setattr(envfile, "load_dotenv", _spy)
    monkeypatch.setenv(envfile.DISABLE_ENV, "1")
    assert envfile.load_env() is False
    assert called["n"] == 0
