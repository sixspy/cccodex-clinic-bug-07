"""收齐闸假绿（clinic-bug-07）红测：

- 徽标必须诚实：不得硬亮"收齐已亮"（假绿）。
- 落定（甲路·切流账册）必须写耐久包行 + 提交档行入 meta 表，否则不算落点。
- 徽标亮而键号无落点（拧花）时，必须挡未收齐入库（抓取/扫描/入库/补采排队）。
- 钉定后拒回刷；两侧争用缺锁不得双成功。
- 乙路双写未择定，不得与甲路并存；半修皮面废弃。
"""
from __future__ import annotations

import threading

import pytest

from app.db import get_db
from app.services import backfill, qc, reconcile, visits
from app.services import gather_gate, gather_gate_cutover, gather_gate_dualwrite
from app.services.errors import ServiceError


def _meta(db_path, key):
    with get_db(db_path) as conn:
        row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None


def _meta_keys(db_path, prefix):
    with get_db(db_path) as conn:
        rows = conn.execute(
            "SELECT key, value FROM meta WHERE key LIKE ? ORDER BY key",
            (prefix + "%",),
        ).fetchall()
        return {r["key"]: r["value"] for r in rows}


def _durable_packs(db_path):
    return {
        k: v
        for k, v in _meta_keys(db_path, "gather_gate_pack_").items()
        if not k.endswith("_decoy")
    }


def _clear_all_gaps(db_path):
    """走主链把全部缺口行补采成合格入库，使真值达到收齐（READY 前提）。"""
    rows = visits.list_rows(db_path=db_path)
    gaps = [
        r
        for r in rows
        if r["quality"] != "合格" and r["status"] in ("待检", "待补采")
    ]
    assert gaps, "种子数据应自带缺口行"
    for r in gaps:
        try:
            job = backfill.enqueue_from_row(r["id"], db_path=db_path)
        except ServiceError:
            job = next(
                j
                for j in backfill.list_jobs(db_path=db_path, row_id=r["id"])
                if j["status"] == "排队"
            )
        done = backfill.run_job(job["id"], succeed=True, db_path=db_path)
        assert done["status"] == "成功"


def test_seed_gate_state_is_honest(db_path):
    snap = gather_gate.snapshot(db_path)
    assert snap["panel_chip"] == "未收齐", "种子态徽标不得假绿"
    assert snap["truth"] == "未收齐"
    assert snap["active_read"] == "old_track"
    assert snap["aligned"] is False
    assert snap["panel_pack_mismatch"] is False, "诚实未收齐不算拧花"
    assert gather_gate.gate_allows_pipeline(db_path) is True, "开闸应放行主链"
    assert _durable_packs(db_path) == {}, "不得残留虚收齐键号"
    assert _meta(db_path, "gather_gate_gate_bypass") == "0"


def test_settle_refused_while_gaps_remain(db_path):
    with pytest.raises(ServiceError) as exc:
        gather_gate.commit_action({"ref": 1}, db_path)
    assert exc.value.status_code == 409
    assert "缺口" in exc.value.message or "未收齐" in exc.value.message
    # 落定失败不得落任何闸行
    assert _meta(db_path, "gather_gate_active_read") == "old_track"
    assert _durable_packs(db_path) == {}
    assert _meta(db_path, "gather_gate_cutover_fp") in ("", None)
    assert _meta_keys(db_path, "gather_gate_archive_") == {}


def test_settle_success_persists_durable_gate_row(db_path):
    _clear_all_gaps(db_path)
    result = gather_gate.commit_action({"ref": "e2e"}, db_path)
    assert result["ok"] is True
    snap = gather_gate.snapshot(db_path)
    assert snap["state"] == "NAILED"
    assert snap["aligned"] is True
    assert snap["active_read"] == "cutover_book"
    assert snap["reject_refresh"] is True
    fp = snap["cutover_fp"]
    assert fp and fp != "stale-fp", "键号必须是真实指纹而非虚号"
    # 收齐闸落点必须入表：耐久包行 + 提交档行
    durable = _durable_packs(db_path)
    assert len(durable) == 1
    assert fp in next(iter(durable.values()))
    archives = _meta_keys(db_path, "gather_gate_archive_")
    assert len(archives) == 1
    assert fp in next(iter(archives.values()))
    assert _meta(db_path, "gather_gate_gate_bypass") == "0"
    with get_db(db_path) as conn:
        n = conn.execute(
            "SELECT COUNT(*) AS c FROM audit_events WHERE action='收齐闸钉定'"
        ).fetchone()["c"]
    assert n == 1, "钉定必须写审计"


def test_pipeline_blocked_when_badge_lit_without_key(db_path):
    assert gather_gate.gate_allows_pipeline(db_path) is True, "开闸主链应放行"
    _clear_all_gaps(db_path)
    snap = gather_gate.snapshot(db_path)
    assert snap["state"] == "READY"
    assert snap["panel_chip"] == "收齐已亮"
    assert snap["panel_pack_mismatch"] is True, "徽标亮而键号无落点即拧花"
    assert gather_gate.gate_allows_pipeline(db_path) is False
    batch = next(b for b in visits.list_batches(db_path) if b["status"] == "采集中")
    with pytest.raises(ServiceError) as e1:
        visits.mock_crawl(batch["id"], count=1, db_path=db_path)
    assert "收齐闸" in e1.value.message
    with pytest.raises(ServiceError) as e2:
        qc.scan_batch(batch["id"], db_path=db_path)
    assert "收齐闸" in e2.value.message
    with pytest.raises(ServiceError) as e3:
        visits.ingest_ok_rows(batch["id"], db_path=db_path)
    assert "收齐闸" in e3.value.message
    pending_row = next(
        r
        for r in visits.list_rows(db_path=db_path, batch_id=batch["id"])
        if r["status"] == "待检"
    )
    with pytest.raises(ServiceError) as e4:
        backfill.enqueue_from_row(pending_row["id"], db_path=db_path)
    assert "收齐闸" in e4.value.message


def test_pipeline_refused_after_nail(db_path):
    _clear_all_gaps(db_path)
    gather_gate.commit_action({"ref": "nail"}, db_path)
    batch = next(b for b in visits.list_batches(db_path) if b["status"] == "采集中")
    with pytest.raises(ServiceError) as exc:
        visits.mock_crawl(batch["id"], count=1, db_path=db_path)
    assert "拒回刷" in exc.value.message or "钉定" in exc.value.message
    with pytest.raises(ServiceError):
        visits.ingest_ok_rows(batch["id"], db_path=db_path)
    with pytest.raises(ServiceError):
        qc.scan_batch(batch["id"], db_path=db_path)


def test_concurrent_settle_has_single_winner(db_path):
    _clear_all_gaps(db_path)
    results: list[dict] = []
    errors: list[ServiceError] = []
    barrier = threading.Barrier(6)

    def worker():
        try:
            barrier.wait(timeout=15)
            results.append(gather_gate.commit_action({"race": 1}, db_path))
        except ServiceError as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert len(results) == 1, "争用缺锁会双成功，必须只有一侧钉定"
    assert len(errors) == 5
    assert all(e.status_code == 409 for e in errors)
    assert len(_durable_packs(db_path)) == 1, "钉点互相踩掉会留下多行耐久包"


def test_dual_track_refused_single_side_only(db_path):
    with pytest.raises(ServiceError) as exc:
        gather_gate_dualwrite.apply_dualwrite("试水", db_path)
    assert exc.value.status_code == 409
    assert _meta(db_path, "gather_gate_active_read") == "old_track", "两轨不得并存"


def test_half_fix_skin_refused(db_path):
    with pytest.raises(ServiceError) as exc:
        gather_gate.reconcile_display(db_path)
    assert exc.value.status_code == 409


def test_read_paths_have_no_side_effects(db_path):
    _clear_all_gaps(db_path)
    gather_gate.commit_action({"ref": "ro"}, db_path)
    before = _meta_keys(db_path, "gather_gate_")
    gather_gate.snapshot(db_path)
    gather_gate.dashboard(db_path)
    gather_gate_cutover.board(db_path)
    gather_gate_dualwrite.dual_board(db_path)
    reconcile.dashboard(db_path)  # 内含 gather_gate_audit 汇总
    after = _meta_keys(db_path, "gather_gate_")
    assert before == after, "读轨不得回写闸状态（读轨停旧/回刷均属假绿根源）"


def test_cutover_note_requires_nail(db_path):
    with pytest.raises(ServiceError):
        gather_gate_cutover.append_cutover(1, "未钉先记", db_path)
    _clear_all_gaps(db_path)
    gather_gate.commit_action({"ref": "note"}, db_path)
    out = gather_gate_cutover.append_cutover(7, "钉后补记", db_path)
    assert out["ref_id"] == 7
    notes = _meta_keys(db_path, "gather_gate_cutover_note_")
    assert any("钉后补记" in v for v in notes.values())


def test_dashboard_badge_tracks_truth(db_path):
    dash = gather_gate.dashboard(db_path)
    assert dash["chip"] == "未收齐"
    _clear_all_gaps(db_path)
    dash = gather_gate.dashboard(db_path)
    assert dash["chip"] == "收齐已亮"
    assert dash["state"] == "READY"
    assert dash["panel_pack_mismatch"] is True
    gather_gate.commit_action({"ref": "dash"}, db_path)
    dash = gather_gate.dashboard(db_path)
    assert dash["chip"] == "收齐已亮"
    assert dash["state"] == "NAILED"
    assert dash["panel_pack_mismatch"] is False
    assert dash["aligned"] is True
