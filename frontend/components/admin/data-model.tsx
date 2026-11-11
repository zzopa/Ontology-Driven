"use client";

import { useEffect, useState } from "react";
import { ArrowRight, Boxes, Database, GitBranch, RefreshCw } from "lucide-react";
import type { BizModelObjectDetail, BizModelOverview, PhysicalModel } from "@/lib/admin";
import { request } from "@/lib/api";
import { Badge, Button, Card } from "../ui";
import { Empty, Loading, useResource } from "./shared";

export function DataModelPanel() {
  const [viewMode, setViewMode] = useState<"biz" | "physical">("biz");
  const overview = useResource<BizModelOverview>("/api/admin/model/overview");
  const physical = useResource<PhysicalModel>("/api/admin/model/physical");
  const [selectedCode, setSelectedCode] = useState("");
  const [detail, setDetail] = useState<BizModelObjectDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  const objects = overview.data?.objects || [];

  useEffect(() => {
    if (!selectedCode && objects.length > 0) {
      setSelectedCode(objects[0].code);
    }
  }, [objects, selectedCode]);

  useEffect(() => {
    if (!selectedCode || viewMode !== "biz") return;
    let cancelled = false;
    setDetailLoading(true);
    request<BizModelObjectDetail>(`/api/admin/model/objects/${encodeURIComponent(selectedCode)}`)
      .then(res => { if (!cancelled) setDetail(res); })
      .catch(() => { if (!cancelled) setDetail(null); })
      .finally(() => { if (!cancelled) setDetailLoading(false); });
    return () => { cancelled = true; };
  }, [selectedCode, viewMode]);

  return (
    <>
      <div className="admin-section-head">
        <div>
          <h2>数据模型</h2>
          <p>浏览业务对象语义建模（meta_biz_*）及底层标准事实/维表物理结构（meta_model_*）</p>
        </div>
        <div className="manager-tools">
          <Button onClick={() => { overview.reload(); physical.reload(); }}>
            <RefreshCw size={13} />刷新模型
          </Button>
        </div>
      </div>

      <div className="gov-subtabs">
        <button className={`gov-subtab ${viewMode === "biz" ? "selected" : ""}`} onClick={() => setViewMode("biz")}>
          <Boxes size={14} />业务语义模型 <Badge>{overview.data?.objectCount ?? 0} 对象 · {overview.data?.attributeCount ?? 0} 属性</Badge>
        </button>
        <button className={`gov-subtab ${viewMode === "physical" ? "selected" : ""}`} onClick={() => setViewMode("physical")}>
          <Database size={14} />物理表结构与外键 <Badge>{physical.data?.entities.length ?? 0} 表 · {physical.data?.relations.length ?? 0} 关系</Badge>
        </button>
      </div>

      {viewMode === "biz" && (
        <>
          <Loading {...overview} />
          {objects.length > 0 ? (
            <div className="model-layout">
              <aside className="model-nav" aria-label="业务对象列表">
                {objects.map(obj => (
                  <button
                    key={obj.code}
                    className={`model-item ${selectedCode === obj.code ? "selected" : ""}`}
                    onClick={() => setSelectedCode(obj.code)}
                  >
                    <strong>{obj.name}</strong>
                    <small>{obj.summary || obj.description || obj.code}</small>
                    <span><code className="gov-code">{obj.code}</code> · {obj.attributeCount} 属性 · {obj.relationCount} 关系</span>
                  </button>
                ))}
              </aside>

              <div className="gov-stack">
                {detailLoading && <Card className="card-pad"><p className="helper">正在加载业务对象详情…</p></Card>}
                {!detailLoading && detail && (
                  <>
                    <Card className="card-pad">
                      <div className="card-head">
                        <div>
                          <h3 className="section-title"><Boxes size={15} />{detail.object.name}</h3>
                          <p className="helper">{detail.object.summary || detail.object.description}</p>
                        </div>
                        <Badge tone="green">{detail.object.code}</Badge>
                      </div>
                      {detail.object.description && detail.object.description !== detail.object.summary && (
                        <p className="long-description">{detail.object.description}</p>
                      )}
                    </Card>

                    {Object.entries(detail.attributesByGroup || {}).map(([groupName, attrs]) => (
                      <Card className="card-pad" key={groupName}>
                        <div className="card-head">
                          <h4 className="section-title">{groupName}</h4>
                          <Badge>{attrs.length} 项</Badge>
                        </div>
                        <div className="table-wrap">
                          <table className="data-table attribute-table admin-table">
                            <thead>
                              <tr><th>业务属性</th><th>类型</th><th>语义说明与示例</th><th>技术落表</th></tr>
                            </thead>
                            <tbody>
                              {attrs.map(a => (
                                <tr key={a.code}>
                                  <td><strong>{a.name}</strong><small>{a.code}</small></td>
                                  <td><Badge>{a.valueType || "string"}</Badge></td>
                                  <td>
                                    <span>{a.description || "—"}</span>
                                    {a.exampleValue && <small>例：{a.exampleValue}</small>}
                                  </td>
                                  <td><code className="gov-code">{a.stdEntity ? `${a.stdEntity}.${a.stdField}` : a.askAttrCode || "—"}</code></td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      </Card>
                    ))}

                    <Card className="card-pad">
                      <div className="card-head">
                        <h4 className="section-title"><GitBranch size={15} />业务关联关系</h4>
                        <Badge>{detail.relations.length} 条</Badge>
                      </div>
                      {detail.relations.length > 0 ? (
                        <div className="table-wrap">
                          <table className="data-table attribute-table admin-table">
                            <thead>
                              <tr><th>关系名</th><th>对象链路</th><th>基数</th><th>说明</th><th>操作</th></tr>
                            </thead>
                            <tbody>
                              {detail.relations.map(rel => (
                                <tr key={rel.code}>
                                  <td><strong>{rel.name}</strong><small>{rel.code}</small></td>
                                  <td>{rel.fromObjectName} <ArrowRight size={11} style={{ display: "inline" }} /> {rel.toObjectName}</td>
                                  <td><Badge>{rel.cardinality}</Badge></td>
                                  <td>{rel.description || "—"}</td>
                                  <td>
                                    <Button variant="ghost" onClick={() => setSelectedCode(rel.peerObject)}>查看对方</Button>
                                  </td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      ) : (
                        <Empty>该对象暂无关联关系</Empty>
                      )}
                    </Card>
                  </>
                )}
              </div>
            </div>
          ) : (
            !overview.loading && <Empty>暂未检测到业务模型定义</Empty>
          )}
        </>
      )}

      {viewMode === "physical" && (
        <div className="gov-stack">
          <Loading {...physical} />
          {(physical.data?.entities || []).map(entity => (
            <Card className="card-pad" key={entity.code}>
              <div className="card-head">
                <div>
                  <h3 className="section-title"><Database size={15} />{entity.name}（{entity.code}）</h3>
                  <p className="helper">粒度：{entity.grainDesc || "—"} · {entity.description}</p>
                </div>
                <Badge tone="green">{entity.entityKind}</Badge>
              </div>
              <div className="table-wrap">
                <table className="data-table attribute-table admin-table">
                  <thead>
                    <tr><th>物理字段</th><th>中文名</th><th>数据类型</th><th>问数属性映射</th><th>说明</th></tr>
                  </thead>
                  <tbody>
                    {entity.fields.slice(0, 20).map(f => (
                      <tr key={f.code}>
                        <td><code className="gov-code">{f.code}</code>{f.isBizKeyPart && <small>业务键</small>}</td>
                        <td><strong>{f.name}</strong></td>
                        <td>{f.dataType || "—"}</td>
                        <td>{f.askAttrCode ? <code className="gov-code">{f.askAttrCode}</code> : "—"}</td>
                        <td>{f.description || "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>
          ))}
        </div>
      )}
    </>
  );
}
