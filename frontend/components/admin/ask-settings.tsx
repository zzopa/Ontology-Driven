"use client";

import { useEffect, useState } from "react";
import { Clock, MapPin, Save } from "lucide-react";
import type { HomeAirportConfig, OperationalDayConfig } from "@/lib/admin";
import { request } from "@/lib/api";
import { Badge, Button, Card } from "../ui";
import { SettingsPanel } from "./settings";
import { Loading, useAction, useResource } from "./shared";

function computeBoundaryLabel(enabled: boolean, startHour: number) {
  if (!enabled) return "已停用（按自然日 00:00–23:59）";
  const s = ((Number(startHour) || 0) % 24 + 24) % 24;
  const e = (s + 23) % 24;
  return `${String(s).padStart(2, "0")}:00 至次日 ${String(e).padStart(2, "0")}:59`;
}

export function AskSettingsPanel() {
  const homeRes = useResource<{ ok: boolean; config: HomeAirportConfig }>("/api/admin/ask-settings/home-airport");
  const opRes = useResource<{ ok: boolean; config: OperationalDayConfig }>("/api/admin/ask-settings/operational-day");
  const action = useAction();

  const [homeForm, setHomeForm] = useState<HomeAirportConfig & { aliasesText: string }>({
    iataCode: "WUH", cityName: "武汉", displayName: "武汉天河", aliases: [], aliasesText: "天河、武汉机场、武汉天河机场",
  });
  const [opForm, setOpForm] = useState<OperationalDayConfig>({
    enabled: true, startHour: 5, timezone: "Asia/Shanghai",
  });

  useEffect(() => {
    if (homeRes.data?.config) {
      const c = homeRes.data.config;
      setHomeForm({ ...c, aliasesText: (c.aliases || []).join("、") });
    }
  }, [homeRes.data]);

  useEffect(() => {
    if (opRes.data?.config) {
      setOpForm(opRes.data.config);
    }
  }, [opRes.data]);

  return (
    <div className="gov-stack">
      <div className="admin-section-head">
        <div>
          <h2>问数设置</h2>
          <p>配置多机场语义锚点（home_airport）、民航运营日日界（operational_day）与平台策略</p>
        </div>
        <Badge tone="green">多场数据时务必配置本场锚点</Badge>
      </div>

      {action.feedback}

      <div className="admin-overview-grid">
        <Card className="card-pad">
          <div className="card-head">
            <h3 className="section-title"><MapPin size={15} />本场机场配置（home_airport）</h3>
            <Badge>{homeForm.iataCode}</Badge>
          </div>
          <Loading {...homeRes} />
          <form onSubmit={e => {
            e.preventDefault();
            action.run(async () => {
              await request("/api/admin/ask-settings/home-airport", {
                method: "PUT",
                body: JSON.stringify({
                  iataCode: homeForm.iataCode,
                  cityName: homeForm.cityName,
                  displayName: homeForm.displayName,
                  aliases: homeForm.aliasesText.split(/[,，、]/).map(s => s.trim()).filter(Boolean),
                }),
              });
              homeRes.reload();
            }, "本场机场设置已保存并生效");
          }}>
            <div className="form-grid">
              <label className="form-field">三字码 IATA<input required maxLength={3} value={homeForm.iataCode} onChange={e => setHomeForm({ ...homeForm, iataCode: e.target.value.toUpperCase() })} /></label>
              <label className="form-field">城市名称<input required value={homeForm.cityName} onChange={e => setHomeForm({ ...homeForm, cityName: e.target.value })} /></label>
              <label className="form-field form-span">展示名称<input required value={homeForm.displayName} onChange={e => setHomeForm({ ...homeForm, displayName: e.target.value })} /></label>
              <label className="form-field form-span">自然语言别名（逗号/顿号分隔）<input value={homeForm.aliasesText} onChange={e => setHomeForm({ ...homeForm, aliasesText: e.target.value })} /></label>
            </div>
            <div className="modal-actions">
              <Button type="submit" variant="primary" disabled={action.busy}><Save size={13} />保存本场</Button>
            </div>
          </form>
        </Card>

        <Card className="card-pad">
          <div className="card-head">
            <h3 className="section-title"><Clock size={15} />运营日日界配置（operational_day）</h3>
            <Badge tone={opForm.enabled ? "green" : "neutral"}>{opForm.enabled ? "已启用" : "自然日"}</Badge>
          </div>
          <Loading {...opRes} />
          <form onSubmit={e => {
            e.preventDefault();
            action.run(async () => {
              await request("/api/admin/ask-settings/operational-day", {
                method: "PUT",
                body: JSON.stringify(opForm),
              });
              opRes.reload();
            }, "运营日配置已保存并生效");
          }}>
            <div className="form-grid">
              <label className="admin-toggle form-span"><input type="checkbox" checked={opForm.enabled} onChange={e => setOpForm({ ...opForm, enabled: e.target.checked })} />启用按民航业务运营日切日统计</label>
              <label className="form-field">日界时刻（0–23 点）<input type="number" min={0} max={23} disabled={!opForm.enabled} value={opForm.startHour} onChange={e => setOpForm({ ...opForm, startHour: Number(e.target.value) })} /></label>
              <label className="form-field">时区<input disabled={!opForm.enabled} value={opForm.timezone} onChange={e => setOpForm({ ...opForm, timezone: e.target.value })} /></label>
              <div className="form-span"><p className="helper">实时切日预览：<strong>{computeBoundaryLabel(opForm.enabled, opForm.startHour)}</strong></p></div>
            </div>
            <div className="modal-actions">
              <Button type="submit" variant="primary" disabled={action.busy}><Save size={13} />保存运营日</Button>
            </div>
          </form>
        </Card>
      </div>

      <SettingsPanel />
    </div>
  );
}
