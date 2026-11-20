"use client";

import React from "react";
import {
  AlertCircle,
  BarChart3,
  Calendar,
  CalendarClock,
  Clock3,
  Layers,
  Plane,
  ShieldCheck,
  TrendingDown,
  TrendingUp,
  Users,
} from "lucide-react";
import s from "./bot-message-content.module.css";

export function RichText({ text }: { text: string }) {
  return (
    <>
      {text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).map((part, index) =>
        part.startsWith("**") && part.endsWith("**") ? (
          <strong key={index}>{part.slice(2, -2)}</strong>
        ) : part.startsWith("`") && part.endsWith("`") ? (
          <code key={index}>{part.slice(1, -1)}</code>
        ) : (
          <span key={index}>{part}</span>
        )
      )}
    </>
  );
}

type MetricData = {
  name: string;
  period?: string;
  value: string;
  unit?: string;
  tag?: string;
};

function parseMetricLine(line: string): MetricData | null {
  const m = line.match(
    /^([^·•:：\n]+?)(?:\s*[·•]\s*([^:：\n]+?))?\s*[:：]\s*(\d{4}-\d{2}-\d{2}|[+-]?[0-9.,]+|未能计算(?:[^。，,\n]+)?|-|—)(?:\s*([^\s（()）。\n]+))?(?:\s*[（(]([^）)]+)[）)])?[。.]?$/
  );
  if (!m) return null;
  const name = m[1].trim();
  if (
    name.length > 35 ||
    /^[，。！？!?]/.test(name) ||
    /^(注意|提示|问题|范围|说明|依据)/.test(name)
  ) {
    return null;
  }
  const period = m[2]?.trim();
  const value = m[3].trim();
  const unit = m[4]?.trim() || "";
  const tag = m[5]?.trim() || "";
  return { name, period, value, unit, tag };
}

function isDisclaimer(line: string): boolean {
  if (
    line.startsWith("本结果仅反映") ||
    line.startsWith("仅反映本地已同步记录") ||
    line.startsWith("查询范围：") ||
    line.startsWith("查询范围:") ||
    line.startsWith("声明：") ||
    line.startsWith("声明:") ||
    line.startsWith("口径说明：")
  ) {
    return true;
  }
  if (
    line.includes("未证明同步完整性") ||
    line.includes("日期连续不证明同步完整") ||
    line.includes("不能据此推断未同步业务") ||
    line.includes("缺少有效值或分母为零") ||
    line.includes("包含模拟测试数据，不能作为")
  ) {
    return true;
  }
  return false;
}

function isBoundaryNotice(line: string): boolean {
  return (
    line.includes("尚未映射到已发布查询能力") ||
    line.includes("未匹配到业务本体对照") ||
    line.includes("回复回复不了") ||
    line.includes("不能由模型补造数据")
  );
}

function parseTurnaroundHero(line: string) {
  const m = line.match(
    /^(\d{4}-\d{2}-\d{2})?\s*([A-Za-z0-9]{2,6})\s*地面过站保障总时长为\s*([\d.]+)\s*分钟。?$/
  );
  if (!m) return null;
  return { date: m[1], flight: m[2], duration: m[3] };
}

function parseDiffLine(line: string) {
  const m = line.match(
    /^(.+?)\s*较基期变化\s*([+-]?[\d.]+)\s*([^；;]+)[；;]\s*增长率\s*([^\n]+)$/
  );
  if (!m) return null;
  return {
    label: m[1].trim(),
    diff: m[2].trim(),
    unit: m[3].trim(),
    rate: m[4].trim(),
  };
}

function getMetricTone(name: string): "blue" | "green" | "purple" | "amber" | "neutral" {
  if (/计划/.test(name)) return "blue";
  if (/实际|执行|执飞|起飞|到达/.test(name)) return "green";
  if (/率|环比|同比|增长|对标/.test(name)) return "purple";
  if (/延误|警告|测试|模拟/.test(name)) return "amber";
  return "blue";
}

function getMetricIcon(name: string) {
  if (/业务日$/.test(name)) return Calendar;
  if (/计划/.test(name)) return CalendarClock;
  if (/实际|执行|执飞|起飞|到达/.test(name)) return Plane;
  if (/时长|时间|靠位|撤轮挡|周转/.test(name)) return Clock3;
  if (/率|环比|同比|增长|对标/.test(name)) return TrendingUp;
  if (/旅客|座位|旅客量/.test(name)) return Users;
  return BarChart3;
}

export function BotMessageContent({ text }: { text: string }) {
  if (!text) return <span>已完成本轮查询，可查看图表与数据明细。</span>;

  const lines = text.split("\n");
  const blocks: React.ReactNode[] = [];
  let listBuffer: { type: "bullet" | "step"; content: string; num?: string }[] = [];
  let metricBuffer: MetricData[] = [];

  const flushMetrics = (keyPrefix: number) => {
    if (!metricBuffer.length) return null;
    const currentMetrics = [...metricBuffer];
    metricBuffer = [];
    return (
      <div key={`metrics-${keyPrefix}`} className={s.botMetricsGrid}>
        {currentMetrics.map((item, i) => {
          const tone = getMetricTone(item.name);
          const Icon = getMetricIcon(item.name);
          return (
            <div key={i} className={`${s.botMetricCard} ${s[`tone_${tone}`]}`}>
              <div className={s.botMetricHead}>
                <div className={s.botMetricTitleGroup}>
                  <span className={s.botMetricIcon}>
                    <Icon size={14} aria-hidden="true" />
                  </span>
                  <span className={s.botMetricTitle} title={item.name}>
                    {item.name}
                  </span>
                </div>
                {item.period && (
                  <span className={s.botMetricPeriod} title={item.period}>
                    <Calendar size={10} aria-hidden="true" />
                    <span>{item.period}</span>
                  </span>
                )}
              </div>
              <div className={s.botMetricValueRow}>
                <div className={s.botMetricValue}>
                  <strong>{item.value}</strong>
                  {item.unit && <small>{item.unit}</small>}
                </div>
                {item.tag && <span className={s.botMetricTag}>{item.tag}</span>}
              </div>
            </div>
          );
        })}
      </div>
    );
  };

  const flushList = (keyPrefix: number) => {
    if (!listBuffer.length) return null;
    const currentList = [...listBuffer];
    listBuffer = [];
    return (
      <div key={`list-${keyPrefix}`} className={s.botListBlock}>
        {currentList.map((item, i) =>
          item.type === "step" ? (
            <div key={i} className={s.botStepRow}>
              <span className={s.botStepBadge}>{item.num}</span>
              <div className={s.botStepText}>
                <RichText text={item.content} />
              </div>
            </div>
          ) : (
            <div key={i} className={s.botBulletRow}>
              <span className={s.botBulletDot} aria-hidden="true" />
              <div className={s.botBulletText}>
                <RichText text={item.content} />
              </div>
            </div>
          )
        )}
      </div>
    );
  };

  const flushAll = (keyPrefix: number) => {
    const nodes: React.ReactNode[] = [];
    if (metricBuffer.length) nodes.push(flushMetrics(keyPrefix));
    if (listBuffer.length) nodes.push(flushList(keyPrefix));
    return nodes;
  };

  lines.forEach((line, idx) => {
    const trimmed = line.trim();
    if (!trimmed) {
      blocks.push(...flushAll(idx));
      return;
    }

    // 1. Disclaimer check
    if (isDisclaimer(trimmed)) {
      blocks.push(...flushAll(idx));
      blocks.push(
        <div key={idx} className={s.botDisclaimerBox}>
          <ShieldCheck size={14} className={s.botDisclaimerIcon} aria-hidden="true" />
          <div className={s.botDisclaimerContent}>
            <RichText text={trimmed} />
          </div>
        </div>
      );
      return;
    }

    // 2. Boundary / Notice check
    if (isBoundaryNotice(trimmed)) {
      blocks.push(...flushAll(idx));
      blocks.push(
        <div key={idx} className={s.botNoticeCard}>
          <AlertCircle size={15} className={s.botNoticeIcon} aria-hidden="true" />
          <div className={s.botNoticeText}>
            <RichText text={trimmed} />
          </div>
        </div>
      );
      return;
    }

    // 3. Turnaround hero check
    const hero = parseTurnaroundHero(trimmed);
    if (hero) {
      blocks.push(...flushAll(idx));
      blocks.push(
        <div key={idx} className={s.botHeroCard}>
          <div className={s.botHeroLeft}>
            <span className={s.botHeroIcon}>
              <Plane size={15} aria-hidden="true" />
            </span>
            <div className={s.botHeroText}>
              <span className={s.botHeroSub}>
                {hero.date ? `${hero.date} · ` : ""}地面过站保障
              </span>
              <strong className={s.botHeroFlight}>{hero.flight}</strong>
            </div>
          </div>
          <div className={s.botHeroRight}>
            <span className={s.botHeroDurationLabel}>总时长</span>
            <span className={s.botHeroDurationVal}>
              <strong>{hero.duration}</strong>
              <small>分钟</small>
            </span>
          </div>
        </div>
      );
      return;
    }

    // 4. Comparison diff line check
    const diff = parseDiffLine(trimmed);
    if (diff) {
      blocks.push(...flushAll(idx));
      const isPos = diff.diff.startsWith("+") || Number(diff.diff) > 0;
      const DiffIcon = isPos ? TrendingUp : TrendingDown;
      blocks.push(
        <div key={idx} className={s.botDiffCard}>
          <div className={s.botDiffHead}>
            <DiffIcon
              size={14}
              className={isPos ? s.diffPosIcon : s.diffNegIcon}
              aria-hidden="true"
            />
            <span className={s.botDiffLabel}>{diff.label}</span>
          </div>
          <div className={s.botDiffValues}>
            <span
              className={`${s.botDiffBadge} ${isPos ? s.diffBadgePos : s.diffBadgeNeg}`}
            >
              变化 {diff.diff} {diff.unit}
            </span>
            <span
              className={`${s.botGrowthBadge} ${isPos ? s.growthBadgePos : s.growthBadgeNeg}`}
            >
              增长率 {diff.rate}
            </span>
          </div>
        </div>
      );
      return;
    }

    // 5. Section Header
    if (
      (trimmed.startsWith("【") && trimmed.endsWith("】")) ||
      (trimmed.startsWith("【") && trimmed.endsWith("】："))
    ) {
      blocks.push(...flushAll(idx));
      const title = trimmed.replace(/^[【]|(?:】：?|[】])$/g, "");
      blocks.push(
        <div key={idx} className={s.botSectionHeader}>
          <span className={s.botSectionBadge}>
            <Layers size={13} aria-hidden="true" />
            <span>{title}</span>
          </span>
        </div>
      );
      return;
    }

    // 6. Metric line
    const metric = parseMetricLine(trimmed);
    if (metric) {
      if (listBuffer.length) blocks.push(flushList(idx));
      metricBuffer.push(metric);
      return;
    }

    // 7. List Step
    const stepMatch = trimmed.match(/^(\d+)\.\s*(.*)$/);
    if (stepMatch) {
      if (metricBuffer.length) blocks.push(flushMetrics(idx));
      listBuffer.push({ type: "step", num: stepMatch[1], content: stepMatch[2] });
      return;
    }

    // 8. Bullet
    if (trimmed.startsWith("• ") || trimmed.startsWith("- ")) {
      if (metricBuffer.length) blocks.push(flushMetrics(idx));
      listBuffer.push({ type: "bullet", content: trimmed.slice(2) });
      return;
    }

    // 9. Tips
    if (trimmed.startsWith("💡") || trimmed.startsWith("（提示：")) {
      blocks.push(...flushAll(idx));
      blocks.push(
        <div key={idx} className={s.botTipBox}>
          <RichText text={trimmed} />
        </div>
      );
      return;
    }

    // 10. Fallback paragraph
    blocks.push(...flushAll(idx));
    blocks.push(
      <div key={idx} className={s.botParagraph}>
        <RichText text={trimmed} />
      </div>
    );
  });

  blocks.push(...flushAll(lines.length));

  return <div className={s.botContentWrap}>{blocks}</div>;
}
