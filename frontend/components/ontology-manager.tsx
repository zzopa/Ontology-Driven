"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { ArrowUpRight, BookOpen, Check, ChevronRight, Database, FileJson2, GitBranch, History, LockKeyhole, Pencil, RefreshCw, Save, Search, Settings2, ShieldCheck, Table2 } from "lucide-react";
import { request } from "@/lib/api";
import type { Entity, Field, Ontology, Status } from "@/lib/types";
import { Badge, Button, Card, Modal, Notice, Shell, Spinner } from "./ui";

const ROLES = { name: "名称字段", company: "所属企业字段", code: "业务编号字段", time: "业务时间字段" };
type Draft = Record<string, unknown> & { entities: Record<string, Record<string, unknown>> };
type EditingEntity = { name: string; aliases: string; bindings: Record<string, string> };

export function OntologyManager() {
  const [state, setState] = useState<Ontology | null>(null), [status, setStatus] = useState<Status | null>(null);
  const [selected, setSelected] = useState(""), [search, setSearch] = useState(""), [tab, setTab] = useState("attributes");
  const [editor, setEditor] = useState(""), [original, setOriginal] = useState("");
  const [history, setHistory] = useState<{ revision: string }[]>([]), [revision, setRevision] = useState("");
  const [pending, setPending] = useState(""), [message, setMessage] = useState(""), [error, setError] = useState(false);
  const [entityForm, setEntityForm] = useState<EditingEntity | null>(null);
  const [fieldForm, setFieldForm] = useState<{ key: string; label: string; description: string; unit: string; json: boolean } | null>(null);
  const [confirmation, setConfirmation] = useState<{ title: string; text: string; action: () => void } | null>(null);
  const [showEditor, setShowEditor] = useState(false), [report, setReport] = useState<unknown>(null), [reportOpen, setReportOpen] = useState(false);
  const [diagnostics, setDiagnostics] = useState(false);
  const dirty = editor !== original, editable = !!state?.can_edit, entity = state?.catalog.entities[selected];
  const notify = (text: string, isError = false) => { setMessage(text); setError(isError); };
  const load = useCallback(async () => {
    const [ontology, service, versions] = await Promise.all([request<Ontology>("/api/ontology"), request<Status>("/api/status"), request<{ history: { revision: string }[] }>("/api/ontology/history")]);
    setState(ontology); setStatus(service); setHistory(versions.history);
    const text = JSON.stringify(ontology.document, null, 2); setEditor(text); setOriginal(text);
    setSelected(current => ontology.catalog.entities[current] ? current : Object.keys(ontology.catalog.entities)[0] || "");
  }, []);
  useEffect(() => { setPending("load"); load().catch(e => notify(e.message, true)).finally(() => setPending("")); }, [load]);
  useEffect(() => {
    const protect = (e: BeforeUnloadEvent) => { if (dirty) e.preventDefault(); };
    window.addEventListener("beforeunload", protect); return () => window.removeEventListener("beforeunload", protect);
  }, [dirty]);
  const items = useMemo(() => Object.entries(state?.catalog.entities || {}).filter(([key, value]) => `${key} ${value.name} ${value.aliases.join(" ")}`.toLowerCase().includes(search.toLowerCase())), [state, search]);

  async function perform(name: string, operation: () => Promise<void>, success: string) {
    if (pending) return; setPending(name); notify("");
    try { await operation(); notify(success); } catch (e) { notify(e instanceof Error ? e.message : "操作失败", true); } finally { setPending(""); }
  }
  function guard(action: () => void, title = "放弃未保存的草稿？", text = "当前有尚未保存的业务规则修改，重新加载会丢弃这些草稿。") {
    if (dirty) setConfirmation({ title, text, action }); else action();
  }
  function parseDraft(): Draft {
    const draft = JSON.parse(editor);
    if (!draft || typeof draft !== "object" || Array.isArray(draft) || !draft.entities || typeof draft.entities !== "object" || Array.isArray(draft.entities)) throw new Error("规则需要包含 entities 对象。请先修正 JSON 草稿。");
    return draft;
  }
  function applyEntity() {
    if (!entityForm || !entityForm.name.trim()) { notify("请填写业务对象名称", true); return; }
    try {
      const draft = parseDraft(), previous = draft.entities[selected] || {};
      draft.entities[selected] = { ...previous, name: entityForm.name.trim(), aliases: entityForm.aliases.split(/[,，、\n]/).map(s => s.trim()).filter(Boolean), bindings: Object.fromEntries(Object.entries(entityForm.bindings).filter(([, value]) => value)) };
      setEditor(JSON.stringify(draft, null, 2)); setEntityForm(null); notify("对象定义已写入本地草稿，点击“校验并保存”后生效。");
    } catch (e) { notify(e instanceof Error ? e.message : "草稿格式无效", true); }
  }
  function editEntity() {
    if (!entity) return;
    try {
      const draft = parseDraft(), value = draft.entities[selected] || {};
      setEntityForm({ name: typeof value.name === "string" ? value.name : entity.name, aliases: Array.isArray(value.aliases) ? value.aliases.join("、") : entity.aliases.join("、"), bindings: { ...entity.bindings, ...(value.bindings as Record<string, string> || {}) } });
    } catch (e) { notify(e instanceof Error ? e.message : "草稿格式无效", true); }
  }
  function editField(key: string, attr: Field) {
    try {
      const draft = parseDraft(), section = attr.column ? "json_attributes" : "attributes";
      const fields = draft.entities[selected]?.[section] as Record<string, Field> | undefined, previous = fields?.[key];
      setFieldForm({ key, json: !!attr.column, label: previous?.label || attr.label || key, description: previous?.description ?? attr.description ?? attr.comment ?? "", unit: previous?.unit ?? attr.unit ?? "" });
    } catch (e) { notify(e instanceof Error ? e.message : "草稿格式无效", true); }
  }
  function applyField() {
    if (!fieldForm?.label.trim()) { notify("请填写中文显示名称", true); return; }
    try {
      const draft = parseDraft(), section = fieldForm.json ? "json_attributes" : "attributes", previous = draft.entities[selected] || {};
      const fields = previous[section] as Record<string, Field> || {};
      draft.entities[selected] = { ...previous, [section]: { ...fields, [fieldForm.key]: { ...fields[fieldForm.key], label: fieldForm.label.trim(), description: fieldForm.description.trim(), unit: fieldForm.unit.trim() } } };
      setEditor(JSON.stringify(draft, null, 2)); setFieldForm(null); notify("字段解释已写入草稿，不修改数据库原始注释。保存后生效。");
    } catch (e) { notify(e instanceof Error ? e.message : "草稿格式无效", true); }
  }
  const closeEntity = useCallback(() => setEntityForm(null), []), closeField = useCallback(() => setFieldForm(null), []), closeConfirmation = useCallback(() => setConfirmation(null), []), closeReport = useCallback(() => setReportOpen(false), []), closeDiagnostics = useCallback(() => setDiagnostics(false), []);
  const edges = state?.catalog.graph.links_all.filter(edge => edge.from_table === selected || edge.to_table === selected) || [];

  return <Shell active="ontology" statusLabel={!state ? "正在读取权限" : editable ? "授权维护 · 可编辑" : "未授权编辑 · 只读"}>
    <div className="heading-row"><div><div className="eyebrow"><span className="eyebrow-line" />BUSINESS ONTOLOGY.</div><h1>让数据关系，有清晰的定义。</h1><p className="heading-description">查看数据库结构，管理业务名称、字段解释、关联关系与统计口径；编辑需要原填报系统的 admin 角色。</p></div><a href="/admin" className="btn btn-secondary"><Settings2 size={12} />{editable ? "进入管理后台" : "登录管理后台"}</a></div>
    <div className="manager-tools"><Button disabled={!!pending} onClick={() => guard(() => perform("load", load, "已重新加载当前版本。"))}><RefreshCw size={12} />重新加载</Button><Button disabled={!editable || !!pending} onClick={() => guard(() => perform("refresh", async () => { await request("/api/metadata/refresh", { method: "POST" }); await load(); }, "结构已重新采集，未修改业务数据。"))}>{pending === "refresh" ? <Spinner /> : <Database size={12} />}刷新数据库结构</Button><Button disabled={!editable || !!pending} onClick={() => perform("validate", async () => { const data = await request<{ report: unknown }>("/api/ontology/validate-relations", { method: "POST" }); setReport(data.report); setReportOpen(true); }, "抽样检查已完成，不代表关系已获得业务确认。")}>{pending === "validate" ? <Spinner /> : <ShieldCheck size={12} />}抽样检查关系</Button><Button onClick={() => setDiagnostics(true)} disabled={!state}><FileJson2 size={12} />结构变化</Button></div>
    {message && <Notice error={error}>{message}</Notice>}
    {!state ? <Card className="card-pad"><div className="empty-panel">{pending ? <><Spinner /><span>正在读取业务目录…</span></> : <><Database size={22} /><span>目录读取失败，请重新加载。</span></>}</div></Card> : <>
      <div className="manager-summary" style={{ marginTop: 22 }}>{[["业务数据库", state.catalog.database], ["业务对象", Object.keys(state.catalog.entities).length], ["关联关系", state.catalog.graph.links_all.length], ["数据库字段", status?.fields ?? "—"]].map(([label, value]) => <Card className="manager-stat" key={label}><strong style={typeof value === "string" ? { fontSize: 17, lineHeight: "33px" } : undefined}>{typeof value === "number" ? value.toLocaleString() : value}</strong><span>{label}</span></Card>)}</div>
      <div className="manager-grid"><Card className="card-pad"><div className="card-head" style={{ marginBottom: 14 }}><h2 className="section-title"><LayersIcon />业务对象</h2><Badge>{items.length}</Badge></div><div className="entity-search"><Search size={13} /><input className="search-input" aria-label="搜索业务对象" placeholder="搜索名称、别名或表名" value={search} onChange={e => setSearch(e.target.value)} /></div><div className="entity-list">{items.map(([key, value]) => <button key={key} className={`entity-item ${selected === key ? "selected" : ""}`} onClick={() => { setSelected(key); setTab("attributes"); }}><span>{value.name}</span><small>{key}</small></button>)}{!items.length && <div className="empty-panel">没有匹配的业务对象</div>}</div></Card>
        <Card>{entity && <><div className="card-pad" style={{ paddingBottom: 0 }}><div className="card-head" style={{ marginBottom: 8 }}><div><h2 className="entity-title">{entity.name}</h2><p className="entity-table-name">{selected}</p></div><Button disabled={!editable || !!pending} onClick={editEntity}><Pencil size={12} />编辑定义</Button></div><div className="alias-list">{entity.aliases.slice(0, 12).map(alias => <Badge key={alias}>{alias}</Badge>)}</div><div className="binding-grid">{Object.entries(ROLES).map(([role, label]) => <div className="binding" key={role}><span>{label}</span><code>{entity.bindings[role] || "未配置"}</code></div>)}</div></div><div className="result-tabs" role="tablist" aria-label="业务定义详情">{[{ id: "attributes", name: "属性字段", icon: Table2, count: Object.keys(entity.attributes).length }, { id: "metrics", name: "统计指标", icon: BookOpen, count: Object.keys(entity.metrics).length }, { id: "relations", name: "关联定义", icon: GitBranch, count: edges.length }].map(item => <button className={`result-tab ${tab === item.id ? "selected" : ""}`} key={item.id} role="tab" aria-selected={tab === item.id} onClick={() => setTab(item.id)}><item.icon size={13} />{item.name}<Badge>{item.count}</Badge></button>)}</div><div className="result-content">
          {tab === "attributes" && <div className="table-wrap"><table className="data-table attribute-table"><thead><tr><th>中文属性 / 字段</th><th>业务解释 / 原注释</th><th>类型 / 单位</th>{editable && <th>管理</th>}</tr></thead><tbody>{Object.entries(entity.attributes).map(([key, attr]) => <tr key={key}><td><strong>{attr.label || key}</strong><small>{key}{attr.column ? ` ← ${attr.column}.${attr.path?.join(".")}` : ""}</small></td><td>{attr.description || attr.comment || "暂无业务解释"}{attr.comment && attr.comment !== attr.description && <div className="helper">原注释：{attr.comment}</div>}</td><td><Badge>{attr.type || "text"}</Badge><div className="helper" style={{ marginTop: 5 }}>{attr.unit || "单位未定义"}</div></td>{editable && <td><Button variant="ghost" aria-label={`编辑 ${attr.label || key}`} disabled={!!pending} onClick={() => editField(key, attr)}><Pencil size={12} /></Button></td>}</tr>)}</tbody></table></div>}
          {tab === "metrics" && (Object.keys(entity.metrics).length ? <div className="record-cards">{Object.entries(entity.metrics).map(([key, metric]) => <div className="record-card card-pad" key={key}><div className="card-head"><h3 className="section-title">{metric.name || key}</h3><Badge tone="green">{metric.unit || "单位未定义"}</Badge></div><p className="entity-table-name">字段 {metric.field} · 指标 {key}</p><p className="long-description" style={{ marginTop: 10 }}>{metric.description || "未补充业务口径说明，请通过规则编辑维护。"}</p></div>)}</div> : <div className="empty-panel"><BookOpen size={22} /><span>尚未配置可用统计指标</span><p>明确金额字段、单位、分组与业务含义后，再通过规则编辑配置。不会猜测统计口径。</p></div>)}
          {tab === "relations" && (edges.length ? <div className="record-cards">{edges.map((edge, i) => <div className="edge-card" key={i}><div style={{ display: "flex", gap: 8, marginBottom: 8 }}><Badge tone={edge.status === "confirmed" ? "green" : "neutral"}>{edge.status}</Badge><Badge>{edge.cardinality}</Badge></div>{edge.source}<ArrowUpRight size={11} style={{ margin: "0 6px", display: "inline", color: "#6ee7b7" }} />{edge.target}<small>{edge.note || "未补充关系解释"} · {edge.validation}</small></div>)}</div> : <div className="empty-panel"><GitBranch size={22} /><span>当前对象未配置关联关系</span></div>)}
        </div></>}</Card>
      </div>
      <Card className="card-pad manager-rules"><div className="card-head"><div><h2 className="section-title"><FileJson2 size={14} />业务规则与版本管理 {dirty && <Badge tone="amber"><span className="dirty-dot" />未保存草稿</Badge>}</h2><p className="card-description">表单修改先写入草稿，经校验保存后生效。保留原规则中的全部未编辑配置。</p></div><Button enterConfirm variant="primary" disabled={!editable || !!pending || !dirty} onClick={() => perform("save", async () => { await request("/api/ontology", { method: "PUT", body: JSON.stringify({ document: parseDraft(), revision: state.revision }) }); await load(); }, "规则已校验并保存，上一版本已保留；已有明细令牌失效。")}>{pending === "save" ? <Spinner /> : <Save size={12} />}校验并保存</Button></div>
        <div className="rules-grid"><div><div className="rules-description"><div><b>对象与属性</b> · 名称、别名、身份字段、JSON 映射及字段解释。</div><div><b>关系与口径</b> · 完整连接字段、关系状态、角色路径、指标单位与分组。</div><div><b>访问权限</b> · {editable ? "当前账号已获授权，可维护本体规则。" : "当前只读，使用原填报系统 admin 角色登录后可维护。"}</div></div><button className="inline-button" style={{ marginTop: 15 }} onClick={() => setShowEditor(v => !v)} aria-expanded={showEditor}><CodeIcon />{showEditor ? "收起 JSON 高级编辑器" : "展开 JSON 高级编辑器"}<ChevronRight size={12} style={{ transform: showEditor ? "rotate(90deg)" : "none" }} /></button>{showEditor && <div style={{ marginTop: 12 }}><label className="helper" htmlFor="ontology-json">完整本体规则 · 与表单共用同一份草稿</label><textarea id="ontology-json" className="editor-textarea" value={editor} spellCheck={false} readOnly={!editable || !!pending} onChange={e => setEditor(e.target.value)} /><p className="helper">candidate、disabled 和 invalid 关系不参与查询。保存校验不能代替业务人员审核。</p></div>}</div><div className="history-panel"><h3 className="section-title"><History size={13} />历史版本</h3><p className="helper">回滚前也会备份当前规则，不会修改业务数据库。</p><label className="form-field history-select">选择版本<select value={revision} onChange={e => setRevision(e.target.value)} disabled={!!pending}><option value="">{history.length ? "选择历史版本" : "暂无历史版本"}</option>{history.map(item => <option key={item.revision} value={item.revision}>{item.revision.slice(0, 8)} · {item.revision.slice(9, 15)}</option>)}</select></label><Button disabled={!editable || !!pending || !revision} onClick={() => setConfirmation({ title: "确认回滚业务规则？", text: `将切换到 ${revision}。${dirty ? "未保存草稿会被丢弃。" : ""}当前已保存规则会先备份，现有明细令牌失效。`, action: () => perform("rollback", async () => { await request("/api/ontology/rollback", { method: "POST", body: JSON.stringify({ revision, expected: state.revision }) }); await load(); }, "已回滚业务规则，回滚前版本已备份。") })}><History size={12} />回滚所选版本</Button><div className="helper" style={{ marginTop: 15 }}>当前版本 {state.revision.slice(0, 12)}<br />结构更新 {new Date(state.catalog.refreshed_at).toLocaleString("zh-CN")}</div></div></div>
      </Card>
    </>}
    {entityForm && entity && <Modal title="编辑业务对象定义" subtitle={`${selected} · 修改本地本体配置，不修改数据库`} onClose={closeEntity}><div className="form-grid management-form"><label className="form-field form-span">业务名称<input value={entityForm.name} onChange={e => setEntityForm({ ...entityForm, name: e.target.value })} /></label><label className="form-field form-span">业务别名 · 用逗号或顿号分隔<input value={entityForm.aliases} onChange={e => setEntityForm({ ...entityForm, aliases: e.target.value })} /></label>{Object.entries(ROLES).map(([role, label]) => <label className="form-field" key={role}>{label}<select value={entityForm.bindings[role] || ""} onChange={e => setEntityForm({ ...entityForm, bindings: { ...entityForm.bindings, [role]: e.target.value } })}><option value="">未配置</option>{Object.entries(entity.attributes).map(([key, attr]) => <option key={key} value={key}>{attr.label || key} · {key}</option>)}</select></label>)}</div><Notice>应绑定真实业务时间和企业身份字段；不能用创建时间替代业务发生时间。写入草稿后还需校验保存。</Notice><div className="modal-actions"><Button onClick={closeEntity}>取消</Button><Button enterConfirm variant="primary" onClick={applyEntity}><Check size={12} />写入草稿</Button></div></Modal>}
    {fieldForm && <Modal title="维护字段解释" subtitle={`${selected}.${fieldForm.key} · 原始数据库注释不会被覆盖`} onClose={closeField}><div className="form-grid management-form"><label className="form-field form-span">中文显示名称<input value={fieldForm.label} onChange={e => setFieldForm({ ...fieldForm, label: e.target.value })} /></label><label className="form-field form-span">业务解释<textarea rows={3} value={fieldForm.description} onChange={e => setFieldForm({ ...fieldForm, description: e.target.value })} /></label><label className="form-field form-span">单位 · 无单位可留空<input placeholder="例如：元、万元；请按真实字段口径填写" value={fieldForm.unit} onChange={e => setFieldForm({ ...fieldForm, unit: e.target.value })} /></label></div><Notice>显示单位不会自动换算数据库数值。仅填写已确认的业务解释与真实单位。</Notice><div className="modal-actions"><Button onClick={closeField}>取消</Button><Button enterConfirm variant="primary" onClick={applyField}><Check size={12} />写入草稿</Button></div></Modal>}
    {confirmation && <Modal title={confirmation.title} onClose={closeConfirmation}><p className="long-description">{confirmation.text}</p><div className="modal-actions"><Button onClick={closeConfirmation}>取消</Button><Button enterConfirm variant="primary" onClick={() => { const action = confirmation.action; setConfirmation(null); action(); }}>确认继续</Button></div></Modal>}
    {reportOpen && <Modal title="关联数据抽样报告" subtitle="只读抽样，不自动确认业务关系" wide onClose={closeReport}><pre className="code-block">{JSON.stringify(report, null, 2)}</pre><Notice>最多抽样 100 个非空键，抽样匹配不代表全量唯一，也不能证明业务语义。</Notice></Modal>}
    {diagnostics && <Modal title="结构变化与失效提示" subtitle="当前数据库结构、版本与本体警告" wide onClose={closeDiagnostics}><pre className="code-block">{JSON.stringify({ version: state?.catalog.version, refreshed_at: state?.catalog.refreshed_at, metadata_diff: status?.metadata_diff, warnings: state?.catalog.warnings }, null, 2)}</pre></Modal>}
  </Shell>;
}

function LayersIcon() { return <Database size={13} />; }
function CodeIcon() { return <FileJson2 size={12} />; }
