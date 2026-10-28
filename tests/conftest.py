import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
for sub in ("agent", "orchestrator", "infra", "scripts"):
    sys.path.insert(0, os.path.join(ROOT, sub))

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
