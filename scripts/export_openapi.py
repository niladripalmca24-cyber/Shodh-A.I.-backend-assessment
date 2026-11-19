import json
import os
import sys

# Ensure project root is in PYTHONPATH
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.main import app

def export_spec():
    openapi_schema = app.openapi()
    output_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "docs", "openapi.json")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(openapi_schema, f, indent=2)
    print(f"OpenAPI 3.1 specification successfully exported to: {output_path}")

if __name__ == "__main__":
    export_spec()
