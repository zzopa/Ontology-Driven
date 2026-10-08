# -*- coding: utf-8 -*-
"""生成合同全链路查询结果的对话内 renderer（静态 SVG）。"""
import os
from scripts.paths import RENDERER_DIR

BASE = str(RENDERER_DIR)
svg = '''<html style="margin:0;padding:0;">
<div style="background-color:transparent;box-sizing:border-box;">
  <svg viewBox="0 0 860 620" style="width:100%;height:auto;display:block;font-family:'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif;">
    <defs>
      <marker id="arrFK" markerWidth="9" markerHeight="9" refX="7" refY="3" orient="auto">
        <path d="M0,0 L7,3 L0,6 Z" fill="#8A8578"/>
      </marker>
      <marker id="arrLO" markerWidth="9" markerHeight="9" refX="7" refY="3" orient="auto">
        <path d="M0,0 L7,3 L0,6 Z" fill="#B9B4A4"/>
      </marker>
    </defs>

    <!-- 图例 -->
    <g font-size="11.5" fill="#555">
      <line x1="30" y1="22" x2="70" y2="22" stroke="#8A8578" stroke-width="1.8" marker-end="url(#arrFK)"/>
      <text x="78" y="26">数据库外键（FK）</text>
      <line x1="205" y1="22" x2="245" y2="22" stroke="#B9B4A4" stroke-width="1.4" stroke-dasharray="5,4" marker-end="url(#arrLO)"/>
      <text x="253" y="26">逻辑关联字段</text>
      <rect x="415" y="13" width="18" height="16" rx="4" fill="#fff" stroke="#7DC89A" stroke-width="1.6"/>
      <text x="441" y="26">查有记录</text>
      <rect x="545" y="13" width="18" height="16" rx="4" fill="#fff" stroke="#C9C4B4" stroke-width="1.4"/>
      <text x="571" y="26">本单暂无记录</text>
      <text x="690" y="26" fill="#999">查询时间 2026-09-28</text>
    </g>

    <!-- 中心 → 履约系列 虚线 -->
    <g stroke="#B9B4A4" stroke-width="1.4" stroke-dasharray="5,4" fill="none">
      <line x1="430" y1="322" x2="122" y2="498" marker-end="url(#arrLO)"/>
      <line x1="430" y1="322" x2="332" y2="498" marker-end="url(#arrLO)"/>
      <line x1="430" y1="322" x2="542" y2="498" marker-end="url(#arrLO)"/>
      <line x1="430" y1="322" x2="752" y2="498" marker-end="url(#arrLO)"/>
    </g>
    <text x="430" y="365" font-size="11.5" fill="#8A8578" text-anchor="middle" stroke="#F4F3EE" stroke-width="4" paint-order="stroke">related_contract_id · 逻辑关联</text>

    <!-- 中心 → 四个一级节点 连线 -->
    <g stroke="#8A8578" stroke-width="1.8" fill="none">
      <line x1="325" y1="262" x2="242" y2="148" marker-end="url(#arrFK)"/>
      <line x1="535" y1="248" x2="618" y2="105" marker-end="url(#arrFK)"/>
      <line x1="535" y1="305" x2="618" y2="368" marker-end="url(#arrFK)"/>
      <line x1="325" y1="315" x2="242" y2="372" marker-end="url(#arrFK)"/>
    </g>
    <text x="252" y="196" font-size="11.5" fill="#8A8578" stroke="#F4F3EE" stroke-width="4" paint-order="stroke">main_id · FK</text>
    <text x="562" y="170" font-size="11.5" fill="#8A8578" stroke="#F4F3EE" stroke-width="4" paint-order="stroke">main_id · FK</text>
    <text x="550" y="342" font-size="11.5" fill="#8A8578" stroke="#F4F3EE" stroke-width="4" paint-order="stroke">submission_id</text>
    <text x="252" y="350" font-size="11.5" fill="#8A8578" stroke="#F4F3EE" stroke-width="4" paint-order="stroke">main_id · FK</text>

    <!-- 履约系列节点 -->
    <g font-size="12" fill="#333">
      <rect x="25"  y="500" width="195" height="62" rx="10" fill="#fff" stroke="#C9C4B4" stroke-width="1.4"/>
      <text x="38"  y="523" font-weight="600">履约结算 ×0</text>
      <text x="38"  y="542" font-size="11" fill="#999">contract_perf_settlement</text>
      <rect x="235" y="500" width="195" height="62" rx="10" fill="#fff" stroke="#C9C4B4" stroke-width="1.4"/>
      <text x="248" y="523" font-weight="600">履约发票 ×0</text>
      <text x="248" y="542" font-size="11" fill="#999">contract_perf_invoice</text>
      <rect x="445" y="500" width="195" height="62" rx="10" fill="#fff" stroke="#C9C4B4" stroke-width="1.4"/>
      <text x="458" y="523" font-weight="600">履约交货 ×0</text>
      <text x="458" y="542" font-size="11" fill="#999">contract_perf_delivery</text>
      <rect x="655" y="500" width="195" height="62" rx="10" fill="#fff" stroke="#C9C4B4" stroke-width="1.4"/>
      <text x="668" y="523" font-weight="600">履约收据 ×0</text>
      <text x="668" y="542" font-size="11" fill="#999">contract_perf_receipt</text>
    </g>

    <!-- 中心节点 -->
    <g>
      <rect x="325" y="225" width="210" height="98" rx="12" fill="#EFEEFF" stroke="#7C8CE0" stroke-width="2.2"/>
      <text x="340" y="247" font-size="14" font-weight="700" fill="#1A1B1C">contract_main</text>
      <text x="340" y="268" font-size="12" fill="#4A4F8C">GL-XZ-2026001</text>
      <text x="340" y="288" font-size="12" fill="#333">武汉天河机场信息服务合同</text>
      <text x="340" y="308" font-size="11.5" fill="#6B7280">12,000 万元 · 已通过 · 71 字段</text>
    </g>

    <!-- 一级节点 -->
    <g font-size="12" fill="#333">
      <rect x="25" y="105" width="215" height="88" rx="10" fill="#fff" stroke="#7DC89A" stroke-width="1.8"/>
      <text x="38" y="127" font-weight="600">合同相对方 ×2</text>
      <text x="38" y="146" font-size="11" fill="#555">contract_counterparty</text>
      <text x="38" y="165" font-size="11" fill="#333">湖北机场集团信息科技…</text>
      <text x="38" y="181" font-size="10.5" fill="#6B7280">91420116MACTDG7E9W</text>

      <rect x="620" y="60" width="215" height="88" rx="10" fill="#fff" stroke="#7DC89A" stroke-width="1.8"/>
      <text x="633" y="82" font-weight="600">付款节点 ×1</text>
      <text x="633" y="101" font-size="11" fill="#555">contract_payment_node</text>
      <text x="633" y="120" font-size="11" fill="#333">node_type=03 · 方向02</text>
      <text x="633" y="136" font-size="10.5" fill="#6B7280">金额 0 万元</text>

      <rect x="620" y="335" width="215" height="88" rx="10" fill="#fff" stroke="#7DC89A" stroke-width="1.8"/>
      <text x="633" y="357" font-weight="600">填报记录 ×1</text>
      <text x="633" y="376" font-size="11" fill="#555">contract_submission</text>
      <text x="633" y="395" font-size="11" fill="#333">合同修改组单 · 50 份</text>
      <text x="633" y="411" font-size="10.5" fill="#6B7280">状态：已通过</text>

      <rect x="25" y="335" width="215" height="88" rx="10" fill="#fff" stroke="#C9C4B4" stroke-width="1.4"/>
      <text x="38" y="357" font-weight="600" fill="#6B7280">购销关联 ×0</text>
      <text x="38" y="376" font-size="11" fill="#999">contract_sales_purchase_link</text>
      <text x="38" y="395" font-size="11" fill="#999">本单暂无购销关联记录</text>
    </g>
  </svg>
  <div style="font-size:12px;color:#6B7280;margin-top:10px;line-height:1.6;">
    数据口径：以上为真实查询结果（hbairport 库 · lightrag 用户），按合同编号 GL-XZ-2026001 于 2026-09-28 查询；
    实线=数据库外键约束（main_id → contract_main.id），虚线=逻辑关联字段（submission_id / related_contract_id）。
  </div>
</div>
</html>'''

with open(os.path.join(BASE, '_renderer_contract.html'), 'w', encoding='utf-8') as f:
    f.write(svg)
print('OK lines:', svg.count('\n') + 1)
