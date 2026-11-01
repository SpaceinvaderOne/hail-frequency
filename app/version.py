"""Build/version info, baked in at image build time.

The Dockerfile passes --build-arg HF_VERSION / HF_BUILD_DATE (populated by CI
from the git tag + date) into ENV vars. Falls back to "dev" for local runs so
there's always *something* to show in Settings and /api/health.
"""

import os

VERSION = os.environ.get("HF_VERSION", "dev")
BUILD_DATE = os.environ.get("HF_BUILD_DATE", "")


def version_info() -> dict:
    return {"version": VERSION, "build_date": BUILD_DATE}
