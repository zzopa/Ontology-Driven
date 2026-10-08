# -*- coding: utf-8 -*-
"""生成对话内 renderer 的完整 HTML 文本（注入紧凑数据）。"""
import os
import json
from scripts.paths import DATA_DIR, RENDERER_DIR

BASE = str(RENDERER_DIR)

DATA = json.load(open(DATA_DIR / 'renderer_data.txt', encoding='utf-8'))

nodes_c = json.dumps(DATA['nodes'], ensure_ascii=False, separators=(',', ':'))
links_c = json.dumps(DATA['links'], ensure_ascii=False, separators=(',', ':'))
cats_c = json.dumps(DATA['cats'], ensure_ascii=False, separators=(',', ':'))

html = """<html style="margin:0;padding:0;">
<div style="background-color:transparent;box-sizing:border-box;">
  <div id="kg" style="width:100%;height:520px;min-width:0;box-sizing:border-box;"></div>
  <script src="https://cdn.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js"></script>
  <script>
  (function() {
    try {
      var el = document.getElementById('kg');
      if (!el) return;
      if (typeof echarts === 'undefined') {
        el.innerHTML = '<div style="padding:20px;color:#6B7280;font-size:13px;">ECharts 库加载失败，已回退为文字说明：hbairport 库共 158 张表、25 个业务域、35 条外键。</div>';
        return;
      }
      var DATA = __DATA__;
      var PAL = ['#7C8CE0','#6FB7DC','#D09A5F','#A982D6','#6FA8E8','#7DC89A','#70BDB8','#D96F8B','#F2C94C','#8FD1B8','#E58A6F','#C9B84F','#B98AD8','#F0C986','#8AD98F'];
      var chart = echarts.init(el);
      chart.setOption({
        tooltip: {
          confine: true, backgroundColor: 'rgba(255,255,255,0.97)',
          borderColor: '#E4E3DD', textStyle: { color: '#1A1B1C', fontSize: 12 },
          formatter: function(p) {
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
        legend: [{ type: 'scroll', top: 4, left: 4, itemWidth: 11, itemHeight: 11,
                   textStyle: { fontSize: 11, color: '#555' },
                   data: DATA.cats }],
        series: [{
          type: 'graph', layout: 'force', roam: true, draggable: true,
          edgeSymbol: ['none', 'arrow'], edgeSymbolSize: 6,
          data: DATA.nodes.map(function(n) {
            return {
              name: n.n, domain: n.d, symbolSize: n.s,
              rows: n.r, fields: n.f, linked: n.l === 1,
              category: n.d,
              itemStyle: { color: PAL[n.c % PAL.length] },
              label: { show: n.l === 1, fontSize: 9, color: '#333',
                       formatter: n.n.length > 16 ? n.n.slice(0, 15) + '…' : n.n }
            };
          }),
          links: DATA.links.map(function(l) {
            return { source: l.s, target: l.t, label: l.l,
                     lineStyle: { color: '#C9C4B4', width: 1.2, curveness: 0.12 } };
          }),
          categories: DATA.cats.map(function(c, i) {
            return { name: c, itemStyle: { color: PAL[i % PAL.length] } };
          }),
          force: { repulsion: 210, edgeLength: [40, 110], gravity: 0.08, friction: 0.55 },
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

html = html.replace('__DATA__', '{"nodes":' + nodes_c + ',"links":' + links_c + ',"cats":' + cats_c + '}')

with open(os.path.join(BASE, '_renderer_inline.html'), 'w', encoding='utf-8') as f:
    f.write(html)

# 抽取 JS 供语法检查
import re
scripts = re.findall(r'<script>(.*?)</script>', html, re.S)
with open(os.path.join(BASE, '_check2.js'), 'w', encoding='utf-8') as f:
    f.write(scripts[-1])
print('RENDERER_BYTES:', len(html.encode('utf-8')))
print('LINES:', html.count('\n') + 1)
