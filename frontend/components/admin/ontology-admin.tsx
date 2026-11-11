"use client";

import { useCallback, useState } from "react";
import { BookOpen, CheckCircle2, Compass, GitBranch, History, Plus, RefreshCw, Save, Sparkles, Tag, Trash2, UploadCloud } from "lucide-react";
import {
  formatDate,
  govStatusBadge,
  type OntologyCapabilityProfile,
  type OpsAttribute,
  type OpsConcept,
  type OpsScenario,
  type OpsSynonym,
  type User,
} from "@/lib/admin";
import { request } from "@/lib/api";
import { Badge, Button, Card, Modal, Notice } from "../ui";
import { GlossaryPanel } from "./glossary";
import { Empty, Loading, useAction, useResource } from "./shared";

type SubTab = "capabilities" | "concepts" | "scenarios" | "glossary" | "versions";

export function OntologyAdminPanel({ current, onPublished }: { current: User; onPublished?: () => void }) {
  const [subTab, setSubTab] = useState<SubTab>("capabilities");
  const profile = useResource<OntologyCapabilityProfile>("/api/admin/ontology-catalog/capabilities");
  const concepts = useResource<{ ok: boolean; items: OpsConcept[] }>("/api/admin/ontology/concepts");
  const synonyms = useResource<{ ok: boolean; items: OpsSynonym[] }>("/api/admin/ontology/synonyms");
  const scenarios = useResource<{ ok: boolean; items: OpsScenario[] }>("/api/admin/ontology/scenarios");
  const attributes = useResource<{ ok: boolean; items: OpsAttribute[] }>("/api/admin/ontology/attributes");
  const versions = useResource<{ ok: boolean; items: Array<{ versionNo: number; changeSummary: string; triggeredBy: string; publishedAt: string }> }>("/api/admin/ontology-catalog/versions");
  const action = useAction();

  const [conceptForm, setConceptForm] = useState<OpsConcept | null>(null);
  const [synForm, setSynForm] = useState<{ conceptCode: string; term: string; termType: string; weight: number } | null>(null);
  const [scenarioForm, setScenarioForm] = useState<OpsScenario & { keywordsText: string; dimsText: string; filtersText: string } | null>(null);

  const closeConcept = useCallback(() => setConceptForm(null), []);
  const closeSyn = useCallback(() => setSynForm(null), []);
  const closeScenario = useCallback(() => setScenarioForm(null), []);

  function publishAll() {
    action.run(async () => {
      await request("/api/admin/ontology-catalog/publish", {
        method: "POST",
        body: JSON.stringify({ changeSummary: "管理后台一键发布并同步运行时本体" }),
      });
      profile.reload();
      versions.reload();
      onPublished?.();
    }, "已将全部已发布概念、同义词、指标、场景与问数设置编译同步至实时本体引擎");
  }

  const p = profile.data;
  const cov = p?.dataCoverage;

  return (
    <>
      <div className="admin-section-head">
        <div>
          <h2>本体管理</h2>
          <p>统筹问数能力总览、业务概念、同义词映射、问答场景及运行时本体版本发布</p>
        </div>
        <div className="manager-tools">
          <a className="btn btn-secondary" href="/ontology"><GitBranch size={13} />物理图谱与版本回滚</a>
          <Button onClick={() => { profile.reload(); concepts.reload(); synonyms.reload(); scenarios.reload(); }}>
            <RefreshCw size={13} />刷新
          </Button>
          <Button variant="primary" disabled={action.busy} onClick={publishAll}>
            <UploadCloud size={14} />发布并同步本体
          </Button>
        </div>
      </div>

      {action.feedback}

      <div className="gov-subtabs" role="tablist" aria-label="本体管理视图">
        <button role="tab" aria-selected={subTab === "capabilities"} className={`gov-subtab ${subTab === "capabilities" ? "selected" : ""}`} onClick={() => setSubTab("capabilities")}>
          <Sparkles size={14} />问数能力总览
        </button>
        <button role="tab" aria-selected={subTab === "concepts"} className={`gov-subtab ${subTab === "concepts" ? "selected" : ""}`} onClick={() => setSubTab("concepts")}>
          <Tag size={14} />概念与同义词 <Badge>{concepts.data?.items.length ?? 0} / {synonyms.data?.items.length ?? 0}</Badge>
        </button>
        <button role="tab" aria-selected={subTab === "scenarios"} className={`gov-subtab ${subTab === "scenarios" ? "selected" : ""}`} onClick={() => setSubTab("scenarios")}>
          <Compass size={14} />场景与属性 <Badge>{scenarios.data?.items.length ?? 0}</Badge>
        </button>
        <button role="tab" aria-selected={subTab === "glossary"} className={`gov-subtab ${subTab === "glossary" ? "selected" : ""}`} onClick={() => setSubTab("glossary")}>
          <BookOpen size={14} />表字段词条映射
        </button>
        <button role="tab" aria-selected={subTab === "versions"} className={`gov-subtab ${subTab === "versions" ? "selected" : ""}`} onClick={() => setSubTab("versions")}>
          <History size={14} />发布历史 <Badge>{versions.data?.items.length ?? 0}</Badge>
        </button>
      </div>

      {subTab === "capabilities" && (
        <div className="gov-stack">
          <Loading {...profile} />
          {p && (
            <>
              <Notice>
                {cov?.start && cov?.end
                  ? `当前标准航班数据覆盖：${cov.start} ~ ${cov.end}（${(cov.totalRecords || 0).toLocaleString()} 条，来源 ${cov.sourceTable || "ops_ask"}）`
                  : "当前尚未检测到 std_flight_core 数据覆盖范围"}
              </Notice>

              <div className="admin-stat-grid">
                <section className="card admin-stat"><div><span>已发布指标</span><CheckCircle2 size={16} /></div><strong>{p.summary.metricCount}</strong><small>metrics_metric</small></section>
                <section className="card admin-stat"><div><span>注册筛选条件</span><Tag size={16} /></div><strong>{p.summary.filterCount}</strong><small>metrics_filter</small></section>
                <section className="card admin-stat"><div><span>分析维度</span><GitBranch size={16} /></div><strong>{p.summary.dimensionCount}</strong><small>metrics_dimension</small></section>
                <section className="card admin-stat"><div><span>场景与同义词</span><Sparkles size={16} /></div><strong>{p.summary.scenarioCount} / {p.summary.synonymCount ?? 0}</strong><small>scenarios / synonyms</small></section>
              </div>

              <Card className="card-pad">
                <div className="card-head"><h3 className="section-title">按查询类型能力矩阵</h3></div>
                <div className="table-wrap">
                  <table className="data-table attribute-table admin-table">
                    <thead>
                      <tr><th>类型</th><th>名称</th><th>支持度</th><th>能力说明</th><th>示例问法</th></tr>
                    </thead>
                    <tbody>
                      {p.queryTypes.map(qt => {
                        const st = govStatusBadge(qt.supportLevel);
                        return (
                          <tr key={qt.queryType}>
                            <td><code className="gov-code">{qt.queryType}</code></td>
                            <td><strong>{qt.name}</strong></td>
                            <td><Badge tone={st.tone}>{st.label}</Badge></td>
                            <td>{qt.description}</td>
                            <td><small>{qt.exampleQuestions.join("；")}</small></td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </Card>

              <div className="admin-overview-grid">
                <Card className="card-pad">
                  <div className="card-head"><h3 className="section-title">时间口径与本场语义锚点</h3></div>
                  <div className="table-wrap">
                    <table className="data-table attribute-table admin-table">
                      <thead><tr><th>口径项</th><th>当前配置</th><th>默认策略</th></tr></thead>
                      <tbody>
                        {p.timeSemantics.map(ts => (
                          <tr key={ts.slot}>
                            <td><strong>{ts.name}</strong><small>{ts.slot}</small></td>
                            <td><small>{ts.values.join(" · ")}</small></td>
                            <td><small>{ts.defaultPolicy}</small></td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </Card>

                <Card className="card-pad">
                  <div className="card-head"><h3 className="section-title">已知边界与推荐评测问法</h3></div>
                  <ul className="helper" style={{ display: "grid", gap: 6, paddingLeft: 18 }}>
                    {p.limitations.map((lim, i) => <li key={i}>{lim}</li>)}
                  </ul>
                  <div className="card-divider" />
                  <div className="sync-pill-row">
                    {p.examples.map((ex, i) => <Badge key={i}>{ex.question}</Badge>)}
                  </div>
                </Card>
              </div>
            </>
          )}
        </div>
      )}

      {subTab === "concepts" && (
        <div className="gov-stack">
          <Card className="card-pad">
            <div className="card-head">
              <div><h3 className="section-title">业务概念（ont_concept）</h3><p className="helper">定义核心指标/场景/属性概念节点，供同义词挂载与查询归类</p></div>
              <Button variant="primary" onClick={() => setConceptForm({ code: "", name: "", domain: "FIMS", conceptType: "METRIC", description: "", status: "PUBLISHED" })}><Plus size={13} />新增概念</Button>
            </div>
            <Loading {...concepts} />
            <div className="table-wrap">
              <table className="data-table attribute-table admin-table">
                <thead><tr><th>概念编码</th><th>名称</th><th>类型 / 域</th><th>描述</th><th>同义词数</th><th>状态</th><th>操作</th></tr></thead>
                <tbody>
                  {(concepts.data?.items || []).map(c => {
                    const st = govStatusBadge(c.status);
                    return (
                      <tr key={c.code}>
                        <td><code className="gov-code">{c.code}</code></td>
                        <td><strong>{c.name}</strong></td>
                        <td><Badge>{c.conceptType}</Badge> <Badge>{c.domain}</Badge></td>
                        <td>{c.description || "—"}</td>
                        <td>{c.synonymCount ?? 0}</td>
                        <td><Badge tone={st.tone}>{st.label}</Badge></td>
                        <td>
                          <div className="manager-tools">
                            <Button variant="ghost" onClick={() => setConceptForm({ ...c })}>编辑</Button>
                            <Button variant="ghost" onClick={() => action.run(async () => {
                              await request(`/api/admin/ontology/concepts/${c.id ?? c.code}`, { method: "DELETE" });
                              concepts.reload(); synonyms.reload();
                            }, "已删除概念")}><Trash2 size={13} /></Button>
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </Card>

          <Card className="card-pad">
            <div className="card-head">
              <div><h3 className="section-title">同义词触发词（ont_synonym）</h3><p className="helper">用户提问中的口语触发词与概念的映射（发布后即时参与本地意图识别）</p></div>
              <Button variant="primary" onClick={() => setSynForm({ conceptCode: concepts.data?.items[0]?.code || "FlightDelay", term: "", termType: "alias", weight: 1.0 })}><Plus size={13} />新增同义词</Button>
            </div>
            <Loading {...synonyms} />
            <div className="sync-pill-row">
              {(synonyms.data?.items || []).map(syn => (
                <Badge key={syn.id} tone="green">
                  {syn.term} → {syn.conceptName} ({syn.conceptCode})
                  <button
                    type="button"
                    aria-label={`删除同义词 ${syn.term}`}
                    style={{ marginLeft: 6, background: "transparent", border: 0, color: "inherit", cursor: "pointer" }}
                    onClick={() => action.run(async () => {
                      await request(`/api/admin/ontology/synonyms/${syn.id}`, { method: "DELETE" });
                      synonyms.reload(); concepts.reload();
                    }, `已删除同义词 ${syn.term}`)}
                  >×</button>
                </Badge>
              ))}
            </div>
            {!synonyms.data?.items.length && !synonyms.loading && <Empty>暂无同义词配置</Empty>}
          </Card>
        </div>
      )}

      {subTab === "scenarios" && (
        <div className="gov-stack">
          <Card className="card-pad">
            <div className="card-head">
              <div><h3 className="section-title">问数场景（ont_scenario）</h3><p className="helper">配置查询类型、关联指标、默认维度与自然语言触发关键词</p></div>
              <Button variant="primary" onClick={() => setScenarioForm({
                code: "", name: "", queryType: "COUNT", metricCode: "flight_count",
                dimensions: [], defaultFilters: {}, selectFields: [], triggerKeywords: [], priority: 80, status: "PUBLISHED",
                keywordsText: "", dimsText: "", filtersText: "{}",
              })}><Plus size={13} />新增场景</Button>
            </div>
            <Loading {...scenarios} />
            <div className="table-wrap">
              <table className="data-table attribute-table admin-table">
                <thead><tr><th>场景编码</th><th>名称</th><th>查询类型</th><th>指标 / 维度</th><th>触发词</th><th>优先级</th><th>操作</th></tr></thead>
                <tbody>
                  {(scenarios.data?.items || []).map(sc => (
                    <tr key={sc.code}>
                      <td><code className="gov-code">{sc.code}</code></td>
                      <td><strong>{sc.name}</strong></td>
                      <td><Badge>{sc.queryType}</Badge></td>
                      <td><code>{sc.metricCode || "—"}</code>{sc.dimensions.length > 0 && <small>维度：{sc.dimensions.join(", ")}</small>}</td>
                      <td><small className="gov-clamp">{sc.triggerKeywords.join("、") || "—"}</small></td>
                      <td>{sc.priority}</td>
                      <td>
                        <div className="manager-tools">
                          <Button variant="ghost" onClick={() => setScenarioForm({
                            ...sc,
                            keywordsText: sc.triggerKeywords.join("，"),
                            dimsText: sc.dimensions.join(","),
                            filtersText: JSON.stringify(sc.defaultFilters || {}),
                          })}>编辑</Button>
                          <Button variant="ghost" onClick={() => action.run(async () => {
                            await request(`/api/admin/ontology/scenarios/${encodeURIComponent(sc.code)}`, { method: "DELETE" });
                            scenarios.reload();
                          }, `已删除场景 ${sc.code}`)}><Trash2 size={13} /></Button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>

          <Card className="card-pad">
            <div className="card-head"><h3 className="section-title">开放检索属性（ont_attribute）</h3><Badge>{attributes.data?.items.length ?? 0} 项</Badge></div>
            <div className="table-wrap">
              <table className="data-table attribute-table admin-table">
                <thead><tr><th>属性编码</th><th>中文名</th><th>表 / 列表达式</th><th>类型</th></tr></thead>
                <tbody>
                  {(attributes.data?.items || []).map(attr => (
                    <tr key={attr.code}>
                      <td><code className="gov-code">{attr.code}</code></td>
                      <td><strong>{attr.name}</strong><small>{attr.description}</small></td>
                      <td><code className="gov-code gov-clamp">{attr.tableName}.{attr.columnName}</code></td>
                      <td>{attr.valueType}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        </div>
      )}

      {subTab === "glossary" && <GlossaryPanel current={current} />}

      {subTab === "versions" && (
        <Card className="card-pad">
          <div className="card-head"><h3 className="section-title">目录发布历史（ont_catalog_version）</h3></div>
          <div className="table-wrap">
            <table className="data-table attribute-table admin-table">
              <thead><tr><th>版本号</th><th>变更摘要</th><th>触发人</th><th>发布时间</th></tr></thead>
              <tbody>
                {(versions.data?.items || []).map((v, i) => (
                  <tr key={i}>
                    <td><Badge tone="green">v{v.versionNo}</Badge></td>
                    <td>{v.changeSummary || "—"}</td>
                    <td>{v.triggeredBy}</td>
                    <td>{formatDate(v.publishedAt)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {!versions.data?.items.length && !versions.loading && <Empty>暂无发布版本记录</Empty>}
        </Card>
      )}

      {conceptForm && (
        <Modal title="保存业务概念" onClose={closeConcept}>
          <form onSubmit={e => {
            e.preventDefault();
            action.run(async () => {
              await request("/api/admin/ontology/concepts", { method: "POST", body: JSON.stringify(conceptForm) });
              setConceptForm(null);
              concepts.reload();
            }, "概念已保存");
          }}>
            <div className="form-grid">
              <label className="form-field">概念编码<input required value={conceptForm.code} onChange={e => setConceptForm({ ...conceptForm, code: e.target.value })} /></label>
              <label className="form-field">概念名称<input required value={conceptForm.name} onChange={e => setConceptForm({ ...conceptForm, name: e.target.value })} /></label>
              <label className="form-field">概念类型<select value={conceptForm.conceptType} onChange={e => setConceptForm({ ...conceptForm, conceptType: e.target.value })}><option value="METRIC">METRIC</option><option value="SCENARIO">SCENARIO</option><option value="ATTRIBUTE">ATTRIBUTE</option></select></label>
              <label className="form-field">域<select value={conceptForm.domain} onChange={e => setConceptForm({ ...conceptForm, domain: e.target.value })}><option value="FIMS">FIMS</option><option value="PRODUCTION">PRODUCTION</option><option value="OPS">OPS</option></select></label>
              <label className="form-field form-span">描述<textarea rows={2} value={conceptForm.description} onChange={e => setConceptForm({ ...conceptForm, description: e.target.value })} /></label>
            </div>
            <div className="modal-actions"><Button onClick={closeConcept}>取消</Button><Button type="submit" variant="primary"><Save size={13} />保存</Button></div>
          </form>
        </Modal>
      )}

      {synForm && (
        <Modal title="新增概念同义词" onClose={closeSyn}>
          <form onSubmit={e => {
            e.preventDefault();
            action.run(async () => {
              await request("/api/admin/ontology/synonyms", { method: "POST", body: JSON.stringify(synForm) });
              setSynForm(null);
              synonyms.reload(); concepts.reload();
            }, "同义词已添加，请点击“发布并同步本体”使其在线生效");
          }}>
            <div className="form-grid">
              <label className="form-field form-span">关联概念
                <select value={synForm.conceptCode} onChange={e => setSynForm({ ...synForm, conceptCode: e.target.value })}>
                  {(concepts.data?.items || []).map(c => <option key={c.code} value={c.code}>{c.name} ({c.code})</option>)}
                </select>
              </label>
              <label className="form-field">自然语言触发词<input required value={synForm.term} onChange={e => setSynForm({ ...synForm, term: e.target.value })} placeholder="如：廊桥率 / 晚点班次" /></label>
              <label className="form-field">权重<input type="number" step="0.1" min={0.1} max={10} value={synForm.weight} onChange={e => setSynForm({ ...synForm, weight: Number(e.target.value) })} /></label>
            </div>
            <div className="modal-actions"><Button onClick={closeSyn}>取消</Button><Button type="submit" variant="primary"><Save size={13} />保存</Button></div>
          </form>
        </Modal>
      )}

      {scenarioForm && (
        <Modal title="保存问数场景" wide onClose={closeScenario}>
          <form onSubmit={e => {
            e.preventDefault();
            action.run(async () => {
              await request("/api/admin/ontology/scenarios", {
                method: "POST",
                body: JSON.stringify({
                  ...scenarioForm,
                  triggerKeywords: scenarioForm.keywordsText.split(/[,，、]/).map(s => s.trim()).filter(Boolean),
                  dimensions: scenarioForm.dimsText.split(/[,，]/).map(s => s.trim()).filter(Boolean),
                  defaultFilters: JSON.parse(scenarioForm.filtersText || "{}"),
                }),
              });
              setScenarioForm(null);
              scenarios.reload();
            }, "场景已保存");
          }}>
            <div className="form-grid">
              <label className="form-field">场景编码<input required value={scenarioForm.code} onChange={e => setScenarioForm({ ...scenarioForm, code: e.target.value })} /></label>
              <label className="form-field">场景名称<input required value={scenarioForm.name} onChange={e => setScenarioForm({ ...scenarioForm, name: e.target.value })} /></label>
              <label className="form-field">查询类型<select value={scenarioForm.queryType} onChange={e => setScenarioForm({ ...scenarioForm, queryType: e.target.value })}><option value="COUNT">COUNT</option><option value="LIST">LIST</option><option value="DETAIL">DETAIL</option><option value="TREND">TREND</option><option value="RANK">RANK</option><option value="OVERVIEW">OVERVIEW</option></select></label>
              <label className="form-field">绑定指标编码<input value={scenarioForm.metricCode} onChange={e => setScenarioForm({ ...scenarioForm, metricCode: e.target.value })} /></label>
              <label className="form-field">默认维度（逗号分隔）<input value={scenarioForm.dimsText} onChange={e => setScenarioForm({ ...scenarioForm, dimsText: e.target.value })} /></label>
              <label className="form-field">优先级<input type="number" value={scenarioForm.priority} onChange={e => setScenarioForm({ ...scenarioForm, priority: Number(e.target.value) })} /></label>
              <label className="form-field form-span">触发关键词（逗号分隔）<textarea rows={2} value={scenarioForm.keywordsText} onChange={e => setScenarioForm({ ...scenarioForm, keywordsText: e.target.value })} /></label>
              <label className="form-field form-span">默认筛选 JSON<textarea rows={2} value={scenarioForm.filtersText} onChange={e => setScenarioForm({ ...scenarioForm, filtersText: e.target.value })} /></label>
            </div>
            <div className="modal-actions"><Button onClick={closeScenario}>取消</Button><Button type="submit" variant="primary"><Save size={13} />保存</Button></div>
          </form>
        </Modal>
      )}
    </>
  );
}
