"use client";

import { useCallback, useState } from "react";
import { Compass, Pencil, Play, Plus, RefreshCw, Save, Trash2 } from "lucide-react";
import { govStatusBadge, type OpsPlanTemplate } from "@/lib/admin";
import { request } from "@/lib/api";
import { Badge, Button, Card, CopyButton, Modal, Notice, Spinner } from "../ui";
import { Empty, Loading, useAction, useResource } from "./shared";

type DryRunResult = {
  ok: boolean;
  resolved: boolean;
  question: string;
  source?: string;
  outcome?: string;
  answer?: string;
  error?: string;
  clarification?: { question: string; options: Array<{ id: string; label: string }> };
  hints?: {
    synonyms?: Array<{ term: string; concept_code: string }>;
    concepts?: Array<{ code: string; name: string; score: number }>;
    scenarios?: Array<{ code: string; name: string; matched_keywords: string[] }>;
  };
  plan?: Record<string, unknown>;
  sql?: string;
  params?: unknown[];
  coverageCheck?: { status: string; message?: string | null };
  capabilityStatus?: string;
  capabilityGate?: { allowed: boolean; status: string; capabilityCode?: string; message?: string; alternatives?: string[] } | null;
  matchedRules?: Array<{ code: string; name: string; versionNo?: number }>;
  matchedTemplate?: { code: string; name: string; planType?: string } | null;
  operationalWindow?: { start: string; end: string; startHour: number } | null;
};

const PRESETS = [
  "昨天靠桥率是多少？",
  "昨天出港延误有多少架次？",
  "昨天从上海来武汉天河的计划进港延误航班有哪些？",
  "昨天旅客人次是多少？",
  "今天各小时计划出港航班趋势",
  "今天各个航司计划出港航班排名",
];

type TemplateForm = {
  code: string;
  name: string;
  planType: string;
  triggerText: string;
  requiredCapsText: string;
  responseTemplate: string;
  status: string;
};

export function PlannerAdminPanel() {
  const templates = useResource<{ ok: boolean; items: OpsPlanTemplate[] }>("/api/admin/planner/templates");
  const action = useAction();
  const [tplForm, setTplForm] = useState<TemplateForm | null>(null);
  const closeTpl = useCallback(() => setTplForm(null), []);
  const [question, setQuestion] = useState(PRESETS[0]);
  const [dayMode, setDayMode] = useState("AUTO");
  const [running, setRunning] = useState(false);
  const [simError, setSimError] = useState("");
  const [result, setResult] = useState<DryRunResult | null>(null);

  async function runDryRun(qText?: string) {
    const q = (qText ?? question).trim();
    if (!q || running) return;
    if (qText) setQuestion(qText);
    setRunning(true);
    setSimError("");
    try {
      const res = await request<DryRunResult>("/api/admin/planner/dry-run", {
        method: "POST",
        body: JSON.stringify({ question: q, dayMode }),
      });
      setResult(res);
    } catch (e) {
      setSimError(e instanceof Error ? e.message : "仿真执行失败");
    } finally {
      setRunning(false);
    }
  }

  return (
    <>
      <div className="admin-section-head">
        <div>
          <h2>查询规划</h2>
          <p>配置查询规划模板、执行步骤并在线仿真测试本体意图识别与只读 SQL 生成链路</p>
        </div>
        <div className="manager-tools">
          <Button onClick={templates.reload}><RefreshCw size={13} />刷新模板</Button>
          <Button variant="primary" onClick={() => setTplForm({
            code: "", name: "", planType: "single_metric",
            triggerText: "", requiredCapsText: "", responseTemplate: "", status: "PUBLISHED",
          })}><Plus size={14} />新增模板</Button>
        </div>
      </div>

      {action.feedback}

      <div className="gov-stack">
        <Card className="card-pad">
          <div className="card-head">
            <div>
              <h3 className="section-title"><Compass size={15} />交互式本体规划仿真器（Dry-Run）</h3>
              <p className="helper">只校验本体同义词、场景规则、能力边界并编译只读 SQL，不执行全表检索或写入会话</p>
            </div>
            <select aria-label="运营日模式" value={dayMode} onChange={e => setDayMode(e.target.value)}>
              <option value="AUTO">运营日自动（跟随设置）</option>
              <option value="OPERATIONAL">强制按运营日</option>
              <option value="CALENDAR">按自然日</option>
            </select>
          </div>

          <form onSubmit={e => { e.preventDefault(); runDryRun(); }} className="admin-toolbar" style={{ marginTop: 10 }}>
            <input
              aria-label="输入演练问题"
              style={{ flex: 1 }}
              value={question}
              onChange={e => setQuestion(e.target.value)}
              placeholder="输入任意自然语言问题检验本体解析、规则与 SQL 生成…"
            />
            <Button type="submit" variant="primary" disabled={running}>
              {running ? <Spinner /> : <Play size={13} />}执行规划仿真
            </Button>
          </form>

          <div className="dryrun-chips">
            {PRESETS.map(p => (
              <button type="button" className="dryrun-chip" key={p} onClick={() => runDryRun(p)}>{p}</button>
            ))}
          </div>

          {simError && <Notice error>{simError}</Notice>}

          {result && (
            <div className="gov-stack" style={{ marginTop: 14 }}>
              {result.resolved ? (
                <>
                  <Notice>
                    解析成功（来源：{result.source}） · 能力状态：{result.capabilityStatus}
                    {result.operationalWindow ? ` · 运营日窗口 ${result.operationalWindow.start} ~ ${result.operationalWindow.end}` : ""}
                  </Notice>

                  <div className="sync-pill-row">
                    {(result.hints?.synonyms || []).map((s, i) => <Badge key={`s-${i}`} tone="green">同义词：{s.term} → {s.concept_code}</Badge>)}
                    {(result.hints?.scenarios || []).map((s, i) => <Badge key={`sc-${i}`}>场景：{s.name} ({s.code})</Badge>)}
                    {result.matchedTemplate && <Badge tone="green">模板：{result.matchedTemplate.name || result.matchedTemplate.code}</Badge>}
                    {(result.matchedRules || []).map(r => <Badge key={r.code}>规则：{r.name}</Badge>)}
                    {result.coverageCheck?.status && <Badge tone={result.coverageCheck.status === "AVAILABLE" ? "green" : "amber"}>数据覆盖：{result.coverageCheck.status}</Badge>}
                  </div>

                  <div>
                    <div className="sql-top"><strong style={{ fontSize: 13 }}>编译生成的参数化 SQL</strong>{result.sql && <CopyButton text={result.sql} label="复制 SQL" />}</div>
                    <pre className="code-block">{result.sql}</pre>
                    <small className="helper">绑定参数：{JSON.stringify(result.params || [])}</small>
                  </div>

                  <details className="process-details">
                    <summary>查看结构化 QueryPlan JSON</summary>
                    <pre className="code-block">{JSON.stringify(result.plan || {}, null, 2)}</pre>
                  </details>
                </>
              ) : (
                <>
                  <Notice error={result.outcome !== "clarification_needed"}>
                    {result.outcome === "clarification_needed" ? "需要澄清：" : result.outcome === "unsupported" ? "能力门控拦截：" : "未能解析该问题："}
                    {result.answer || result.error || "请补充完整查询条件"}
                  </Notice>
                  {!!result.clarification?.options.length && (
                    <div className="sync-pill-row">
                      {result.clarification.options.map(opt => <Badge key={opt.id}>可选项：{opt.label}</Badge>)}
                    </div>
                  )}
                  {!!result.capabilityGate?.alternatives?.length && (
                    <div className="dryrun-chips">
                      {result.capabilityGate.alternatives.map(alt => (
                        <button type="button" className="dryrun-chip" key={alt} onClick={() => runDryRun(alt)}>替代：{alt}</button>
                      ))}
                    </div>
                  )}
                </>
              )}
            </div>
          )}
        </Card>

        <Card className="card-pad">
          <div className="card-head">
            <h3 className="section-title">查询规划模板（plan_template / plan_step）</h3>
            <Badge>{templates.data?.items.length ?? 0} 套模板</Badge>
          </div>
          <Loading {...templates} />
          <div className="table-wrap">
            <table className="data-table attribute-table admin-table">
              <thead><tr><th>模板编码</th><th>名称</th><th>类型</th><th>触发概念</th><th>编排步骤</th><th>状态</th><th>操作</th></tr></thead>
              <tbody>
                {(templates.data?.items || []).map(t => {
                  const st = govStatusBadge(t.status);
                  return (
                    <tr key={t.code}>
                      <td><code className="gov-code">{t.code}</code></td>
                      <td><strong>{t.name}</strong><small>{t.responseTemplate}</small></td>
                      <td><Badge>{t.planType}</Badge></td>
                      <td>{(t.triggerConcepts || []).map(c => <Badge key={c}>{c}</Badge>)}</td>
                      <td><small>{(t.steps || []).map(s => `${s.stepOrder}.${s.stepCode}`).join(" → ") || "默认标准流程"}</small></td>
                      <td><Badge tone={st.tone}>{st.label}</Badge></td>
                      <td>
                        <div className="manager-tools">
                          <Button variant="ghost" onClick={() => setTplForm({
                            code: t.code,
                            name: t.name,
                            planType: t.planType,
                            triggerText: (t.triggerConcepts || []).join(", "),
                            requiredCapsText: (t.requiredCaps || []).join(", "),
                            responseTemplate: t.responseTemplate || "",
                            status: t.status || "PUBLISHED",
                          })}><Pencil size={13} /></Button>
                          <Button variant="ghost" onClick={() => action.run(async () => {
                            await request(`/api/admin/planner/templates/${encodeURIComponent(t.code)}`, { method: "DELETE" });
                            templates.reload();
                          }, `已删除模板 ${t.code}`)}><Trash2 size={13} /></Button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          {!templates.data?.items.length && !templates.loading && <Empty>暂无规划模板</Empty>}
        </Card>
      </div>

      {tplForm && (
        <Modal title="编辑查询规划模板" subtitle="支持绑定触发概念、必需能力编码和确定性摘要模板" wide onClose={closeTpl}>
          <form onSubmit={e => {
            e.preventDefault();
            action.run(async () => {
              await request("/api/admin/planner/templates", {
                method: "POST",
                body: JSON.stringify({
                  code: tplForm.code,
                  name: tplForm.name,
                  planType: tplForm.planType,
                  triggerConcepts: tplForm.triggerText.split(/[,，]/).map(s => s.trim()).filter(Boolean),
                  requiredCaps: tplForm.requiredCapsText.split(/[,，]/).map(s => s.trim()).filter(Boolean),
                  responseTemplate: tplForm.responseTemplate,
                  status: tplForm.status,
                }),
              });
              setTplForm(null);
              templates.reload();
            }, "规划模板已保存");
          }}>
            <div className="form-grid">
              <label className="form-field">模板编码<input required value={tplForm.code} onChange={e => setTplForm({ ...tplForm, code: e.target.value })} placeholder="如 tpl_single_metric" /></label>
              <label className="form-field">模板名称<input required value={tplForm.name} onChange={e => setTplForm({ ...tplForm, name: e.target.value })} /></label>
              <label className="form-field">模板类型<select value={tplForm.planType} onChange={e => setTplForm({ ...tplForm, planType: e.target.value })}><option value="single_metric">single_metric（单指标）</option><option value="flight_list">flight_list（航班明细）</option><option value="count_query">count_query（计数查询）</option><option value="acdm_report">acdm_report（专题报告）</option></select></label>
              <label className="form-field">状态<select value={tplForm.status} onChange={e => setTplForm({ ...tplForm, status: e.target.value })}><option value="PUBLISHED">PUBLISHED（已发布）</option><option value="DRAFT">DRAFT（草稿）</option><option value="ARCHIVED">ARCHIVED（归档）</option></select></label>
              <label className="form-field form-span">触发概念（逗号分隔）<input value={tplForm.triggerText} onChange={e => setTplForm({ ...tplForm, triggerText: e.target.value })} placeholder="如 FlightDelay, BridgeStandRate" /></label>
              <label className="form-field form-span">依赖能力编码（逗号分隔）<input value={tplForm.requiredCapsText} onChange={e => setTplForm({ ...tplForm, requiredCapsText: e.target.value })} placeholder="如 flight.metrics, flight.dynamics" /></label>
              <label className="form-field form-span">摘要响应模板（支持 {'{period}'}, {'{metricName}'}, {'{value}'}, {'{unit}'}, {'{count}'}）<textarea rows={2} value={tplForm.responseTemplate} onChange={e => setTplForm({ ...tplForm, responseTemplate: e.target.value })} placeholder="如 {period} {metricName}为 {value}{unit}（共 {count} 条记录）。" /></label>
            </div>
            <div className="modal-actions">
              <Button onClick={closeTpl}>取消</Button>
              <Button type="submit" variant="primary" disabled={action.busy}><Save size={13} />保存模板</Button>
            </div>
          </form>
        </Modal>
      )}
    </>
  );
}
