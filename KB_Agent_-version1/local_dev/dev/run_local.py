"""
Run the agent locally exactly as AgentCore Runtime hosts it.

    python local_dev/dev/run_local.py

Serves POST /invocations and GET /ping on http://localhost:8080 . It reads
local_dev/.env (ENVIRONMENT=local is set here, because the deployed default
is "production"). Add READ_ONLY_MODE=true to that file, or to the shell,
before pointing it at real tables.
"""

import os
import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[2] / "app" / "dtp_operate_agent"

os.environ.setdefault("ENVIRONMENT", "local")

sys.path.insert(0, str(APP_DIR))

os.chdir(APP_DIR)

import main  # noqa: E402

if __name__ == "__main__":
    main.app.run()
