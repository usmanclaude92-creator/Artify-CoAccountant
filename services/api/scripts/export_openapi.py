"""Write the API's OpenAPI document to packages/api-client/openapi.json (deterministic).

CI regenerates it and fails if the committed file differs ("contract drift"). With --check the
file is not written; the exit code is 1 when it is stale."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from artify_api.main import create_app

TARGET = Path(__file__).resolve().parents[3] / "packages" / "api-client" / "openapi.json"


def main() -> int:
    rendered = json.dumps(create_app().openapi(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    if "--check" in sys.argv:
        if not TARGET.exists() or TARGET.read_text(encoding="utf-8") != rendered:
            print("openapi.json is out of date: run `make openapi`", file=sys.stderr)
            return 1
        print("openapi.json is up to date")
        return 0
    TARGET.write_text(rendered, encoding="utf-8")
    print(f"wrote {TARGET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
