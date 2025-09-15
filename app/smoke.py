"""
Simple import smoke test. Run this before starting uvicorn to catch
syntax/import errors that would crash the Render dyno.
"""
import importlib
import sys

MODULES = [
    "app.db",
    "app.policy",
    "app.fetcher",
    "app.main",
]

def main():
    try:
        for m in MODULES:
            importlib.import_module(m)
        print("import-ok")
        sys.exit(0)
    except Exception as e:
        # Print full error for the platform logs, exit non-zero to fail fast.
        print(f"[smoke] import failure: {e}", file=sys.stderr)
        raise

if __name__ == "__main__":
    main()
