# -*- coding: utf-8 -*-
"""生成合同查询可达节点的字段级全景图 renderer（静态 SVG）。"""
import os
from scripts.paths import RENDERER_DIR

BASE = str(RENDERER_DIR)
svg = '''<html style="margin:0;padding:0;">
<div style="background-color:transparent;box-sizing:border-box;">
  <svg viewBox="0 0 960 660" style="width:100%;height:auto;display:block;font-family:'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif;">
    <defs>
      <marker id="aFK" markerWidth="9" markerHeight="9" refX="7" refY="3" orient="auto"><path d="M0,0 L7,3 L0,6 Z" fill="#7C8CE0"/></marker>
      <marker id="aLO" markerWidth="9" markerHeight="9" refX="7" refY="3" orient="auto"><path d="M0,0 L7,3 L0,6 Z" fill="#8A8578"/></marker>
    </defs>

    <!-- 图例 -->
    <g font-size="11.5" fill="#555">
      <line x1="30" y1="22" x2="70" y2="22" stroke="#7C8CE0" stroke-width="1.8" marker-end="url(#aFK)"/>
      <text x="78" y="26">数据库外键</text>
      <line x1="205" y1="22" x2="245" y2="22" stroke="#8A8578" stroke-width="1.5" stroke-dasharray="5,4" marker-end="url(#aLO)"/>
      <text x="253" y="26">逻辑关联（数据级验证）</text>
      <rect x="465" y="13" width="18" height="16" rx="4" fill="#fff" stroke="#7DC89A" stroke-width="1.6"/>
      <text x="491" y="26">本单命中</text>
      <rect x="590" y="13" width="18" height="16" rx="4" fill="#fff" stroke="#C9C4B4" stroke-width="1.4"/>
      <text x="616" y="26">结构存在 · 本单暂无</text>
    </g>

    <!-- 连线：中心→左列 FK -->
    <g stroke="#7C8CE0" stroke-width="1.8" fill="none">
      <line x1="330" y1="185" x2="232" y2="150" marker-end="url(#aFK)"/>
      <line x1="330" y1="265" x2="232" y2="270" marker-end="url(#aFK)"/>
      <line x1="330" y1="365" x2="232" y2="390" marker-end="url(#aFK)"/>
    </g>
    <text x="245" y="160" font-size="11" fill="#4A4F8C" stroke="#F4F3EE" stroke-width="4" paint-order="stroke">main_id</text>
    <text x="245" y="285" font-size="11" fill="#4A4F8C" stroke="#F4F3EE" stroke-width="4" paint-order="stroke">main_id</text>
    <text x="245" y="405" font-size="11" fill="#4A4F8C" stroke="#F4F3EE" stroke-width="4" paint-order="stroke">main_id</text>

    <!-- 连线：中心→右列逻辑关联 -->
    <g stroke="#8A8578" stroke-width="1.5" stroke-dasharray="5,4" fill="none">
      <line x1="630" y1="395" x2="660" y2="185" marker-end="url(#aLO)"/>
      <line x1="630" y1="425" x2="660" y2="260" marker-end="url(#aLO)"/>
      <line x1="630" y1="450" x2="660" y2="335" marker-end="url(#aLO)"/>
      <line x1="630" y1="470" x2="660" y2="420" marker-end="url(#aLO)"/>
    </g>
    <text x="648" y="275" font-size="11" fill="#6B7280" stroke="#F4F3EE" stroke-width="4" paint-order="stroke">submission_id</text>
    <text x="648" y="300" font-size="11" fill="#6B7280" stroke="#F4F3EE" stroke-width="4" paint-order="stroke">submitter_user_id</text>
    <text x="648" y="395" font-size="11" fill="#6B7280" stroke="#F4F3EE" stroke-width="4" paint-order="stroke">enterprise_scc</text>
    <text x="648" y="460" font-size="11" fill="#6B7280" stroke="#F4F3EE" stroke-width="4" paint-order="stroke">gzw_biz_id</text>

    <!-- 连线：中心→下方履约系列 -->
    <g stroke="#B9B4A4" stroke-width="1.3" stroke-dasharray="5,4" fill="none">
      <line x1="470" y1="460" x2="140" y2="540" marker-end="url(#aLO)"/>
      <line x1="480" y1="460" x2="365" y2="540" marker-end="url(#aLO)"/>
      <line x1="490" y1="460" x2="590" y2="540" marker-end="url(#aLO)"/>
      <line x1="500" y1="460" x2="815" y2="540" marker-end="url(#aLO)"/>
    </g>
    <text x="430" y="500" font-size="11" fill="#999" stroke="#F4F3EE" stroke-width="4" paint-order="stroke">related_contract_id · 逻辑关联</text>

    <!-- 中心节点：contract_main -->
    <g>
      <rect x="330" y="150" width="300" height="322" rx="12" fill="#EFEEFF" stroke="#7C8CE0" stroke-width="2.2"/>
      <text x="348" y="173" font-size="14" font-weight="700" fill="#1A1B1C">contract_main · 主表</text>
      <text x="348" y="191" font-size="11" fill="#4A4F8C">GL-XZ-2026001 · 71 字段 · 已通过</text>
      <g font-size="10.5">
        <text x="348" y="214" font-weight="600" fill="#4A4F8C">识别</text>
        <text x="348" y="230" fill="#333">contract_name 武汉天河机场信息服务合同</text>
        <text x="348" y="244" fill="#333">title / subject_matter / contract_phase 01</text>
        <text x="348" y="270" font-weight="600" fill="#4A4F8C">金额</text>
        <text x="348" y="286" fill="#333">lump_sum_amount_wan 12,000 · CNY</text>
        <text x="348" y="300" fill="#333">guarantee / collateral / winning 字段</text>
        <text x="348" y="326" font-weight="600" fill="#4A4F8C">时间 / 采购</text>
        <text x="348" y="342" fill="#333">signing_at 2026-01-15 · 履约期 364 天</text>
        <text x="348" y="356" fill="#333">pricing / procurement / via_bidding</text>
        <text x="348" y="382" font-weight="600" fill="#7C4A8C">关联锚点（字段→目标表）</text>
        <text x="348" y="398" fill="#4A4F8C">submission_id → contract_submission</text>
        <text x="348" y="412" fill="#4A4F8C">submitter_user_id → app_user</text>
        <text x="348" y="426" fill="#4A4F8C">enterprise_scc → comp_enterprise_ledger</text>
        <text x="348" y="440" fill="#4A4F8C">gzw_biz_id → gzw_business_data</text>
        <text x="348" y="456" fill="#999">related_contract_no / tender_code / source</text>
      </g>
    </g>

    <!-- 左列：FK 直连 -->
    <g font-size="11.5" fill="#333">
      <rect x="25" y="110" width="205" height="80" rx="10" fill="#fff" stroke="#7DC89A" stroke-width="1.8"/>
      <text x="38" y="132" font-weight="600">合同相对方 ×1</text>
      <text x="38" y="150" font-size="10.5" fill="#555">contract_counterparty</text>
      <text x="38" y="167" font-size="10.5" fill="#333">湖北机场集团信息科技…</text>
      <text x="38" y="182" font-size="10" fill="#6B7280">main_id → contract_main.id</text>

      <rect x="25" y="230" width="205" height="80" rx="10" fill="#fff" stroke="#7DC89A" stroke-width="1.8"/>
      <text x="38" y="252" font-weight="600">付款节点 ×1</text>
      <text x="38" y="270" font-size="10.5" fill="#555">contract_payment_node</text>
      <text x="38" y="287" font-size="10.5" fill="#333">node_type=03 · 金额 0 万</text>
      <text x="38" y="302" font-size="10" fill="#6B7280">main_id → contract_main.id</text>

      <rect x="25" y="350" width="205" height="80" rx="10" fill="#fff" stroke="#C9C4B4" stroke-width="1.4"/>
      <text x="38" y="372" font-weight="600" fill="#6B7280">购销关联 ×0</text>
      <text x="38" y="390" font-size="10.5" fill="#999">contract_sales_purchase_link</text>
      <text x="38" y="407" font-size="10.5" fill="#999">本单暂无购销记录</text>
      <text x="38" y="422" font-size="10" fill="#999">main_id → contract_main.id</text>
    </g>

    <!-- 右列：逻辑关联 -->
    <g font-size="11.5" fill="#333">
      <rect x="660" y="145" width="265" height="80" rx="10" fill="#fff" stroke="#7DC89A" stroke-width="1.8"/>
      <text x="673" y="167" font-weight="600">填报记录 ×1</text>
      <text x="673" y="185" font-size="10.5" fill="#555">contract_submission</text>
      <text x="673" y="202" font-size="10.5" fill="#333">合同修改组单 · 50 份 · 已通过</text>
      <text x="673" y="217" font-size="10" fill="#6B7280">submission_id（含审批字段）</text>

      <rect x="660" y="235" width="265" height="80" rx="10" fill="#fff" stroke="#7DC89A" stroke-width="1.8"/>
      <text x="673" y="257" font-weight="600">提交人 ×1</text>
      <text x="673" y="275" font-size="10.5" fill="#555">app_user</text>
      <text x="673" y="292" font-size="10.5" fill="#333">submitter_user_id 命中用户</text>
      <text x="673" y="307" font-size="10" fill="#6B7280">submitter_user_id（逻辑关联）</text>

      <rect x="660" y="325" width="265" height="80" rx="10" fill="#fff" stroke="#7DC89A" stroke-width="1.8"/>
      <text x="673" y="347" font-weight="600">企业台账 ×1</text>
      <text x="673" y="365" font-size="10.5" fill="#555">comp_enterprise_ledger</text>
      <text x="673" y="382" font-size="10.5" fill="#333">91420712MAG0C45Q72</text>
      <text x="673" y="397" font-size="10" fill="#6B7280">enterprise_scc（逻辑关联）</text>

      <rect x="660" y="415" width="265" height="80" rx="10" fill="#fff" stroke="#7DC89A" stroke-width="1.8"/>
      <text x="673" y="437" font-weight="600">国资业务数据 ×1</text>
      <text x="673" y="455" font-size="10.5" fill="#555">gzw_business_data</text>
      <text x="673" y="472" font-size="10.5" fill="#333">MAG0C45Q722026011613000000000001</text>
      <text x="673" y="487" font-size="10" fill="#6B7280">gzw_biz_id（逻辑关联）</text>
    </g>

    <!-- 下方：履约系列 -->
    <g font-size="11.5" fill="#333">
      <rect x="40"  y="540" width="200" height="60" rx="10" fill="#fff" stroke="#C9C4B4" stroke-width="1.4"/>
      <text x="53"  y="562" font-weight="600" fill="#6B7280">履约结算 ×0</text>
      <text x="53"  y="580" font-size="10" fill="#999">contract_perf_settlement</text>
      <rect x="265" y="540" width="200" height="60" rx="10" fill="#fff" stroke="#C9C4B4" stroke-width="1.4"/>
      <text x="278" y="562" font-weight="600" fill="#6B7280">履约发票 ×0</text>
      <text x="278" y="580" font-size="10" fill="#999">contract_perf_invoice</text>
      <rect x="490" y="540" width="200" height="60" rx="10" fill="#fff" stroke="#C9C4B4" stroke-width="1.4"/>
      <text x="503" y="562" font-weight="600" fill="#6B7280">履约交货 ×0</text>
      <text x="503" y="580" font-size="10" fill="#999">contract_perf_delivery</text>
      <rect x="715" y="540" width="200" height="60" rx="10" fill="#fff" stroke="#C9C4B4" stroke-width="1.4"/>
      <text x="728" y="562" font-weight="600" fill="#6B7280">履约收据 ×0</text>
      <text x="728" y="580" font-size="10" fill="#999">contract_perf_receipt</text>
    </g>
  </svg>
  <div style="font-size:12px;color:#6B7280;margin-top:10px;line-height:1.6;">
    口径：节点与命中数来自 hbairport 库实际查询验证（合同 GL-XZ-2026001 · 2026-09-28）。实线=数据库外键；
    虚线=无外键约束但字段值真实匹配的逻辑关联（已逐条数据验证）。履约系列为结构存在、本单暂无履约数据；
    换有履约记录的合同编号即可查到。提交部门本单为空（submitter_department_id=NULL），审批流经 contract_submission 的审批字段可继续延伸。
  </div>
</div>
</html>'''

with open(os.path.join(BASE, '_renderer_contract_nodes.html'), 'w', encoding='utf-8') as f:
    f.write(svg)
print('lines:', svg.count('\n') + 1)
