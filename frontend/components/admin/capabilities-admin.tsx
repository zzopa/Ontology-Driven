"use client";

import { useCallback, useState } from "react";
import { Cpu, Pencil, Plus, RefreshCw, Save, Trash2 } from "lucide-react";
import { govStatusBadge, type OpsCapability } from "@/lib/admin";
import { request } from "@/lib/api";
import { Badge, Button, Card, Modal } from "../ui";
import { Empty, Loading, useAction, useResource } from "./shared";

export function CapabilitiesAdminPanel() {
  const caps = useResource<{ ok: boolean; items: OpsCapability[] }>("/api/admin/capabilities");
  const action = useAction();
  const [form, setForm] = useState<OpsCapability & { coverageText: string; alternativesText: string } | null>(null);
  const close = useCallback(() => setForm(null), []);

  return (
    <>
      <div className="admin-section-head">
        <div>
          <h2>能力注册</h2>
          <p>配置已接入的数据能力域、允许字段边界、阶段状态与超范围兜底话术（cap_capability）</p>
        </div>
        <div className="manager-tools">
          <Button onClick={caps.reload}><RefreshCw size={13} />刷新</Button>
          <Button variant="primary" onClick={() => setForm({
            code: "", name: "", domain: "FIMS", phase: 1, status: "AVAILABLE",
            coverageJson: {}, fallbackMessage: "", alternatives: [],
            coverageText: "{}", alternativesText: "",
          })}>
            <Plus size={14} />注册能力
          </Button>
        </div>
      </div>

      {action.feedback}
      <Card className="card-pad">
        <Loading {...caps} />
        <div className="table-wrap">
          <table className="data-table attribute-table admin-table">
            <thead>
              <tr>
                <th>能力编码</th>
                <th>能力名称</th>
                <th>域 / 阶段</th>
                <th>状态</th>
                <th>兜底话术与替代建议</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {(caps.data?.items || []).map(item => {
                const st = govStatusBadge(item.status);
                const fields = Array.isArray(item.coverageJson?.fields) ? (item.coverageJson.fields as string[]) : [];
                return (
                  <tr key={item.code}>
                    <td><code className="gov-code">{item.code}</code></td>
                    <td>
                      <strong>{item.name}</strong>
                      {fields.length > 0 && <small>开放字段 {fields.length} 个</small>}
                    </td>
                    <td><Badge>{item.domain}</Badge> <Badge>P{item.phase}</Badge></td>
                    <td><Badge tone={st.tone}>{st.label}</Badge></td>
                    <td>
                      <div className="gov-clamp" title={item.fallbackMessage}>{item.fallbackMessage || "—"}</div>
                      {item.alternatives?.length > 0 && <small>建议：{item.alternatives.join("；")}</small>}
                    </td>
                    <td>
                      <div className="manager-tools">
                        <Button variant="ghost" onClick={() => setForm({
                          ...item,
                          coverageText: JSON.stringify(item.coverageJson || {}, null, 2),
                          alternativesText: (item.alternatives || []).join("\n"),
                        })}><Pencil size={13} /></Button>
                        <Button variant="ghost" onClick={() => action.run(async () => {
                          await request(`/api/admin/capabilities/${encodeURIComponent(item.code)}`, { method: "DELETE" });
                          caps.reload();
                        }, `已删除能力 ${item.code}`)}><Trash2 size={13} /></Button>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        {!caps.data?.items.length && !caps.loading && <Empty>暂无注册能力</Empty>}
      </Card>

      {form && (
        <Modal title="编辑能力注册配置" wide onClose={close}>
          <form onSubmit={e => {
            e.preventDefault();
            action.run(async () => {
              const cov = JSON.parse(form.coverageText || "{}");
              await request("/api/admin/capabilities", {
                method: "POST",
                body: JSON.stringify({
                  ...form,
                  coverageJson: cov,
                  alternatives: form.alternativesText.split("\n").map(s => s.trim()).filter(Boolean),
                }),
              });
              setForm(null);
              caps.reload();
            }, "能力注册配置已保存");
          }}>
            <div className="form-grid">
              <label className="form-field">能力编码<input required value={form.code} onChange={e => setForm({ ...form, code: e.target.value })} placeholder="如 flight.dynamics" /></label>
              <label className="form-field">能力名称<input required value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} /></label>
              <label className="form-field">域<select value={form.domain} onChange={e => setForm({ ...form, domain: e.target.value })}><option value="FLIGHT">FLIGHT</option><option value="FIMS">FIMS</option><option value="PRODUCTION">PRODUCTION</option><option value="ONTIME">ONTIME</option><option value="REPORT">REPORT</option><option value="SAFETY_GAP">SAFETY_GAP</option><option value="OPS">OPS</option></select></label>
              <label className="form-field">阶段（Phase 1-9）<input type="number" min={1} max={9} value={form.phase} onChange={e => setForm({ ...form, phase: Number(e.target.value) || 1 })} /></label>
              <label className="form-field">状态<select value={form.status} onChange={e => setForm({ ...form, status: e.target.value })}><option value="AVAILABLE">AVAILABLE（可用）</option><option value="PLANNED">PLANNED（规划中）</option><option value="UNAVAILABLE">UNAVAILABLE（不可用）</option></select></label>
              <label className="form-field form-span">超范围或未开放时兜底话术<textarea rows={2} value={form.fallbackMessage} onChange={e => setForm({ ...form, fallbackMessage: e.target.value })} /></label>
              <label className="form-field form-span">推荐替代问法（每行一条）<textarea rows={2} value={form.alternativesText} onChange={e => setForm({ ...form, alternativesText: e.target.value })} /></label>
              <label className="form-field form-span">覆盖元信息 JSON（coverage_json）<textarea rows={4} value={form.coverageText} onChange={e => setForm({ ...form, coverageText: e.target.value })} /></label>
            </div>
            <div className="modal-actions">
              <Button onClick={close}>取消</Button>
              <Button type="submit" variant="primary" disabled={action.busy}><Save size={13} />保存</Button>
            </div>
          </form>
        </Modal>
      )}
    </>
  );
}
