# -*- coding: utf-8 -*-
"""ask_demo 问答检索 demo 展示 renderer（静态 SVG）。"""
import os
from scripts.paths import RENDERER_DIR

BASE = str(RENDERER_DIR)
svg = '''<html style="margin:0;padding:0;">
<div style="background-color:transparent;box-sizing:border-box;">
  <svg viewBox="0 0 960 620" style="width:100%;height:auto;display:block;font-family:'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif;">
    <!-- 顶部：输入示意 -->
    <rect x="14" y="14" width="932" height="56" rx="10" fill="#fff" stroke="#7C8CE0" stroke-width="1.8"/>
    <rect x="30" y="28" width="790" height="30" rx="8" fill="#F4F3EE"/>
    <text x="44" y="49" font-size="13.5" fill="#333">查询合同 GL-XZ-2026001 的所有信息</text>
    <rect x="836" y="28" width="94" height="30" rx="8" fill="#7C8CE0"/>
    <text x="852" y="49" font-size="13" font-weight="600" fill="#fff">回车检索</text>

    <!-- 解析/图谱条 -->
    <text x="30" y="98" font-size="12" fill="#555">[解析] 主体=contract_main · 意图=detail · 条件=contract_no='GL-XZ-2026001'</text>
    <text x="30" y="118" font-size="12" fill="#555">[图谱] 沿 48 条关联边 BFS 展开 11 张相关表，逐层取数</text>

    <!-- 主表卡片 -->
    <rect x="14" y="132" width="932" height="96" rx="10" fill="#F4F6FF" stroke="#C9D2F0" stroke-width="1.2"/>
    <text x="30" y="154" font-size="13" font-weight="700" fill="#1A1B1C">【主表】合同主表 contract_main · 命中 1 条</text>
    <text x="30" y="176" font-size="11" fill="#333">contract_no=GL-XZ-2026001 · contract_name=武汉天河机场信息服务合同</text>
    <text x="30" y="194" font-size="11" fill="#333">lump_sum_amount_wan=12,000 · signing_at=2026-01-15 · status=已通过</text>
    <text x="30" y="214" font-size="11" fill="#6B7280">submission_id=2720f… · submitter_user_id=599f1c… · enterprise_scc=91420712MAG0C45Q72</text>

    <!-- 关联表命中概览 -->
    <text x="30" y="252" font-size="13" font-weight="700" fill="#1A1B1C">【关联表】沿图谱逐层取数（命中即得）</text>
    <g font-size="11.5">
      <rect x="14" y="264" width="228" height="150" rx="10" fill="#fff" stroke="#E4E3DD" stroke-width="1"/>
      <text x="28" y="286" font-weight="600" fill="#4A4F8C">FK 直连</text>
      <text x="28" y="308" fill="#333">contract_counterparty  2 条</text>
      <text x="28" y="328" fill="#333">  → 湖北机场集团信息科技…</text>
      <text x="28" y="350" fill="#333">contract_payment_node  1 条</text>
      <text x="28" y="370" fill="#333">  → node_type=03</text>
      <text x="28" y="392" fill="#6B7280">contract_sales_purchase_link  0 条</text>

      <rect x="252" y="264" width="228" height="150" rx="10" fill="#fff" stroke="#E4E3DD" stroke-width="1"/>
      <text x="266" y="286" font-weight="600" fill="#7C4A8C">逻辑关联（数据级验证）</text>
      <text x="266" y="308" fill="#333">contract_submission  1 条</text>
      <text x="266" y="328" fill="#333">  → 修改组单 50 份 · 已通过</text>
      <text x="266" y="350" fill="#333">comp_enterprise_ledger  4 条</text>
      <text x="266" y="370" fill="#333">  → 企业按月台账</text>
      <text x="266" y="392" fill="#6B7280">履约四表  0 条（本单未履约）</text>

      <rect x="490" y="264" width="228" height="150" rx="10" fill="#fff" stroke="#E4E3DD" stroke-width="1"/>
      <text x="504" y="286" font-weight="600" fill="#6B7280">二级展开</text>
      <text x="504" y="308" fill="#333">comp_enterprise_ledger_submission  1 条</text>
      <text x="504" y="328" fill="#333">  → 企业薪酬分户组单</text>
      <text x="504" y="350" fill="#6B7280">contract_performance_submission  0 条</text>
      <text x="504" y="392" fill="#999">沿首层结果再匹配</text>

      <rect x="728" y="264" width="218" height="150" rx="10" fill="#F2FBF4" stroke="#BFE3CB" stroke-width="1.2"/>
      <text x="742" y="286" font-weight="600" fill="#1E6B1E">检索结论</text>
      <text x="742" y="308" fill="#333">12 张表 · 命中 6 张</text>
      <text x="742" y="328" fill="#333">8 条关联记录</text>
      <text x="742" y="350" fill="#333">281 个业务字段</text>
      <text x="742" y="390" font-size="10.5" fill="#6B7280">未命中表明确标注</text>
      <text x="742" y="406" font-size="10.5" fill="#6B7280">不藏不混</text>
    </g>

    <!-- 支持的问题类型 -->
    <text x="30" y="446" font-size="13" font-weight="700" fill="#1A1B1C">支持四类问答（实测通过）</text>
    <g font-size="11.5">
      <rect x="14" y="456" width="228" height="70" rx="8" fill="#fff" stroke="#E4E3DD" stroke-width="1"/>
      <text x="28" y="478" font-weight="600">① 单条全链路</text>
      <text x="28" y="498" fill="#6B7280">查询合同 GL-XZ-2026001</text>
      <text x="28" y="514" fill="#6B7280">的所有信息</text>
      <rect x="252" y="456" width="228" height="70" rx="8" fill="#fff" stroke="#E4E3DD" stroke-width="1"/>
      <text x="266" y="478" font-weight="600">② 统计聚合</text>
      <text x="266" y="498" fill="#6B7280">今年签了多少合同 →</text>
      <text x="266" y="514" fill="#6B7280">1717 条（2026 年）</text>
      <rect x="490" y="456" width="228" height="70" rx="8" fill="#fff" stroke="#E4E3DD" stroke-width="1"/>
      <text x="504" y="478" font-weight="600">③ 金额汇总</text>
      <text x="504" y="498" fill="#6B7280">合同总金额是多少 →</text>
      <text x="504" y="514" fill="#6B7280">SUM+MAX 附异常提示</text>
      <rect x="728" y="456" width="218" height="70" rx="8" fill="#fff" stroke="#E4E3DD" stroke-width="1"/>
      <text x="742" y="478" font-weight="600">④ 条件列表</text>
      <text x="742" y="498" fill="#6B7280">金额大于1000万 /</text>
      <text x="742" y="514" fill="#6B7280">列出所有合同</text>
    </g>

    <text x="14" y="556" font-size="11" fill="#999">demo：ask_demo.py（D:\\project\\test-ai\\）· 逐层取数：主表先查 → 关联表用父表锚点值沿图匹配 · 数据：hbairport 真实库 2026-09-28 实测</text>
    <text x="14" y="578" font-size="11" fill="#999">运行：python -m scripts.demo.ask_demo（交互问答）/ python -m scripts.demo.ask_demo "一句话"（单问单答）</text>
  </svg>
</div>
</html>'''

with open(os.path.join(BASE, '_renderer_ask_demo.html'), 'w', encoding='utf-8') as f:
    f.write(svg)
print('lines:', svg.count('\n') + 1)
