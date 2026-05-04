"""
Unit-test conftest — stubs heavy optional dependencies that are not installed
in the ocr-dev conda environment (msal, etc.) so that tests can import the
production modules without needing the full dependency tree.
"""

from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock


def _stub_module(name: str, **attrs) -> types.ModuleType:
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    return mod


# ── msal ─────────────────────────────────────────────────────────────────────
# Only stubbed if not already installed.
if "msal" not in sys.modules:
    msal_stub = _stub_module(
        "msal",
        ConfidentialClientApplication=MagicMock,
        PublicClientApplication=MagicMock,
    )
    sys.modules["msal"] = msal_stub

# ── celery (may not be installed in ocr-dev) ─────────────────────────────────
if "celery" not in sys.modules:
    celery_stub = _stub_module("celery", Celery=MagicMock, task=MagicMock)
    celery_schedules = _stub_module("celery.schedules", crontab=MagicMock)
    sys.modules["celery"] = celery_stub
    sys.modules["celery.schedules"] = celery_schedules
