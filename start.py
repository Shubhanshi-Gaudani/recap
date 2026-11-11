"""Start here: python3 start.py — guided setup and build (see recap/setup.py)."""
from recap.setup import main

if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        raise SystemExit("\nStopped. Nothing was saved.")
