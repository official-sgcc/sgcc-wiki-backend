"""Keep the existing EC2 service entry point usable during uv migration."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / 'src'))

from sgcc_wiki_backend.main import app  # noqa: E402
