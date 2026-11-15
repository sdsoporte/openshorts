import os
import sys

# Make the repo root importable so tests can import the app modules directly.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Tests always run the app in self-host (BYOK) mode. app.py freezes
# BILLING_ENABLED at import time and load_dotenv never overrides an existing
# variable, so this must be set here — before any test module imports app — or
# the suite's behavior would depend on the developer's personal .env.
os.environ["BILLING_ENABLED"] = "0"

# The developer's .env reaches this process two ways: python-dotenv reads it, and
# a container started with `env_file: .env` injects it. Either one makes the suite
# depend on whatever provider that file points at — eight tests in
# test_llm_backend broke exactly that way the first time a real .env existed.
# Cleared before collection so a test that wants a provider sets it itself.
for _name in [n for n in os.environ if n.startswith(("LLM_", "GEMINI_"))]:
    del os.environ[_name]

# Clearing the variables is not enough: importing app.py calls load_env(), which
# reads /app/.env all over again (and as root in a container it succeeds). The
# switch makes that a no-op for the whole suite.
os.environ["OPENSHORTS_NO_DOTENV"] = "1"
