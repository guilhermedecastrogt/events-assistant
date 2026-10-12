"""Database connection helpers."""

import os

import psycopg
from dotenv import find_dotenv, load_dotenv


def get_database_url() -> str:
    """Return DATABASE_URL from the environment or the nearest `.env` (searched from the cwd)."""
    load_dotenv(find_dotenv(usecwd=True))
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set; copy .env.example to .env or export it")
    return url


def connect(url: str | None = None, *, autocommit: bool = False) -> psycopg.Connection:
    """Open a psycopg connection to `url` (default: DATABASE_URL)."""
    return psycopg.connect(url or get_database_url(), autocommit=autocommit)
