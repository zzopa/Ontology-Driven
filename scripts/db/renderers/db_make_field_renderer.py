# -*- coding: utf-8 -*-
"""生成字段级力导向 renderer（外键字段+被引用字段，51 个核心节点）。"""
import os
import json
from scripts.paths import DATA_DIR, RENDERER_DIR

BASE = str(RENDERER_DIR)

FG = json.load(open(DATA_DIR / 'field_graph.json', encoding='utf-8'))

# 核心节点：FK 字段或被引用字段
core = [n for n in FG['field_nodes'] if n['fk'] or any(l['target'] == n['key'] for l in FG['links'])]
core_keys = {n['key'] for n in core}

domains = []
dom_idx = {}
for n in core:
    if n['domain'] not in dom_idx:
        dom_idx[n['domain']] = len(domains)
        domains.append(n['domain'])

nodes = [{'n': n['key'], 't': n['table'], 'c': n['col'], 'd': n['domain'],
          'i': dom_idx[n['domain']], 'p': 1 if n['pk'] else 0, 'f': 1 if n['fk'] else 0}
         for n in core]
links = [{'s': l['source'], 't': l['target']} for l in FG['links']]

compact = json.dumps({'nodes': nodes, 'links': links, 'cats': domains},
                     ensure_ascii=False, separators=(',', ':'))

html = """<html style="margin:0;padding:0;">
<div style="background-color:transparent;box-sizing:border-box;">
  <div id="fg" style="width:100%;height:500px;min-width:0;box-sizing:border-box;"></div>
  <script src="https://cdn.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js"></script>
  <script>
  (function() {
    try {
      var el = document.getElementById('fg');
      if (!el) return;
      if (typeof echarts === 'undefined') {
        el.innerHTML = '<div style="padding:20px;color:#6B7280;font-size:13px;">ECharts 库加载失败，已回退为文字说明：字段级图谱共 74 个字段节点、35 条字段级外键。</div>';
        return;
      }
      var D = __DATA__;
      var PAL = ['#7C8CE0','#6FB7DC','#D09A5F','#A982D6','#6FA8E8','#7DC89A','#70BDB8','#D96F8B','#F2C94C','#8FD1B8','#E58A6F','#C9B84F','#B98AD8','#F0C986','#8AD98F'];
      var chart = echarts.init(el);
      chart.setOption({
        tooltip: {
          confine: true, backgroundColor: 'rgba(255,255,255,0.97)',
          borderColor: '#E4E3DD', textStyle: { color: '#1A1B1C', fontSize: 12 },
          formatter: function(p) {
            if (p.dataType === 'edge') {
              return '<b>' + p.data.source + '</b> → <b>' + p.data.target + '</b>（外键）';
            }
            var d = p.data;
            return '<b>' + d.n + '</b><br>业务域：' + d.d +
              (d.p ? '<br><span style="color:#C9A227">🔑 主键</span>' : '') +
              (d.f ? '<br><span style="color:#7C8CE0">→ 外键字段</span>' : '');
          }
        },
        legend: [{ type: 'scroll', top: 4, left: 4, itemWidth: 11, itemHeight: 11,
                   textStyle: { fontSize: 10, color: '#555' }, data: D.cats }],
        series: [{
          type: 'graph', layout: 'force', roam: true, draggable: true,
          edgeSymbol: ['none', 'arrow'], edgeSymbolSize: 6,
          data: D.nodes.map(function(n) {
            var label = n.c.length > 14 ? n.c.slice(0, 13) + '…' : n.c;
            return {
              name: n.n, domain: n.d, pk: n.p === 1, fk: n.f === 1,
              category: n.d,
              symbolSize: n.p ? 18 : 12,
              symbol: n.p ? 'diamond' : 'circle',
              itemStyle: { color: PAL[n.i % PAL.length],
                           borderColor: n.p ? '#C9A227' : '#fff', borderWidth: n.p ? 2 : 0 },
              label: { show: true, fontSize: 9, color: '#444', formatter: label },
              tooltip: { formatter: function(){ return ''; } }
            };
          }),
          links: D.links.map(function(l) {
            return { source: l.s, target: l.t,
                     lineStyle: { color: '#B9B4A4', width: 1.3, curveness: 0.1 } };
          }),
          categories: D.cats.map(function(c, i) {
            return { name: c, itemStyle: { color: PAL[i % PAL.length] } };
          }),
          force: { repulsion: 150, edgeLength: [50, 120], gravity: 0.1, friction: 0.6 },
          labelLayout: { hideOverlap: true },
          emphasis: { focus: 'adjacency', scale: true,
                      label: { show: true, fontSize: 11, fontWeight: 'bold' } }
        }]
      });
      window.addEventListener('resize', function() { chart.resize(); });
    } catch (e) { console.error(e); }
  })();
  </script>
</div>
</html>"""

html = html.replace('__DATA__', '{"nodes":' + json.dumps(nodes, ensure_ascii=False, separators=(',', ':')) +
              ',"links":' + json.dumps(links, ensure_ascii=False, separators=(',', ':')) +
              ',"cats":' + json.dumps(domains, ensure_ascii=False, separators=(',', ':')) + '}')

with open(os.path.join(BASE, '_renderer_field.html'), 'w', encoding='utf-8') as f:
    f.write(html)
print('renderer lines:', html.count('\n') + 1, 'bytes:', len(html.encode('utf-8')))
