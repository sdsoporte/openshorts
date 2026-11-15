"""Load a local .env without letting file permissions take the process down.

``python-dotenv``'s ``load_dotenv()`` raises when the file exists but the
process cannot read it. That is exactly what a correctly-permissioned secrets
file looks like from inside a container running as a different user: a host
``.env`` at 0600 owned by the host user, mounted at ``/app``, read by
``appuser`` (999) answers ``PermissionError`` and every import of
``s3_uploader`` — and therefore of ``app.py`` — dies before the server starts.

Neither case is worth a crash: Compose's ``env_file`` already injects every
variable, so a failed read is a no-op, and a readable file is a convenience for
running ``main.py`` outside Docker. Nothing in the pipeline depends on this
succeeding.
"""
from __future__ import annotations

import os

from dotenv import load_dotenv

# A deployment that manages its environment itself (Compose env_file, Coolify,
# systemd) can switch the file off entirely. The test suite sets it so a
# developer's real .env cannot leak into an assertion.
DISABLE_ENV = "OPENSHORTS_NO_DOTENV"


def load_env(*args, **kwargs) -> bool:
    """``load_dotenv()`` that returns False instead of raising OSError."""
    if (os.environ.get(DISABLE_ENV) or "").strip().lower() in ("1", "true", "yes"):
        return False
    try:
        return bool(load_dotenv(*args, **kwargs))
    except OSError:
        return False
