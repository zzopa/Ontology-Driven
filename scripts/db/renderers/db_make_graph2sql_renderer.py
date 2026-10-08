# -*- coding: utf-8 -*-
"""图谱→SQL 引擎工作流 renderer（静态 SVG）。"""
import os
from scripts.paths import RENDERER_DIR

BASE = str(RENDERER_DIR)
svg = '''<html style="margin:0;padding:0;">
<div style="background-color:transparent;box-sizing:border-box;">
  <svg viewBox="0 0 980 560" style="width:100%;height:auto;display:block;font-family:'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif;">
    <defs>
      <marker id="arr" markerWidth="9" markerHeight="9" refX="7" refY="3" orient="auto"><path d="M0,0 L7,3 L0,6 Z" fill="#7C8CE0"/></marker>
    </defs>

    <!-- 5 阶段流程条 -->
    <g font-size="12" fill="#1A1B1C">
      <rect x="10"  y="20" width="180" height="86" rx="10" fill="#fff" stroke="#7C8CE0" stroke-width="1.8"/>
      <text x="24" y="42" font-weight="700">① 输入一句话</text>
      <text x="24" y="62" font-size="10.5" fill="#555">查询合同</text>
      <text x="24" y="78" font-size="10.5" fill="#555">GL-XZ-2026001</text>
      <text x="24" y="94" font-size="10.5" fill="#555">的所有信息</text>

      <rect x="216" y="20" width="180" height="86" rx="10" fill="#fff" stroke="#7C8CE0" stroke-width="1.8"/>
      <text x="230" y="42" font-weight="700">② 识别主体/条件</text>
      <text x="230" y="62" font-size="10.5" fill="#555">主体 → contract_main</text>
      <text x="230" y="78" font-size="10.5" fill="#555">条件 → contract_no</text>
      <text x="230" y="94" font-size="10.5" fill="#555">'GL-XZ-2026001'</text>

      <rect x="422" y="20" width="180" height="86" rx="10" fill="#fff" stroke="#7C8CE0" stroke-width="1.8"/>
      <text x="436" y="42" font-weight="700">③ 图谱 BFS 展开</text>
      <text x="436" y="62" font-size="10.5" fill="#555">沿 35 FK + 13 逻辑边</text>
      <text x="436" y="78" font-size="10.5" fill="#555">BFS 2 层 · 排除系统表</text>
      <text x="436" y="94" font-size="10.5" fill="#555">命中 11 张关联表</text>

      <rect x="628" y="20" width="180" height="86" rx="10" fill="#fff" stroke="#7C8CE0" stroke-width="1.8"/>
      <text x="642" y="42" font-weight="700">④ 拼接 SQL</text>
      <text x="642" y="62" font-size="10.5" fill="#555">12 表 LEFT JOIN 链</text>
      <text x="642" y="78" font-size="10.5" fill="#555">281 个业务字段</text>
      <text x="642" y="94" font-size="10.5" fill="#555">类型不一致自动转 text</text>

      <rect x="834" y="20" width="136" height="86" rx="10" fill="#fff" stroke="#52C41A" stroke-width="2"/>
      <text x="848" y="42" font-weight="700" fill="#1E6B1E">⑤ 执行验证</text>
      <text x="848" y="64" font-size="12" font-weight="600" fill="#52C41A">8 行</text>
      <text x="848" y="82" font-size="10.5" fill="#555">351 列</text>
    </g>
    <g stroke="#7C8CE0" stroke-width="1.6" fill="none">
      <line x1="190" y1="63" x2="213" y2="63" marker-end="url(#arr)"/>
      <line x1="396" y1="63" x2="419" y2="63" marker-end="url(#arr)"/>
      <line x1="602" y1="63" x2="625" y2="63" marker-end="url(#arr)"/>
      <line x1="808" y1="63" x2="831" y2="63" marker-end="url(#arr)"/>
    </g>

    <!-- 展开的表分组 -->
    <text x="14" y="140" font-size="13" font-weight="700" fill="#1A1B1C">图谱展开的 11 张关联表（按关联类型）</text>

    <g font-size="11">
      <text x="14" y="166" font-weight="600" fill="#4A4F8C">FK 直连（3）</text>
      <rect x="14" y="174" width="300" height="118" rx="8" fill="#F4F6FF" stroke="#C9D2F0" stroke-width="1"/>
      <text x="24" y="196">contract_counterparty  相对方</text>
      <text x="24" y="216">contract_payment_node  付款节点</text>
      <text x="24" y="236">contract_sales_purchase_link  购销关联</text>
      <text x="24" y="258" font-size="10" fill="#6B7280">连接字段 main_id → contract_main.id</text>
      <text x="24" y="278" font-size="10" fill="#6B7280">数据库外键约束（35 条中的 3 条）</text>

      <text x="336" y="166" font-weight="600" fill="#7C4A8C">逻辑关联（6）</text>
      <rect x="336" y="174" width="310" height="118" rx="8" fill="#F9F4FC" stroke="#E0C9EE" stroke-width="1"/>
      <text x="346" y="196">contract_submission  填报记录（submission_id）</text>
      <text x="346" y="216">comp_enterprise_ledger  企业台账（enterprise_scc）</text>
      <text x="346" y="236">contract_perf_settlement  履约结算（related_contract_id）</text>
      <text x="346" y="256">contract_perf_invoice / delivery / receipt  履约三表</text>
      <text x="346" y="278" font-size="10" fill="#6B7280">无外键约束，数据级验证过的字段关联</text>

      <text x="668" y="166" font-weight="600" fill="#6B7280">二级展开（2）</text>
      <rect x="668" y="174" width="298" height="118" rx="8" fill="#F7F7F4" stroke="#DDD9CE" stroke-width="1"/>
      <text x="678" y="196">comp_enterprise_ledger_submission</text>
      <text x="678" y="216">  企业台账填报（FK）</text>
      <text x="678" y="236">contract_performance_submission</text>
      <text x="678" y="256">  履约填报（逻辑）</text>
      <text x="678" y="278" font-size="10" fill="#6B7280">沿首层表再走一跳</text>
    </g>

    <!-- JOIN 链示意 -->
    <text x="14" y="324" font-size="13" font-weight="700" fill="#1A1B1C">自动拼接的 JOIN 链（节选）</text>
    <rect x="14" y="336" width="952" height="120" rx="8" fill="#fff" stroke="#E4E3DD" stroke-width="1"/>
    <g font-size="10.5" fill="#333">
      <text x="28" y="360" font-family="Consolas,monospace">FROM contract_main t0</text>
      <text x="28" y="380" font-family="Consolas,monospace">LEFT JOIN contract_counterparty t1 ON t1.main_id = t0.id                    -- FK</text>
      <text x="28" y="400" font-family="Consolas,monospace">LEFT JOIN contract_payment_node  t2 ON t2.main_id = t0.id                    -- FK</text>
      <text x="28" y="420" font-family="Consolas,monospace">LEFT JOIN contract_submission    t4 ON t0.submission_id = t4.id             -- 逻辑</text>
      <text x="28" y="440" font-family="Consolas,monospace">LEFT JOIN comp_enterprise_ledger t5 ON t0.enterprise_scc::text = t5.enterprise_scc::text  -- 逻辑·类型自适应</text>
      <text x="28" y="460" font-family="Consolas,monospace">WHERE t0.contract_no = 'GL-XZ-2026001'</text>
    </g>
    <g font-size="11" fill="#6B7280">
      <rect x="14" y="474" width="952" height="58" rx="8" fill="#F2FBF4" stroke="#BFE3CB" stroke-width="1"/>
      <text x="28" y="496" font-weight="600" fill="#1E6B1E">执行验证（真实数据）</text>
      <text x="28" y="518">合同 GL-XZ-2026001 → 返回 8 行 · 351 列；合同 YW-KH-2026035 → 返回 8 行 · 351 列。两步均可复跑、可审计。</text>
    </g>
    <text x="14" y="554" font-size="10.5" fill="#999">引擎：scripts/demo/graph2sql.py · 数据底座：data/graph_full.json（35 FK + 13 逻辑边）+ data/hbairport_schema.json（全字段类型）· 2026-09-28 实测</text>
  </svg>
</div>
</html>'''

with open(os.path.join(BASE, '_renderer_graph2sql_workflow.html'), 'w', encoding='utf-8') as f:
    f.write(svg)
print('lines:', svg.count('\n') + 1)
