"""收齐闸（甲路·切流账册）：徽标派生自真值，落定写耐久包并拒回刷。

状态机：
- OPEN   有缺口：闸未钉，抓取/质控/入库/补采排队放行。
- READY  真值已收齐但闸未钉定：徽标亮而键号无落点（拧花），挡未收齐入库。
- NAILED 已钉定：切流账册键号落库，拒回刷，管道写动一律 409。

乙路（双写 live_flag）未择定，两轨不并存；见 gather_gate_dualwrite。
"""
from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime

from app.db import get_db
from app.services import audit as audit_svc
from app.services.errors import ServiceError

_SLUG = "gather_gate"
_OK = "收齐已亮"
_BAD = "未收齐"
_TITLE = "收齐闸"
_OLD = "old_track"
_CUTOVER = "cutover_book"
_LIVE = "live_flag"  # 乙路读轨常量，仅保留供历史档读取，本題未择定
_STALE_FP = "stale-fp"

STATE_OPEN = "OPEN"
STATE_READY = "READY"
STATE_NAILED = "NAILED"


def _meta_get(conn, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def _meta_set(conn, key: str, value: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
        (key, value),
    )


def _collection_counts(conn) -> dict:
    gaps = conn.execute(
        """
        SELECT COUNT(*) AS c FROM visit_rows
        WHERE quality!='合格' AND status IN ('待检','待补采')
        """
    ).fetchone()["c"]
    open_findings = conn.execute(
        "SELECT COUNT(*) AS c FROM qc_findings WHERE status='开放'"
    ).fetchone()["c"]
    pending_jobs = conn.execute(
        """
        SELECT COUNT(*) AS c FROM backfill_jobs
        WHERE status IN ('排队','执行中')
        """
    ).fetchone()["c"]
    item_total = conn.execute("SELECT COUNT(*) AS c FROM visit_rows").fetchone()["c"]
    ok_total = conn.execute(
        "SELECT COUNT(*) AS c FROM visit_rows WHERE quality='合格'"
    ).fetchone()["c"]
    return {
        "gaps": gaps,
        "open_findings": open_findings,
        "pending_jobs": pending_jobs,
        "item_total": item_total,
        "ok_total": ok_total,
    }


def _live_truth(counts: dict) -> str:
    if (
        counts["gaps"] == 0
        and counts["open_findings"] == 0
        and counts["pending_jobs"] == 0
    ):
        return _OK
    return _BAD


def _nailed(conn) -> bool:
    return (
        _meta_get(conn, f"{_SLUG}_active_read", _OLD) == _CUTOVER
        and _meta_get(conn, f"{_SLUG}_reject_refresh", "0") == "1"
        and _meta_get(conn, f"{_SLUG}_cutover_fp", "") not in ("", _STALE_FP)
    )


def _state(conn) -> str:
    if _nailed(conn):
        return STATE_NAILED
    if _live_truth(_collection_counts(conn)) == _OK:
        return STATE_READY
    return STATE_OPEN


def _chip_for_state(state: str) -> str:
    return _OK if state in (STATE_READY, STATE_NAILED) else _BAD


def panel_chip(db_path: str | None = None) -> str:
    """徽标派生自闸状态，不得脱离真值硬亮。"""
    with get_db(db_path) as conn:
        return _chip_for_state(_state(conn))


def truth_flag(db_path: str | None = None) -> str:
    with get_db(db_path) as conn:
        return _live_truth(_collection_counts(conn))


def active_read_ref(db_path: str | None = None) -> str:
    with get_db(db_path) as conn:
        return _meta_get(conn, f"{_SLUG}_active_read", _OLD)


def cutover_fp(db_path: str | None = None) -> str:
    with get_db(db_path) as conn:
        return _meta_get(conn, f"{_SLUG}_cutover_fp", "")


def reject_refresh(db_path: str | None = None) -> bool:
    with get_db(db_path) as conn:
        return _meta_get(conn, f"{_SLUG}_reject_refresh", "0") == "1"


def live_flag(db_path: str | None = None) -> str:
    with get_db(db_path) as conn:
        return _meta_get(conn, f"{_SLUG}_live_flag", "")


def archive_rows(db_path: str | None = None) -> list[dict]:
    with get_db(db_path) as conn:
        rows = conn.execute(
            "SELECT key, value FROM meta WHERE key LIKE ? ORDER BY key",
            (f"{_SLUG}_archive_%",),
        ).fetchall()
        return [{"key": r["key"], "value": r["value"]} for r in rows]


def pack_rows(db_path: str | None = None) -> list[dict]:
    with get_db(db_path) as conn:
        rows = conn.execute(
            "SELECT key, value FROM meta WHERE key LIKE ? ORDER BY key",
            (f"{_SLUG}_pack_%",),
        ).fetchall()
        return [{"key": r["key"], "value": r["value"]} for r in rows]


def seed_skew(db_path: str | None = None) -> None:
    """播种诚实基线：徽标不亮、无键号落点、旧读轨、闸开、无旁路。"""
    with get_db(db_path) as conn:
        _meta_set(conn, f"{_SLUG}_panel_chip", _BAD)
        _meta_set(conn, f"{_SLUG}_truth", _BAD)
        _meta_set(conn, f"{_SLUG}_counter", "0")
        _meta_set(conn, f"{_SLUG}_gate_bypass", "0")
        _meta_set(conn, f"{_SLUG}_active_read", _OLD)
        _meta_set(conn, f"{_SLUG}_cutover_fp", "")
        _meta_set(conn, f"{_SLUG}_reject_refresh", "0")
        _meta_set(conn, f"{_SLUG}_live_flag", "")
        _meta_set(conn, f"{_SLUG}_mode", "")


def gate_allows_pipeline(db_path: str | None = None) -> bool:
    with get_db(db_path) as conn:
        return _state(conn) == STATE_OPEN


def assert_pipeline_open(db_path: str | None = None) -> None:
    """管道闸口：READY（拧花）与 NAILED（拒回刷）一律挡下。"""
    with get_db(db_path) as conn:
        state = _state(conn)
        if state == STATE_NAILED:
            fp = _meta_get(conn, f"{_SLUG}_cutover_fp", "")
            raise ServiceError(
                f"{_TITLE}已钉定（切流账册键号 {fp}），拒回刷："
                "禁止抓取/质控扫描/入库/补采写动",
                409,
            )
        if state == STATE_READY:
            raise ServiceError(
                f"{_TITLE}未钉定：收齐徽标已亮但收齐键号无落点，"
                "禁止未收齐入库；请先在台参数落台钉定",
                409,
            )


def commit_action(payload: dict | None = None, db_path: str | None = None) -> dict:
    """甲路落定：切流账册钉定。失败讲明白，成功马上写耐久包+提交档落库。

    BEGIN IMMEDIATE 抢占写锁，争用只有一侧能钉定，杜绝双成功互踩钉点。
    """
    payload = payload or {}
    result: dict | None = None
    try:
        with get_db(db_path) as conn:
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("BEGIN IMMEDIATE")
            if _nailed(conn):
                fp = _meta_get(conn, f"{_SLUG}_cutover_fp", "")
                raise ServiceError(
                    f"{_TITLE}已钉定（键号 {fp}），拒绝重复钉定（拒回刷）",
                    409,
                )
            counts = _collection_counts(conn)
            if _live_truth(counts) != _OK:
                raise ServiceError(
                    f"{_TITLE}落定失败：仍有缺口行{counts['gaps']} · "
                    f"开放发现{counts['open_findings']} · "
                    f"补采中{counts['pending_jobs']}，未收齐不得亮绿",
                    409,
                )
            seq = int(_meta_get(conn, f"{_SLUG}_counter", "0") or "0") + 1
            stamp = datetime.now().isoformat(timespec="seconds")
            fp = "gg-" + hashlib.sha256(
                f"{counts['item_total']}|{counts['ok_total']}|{seq}|{stamp}".encode()
            ).hexdigest()[:16]
            _meta_set(conn, f"{_SLUG}_counter", str(seq))
            # 耐久包行：物化包凭证，读轨专认
            _meta_set(
                conn,
                f"{_SLUG}_pack_{seq:04d}",
                f"{fp}|行{counts['item_total']}|合格{counts['ok_total']}|{stamp}",
            )
            # 提交档：马上写完落库
            _meta_set(
                conn,
                f"{_SLUG}_archive_{seq:04d}",
                f"settled|{fp}|{stamp}|{str(payload)[:80]}",
            )
            _meta_set(conn, f"{_SLUG}_cutover_fp", fp)
            _meta_set(conn, f"{_SLUG}_reject_refresh", "1")
            _meta_set(conn, f"{_SLUG}_active_read", _CUTOVER)
            _meta_set(conn, f"{_SLUG}_gate_bypass", "0")
            _meta_set(conn, f"{_SLUG}_truth", _OK)
            _meta_set(conn, f"{_SLUG}_panel_chip", _OK)
            _meta_set(conn, f"{_SLUG}_mode", _CUTOVER)
            _meta_set(conn, f"{_SLUG}_settled_at", stamp)
            # 清掉旧虚收齐键号残留，读轨只认耐久包
            conn.execute("DELETE FROM meta WHERE key=?", (f"{_SLUG}_pack_decoy",))
            result = {
                "ok": True,
                "chip": _OK,
                "title": _TITLE,
                "counter": seq,
                "state": STATE_NAILED,
                "active_read": _CUTOVER,
                "cutover_fp": fp,
                "reject_refresh": True,
                "archive_count": seq,
                "live_flag": "",
                "message": f"{_TITLE}已钉定：切流账册键号 {fp}，拒回刷",
            }
    except sqlite3.OperationalError as exc:
        if "locked" in str(exc).lower():
            raise ServiceError(
                f"{_TITLE}钉定争用未获锁，本次落定作废，请重试", 409
            ) from exc
        raise
    if result is not None:
        audit_svc.write_event(
            "收齐闸钉定",
            "gather_gate",
            result["counter"],
            f"键号{result['cutover_fp']}",
            db_path=db_path,
        )
        return result
    raise ServiceError(f"{_TITLE}落定失败：未知原因", 500)


def half_fix_display(db_path: str | None = None) -> dict:
    """半修皮面已废弃：只亮徽标不写闸行属于假绿根源。"""
    raise ServiceError(
        f"{_TITLE}半修皮面已废弃：只认切流账册钉定落点，不写闸行不算收工",
        409,
    )


def reconcile_display(db_path: str | None = None) -> dict:
    return half_fix_display(db_path)


def snapshot(db_path: str | None = None) -> dict:
    """只读快照：读轨不得回写闸状态。"""
    with get_db(db_path) as conn:
        counts = _collection_counts(conn)
        state = _state(conn)
        live = _live_truth(counts)
        ref = _meta_get(conn, f"{_SLUG}_active_read", _OLD)
        fp = _meta_get(conn, f"{_SLUG}_cutover_fp", "")
        rej = _meta_get(conn, f"{_SLUG}_reject_refresh", "0") == "1"
        packs = conn.execute(
            "SELECT key, value FROM meta WHERE key LIKE ? ORDER BY key",
            (f"{_SLUG}_pack_%",),
        ).fetchall()
        archives = conn.execute(
            "SELECT COUNT(*) AS c FROM meta WHERE key LIKE ?",
            (f"{_SLUG}_archive_%",),
        ).fetchone()["c"]
    chip = _chip_for_state(state)
    durable = [dict(r) for r in packs if not str(r["key"]).endswith("_decoy")]
    aligned = state == STATE_NAILED and live == _OK
    return {
        "title": _TITLE,
        "state": state,
        "panel_chip": chip,
        "truth": live,
        "pack_count": len(packs),
        "durable_count": len(durable),
        "active_read": ref,
        "cutover_fp": fp,
        "reject_refresh": rej,
        "archive_count": archives,
        "live_flag": live_flag(db_path),
        "aligned": aligned,
        "panel_pack_mismatch": chip == _OK and not aligned,
        "gate_bypass": False,
        "counts": counts,
    }


def dashboard(db_path: str | None = None) -> dict:
    snap = snapshot(db_path)
    rows = pack_rows(db_path)
    return {
        "title": _TITLE,
        "chip": snap["panel_chip"],
        "truth": snap["truth"],
        "state": snap["state"],
        "pack_count": snap["pack_count"],
        "durable_count": snap["durable_count"],
        "rows": rows,
        "active_read": snap["active_read"],
        "cutover_fp": snap["cutover_fp"],
        "reject_refresh": snap["reject_refresh"],
        "archive_count": snap["archive_count"],
        "live_flag": snap["live_flag"],
        "aligned": snap["aligned"],
        "panel_pack_mismatch": snap["panel_pack_mismatch"],
        "counts": snap["counts"],
        "ok_chip": _OK,
        "bad_chip": _BAD,
        "label": "收齐钉定",
        "read_tracks": {
            "old_track": _OLD,
            "cutover_book": _CUTOVER,
            "live_flag": _LIVE,
        },
        "chosen_track": _CUTOVER,
    }


def list_events(db_path: str | None = None) -> list[dict]:
    with get_db(db_path) as conn:
        rows = conn.execute(
            "SELECT key, value FROM meta WHERE key LIKE ? ORDER BY key",
            (f"{_SLUG}_evt_%",),
        ).fetchall()
        return [{"key": r["key"], "note": r["value"]} for r in rows]


def record_event(note: str, db_path: str | None = None) -> dict:
    """记切流事件：只写事件行，绝不再顺手亮徽标。"""
    import time

    with get_db(db_path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
            (f"{_SLUG}_evt_{time.time_ns()}", note or _BAD),
        )
    return dashboard(db_path)


def require_pack_or_raise(db_path: str | None = None) -> None:
    """专认物化包：未钉定（无耐久包落点）即拒。"""
    snap = snapshot(db_path)
    if not snap["aligned"]:
        raise ServiceError(f"{_TITLE}读轨未对齐，禁止放行", 409)
