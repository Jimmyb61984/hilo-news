import sys
import yaml
import json
from pathlib import Path
from jsonschema import validate, ValidationError

# Paths (repo root, not app/)
ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
SCHEMAS = ROOT / "schemas"


def _load_yaml(p: Path):
    with p.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _load_json(p: Path):
    with p.open("r", encoding="utf-8") as f:
        return json.load(f)


def validate_file(data_file: Path, schema_file: Path, name: str) -> bool:
    try:
        data = _load_yaml(data_file)
        schema = _load_json(schema_file)
        validate(instance=data, schema=schema)
        print(f"[OK] {name} validated successfully")
        return True
    except FileNotFoundError:
        print(f"[ERROR] Missing file: {data_file}")
        return False
    except ValidationError as e:
        print(f"[ERROR] {name} failed validation: {e.message}")
        return False
    except Exception as e:
        print(f"[ERROR] Unexpected error validating {name}: {e}")
        return False


def main():
    ok = True
    ok &= validate_file(DATA / "leagues.yaml", SCHEMAS / "leagues.schema.json", "leagues.yaml")
    ok &= validate_file(DATA / "teams.yaml", SCHEMAS / "teams.schema.json", "teams.yaml")
    ok &= validate_file(DATA / "sources.yaml", SCHEMAS / "sources.schema.json", "sources.yaml")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
