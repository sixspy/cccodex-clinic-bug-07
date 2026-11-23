import { api, escapeHtml, fetchGate, gateChipHtml } from '../api.js';

export async function renderSettings(el) {
  const data = await api('/api/settings');
  const items = data.items || [];
  const snapshot = data.snapshot || {};

  el.innerHTML = `
    <div class="card">
      <h2>台参数</h2>
      <p class="muted">控制质控自动扫描开关、补采最大次数、配额告警与对账口径标签等。修改会写审计。</p>
      <div class="grid">
        <div class="stat"><div class="n">${snapshot.backfill_max_attempt || '-'}</div><div class="l">补采最大次数</div></div>
        <div class="stat"><div class="n">${snapshot.qc_auto_scan || '-'}</div><div class="l">自动质控</div></div>
        <div class="stat"><div class="n">${snapshot.site_quota_warn || '-'}</div><div class="l">配额告警阈值</div></div>
        <div class="stat"><div class="n">${escapeHtml((snapshot.clinic_timezone || '-').slice(0, 12))}</div><div class="l">时区</div></div>
      </div>
    </div>
    <div class="card">
      <table>
        <thead><tr><th>键</th><th>说明</th><th>当前值</th><th>新值</th><th>操作</th></tr></thead>
        <tbody>
          ${items.map((s) => `
            <tr>
              <td><code>${escapeHtml(s.key)}</code></td>
              <td>${escapeHtml(s.label)}</td>
              <td><strong>${escapeHtml(s.value)}</strong></td>
              <td><input data-key="${escapeHtml(s.key)}" value="${escapeHtml(s.value)}" /></td>
              <td><button data-save="${escapeHtml(s.key)}">保存</button></td>
            </tr>`).join('')}
        </tbody>
      </table>
      <div id="msg" class="muted" style="margin-top:10px"></div>
    </div>
    <div class="card">
      <h2>预留切口（后续题面）</h2>
      <ul class="muted">
        <li>访视行跨中心去重策略与冲突裁决面板</li>
        <li>方案换版后存量批次字段兼容扫描</li>
        <li>补采任务分片与并发上限</li>
        <li>对账报告导出与差异工单</li>
      </ul>
      <div class="detail-box">${escapeHtml(JSON.stringify(snapshot, null, 2))}</div>
    </div>`;

  const msg = el.querySelector('#msg');
  el.querySelectorAll('[data-save]').forEach((btn) => {
    btn.onclick = async () => {
      const key = btn.dataset.save;
      const input = el.querySelector(`input[data-key="${key}"]`);
      try {
        const updated = await api('/api/settings/update', {
          method: 'POST',
          body: JSON.stringify({ key, value: input.value }),
        });
        msg.className = 'muted';
        msg.textContent = `已保存 ${updated.key} = ${updated.value}`;
        await renderSettings(el);
      } catch (e) {
        msg.className = 'err';
        msg.textContent = e.message;
      }
    };
  });

  try {
    const dash = await fetchGate();
    const stateLabel = { OPEN: '开闸（有缺口，管道放行）', READY: '已收齐未钉定（徽标亮而键号无落点，管道挡下）', NAILED: '已钉定（拒回刷）' }[dash.state] || dash.state;
    const packRows = (dash.rows || [])
      .filter((r) => !String(r.key).endsWith('_decoy'))
      .map((r) => `<tr><td><code>${escapeHtml(r.key)}</code></td><td>${escapeHtml(r.value)}</td></tr>`)
      .join('');
    el.innerHTML += `<div class="card" style="margin-top:1rem">
      <h3>收齐钉定（甲路 · 切流账册）</h3>
      <p>${gateChipHtml(dash, { withKey: true })}</p>
      <p class="muted">状态 ${escapeHtml(stateLabel)} · 读轨 ${escapeHtml(dash.active_read || '-')} · 收齐键号 ${escapeHtml(dash.cutover_fp || '（无落点）')} · 拒回刷 ${dash.reject_refresh ? '开' : '关'} · 提交档 ${dash.archive_count || 0} 条 · 对齐 ${dash.aligned ? '是' : '否'}</p>
      <p class="muted">真值 ${escapeHtml(dash.truth || '-')} · 缺口 ${(dash.counts && dash.counts.gaps) ?? '-'} · 开放发现 ${(dash.counts && dash.counts.open_findings) ?? '-'} · 补采中 ${(dash.counts && dash.counts.pending_jobs) ?? '-'}</p>
      <div class="row">
        <button id="go_gather_gate" type="button">落台钉定</button>
        <button id="cut_gather_gate" type="button" class="ghost">切流账册补记</button>
      </div>
      <div id="gateMsg" class="muted" style="margin-top:8px"></div>
      <table style="margin-top:8px">
        <thead><tr><th>耐久包行（收齐闸落点）</th><th>内容</th></tr></thead>
        <tbody>${packRows || '<tr><td colspan="2" class="muted">尚无落点：未钉定</td></tr>'}</tbody>
      </table>
    </div>`;
    const gateMsg = el.querySelector('#gateMsg');
    const go = el.querySelector('#go_gather_gate');
    if (go) go.onclick = async () => {
      try {
        const r = await api('/api/gather_gate/commit', { method: 'POST', body: JSON.stringify({ ref: 1 }) });
        gateMsg.className = 'muted';
        gateMsg.textContent = r.message || '已钉定';
      } catch (e) {
        gateMsg.className = 'err';
        gateMsg.textContent = e.message;
        return;
      }
      await renderSettings(el);
    };
    const cut = el.querySelector('#cut_gather_gate');
    if (cut) cut.onclick = async () => {
      try {
        const r = await api('/api/gather_gate/cutover', { method: 'POST', body: JSON.stringify({ ref_id: Date.now() % 100000, note: '收齐闸补记' }) });
        gateMsg.className = 'muted';
        gateMsg.textContent = `已补记 ref=${r.ref_id}`;
      } catch (e) {
        gateMsg.className = 'err';
        gateMsg.textContent = e.message;
        return;
      }
      await renderSettings(el);
    };
  } catch (e) { /* optional surface */ }

}
