# -*- coding: utf-8 -*-
"""解析记忆层效果 renderer（静态 SVG）。"""
import os
from scripts.paths import RENDERER_DIR

BASE = str(RENDERER_DIR)
svg = '''<html style="margin:0;padding:0;">
<div style="background-color:transparent;box-sizing:border-box;">
  <svg viewBox="0 0 960 560" style="width:100%;height:auto;display:block;font-family:'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif;">
    <text x="14" y="26" font-size="14" font-weight="700" fill="#1A1B1C">解析记忆层效果：四类“漏检问法”全部命中（实测）</text>

    <!-- 之前 vs 现在 -->
    <g font-size="11.5">
      <rect x="14" y="40" width="455" height="52" rx="8" fill="#FBF3F3" stroke="#EAC9C9" stroke-width="1"/>
      <text x="28" y="60" font-weight="600" fill="#EA6668">改造前（纯关键词映射）</text>
      <text x="28" y="80" fill="#555">“单子”“天运达”“上个月”等说法 → 主体识别失败，查不到</text>

      <rect x="491" y="40" width="455" height="52" rx="8" fill="#F2FBF4" stroke="#BFE3CB" stroke-width="1"/>
      <text x="505" y="60" font-weight="600" fill="#1E6B1E">改造后（解析记忆层）</text>
      <text x="505" y="80" fill="#333">别名 / 企业映射 / 时间感知 / 历史复用 → 全部命中</text>
    </g>

    <!-- 四条命中路径 -->
    <g font-size="11.5">
      <rect x="14" y="104" width="932" height="58" rx="8" fill="#fff" stroke="#E4E3DD" stroke-width="1"/>
      <circle cx="34" cy="133" r="9" fill="#7C8CE0"/>
      <text x="28" y="137" font-size="10" fill="#fff" font-weight="700">1</text>
      <text x="52" y="128" fill="#333">“查一下 GL-XZ-2026001 这个单子”</text>
      <text x="52" y="148" fill="#6B7280">主体别名“单子”→ contract_main · 编号正则抓 GL-XZ-2026001 → 命中（来源=rule）</text>

      <rect x="14" y="170" width="932" height="58" rx="8" fill="#fff" stroke="#E4E3DD" stroke-width="1"/>
      <circle cx="34" cy="199" r="9" fill="#7C8CE0"/>
      <text x="28" y="203" font-size="10" fill="#fff" font-weight="700">2</text>
      <text x="52" y="194" fill="#333">“天运达的付款节点”</text>
      <text x="52" y="214" fill="#6B7280">企业简称“天运达”→ scc=91420712MAG0C45Q72 · 主体自动 fallback 合同 → 命中（来源=rule）</text>

      <rect x="14" y="236" width="932" height="58" rx="8" fill="#fff" stroke="#E4E3DD" stroke-width="1"/>
      <circle cx="34" cy="265" r="9" fill="#7C8CE0"/>
      <text x="28" y="269" font-size="10" fill="#fff" font-weight="700">3</text>
      <text x="52" y="260" fill="#333">“上个月的合同”</text>
      <text x="52" y="280" fill="#6B7280">时间感知：上个月 → signing_at LIKE '2026-08%' → 命中 50 条（来源=rule）</text>

      <rect x="14" y="302" width="932" height="58" rx="8" fill="#fff" stroke="#BFE3CB" stroke-width="1.6"/>
      <circle cx="34" cy="331" r="9" fill="#52C41A"/>
      <text x="28" y="335" font-size="10" fill="#fff" font-weight="700">4</text>
      <text x="52" y="326" fill="#333">“看看 GL-XZ-2026001 的所有信息”（第二次问，说法不同）</text>
      <text x="52" y="346" fill="#6B7280">记忆层编号复用 → 直接继承 contract_main + 编号条件，不再重新解析（来源=memory·编号复用）</text>
    </g>

    <!-- 记忆机制 -->
    <text x="14" y="390" font-size="13" font-weight="700" fill="#1A1B1C">记忆层机制（ask_demo_memory.json）</text>
    <g font-size="11.5">
      <rect x="14" y="400" width="455" height="120" rx="8" fill="#F9F4FC" stroke="#E0C9EE" stroke-width="1"/>
      <text x="28" y="422" font-weight="600" fill="#7C4A8C">三层匹配（按序）</text>
      <text x="28" y="444" fill="#333">① 编号复用：问题含编号 X，且记忆中有</text>
      <text x="28" y="462" fill="#333">   同编号条目 → 继承主体+条件（最稳）</text>
      <text x="28" y="484" fill="#333">② 相似度复用：difflib 相似度 ≥0.62</text>
      <text x="28" y="502" fill="#333">   继承整条解析参数</text>
      <text x="28" y="524" fill="#333">③ 规则兜底：别名/企业/时间/金额/年份</text>

      <rect x="491" y="400" width="455" height="120" rx="8" fill="#F4F6FF" stroke="#C9D2F0" stroke-width="1"/>
      <text x="505" y="422" font-weight="600" fill="#4A4F8C">沉淀与累积</text>
      <text x="505" y="444" fill="#333">每次成功解析自动入库（同主体同条件只累计 hits）</text>
      <text x="505" y="464" fill="#333">当前记忆：3 条 · 已累积 5 次命中</text>
      <text x="505" y="484" fill="#333">随使用增长：问法变体越多，后续命中率越高</text>
      <text x="505" y="506" fill="#333">企业全名映射：启动时从库拉取 28 家</text>
      <text x="505" y="524" fill="#333">（comp_enterprise_ledger）+ 静态简称表</text>
    </g>

    <text x="14" y="548" font-size="10.5" fill="#999">实测：2026-09-28 · hbairport 真实库 · scripts/demo/ask_demo.py + data/ask_demo_memory.json</text>
  </svg>
</div>
</html>'''

with open(os.path.join(BASE, '_renderer_memory_layer.html'), 'w', encoding='utf-8') as f:
    f.write(svg)
print('lines:', svg.count('\n') + 1)
