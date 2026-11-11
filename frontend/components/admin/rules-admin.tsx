"use client";

import { useCallback, useState } from "react";
import { BookCheck, Pencil, Plus, RefreshCw, Save, Trash2 } from "lucide-react";
import { govStatusBadge, type OpsRule } from "@/lib/admin";
import { request } from "@/lib/api";
import { Badge, Button, Card, Modal } from "../ui";
import { Empty, Loading, useAction, useResource } from "./shared";

export function RulesAdminPanel() {
  const rules = useResource<{ ok: boolean; items: OpsRule[] }>("/api/admin/rules");
  const action = useAction();
  const [form, setForm] = useState<(OpsRule & { outputText: string }) | null>(null);
  const close = useCallback(() => setForm(null), []);

  return (
    <>
      <div className="admin-section-head">
        <div>
          <h2>规则管理</h2>
          <p>管理延误判定、派生字段口径及版本化规则表达式（rule_definition / rule_version）</p>
        </div>
        <div className="manager-tools">
          <Button onClick={rules.reload}><RefreshCw size={13} />刷新</Button>
          <Button variant="primary" onClick={() => setForm({
            code: "", name: "", ruleType: "validation", domain: "FLIGHT",
            description: "", status: "PUBLISHED", scopeType: "global",
            expression: "", outputMapping: {}, outputText: "{}", priority: 100,
          })}>
            <Plus size={14} />新增规则
          </Button>
        </div>
      </div>

      {action.feedback}
      <Card className="card-pad">
        <Loading {...rules} />
        <div className="table-wrap">
          <table className="data-table attribute-table admin-table">
            <thead>
              <tr>
                <th>规则编码</th>
                <th>规则名称</th>
                <th>类型</th>
                <th>域</th>
                <th>规则表达式 / 描述</th>
                <th>状态</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {(rules.data?.items || []).map(r => {
                const st = govStatusBadge(r.status);
                return (
                  <tr key={r.code}>
                    <td><code className="gov-code">{r.code}</code></td>
                    <td><strong>{r.name}</strong><small>版本 v{r.versionNo || 1} · 优先级 {r.priority}</small></td>
                    <td><Badge>{r.ruleType}</Badge></td>
                    <td><Badge>{r.domain}</Badge></td>
                    <td>
                      <div>{r.description || "—"}</div>
                      {r.expression && <code className="gov-code gov-clamp">{r.expression}</code>}
                    </td>
                    <td><Badge tone={st.tone}>{st.label}</Badge></td>
                    <td>
                      <div className="manager-tools">
                        <Button variant="ghost" onClick={() => setForm({ ...r, outputText: JSON.stringify(r.outputMapping || {}, null, 2) })}><Pencil size={13} /></Button>
                        <Button variant="ghost" onClick={() => action.run(async () => {
                          await request(`/api/admin/rules/${encodeURIComponent(r.code)}`, { method: "DELETE" });
                          rules.reload();
                        }, `已删除规则 ${r.code}`)}><Trash2 size={13} /></Button>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        {!rules.data?.items.length && !rules.loading && <Empty>暂无业务规则定义</Empty>}
      </Card>

      {form && (
        <Modal title="编辑规则与版本" subtitle="每次保存将自动递增规则版本号" wide onClose={close}>
          <form onSubmit={e => {
            e.preventDefault();
            action.run(async () => {
              const outMap = JSON.parse(form.outputText || "{}");
              await request("/api/admin/rules", { method: "POST", body: JSON.stringify({ ...form, outputMapping: outMap }) });
              setForm(null);
              rules.reload();
            }, "规则版本已保存并生效");
          }}>
            <div className="form-grid">
              <label className="form-field">规则编码<input required value={form.code} onChange={e => setForm({ ...form, code: e.target.value })} placeholder="如 dep_delay_v1" /></label>
              <label className="form-field">规则名称<input required value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} /></label>
              <label className="form-field">类型<select value={form.ruleType} onChange={e => setForm({ ...form, ruleType: e.target.value })}><option value="derived_field">derived_field（派生字段）</option><option value="validation">validation（约束校验）</option><option value="scope">scope（范围约束）</option><option value="caliber">caliber（统计口径）</option><option value="routing">routing（路由约束）</option></select></label>
              <label className="form-field">域<select value={form.domain} onChange={e => setForm({ ...form, domain: e.target.value })}><option value="FLIGHT">FLIGHT</option><option value="FIMS">FIMS</option><option value="PRODUCTION">PRODUCTION</option><option value="ONTIME">ONTIME</option><option value="RESOURCE">RESOURCE</option><option value="TURNAROUND">TURNAROUND</option><option value="REPORT">REPORT</option><option value="OPS">OPS</option></select></label>
              <label className="form-field">状态<select value={form.status} onChange={e => setForm({ ...form, status: e.target.value })}><option value="PUBLISHED">PUBLISHED（已发布）</option><option value="DRAFT">DRAFT（草稿）</option><option value="ARCHIVED">ARCHIVED（归档）</option></select></label>
              <label className="form-field">优先级<input type="number" min={1} max={999} value={form.priority} onChange={e => setForm({ ...form, priority: Number(e.target.value) })} /></label>
              <label className="form-field form-span">规则说明<textarea rows={2} value={form.description} onChange={e => setForm({ ...form, description: e.target.value })} /></label>
              <label className="form-field form-span">判定表达式<textarea rows={2} value={form.expression} onChange={e => setForm({ ...form, expression: e.target.value })} placeholder="如 direction == 'DEP' && delayMinutes > delayThreshold" /></label>
              <label className="form-field form-span">运行时约束映射 JSON（output_mapping，可配置 applies_to / required_filters）<textarea rows={3} value={form.outputText} onChange={e => setForm({ ...form, outputText: e.target.value })} placeholder='{"applies_to":["std_flight_core"],"required_filters":{"flight_task_cd":"W/Z"}}' /></label>
            </div>
            <div className="modal-actions">
              <Button onClick={close}>取消</Button>
              <Button type="submit" variant="primary" disabled={action.busy}><Save size={13} />保存规则</Button>
            </div>
          </form>
        </Modal>
      )}
    </>
  );
}
