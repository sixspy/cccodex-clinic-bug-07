"""收齐闸切流账册（甲路）：钉定后锁当刻指纹并拒回刷；补记须先钉定。"""
from __future__ import annotations

from app.db import get_db
from app.services.errors import ServiceError
from app.services.gather_gate import (
    active_read_ref,
    cutover_fp,
    dashboard,
    panel_chip,
    record_event,
    reject_refresh,
    snapshot,
    truth_flag,
)

_SLUG = "gather_gate"
_TITLE = "收齐闸"


def append_cutover(ref_id: int, note: str, db_path: str | None = None) -> dict:
    """钉定后向切流账册补记一条；未钉定拒写，免得软笔记冒充落点。"""
    snap = snapshot(db_path)
    if snap["state"] != "NAILED":
        raise ServiceError(
            f"{_TITLE}未钉定，切流账册拒写：请先落台钉定再补记", 409
        )
    with get_db(db_path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
            (f"{_SLUG}_cutover_note_{ref_id}", note or _TITLE),
        )
    record_event(f"cutover-note ref={ref_id} {note}", db_path)
    return {
        "ref_id": ref_id,
        "note": note,
        "panel": panel_chip(db_path),
        "truth": truth_flag(db_path),
        "active_read": active_read_ref(db_path),
        "cutover_fp": cutover_fp(db_path),
        "reject_refresh": reject_refresh(db_path),
    }


def cutover_rows(db_path: str | None = None) -> list[dict]:
    with get_db(db_path) as conn:
        rows = conn.execute(
            "SELECT key, value FROM meta WHERE key LIKE ? ORDER BY key",
            (f"{_SLUG}_cutover_%",),
        ).fetchall()
        return [
            {
                "key": r["key"],
                "value": r["value"],
                "chip": panel_chip(db_path),
                "active_read": active_read_ref(db_path),
            }
            for r in rows
        ]


def board(db_path: str | None = None) -> dict:
    dash = dashboard(db_path)
    dash["cutover"] = cutover_rows(db_path)
    dash["fate"] = "cutover_book"
    return dash
