"""Loads settings from .env so no other file has to know where they come from."""

import os
import sys

from dotenv import load_dotenv

load_dotenv()

REQUIRED = ["OPENROUTER_API_KEY", "BASE_URL", "MODEL"]

missing = [name for name in REQUIRED if not os.getenv(name)]
if missing:
    sys.exit(f"Missing in .env: {', '.join(missing)}. Copy .env.example to .env and fill it in.")

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
BASE_URL = os.getenv("BASE_URL")
MODEL = os.getenv("MODEL")


if __name__ == "__main__":
    # Never print the key itself, only whether it's there.
    print(f"Model:    {MODEL}")
    print(f"Base URL: {BASE_URL}")
    print("config OK")
