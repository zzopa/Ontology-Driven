# -*- coding: utf-8 -*-
"""构建交互式字段级图谱应用 HTML（hbairport字段图谱.html）。"""
import os
import json
from scripts.paths import DATA_DIR, GRAPH_DIR

BASE = str(DATA_DIR)

DATA = json.load(open(os.path.join(BASE, 'field_graph.json'), encoding='utf-8'))
data_c = json.dumps(DATA, ensure_ascii=False, separators=(',', ':'))

HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>hbairport 字段级知识图谱</title>
<style>
  *{box-sizing:border-box;margin:0;padding:0;}
  body{font-family:'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif;background:#F4F3EE;color:#1A1B1C;}
  .wrap{max-width:1500px;margin:0 auto;padding:14px;}
  header{display:flex;flex-wrap:wrap;align-items:center;gap:10px;margin-bottom:10px;}
  h1{font-size:18px;font-weight:700;}
  .stats{font-size:12px;color:#6B7280;}
  .stats b{color:#4A4F8C;}
  #search{flex:1 1 260px;max-width:420px;padding:8px 12px;border:1px solid #D8D6CE;border-radius:8px;
          font-size:13px;background:#fff;outline:none;}
  #search:focus{border-color:#7C8CE0;}
  .legend{font-size:11.5px;color:#555;display:flex;flex-wrap:wrap;gap:14px;align-items:center;margin-bottom:8px;}
  .lg{display:inline-flex;align-items:center;gap:4px;}
  .badge{display:inline-block;padding:0 5px;border-radius:4px;font-size:10px;font-weight:700;line-height:15px;}
  .b-pk{background:#F2C94C;color:#4A3B00;}
  .b-fk{background:#7C8CE0;color:#fff;}
  .b-ref{background:#94D8C3;color:#0B4F3A;}
  .b-plain{background:#EFEEE8;color:#888;}
  #stage{position:relative;}
  #lines{position:absolute;top:0;left:0;width:100%;height:100%;pointer-events:none;z-index:0;}
  .domain-group{margin-bottom:16px;}
  .domain-title{font-size:13px;font-weight:600;color:#4A4F8C;margin:6px 0 8px;padding-bottom:4px;border-bottom:1px solid #E4E3DD;}
  .domain-title .cnt{color:#999;font-weight:400;font-size:12px;}
  .cards{display:flex;flex-wrap:wrap;gap:10px;}
  .card{width:262px;background:#fff;border:1px solid #E4E3DD;border-radius:12px;overflow:hidden;
        box-shadow:0 1px 4px rgba(0,0,0,0.04);z-index:1;}
  .card.dim{opacity:0.35;}
  .card-head{padding:8px 10px;background:#F8F7F2;border-bottom:1px solid #E4E3DD;display:flex;flex-wrap:wrap;
             align-items:baseline;gap:6px;}
  .card-head .t{font-size:13px;font-weight:700;}
  .card-head .d{font-size:10.5px;color:#6B7280;}
  .card-head .r{font-size:10.5px;color:#999;margin-left:auto;}
  .frow{display:flex;align-items:center;gap:5px;padding:2px 10px;font-size:11px;line-height:17px;
        cursor:pointer;border-bottom:1px dashed #F0EFE9;}
  .frow:hover{background:#F5F4FE;}
  .frow.hl{background:#FFF7E0;}
  .frow.linked{background:#F5F4FE;}
  .frow .fn{font-weight:600;color:#333;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
  .frow .ty{color:#9A978D;font-size:10px;flex-shrink:0;}
  .frow .go{margin-left:auto;display:inline-flex;gap:3px;align-items:center;flex-shrink:0;}
  .frow .go .tar{color:#6A76B8;font-size:10px;max-width:120px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
  .expand{padding:4px 10px;font-size:11px;color:#6A76B8;cursor:pointer;text-align:center;background:#FAFAF7;}
  .expand:hover{background:#F2F1FA;}
  #detail{position:sticky;bottom:0;margin-top:10px;background:rgba(255,255,255,0.97);border:1px solid #E4E3DD;
          border-radius:10px;padding:8px 12px;font-size:12px;display:none;z-index:2;box-shadow:0 -2px 10px rgba(0,0,0,0.05);}
  #detail b{color:#4A4F8C;}
  .tip{font-size:11.5px;color:#6B7280;margin-top:10px;line-height:1.7;}
  .empty{display:none;padding:30px;text-align:center;color:#999;font-size:13px;}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>hbairport 字段级知识图谱</h1>
    <span class="stats"><b id="st"></b></span>
    <input id="search" type="text" placeholder="搜索表名 / 字段名，如：main_id、contract、payroll…">
  </header>
  <div class="legend">
    <span class="lg"><span class="badge b-pk">PK</span> 主键字段</span>
    <span class="lg"><span class="badge b-fk">FK</span> 外键字段（连线发出端）</span>
    <span class="lg"><span class="badge b-ref">被引用</span> 外键目标字段（连线箭头端）</span>
    <span class="lg"><span class="badge b-plain">—</span> 普通字段（折叠，点击卡片“展开全部”查看）</span>
    <span class="lg">点击字段高亮其外键链路 · 鼠标滚轮缩放页面</span>
  </div>
  <div id="stage">
    <svg id="lines"></svg>
    <div id="groups"></div>
    <div id="empty" class="empty">没有匹配的结果，换个关键词试试</div>
  </div>
  <div id="detail"></div>
  <div class="tip">
    数据来源：hbairport 库（lightrag 用户）information_schema / pg_catalog 实时读取 · 2026-09-28。
    卡片仅包含<b>参与外键关联的 42 张表</b>；全库 158 张表的字段结构见 <code>hbairport_schema.json</code>。
    行数为 PostgreSQL 统计估算值（reltuples）。
  </div>
</div>
<script>
(function(){
  var D = __DATA__;
  var stage = document.getElementById('stage');
  var linesSvg = document.getElementById('lines');
  var groups = document.getElementById('groups');
  var detail = document.getElementById('detail');
  var searchEl = document.getElementById('search');
  var emptyEl = document.getElementById('empty');

  document.getElementById('st').textContent =
    D.stats.tables + ' 张表 · ' + D.stats.fields + ' 个字段 · ' + D.stats.pk_fields + ' 个主键 · ' +
    D.stats.fks + ' 条外键 · ' + D.stats.link_fields + ' 个字段参与关联';

  // 按域分组卡片
  var byDomain = {};
  D.cards.forEach(function(c){
    (byDomain[c.domain] = byDomain[c.domain] || []).push(c);
  });
  var domainOrder = Object.keys(byDomain).sort(function(a,b){return byDomain[b].length - byDomain[a].length;});

  var frowById = {};   // key -> 行元素
  var expandBtn = {};  // table -> 展开按钮元素

  domainOrder.forEach(function(dom){
    var g = document.createElement('div');
    g.className = 'domain-group';
    g.dataset.domain = dom;
    g.innerHTML = '<div class="domain-title">' + dom + ' <span class="cnt">' + byDomain[dom].length + ' 张表</span></div>';
    var cards = document.createElement('div');
    cards.className = 'cards';
    byDomain[dom].forEach(function(c){
      cards.appendChild(buildCard(c));
    });
    g.appendChild(cards);
    groups.appendChild(g);
  });

  function buildCard(c){
    var card = document.createElement('div');
    card.className = 'card';
    card.dataset.table = c.table;
    var pkFk = c.fields.filter(function(f){return f.pk || f.fk;});
    var plain = c.fields.filter(function(f){return !(f.pk || f.fk);});
    var head = document.createElement('div');
    head.className = 'card-head';
    head.innerHTML = '<span class="t">' + c.table + '</span><span class="d">' + c.domain + '</span>' +
                     '<span class="r">' + (c.rows ? c.rows.toLocaleString() + ' 行' : '—') + '</span>';
    card.appendChild(head);
    var body = document.createElement('div');
    body.className = 'card-body';
    body.dataset.part = 'pkfk';
    pkFk.forEach(function(f){ body.appendChild(frow(f, c.table)); });
    card.appendChild(body);
    if (plain.length){
      var btn = document.createElement('div');
      btn.className = 'expand';
      btn.textContent = '展开全部 ' + c.fields.length + ' 字段（另有 ' + plain.length + ' 个普通字段）';
      btn.dataset.table = c.table;
      expandBtn[c.table] = btn;
      btn.addEventListener('click', function(){
        var b = card.querySelector('[data-part="plain"]');
        if (b){ b.style.display = 'none'; btn.textContent = '展开全部 ' + c.fields.length + ' 字段（另有 ' + plain.length + ' 个普通字段）'; }
        else {
          var p = document.createElement('div');
          p.className = 'card-body';
          p.dataset.part = 'plain';
          plain.forEach(function(f){ p.appendChild(frow(f, c.table)); });
          card.appendChild(p);
          btn.textContent = '收起普通字段';
        }
        drawLines();
      });
      card.appendChild(btn);
    }
    return card;
  }

  function frow(f, tn){
    var row = document.createElement('div');
    row.className = 'frow';
    var key = tn + '.' + f.col;
    row.dataset.key = key;
    var mark = '';
    if (f.fk) mark = '<span class="badge b-fk">FK</span><span class="go"><span class="tar">→ ' + f.fk_target + '</span></span>';
    else if (f.pk) mark = '<span class="badge b-pk">PK</span>';
    else mark = '<span class="badge b-plain">—</span>';
    row.innerHTML = '<span class="fn">' + f.col + '</span><span class="ty">' + f.type + '</span>' + mark;
    row.addEventListener('click', function(ev){
      ev.stopPropagation();
      highlightField(key, row);
    });
    return row;
  }

  // 高亮字段链路
  function highlightField(key, row){
    var lines = D.links;
    var targets = {};
    lines.forEach(function(l){
      if (l.source === key) targets[l.target] = 1;
      if (l.target === key) targets[l.source] = 1;
    });
    // 清除旧高亮
    var all = groups.querySelectorAll('.frow');
    all.forEach(function(r){ r.classList.remove('hl'); r.classList.remove('linked'); });
    if (row) row.classList.add('hl');
    Object.keys(targets).forEach(function(k){
      var el = groups.querySelector('.frow[data-key="' + k + '"]');
      if (el) el.classList.add('linked');
    });
    // 详情
    var f = D.field_nodes.filter(function(n){ return n.key === key; })[0];
    if (f){
      detail.style.display = 'block';
      detail.innerHTML = '<b>' + f.table + '.' + f.col + '</b> · 域：' + f.domain + ' · 类型：' + f.type +
        (f.nullable ? '' : ' · 非空') +
        (f.pk ? ' · 主键' : '') +
        (f.fk ? ' · 外键 → ' + f.fk_target : '') +
        '<span data-close="1" style="float:right;color:#999;cursor:pointer">✕</span>';
    } else {
      detail.style.display = 'block';
      detail.innerHTML = '<b>' + key + '</b>（普通字段，未参与外键关联）<span data-close="1" style="float:right;color:#999;cursor:pointer">✕</span>';
    }
    drawLines();
  }

  // 画外键连线
  function drawLines(){
    var s = linesSvg;
    var sr = stage.getBoundingClientRect();
    s.setAttribute('width', stage.scrollWidth);
    s.setAttribute('height', stage.scrollHeight);
    s.innerHTML = '';
    var html = '';
    D.links.forEach(function(l){
      var a = groups.querySelector('.frow[data-key="' + l.source + '"]');
      var b = groups.querySelector('.frow[data-key="' + l.target + '"]');
      if (!a || !b) return;
      var ra = a.getBoundingClientRect(), rb = b.getBoundingClientRect();
      var x1 = ra.right - sr.left, y1 = ra.top + ra.height/2 - sr.top;
      var x2 = rb.left - sr.left + 4, y2 = rb.top + rb.height/2 - sr.top;
      var mx = (x1 + x2) / 2, my = (y1 + y2) / 2;
      var isHl = a.classList.contains('hl') || b.classList.contains('hl') ||
                 a.classList.contains('linked') || b.classList.contains('linked');
      var col = isHl ? '#D96F8B' : 'rgba(138,133,120,0.45)';
      var w = isHl ? 2.4 : 1.1;
      // 贝塞尔
      html += '<path d="M ' + x1 + ' ' + y1 + ' Q ' + mx + ' ' + my + ' ' + x2 + ' ' + y2 +
              '" stroke="' + col + '" stroke-width="' + w + '" fill="none" stroke-dasharray="' + (isHl?'':'4,3') + '"/>';
      // 箭头
      var ang = Math.atan2(y2 - my, x2 - mx);
      var ax = x2 - 7 * Math.cos(ang), ay = y2 - 7 * Math.sin(ang);
      html += '<path d="M ' + ax + ' ' + ay + ' L ' + (x2 - 2*Math.cos(ang + 0.5)) + ' ' + (y2 - 2*Math.sin(ang + 0.5)) +
              ' L ' + (x2 - 2*Math.cos(ang - 0.5)) + ' ' + (y2 - 2*Math.sin(ang - 0.5)) + ' Z" fill="' + col + '"/>';
    });
    s.innerHTML = html;
  }

  // 搜索
  var lastQuery = '';
  function doSearch(){
    var q = searchEl.value.trim().toLowerCase();
    if (q === lastQuery) return;
    lastQuery = q;
    var cards = groups.querySelectorAll('.card');
    var groupsEl = groups.querySelectorAll('.domain-group');
    var hitAny = false;
    cards.forEach(function(card){
      var tn = card.dataset.table.toLowerCase();
      var matchTable = tn.indexOf(q) >= 0;
      // 字段匹配：展开并高亮
      var fieldHit = [];
      card.querySelectorAll('.frow').forEach(function(r){
        var k = r.dataset.key.toLowerCase();
        if (k.indexOf(q) >= 0 && q) fieldHit.push(r);
      });
      if (fieldHit.length && !card.querySelector('[data-part="plain"]')){
        var btn = expandBtn[card.dataset.table];
        if (btn) btn.click();
      }
      var visible = matchTable || fieldHit.length > 0;
      card.style.display = visible ? '' : 'none';
      if (visible) hitAny = true;
      card.querySelectorAll('.frow').forEach(function(r){
        r.classList.remove('hl'); r.classList.remove('linked');
        if (fieldHit.indexOf(r) >= 0) r.classList.add('hl');
      });
    });
    groupsEl.forEach(function(g){
      var any = Array.prototype.some.call(g.querySelectorAll('.card'), function(c){ return c.style.display !== 'none'; });
      g.style.display = any ? '' : 'none';
    });
    emptyEl.style.display = hitAny || !q ? 'none' : 'block';
    drawLines();
  }
  searchEl.addEventListener('input', doSearch);
  detail.addEventListener('click', function(e){
    if (e.target && e.target.getAttribute('data-close')) detail.style.display = 'none';
  });

  window.addEventListener('resize', function(){ drawLines(); });
  drawLines();
})();
</script>
</body>
</html>"""

HTML = HTML.replace('__DATA__', data_c)
with open(os.path.join(str(GRAPH_DIR), 'hbairport字段图谱.html'), 'w', encoding='utf-8') as f:
    f.write(HTML)
print('SAVED hbairport字段图谱.html, bytes =', len(HTML.encode('utf-8')))
