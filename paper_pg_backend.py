"""Adaptador transaccional para SQLite local o PostgreSQL privado PAPER.

El SQL de paper_engine_pro se conserva idéntico. En PostgreSQL todas las
tablas viven en el esquema trader_pro_paper, aislado del esquema public.

NO se ejecutan operaciones reales Binance. La contraseña PostgreSQL debe
proceder exclusivamente de Secrets/variables seguras.
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path


SCHEMA = "trader_pro_paper"
TABLES = {"account", "trades", "events", "worker_status"}


def is_postgres_target(value: str | Path) -> bool:
    v=str(value)
    return v.startswith("postgresql://") or v.startswith("postgres://")


def rewrite_postgres_sql(sql: str) -> str:
    """SQL SQLite acotado a los patrones que utiliza PAPER."""
    text=sql.strip()
    text=re.sub(r"\bINSERT\s+OR\s+IGNORE\s+INTO\b","INSERT INTO",text,flags=re.IGNORECASE)
    insert_ignored=bool(re.search(r"\bINSERT\s+OR\s+IGNORE\s+INTO\b",sql,flags=re.IGNORECASE))
    text=re.sub(
        r"\b(account|trades|events|worker_status)\b",
        lambda m:f"{SCHEMA}.{m.group(1)}",
        text,
    )
    text=re.sub(
        r"\bINTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT\b",
        "BIGSERIAL PRIMARY KEY",
        text,
        flags=re.IGNORECASE,
    )
    text=re.sub(r"\bMAX\s*\(\s*high_water_usdt\s*,\s*\?\s*\)",
                "GREATEST(high_water_usdt,?)",text,flags=re.IGNORECASE)
    text=text.replace("?","%s")
    if insert_ignored:
        text+=" ON CONFLICT DO NOTHING"
    return text


class PostgresPaperConnection:
    def __init__(self, url: str):
        # No redactar/reproducir el DSN con usuario/contraseña en logs.
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:
            raise RuntimeError("Instale psycopg[binary] para PostgreSQL PAPER.") from exc
        self._con=psycopg.connect(
            url,connect_timeout=10,row_factory=dict_row,
            prepare_threshold=None,sslmode="require",
        )
        self._con.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")

    def __enter__(self):
        return self

    def __exit__(self, typ, exc, tb):
        try:
            if typ:
                self._con.rollback()
            else:
                self._con.commit()
        finally:
            self._con.close()
        return False

    def execute(self, sql: str, args=()):
        if sql.strip().upper()=="BEGIN IMMEDIATE":
            # Serializa entradas, cierres, reset y actualizaciones de estado
            # de todos los procesos usando la misma cuenta virtual.
            return self._con.execute(
                f"SELECT id FROM {SCHEMA}.account WHERE id=1 FOR UPDATE"
            )
        return self._con.execute(rewrite_postgres_sql(sql),args)


def connect_paper(target: str | Path):
    if is_postgres_target(target):
        return PostgresPaperConnection(str(target))
    path=Path(target)
    path.parent.mkdir(parents=True,exist_ok=True)
    con=sqlite3.connect(str(path),timeout=8,isolation_level=None)
    con.row_factory=sqlite3.Row
    con.execute("PRAGMA busy_timeout=8000")
    con.execute("PRAGMA foreign_keys=ON")
    return con
