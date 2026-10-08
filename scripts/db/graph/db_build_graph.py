# -*- coding: utf-8 -*-
"""生成 hbairport 知识图谱的 ECharts graph 数据 + 自包含 HTML 文件。"""
import json
import math
import os
from scripts.paths import DATA_DIR, GRAPH_DIR

BASE = str(DATA_DIR)

SCHEMA = os.path.join(BASE, 'hbairport_schema.json')
ANALYSIS = os.path.join(BASE, 'hbairport_analysis.json')
OUT_JSON = os.path.join(BASE, 'graph_data.json')
OUT_HTML = os.path.join(str(GRAPH_DIR), 'hbairport知识图谱.html')

# 15 色清透色板（m-01..m-15 深一点以保证对比度）
PALETTE = [
    '#7C8CE0', '#6FB7DC', '#D09A5F', '#A982D6', '#6FA8E8',
    '#7DC89A', '#70BDB8', '#D96F8B', '#F2C94C', '#8FD1B8',
    '#E58A6F', '#C9B84F', '#B98AD8', '#F0C986', '#8AD98F',
]

# 域顺序（按表数排序，与 analysis 一致）
DOMAIN_ORDER = [
    '人力薪酬', '合同管理', '国资上报', '投资计划', '采购管理',
    '资产管理', '综合接待', '权限管理', '数据验证', '组织架构',
    '地理信息', '财务预算', '审批流程', '资金账户', '数据字典',
    '担保管理', '借贷管理', '合作方管理', '流程管理', '应收管理',
    '系统同步', '数据填报', '系统元数据', '其他', '系统审计',
]


def bare(name):
    return name.split('.')[-1]


def main():
    with open(SCHEMA, encoding='utf-8') as f:
        schema = json.load(f)
    with open(ANALYSIS, encoding='utf-8') as f:
        analysis = json.load(f)

    dom_of = {}
    for dom, st in analysis['domains'].items():
        for t in st['tables']:
            dom_of[t] = dom

    involved = set(analysis['involved_tables'])

    # 域 -> 索引（按 DOMAIN_ORDER，不在列表里的追加）
    dom_idx = {d: i for i, d in enumerate(DOMAIN_ORDER)}
    idx_dom = {i: d for d, i in dom_idx.items()}

    nodes = []
    for t in schema['tables']:
        name = bare(t['table'])
        dom = dom_of.get(name, '其他')
        ci = dom_idx.get(dom, 24)
        rows = max(t['est_rows'] or 0, 0)
        # 节点大小：按行数对数缩放，参与外键的表加大
        size = 10 + 3.2 * math.log10(rows + 1) if rows else 8
        if name in involved:
            size += 3
        nodes.append({
            'name': name,
            'domain': dom,
            'ci': ci,
            'size': round(min(size, 34), 1),
            'rows': rows,
            'fields': len(t['columns']),
            'pk': t['primary_key'][:1] if t['primary_key'] else '',
            'linked': name in involved,
        })

    links = []
    for fk in schema['foreign_keys']:
        links.append({
            'source': bare(fk['table']),
            'target': bare(fk['ref_table']),
            'label': f"{bare(fk['column'])} → {bare(fk['ref_column'])}",
        })

    categories = [{'name': idx_dom[i]} for i in range(len(idx_dom))]

    graph = {'nodes': nodes, 'links': links, 'categories': categories}
    with open(OUT_JSON, 'w', encoding='utf-8') as f:
        json.dump(graph, f, ensure_ascii=False)

    # 构建自包含 HTML
    nodes_c = json.dumps(nodes, ensure_ascii=False, separators=(',', ':'))
    links_c = json.dumps(links, ensure_ascii=False, separators=(',', ':'))
    cats_c = json.dumps(categories, ensure_ascii=False, separators=(',', ':'))
    pal_c = json.dumps(PALETTE, separators=(',', ':'))

    html = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>hbairport 数据库知识图谱（158 表 · 35 外键）</title>
<script src="https://cdn.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js"></script>
<style>
  body{margin:0;font-family:'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif;background:#F4F3EE;}
  .wrap{max-width:1400px;margin:0 auto;padding:16px;}
  h1{font-size:20px;color:#1A1B1C;margin:8px 0 4px;}
  .sub{font-size:13px;color:#6B7280;margin-bottom:12px;}
  #graph{width:100%;height:86vh;min-height:560px;background:#fff;border-radius:14px;
         border:1px solid #E4E3DD;box-shadow:0 2px 10px rgba(0,0,0,0.04);}
  .tips{font-size:12px;color:#6B7280;margin-top:10px;line-height:1.7;}
  code{background:#EFEEE8;padding:1px 6px;border-radius:4px;font-size:12px;}
</style>
</head>
<body>
<div class="wrap">
  <h1>hbairport 数据库知识图谱</h1>
  <div class="sub">PostgreSQL · lightrag 用户 · 158 张表 / 25 个业务域 / 35 条外键（有向边，箭头指向被引用表）· 数据来自 information_schema 实时读取</div>
  <div id="graph"></div>
  <div class="tips">
    操作：滚轮缩放 · 拖拽节点调整布局 · 悬停查看表详情与字段级外键。节点大小≈表数据行数（对数），圆角框=参与外键关联。<br>
    强关联分布：<code>人力薪酬 9 条</code> · <code>权限管理 8 条</code> · <code>采购管理 8 条</code> · <code>合同管理 3 条</code>；唯一跨域外键为 <code>app_user.department_id → department.id</code>（权限管理 ↔ 组织架构）。
  </div>
</div>
<script>
(function(){
  var el = document.getElementById('graph');
  if (!el) return;
  if (typeof echarts === 'undefined') {
    el.innerHTML = '<div style="padding:40px;color:#6B7280;font-size:14px;">ECharts 加载失败，请检查网络后刷新。</div>';
    return;
  }
  var chart = echarts.init(el);
  var nodes = __NODES__;
  var links = __LINKS__;
  var cats = __CATS__;
  var palette = __PAL__;
  chart.setOption({
    backgroundColor: 'transparent',
    tooltip: {
      confine: true,
      backgroundColor: 'rgba(255,255,255,0.97)',
      borderColor: '#E4E3DD',
      textStyle: { color: '#1A1B1C', fontSize: 12 },
      formatter: function(p){
        if (p.dataType === 'edge') {
          return '<b>' + p.data.source + '</b> → <b>' + p.data.target + '</b><br>' + p.data.label + '（外键）';
        }
        var d = p.data;
        return '<b>' + d.name + '</b><br>业务域：' + d.domain +
               '<br>字段数：' + d.fields + (d.pk ? ' · 主键：' + d.pk : '') +
               '<br>行数估计：' + (d.rows ? d.rows.toLocaleString() : '未知') +
               (d.linked ? '<br><span style="color:#7C8CE0">● 参与外键关联</span>' : '');
      }
    },
    legend: [{ type: 'scroll', top: 8, left: 8, itemWidth: 12, itemHeight: 12,
               textStyle: { fontSize: 11, color: '#555' },
               data: cats.map(function(c){ return c.name; }) }],
    series: [{
      type: 'graph',
      layout: 'force',
      data: nodes.map(function(n){
        return {
          name: n.name, value: n.rows,
          category: n.domain, symbolSize: n.size,
          fields: n.fields, pk: n.pk, rows: n.rows, domain: n.domain,
          linked: n.linked,
          itemStyle: { color: palette[n.ci % palette.length] },
          label: { show: n.linked, fontSize: 9, color: '#333',
                   formatter: n.name.length > 16 ? n.name.slice(0,15)+'…' : n.name }
        };
      }),
      links: links.map(function(l){
        return { source: l.source, target: l.target, label: l.label,
                 lineStyle: { color: '#B9B4A4', width: 1.4, curveness: 0.12 },
                 emphasis: { lineStyle: { color: '#7C8CE0', width: 2.4 } } };
      }),
      categories: cats.map(function(c, i){
        return { name: c.name, itemStyle: { color: palette[i % palette.length] } };
      }),
      roam: true,
      draggable: true,
      edgeSymbol: ['none', 'arrow'],
      edgeSymbolSize: 6,
      force: { repulsion: 210, edgeLength: [40, 110], gravity: 0.08, friction: 0.55 },
      labelLayout: { hideOverlap: true },
      emphasis: { focus: 'adjacency', scale: true,
                  label: { show: true, fontSize: 11, fontWeight: 'bold' } },
      lineStyle: { color: '#C9C4B4', width: 1.2, curveness: 0.12 }
    }]
  });
  window.addEventListener('resize', function(){ chart.resize(); });
})();
</script>
</body>
</html>"""

    html = (html.replace('__NODES__', nodes_c)
                .replace('__LINKS__', links_c)
                .replace('__CATS__', cats_c)
                .replace('__PAL__', pal_c))

    with open(OUT_HTML, 'w', encoding='utf-8') as f:
        f.write(html)

    print('NODES:', len(nodes), 'LINKS:', len(links), 'CATS:', len(categories))
    print('SAVED:', OUT_JSON)
    print('SAVED:', OUT_HTML)
    print('HTML_SIZE:', os.path.getsize(OUT_HTML), 'bytes')


if __name__ == '__main__':
    main()
