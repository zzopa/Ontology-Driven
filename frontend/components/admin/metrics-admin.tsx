"use client";

import { useCallback, useMemo, useState } from "react";
import { BarChart3, Filter, Layers, Pencil, Plus, RefreshCw, Save, Search, Trash2 } from "lucide-react";
import {
  govStatusBadge,
  type OpsDimension,
  type OpsFilter,
  type OpsMetric,
} from "@/lib/admin";
import { request } from "@/lib/api";
import { Badge, Button, Card, Modal, Notice } from "../ui";
import { Empty, Loading, useAction, useResource } from "./shared";

type SubTab = "metrics" | "dimensions" | "filters";

export function MetricsAdminPanel() {
  const [subTab, setSubTab] = useState<SubTab>("metrics");
  const [search, setSearch] = useState("");
  const [domainFilter, setDomainFilter] = useState("");

  const metrics = useResource<{ ok: boolean; items: OpsMetric[] }>("/api/admin/metrics");
  const dimensions = useResource<{ ok: boolean; items: OpsDimension[] }>("/api/admin/dimensions");
  const filters = useResource<{ ok: boolean; items: OpsFilter[] }>("/api/admin/filters");
  const action = useAction();

  const [metricForm, setMetricForm] = useState<OpsMetric & { defaultFiltersText: string; joinsText: string } | null>(null);
  const [dimForm, setDimForm] = useState<OpsDimension | null>(null);
  const [filterForm, setFilterForm] = useState<OpsFilter | null>(null);

  const closeMetric = useCallback(() => setMetricForm(null), []);
  const closeDim = useCallback(() => setDimForm(null), []);
  const closeFilter = useCallback(() => setFilterForm(null), []);

  const metricDomains = useMemo(
    () => Array.from(new Set((metrics.data?.items || []).map(i => i.domain).filter(Boolean))),
    [metrics.data?.items],
  );

  const filteredMetrics = useMemo(() => {
    const q = search.trim().toLowerCase();
    return (metrics.data?.items || []).filter(item => {
      if (domainFilter && item.domain !== domainFilter) return false;
      if (!q) return true;
      return `${item.code} ${item.name} ${item.domain} ${item.formulaDesc} ${item.baseTable}`.toLowerCase().includes(q);
    });
  }, [metrics.data?.items, search, domainFilter]);

  const filteredDims = useMemo(() => {
    const q = search.trim().toLowerCase();
    return (dimensions.data?.items || []).filter(
      item => !q || `${item.code} ${item.name} ${item.expression} ${item.alias}`.toLowerCase().includes(q),
    );
  }, [dimensions.data?.items, search]);

  const filteredFilters = useMemo(() => {
    const q = search.trim().toLowerCase();
    return (filters.data?.items || []).filter(
      item => !q || `${item.code} ${item.name} ${item.columnExpr} ${item.operator}`.toLowerCase().includes(q),
    );
  }, [filters.data?.items, search]);

  function openNew() {
    if (subTab === "metrics") {
      setMetricForm({
        code: "", name: "", domain: "FIMS", description: "", formulaDesc: "", aggregation: "count",
        unit: "架次", ruleCode: "", availability: "AVAILABLE", phase: 1, status: "PUBLISHED",
        baseTable: "std_flight_core", aggregateExpression: "COUNT(*)", valueAlias: "metric_val",
        dateField: "flight_date", defaultFilters: {}, joinsJson: [], requiredFields: ["flight_date"],
        defaultFiltersText: "{}", joinsText: "[]",
      });
    } else if (subTab === "dimensions") {
      setDimForm({ code: "", name: "", expression: "c.airline_code", alias: "", requiredField: "", status: "PUBLISHED" });
    } else {
      setFilterForm({ code: "", name: "", columnExpr: "c.terminal", operator: "eq", joinIndicator: false, valueType: "string", status: "PUBLISHED" });
    }
  }

  return (
    <>
      <div className="admin-section-head">
        <div>
          <h2>指标管理</h2>
          <p>维护问数可用的业务指标定义、计算口径、分析维度与筛选条件</p>
        </div>
        <div className="manager-tools">
          <Button onClick={() => { metrics.reload(); dimensions.reload(); filters.reload(); }}>
            <RefreshCw size={13} />刷新
          </Button>
          <Button variant="primary" onClick={openNew}>
            <Plus size={14} />{subTab === "metrics" ? "新增指标" : subTab === "dimensions" ? "新增维度" : "新增筛选"}
          </Button>
        </div>
      </div>

      {action.feedback}

      <div className="gov-subtabs" role="tablist" aria-label="指标体系类别">
        <button role="tab" aria-selected={subTab === "metrics"} className={`gov-subtab ${subTab === "metrics" ? "selected" : ""}`} onClick={() => setSubTab("metrics")}>
          <BarChart3 size={14} />指标列表 <Badge>{metrics.data?.items.length ?? 0}</Badge>
        </button>
        <button role="tab" aria-selected={subTab === "dimensions"} className={`gov-subtab ${subTab === "dimensions" ? "selected" : ""}`} onClick={() => setSubTab("dimensions")}>
          <Layers size={14} />分析维度 <Badge>{dimensions.data?.items.length ?? 0}</Badge>
        </button>
        <button role="tab" aria-selected={subTab === "filters"} className={`gov-subtab ${subTab === "filters" ? "selected" : ""}`} onClick={() => setSubTab("filters")}>
          <Filter size={14} />筛选条件 <Badge>{filters.data?.items.length ?? 0}</Badge>
        </button>
      </div>

      <Card className="card-pad">
        <div className="admin-toolbar">
          <label className="admin-search">
            <Search size={14} />
            <input aria-label="搜索指标或维度" value={search} onChange={e => setSearch(e.target.value)} placeholder="搜索编码、名称、业务口径或 SQL 表达式" />
          </label>
          {subTab === "metrics" && (
            <select aria-label="按域筛选" value={domainFilter} onChange={e => setDomainFilter(e.target.value)}>
              <option value="">全部域</option>
              {metricDomains.map(d => <option key={d} value={d}>{d}</option>)}
            </select>
          )}
        </div>

        {subTab === "metrics" && (
          <>
            <Loading {...metrics} />
            <div className="table-wrap">
              <table className="data-table attribute-table admin-table">
                <thead>
                  <tr>
                    <th>指标编码</th>
                    <th>指标名称</th>
                    <th>域</th>
                    <th>业务口径</th>
                    <th>可用性</th>
                    <th>阶段</th>
                    <th>操作</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredMetrics.map(item => {
                    const av = govStatusBadge(item.availability);
                    const st = govStatusBadge(item.status);
                    return (
                      <tr key={item.code}>
                        <td><code className="gov-code">{item.code}</code></td>
                        <td>
                          <strong>{item.name}</strong>
                          <small>{item.baseTable} · 单位 {item.unit || "—"} · <Badge tone={st.tone}>{st.label}</Badge></small>
                        </td>
                        <td><Badge>{item.domain}</Badge></td>
                        <td><span className="gov-clamp" title={item.formulaDesc || item.description}>{item.formulaDesc || item.description || "—"}</span></td>
                        <td><Badge tone={av.tone}>{av.label}</Badge></td>
                        <td><Badge>P{item.phase}</Badge></td>
                        <td>
                          <div className="manager-tools">
                            <Button
                              variant="ghost"
                              aria-label={`编辑指标 ${item.code}`}
                              onClick={() => setMetricForm({
                                ...item,
                                defaultFiltersText: JSON.stringify(item.defaultFilters || {}, null, 2),
                                joinsText: JSON.stringify(item.joinsJson || [], null, 2),
                              })}
                            >
                              <Pencil size={13} />
                            </Button>
                            <Button
                              variant="ghost"
                              aria-label={`删除指标 ${item.code}`}
                              onClick={() => action.run(async () => {
                                await request(`/api/admin/metrics/${encodeURIComponent(item.code)}`, { method: "DELETE" });
                                metrics.reload();
                              }, `已删除指标 ${item.code}`)}
                            >
                              <Trash2 size={13} />
                            </Button>
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            {!filteredMetrics.length && !metrics.loading && <Empty>暂无匹配的指标定义</Empty>}
          </>
        )}

        {subTab === "dimensions" && (
          <>
            <Loading {...dimensions} />
            <div className="table-wrap">
              <table className="data-table attribute-table admin-table">
                <thead>
                  <tr><th>维度编码</th><th>名称</th><th>SQL 表达式</th><th>别名</th><th>状态</th><th>操作</th></tr>
                </thead>
                <tbody>
                  {filteredDims.map(dim => {
                    const st = govStatusBadge(dim.status);
                    return (
                      <tr key={dim.code}>
                        <td><code className="gov-code">{dim.code}</code></td>
                        <td><strong>{dim.name}</strong><small>{dim.requiredField || "—"}</small></td>
                        <td><code className="gov-code gov-clamp" title={dim.expression}>{dim.expression}</code></td>
                        <td>{dim.alias}</td>
                        <td><Badge tone={st.tone}>{st.label}</Badge></td>
                        <td>
                          <div className="manager-tools">
                            <Button variant="ghost" onClick={() => setDimForm({ ...dim })}><Pencil size={13} /></Button>
                            <Button variant="ghost" onClick={() => action.run(async () => {
                              await request(`/api/admin/dimensions/${encodeURIComponent(dim.code)}`, { method: "DELETE" });
                              dimensions.reload();
                            }, `已删除维度 ${dim.code}`)}><Trash2 size={13} /></Button>
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            {!filteredDims.length && !dimensions.loading && <Empty>暂无分析维度定义</Empty>}
          </>
        )}

        {subTab === "filters" && (
          <>
            <Loading {...filters} />
            <div className="table-wrap">
              <table className="data-table attribute-table admin-table">
                <thead>
                  <tr><th>筛选编码</th><th>名称</th><th>列表达式 / 子查询</th><th>操作符</th><th>类型</th><th>操作</th></tr>
                </thead>
                <tbody>
                  {filteredFilters.map(flt => (
                    <tr key={flt.code}>
                      <td><code className="gov-code">{flt.code}</code></td>
                      <td><strong>{flt.name}</strong>{flt.joinIndicator && <small>关联 indicator</small>}</td>
                      <td><code className="gov-code gov-clamp" title={flt.columnExpr}>{flt.columnExpr}</code></td>
                      <td><Badge>{flt.operator}</Badge></td>
                      <td>{flt.valueType}</td>
                      <td>
                        <div className="manager-tools">
                          <Button variant="ghost" onClick={() => setFilterForm({ ...flt })}><Pencil size={13} /></Button>
                          <Button variant="ghost" onClick={() => action.run(async () => {
                            await request(`/api/admin/filters/${encodeURIComponent(flt.code)}`, { method: "DELETE" });
                            filters.reload();
                          }, `已删除筛选条件 ${flt.code}`)}><Trash2 size={13} /></Button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {!filteredFilters.length && !filters.loading && <Empty>暂无筛选条件定义</Empty>}
          </>
        )}
      </Card>

      {metricForm && (
        <Modal title="保存业务指标" subtitle="提交后请在「本体管理」点击发布以同步至实时问数引擎" wide onClose={closeMetric}>
          <form onSubmit={e => {
            e.preventDefault();
            action.run(async () => {
              const df = JSON.parse(metricForm.defaultFiltersText || "{}");
              const joins = JSON.parse(metricForm.joinsText || "[]");
              await request("/api/admin/metrics", {
                method: "POST",
                body: JSON.stringify({ ...metricForm, defaultFilters: df, joinsJson: joins }),
              });
              setMetricForm(null);
              metrics.reload();
            }, "指标定义已保存，请到本体管理点击“发布并同步本体”");
          }}>
            <div className="form-grid">
              <label className="form-field">指标编码<input required value={metricForm.code} onChange={e => setMetricForm({ ...metricForm, code: e.target.value })} placeholder="如 dep_delay_count" /></label>
              <label className="form-field">指标名称<input required value={metricForm.name} onChange={e => setMetricForm({ ...metricForm, name: e.target.value })} /></label>
              <label className="form-field">域<select value={metricForm.domain} onChange={e => setMetricForm({ ...metricForm, domain: e.target.value })}><option value="FIMS">FIMS</option><option value="PRODUCTION">PRODUCTION</option><option value="OPS">OPS</option><option value="ACDM">ACDM</option></select></label>
              <label className="form-field">单位<input value={metricForm.unit} onChange={e => setMetricForm({ ...metricForm, unit: e.target.value })} placeholder="架次 / 人次 / kg / %" /></label>
              <label className="form-field">基准表<input required value={metricForm.baseTable} onChange={e => setMetricForm({ ...metricForm, baseTable: e.target.value })} /></label>
              <label className="form-field">结果别名<input required value={metricForm.valueAlias} onChange={e => setMetricForm({ ...metricForm, valueAlias: e.target.value })} /></label>
              <label className="form-field">可用性<select value={metricForm.availability} onChange={e => setMetricForm({ ...metricForm, availability: e.target.value })}><option value="AVAILABLE">AVAILABLE（可用）</option><option value="PARTIAL">PARTIAL（部分可用）</option><option value="PLANNED">PLANNED（规划中）</option><option value="UNAVAILABLE">UNAVAILABLE（不可用）</option></select></label>
              <label className="form-field">发布状态<select value={metricForm.status} onChange={e => setMetricForm({ ...metricForm, status: e.target.value })}><option value="PUBLISHED">PUBLISHED（已发布）</option><option value="DRAFT">DRAFT（草稿）</option><option value="ARCHIVED">ARCHIVED（已归档）</option></select></label>
              <label className="form-field form-span">业务口径说明<input value={metricForm.formulaDesc} onChange={e => setMetricForm({ ...metricForm, formulaDesc: e.target.value })} /></label>
              <label className="form-field form-span">聚合 SQL 表达式<textarea rows={2} required value={metricForm.aggregateExpression} onChange={e => setMetricForm({ ...metricForm, aggregateExpression: e.target.value })} /></label>
              <label className="form-field">默认过滤 JSON<textarea rows={3} value={metricForm.defaultFiltersText} onChange={e => setMetricForm({ ...metricForm, defaultFiltersText: e.target.value })} /></label>
              <label className="form-field">关联表 joins_json<textarea rows={3} value={metricForm.joinsText} onChange={e => setMetricForm({ ...metricForm, joinsText: e.target.value })} /></label>
            </div>
            <div className="modal-actions">
              <Button onClick={closeMetric}>取消</Button>
              <Button type="submit" variant="primary" disabled={action.busy}><Save size={13} />保存指标</Button>
            </div>
          </form>
        </Modal>
      )}

      {dimForm && (
        <Modal title="保存分析维度" onClose={closeDim}>
          <form onSubmit={e => {
            e.preventDefault();
            action.run(async () => {
              await request("/api/admin/dimensions", { method: "POST", body: JSON.stringify(dimForm) });
              setDimForm(null);
              dimensions.reload();
            }, "维度定义已保存");
          }}>
            <div className="form-grid">
              <label className="form-field">维度编码<input required value={dimForm.code} onChange={e => setDimForm({ ...dimForm, code: e.target.value })} /></label>
              <label className="form-field">维度名称<input required value={dimForm.name} onChange={e => setDimForm({ ...dimForm, name: e.target.value })} /></label>
              <label className="form-field">输出别名<input required value={dimForm.alias} onChange={e => setDimForm({ ...dimForm, alias: e.target.value })} /></label>
              <label className="form-field">状态<select value={dimForm.status} onChange={e => setDimForm({ ...dimForm, status: e.target.value })}><option value="PUBLISHED">PUBLISHED</option><option value="DRAFT">DRAFT</option></select></label>
              <label className="form-field form-span">SQL 表达式<textarea rows={3} required value={dimForm.expression} onChange={e => setDimForm({ ...dimForm, expression: e.target.value })} /></label>
            </div>
            <div className="modal-actions">
              <Button onClick={closeDim}>取消</Button>
              <Button type="submit" variant="primary" disabled={action.busy}><Save size={13} />保存</Button>
            </div>
          </form>
        </Modal>
      )}

      {filterForm && (
        <Modal title="保存筛选条件" onClose={closeFilter}>
          <form onSubmit={e => {
            e.preventDefault();
            action.run(async () => {
              await request("/api/admin/filters", { method: "POST", body: JSON.stringify(filterForm) });
              setFilterForm(null);
              filters.reload();
            }, "筛选条件已保存");
          }}>
            <div className="form-grid">
              <label className="form-field">筛选编码<input required value={filterForm.code} onChange={e => setFilterForm({ ...filterForm, code: e.target.value })} /></label>
              <label className="form-field">中文名称<input required value={filterForm.name} onChange={e => setFilterForm({ ...filterForm, name: e.target.value })} /></label>
              <label className="form-field">操作符<select value={filterForm.operator} onChange={e => setFilterForm({ ...filterForm, operator: e.target.value })}><option value="eq">eq (=)</option><option value="ne">ne (!=)</option><option value="like">like</option><option value="exists">exists (子查询)</option><option value="in">in</option></select></label>
              <label className="form-field">值类型<select value={filterForm.valueType} onChange={e => setFilterForm({ ...filterForm, valueType: e.target.value })}><option value="string">string</option><option value="boolean">boolean</option><option value="number">number</option><option value="date">date</option></select></label>
              <label className="form-field form-span">列表达式 / EXISTS SQL<textarea rows={3} required value={filterForm.columnExpr} onChange={e => setFilterForm({ ...filterForm, columnExpr: e.target.value })} /></label>
              <label className="admin-toggle form-span"><input type="checkbox" checked={filterForm.joinIndicator} onChange={e => setFilterForm({ ...filterForm, joinIndicator: e.target.checked })} />需要关联 std_flight_indicator (i)</label>
            </div>
            <Notice>子查询使用 ? 作为安全参数占位符，查询执行时严格采用参数化绑定。</Notice>
            <div className="modal-actions">
              <Button onClick={closeFilter}>取消</Button>
              <Button type="submit" variant="primary" disabled={action.busy}><Save size={13} />保存</Button>
            </div>
          </form>
        </Modal>
      )}
    </>
  );
}
