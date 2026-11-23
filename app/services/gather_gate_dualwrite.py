"""收齐闸双写两轨（乙路）：本题未择定。骨架保留为只读历史档，写侧拒动。

择边结论：读轨落定甲路切流账册（cutover_book），两轨不并存；
乙路不得再亮镜像芯片、不得迁入 live_flag。
"""
from __future__ import annotations

from app.db import get_db
from app.services.errors import ServiceError
from app.services.gather_gate import (
    active_read_ref,
    archive_rows,
    dashboard,
    live_flag,
    truth_flag,
)

_SLUG = "gather_gate"
_OK = "收齐已亮"
_BAD = "未收齐"
_TITLE = "收齐闸"


def dual_chip(db_path: str | None = None) -> str:
    with get_db(db_path) as conn:
        row = conn.execute(
            "SELECT value FROM meta WHERE key=?",
            (f"{_SLUG}_dual_chip",),
        ).fetchone()
        return row["value"] if row else _BAD


def apply_dualwrite(note: str = "", db_path: str | None = None) -> dict:
    """乙路写侧未择定：两轨不并存，拒动。"""
    raise ServiceError(
        f"{_TITLE}乙路双写未择定：读轨已落定甲路切流账册，两轨不并存", 409
    )


def dual_board(db_path: str | None = None) -> dict:
    """只读乙路历史档面，供审计对照；无任何回写。"""
    dash = dashboard(db_path)
    return {
        "dual_chip": dual_chip(db_path),
        "truth": truth_flag(db_path),
        "active_read": active_read_ref(db_path),
        "archive": archive_rows(db_path),
        "live_flag": live_flag(db_path),
        "dash": dash,
        "fate": "dualwrite_decommissioned",
        "panel_pack_mismatch": dash["panel_pack_mismatch"],
        "title": f"{_TITLE}双写面（乙路未择定）",
    }


def half_dual(db_path: str | None = None) -> dict:
    """只读对照：乙路不再亮镜像芯片。"""
    return {
        "dual_chip": dual_chip(db_path),
        "truth": truth_flag(db_path),
        "active_read": active_read_ref(db_path),
    }
