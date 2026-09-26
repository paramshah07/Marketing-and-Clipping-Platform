"""Write backend/openapi.json from the FastAPI app, no server needed. The frontend's generated
client (npm run gen:api) reads the committed file. Run after any API change:

    docker compose run --rm --no-deps api python scripts/dump_openapi.py
"""

import json
from pathlib import Path

from app.main import app

out = Path(__file__).resolve().parents[1] / "openapi.json"
out.write_text(json.dumps(app.openapi(), indent=2) + "\n")
print(f"wrote {out}")
