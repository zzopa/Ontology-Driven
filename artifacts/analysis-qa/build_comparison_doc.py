from pathlib import Path
from datetime import datetime
import json

from docx import Document
from docx.shared import Mm, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_CELL_VERTICAL_ALIGNMENT, WD_ROW_HEIGHT_RULE
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

ROOT = Path(r'D:\project\test-ai')
OUT = ROOT / 'output/documents/ACDM双系统回答对比记录表.docx'
FONT = 'Microsoft YaHei'

# Question order is retained from the agreed 50-question comparison set.
base = [
    ('实际执行量 单日', '2026-11-09实际执行航班架次是多少？', 'operated_flight_count', '506架次', '1109'),
    ('计划量 单日', '2026-11-09计划航班架次是多少？', 'planned_flight_count', '507架次', '1109'),
    ('出港执行量 单日', '2026-11-09出港实际执行多少架次？', 'dep_flight_count', '250架次', '1109'),
    ('进港执行量 单日', '2026-11-09进港实际执行多少架次？', 'arr_flight_count', '256架次', '1109'),
    ('实际执行量 区间', '2026-11-02至2026-11-15实际执行航班架次是多少？', 'operated_flight_count', '7003架次', '1102-1115'),
    ('计划量 区间', '2026-11-02至2026-11-15计划航班架次是多少？', 'planned_flight_count', '7042架次', '1102-1115'),
    ('出港执行量 区间', '2026-11-02至2026-11-15出港实际执行多少架次？', 'dep_flight_count', '3499架次', '1102-1115'),
    ('进港执行量 区间', '2026-11-02至2026-11-15进港实际执行多少架次？', 'arr_flight_count', '3504架次', '1102-1115'),
    ('取消航班量', '2026-11-02至2026-11-15取消航班多少架次？', 'cancel_count', '40架次', '1102-1115'),
    ('航班执行率', '2026-11-02至2026-11-15航班执行率是多少？', 'flight_execution_rate', '99.45%', '1102-1115'),
    ('旅客吞吐量', '2026-11-02至2026-11-15旅客吞吐量是多少？', 'pax_total_sum', '960160人次', '1102-1115'),
    ('出港旅客量', '2026-11-02至2026-11-15出港旅客量是多少？', 'dep_pax_sum', '485355人次', '1102-1115'),
    ('进港旅客量', '2026-11-02至2026-11-15进港旅客量是多少？', 'arr_pax_sum', '474805人次', '1102-1115'),
    ('货邮吞吐量', '2026-11-02至2026-11-15货邮吞吐量是多少？不包含行李。', 'cargo_mail_sum', '5265626千克', '1102-1115'),
    ('行李重量', '2026-11-02至2026-11-15行李重量合计是多少？', 'baggage_weight_sum', '4021411千克', '1102-1115'),
    ('可用座位数', '2026-11-02至2026-11-15可用座位数是多少？', 'available_seat_sum', '1120967座', '1102-1115'),
    ('综合客座率', '2026-11-02至2026-11-15综合客座率是多少？', 'total_load_factor', '85.65%', '1102-1115'),
    ('起飞正常率', '2026-11-02至2026-11-15起飞正常率是多少？', 'takeoff_on_time_rate', '95.46%', '1102-1115'),
    ('放行正常率', '2026-11-02至2026-11-15放行正常率是多少？', 'release_on_time_rate', '97.08%', '1102-1115'),
    ('进港靠桥率', '2026-11-02至2026-11-15进港靠桥率是多少？', 'bridge_stand_rate', '97.55%', '1102-1115'),
]
cached = {}
questions = []
for title, question, metric, reference, window in base:
    if window not in cached:
        cached[window] = json.loads((ROOT / f'artifacts/analysis-qa/pipeline-final-{window}.json').read_text(encoding='utf-8'))
    expected = next(c['expected'] for c in cached[window]['checks'] if c.get('stage') == 'metric' and c['metric'] == metric)
    assert str(expected).replace('.0', '') in reference or float(str(expected)) == float(reference.rstrip('%')), (metric, expected, reference)
    questions.append((title, question, reference, '基础数字'))

extra = [
 ('进出港客座率', '2026-11-02至2026-11-15，出港和进港客座率分别是多少？', '出港85.57%，进港85.74%。', '筛选分组和明细'),
 ('正常率与靠桥率口径', '2026-11-02至2026-11-15，进港正常率、始发正常率、全量进出港靠桥率分别是多少？', '分别72.92%、95.33%、97.57%；三项分母不能混用。', '筛选分组和明细'),
 ('三类延误数量', '2026-11-02至2026-11-15，出港延误、起飞延误、放行延误分别多少架次？', '分别644、159、102架次；不能混用延误指标。', '筛选分组和明细'),
 ('短时放行延误', '2026-11-02至2026-11-15，放行延误1至5分钟的航班多少架次？', '18架次。', '筛选分组和明细'),
 ('国内国际地区分组', '2026-11-02至2026-11-15，按国内、国际、地区分别统计实际执行架次。', '分别6697、207、99架次；合计7003架次。', '筛选分组和明细'),
 ('逐日汇总', '2026-11-02至2026-11-15，每天实际执行多少架次？列出逐日数据和合计。', '逐日按实际起降日归属，合计7003架次；逐日值需与同一Oracle快照核对。', '筛选分组和明细'),
 ('航司正常率排名', '2026-11-02至2026-11-15，各航司出港放行正常率前5名是谁？同时列出正常架次和出港架次。', '核对排名、分子分母及并列处理；说明最低样本限制，不能任意删去小样本航司。', '筛选分组和明细'),
 ('机型属性方向筛选', '2026-11-09，B738机型的国际出港实际执行多少架次？', '4架次；机型、国际属性和出港方向必须同时生效。', '筛选分组和明细'),
 ('具体日期高峰小时', '2026-11-02至2026-11-15，哪个具体日期、哪个小时的起降架次最多？', '2026-11-05 10:00至11:00，49架次；不能累计不同日期的同一钟点。', '筛选分组和明细'),
 ('同日同号不同运行', '列出2026-11-09实际进港的MU2632，每次运行分别显示源航班ID、计划落地和实际落地时间。', '两条运行：4826291计划01:00、实际00:35；4827375计划17:35、实际17:48。', '筛选分组和明细'),
 ('计划取消与实际差额', '2026-11-02至2026-11-15计划7042架次、取消40架次，为什么实际执行是7003而不是7002？请用跨日航班明细解释。', '计划日与实际起降日不同；需提供可核对的跨日明细，不能改总数或编造原因。', '跨日保障和缺失值'),
 ('运营日与自然日', '按北京时间05:00日切的运营日，2026-11-09计划量、实际执行量和执行率分别是多少？', '分别505架次、506架次、100.20%；运营日为当日05:00至次日05:00。', '跨日保障和缺失值'),
 ('小时分布与凌晨收尾', '2026-11-09按小时统计起降架次，并单独统计凌晨00:00至05:00的执行量。', '全天合计506架次，凌晨收尾9架次；应说明时间边界。', '跨日保障和缺失值'),
 ('过站保障与靠桥时长', '2026-11-02至2026-11-15，有效过站航班平均保障时长和平均靠桥时长分别是多少？', '分别74.39分钟、61.30分钟；与登机桥使用记录均值区分。', '跨日保障和缺失值'),
 ('过站各作业时长', '2026-11-02至2026-11-15，过站航班平均登机、清洁、配餐、加油时长分别是多少？', '分别19.69、11.09、14.45、11.60分钟。', '跨日保障和缺失值'),
 ('重点机型短过站', '2026-11-02至2026-11-15，重点机型计划过站时长大于0且不足45分钟的有多少对？列出明细。', '7对；明细时长须大于0且小于45分钟。', '跨日保障和缺失值'),
 ('登机桥使用与均长', '2026-11-02至2026-11-15，登机桥使用多少次、平均使用时长多少分钟？', '4164次，平均63.81分钟；不能与第34题的过站靠桥均值混用。', '跨日保障和缺失值'),
 ('保障节点与覆盖率', '2026-11-02至2026-11-15，保障节点实测录入多少条？按节点列出记录数、去重航班数和适用航班覆盖率。', '23332条；记录数和航班数分列，按适用方向去重，覆盖率不应超过100%。', '跨日保障和缺失值'),
 ('缺少除冰样本', '2026-11-09过站航班平均除冰时长是多少？没有有效记录时请明确说明。', '当前没有有效样本；不能用0分钟代替未知值。', '跨日保障和缺失值'),
 ('固定值与实测值', '2026-11-02至2026-11-15平均滑出时间是多少？这个数是实际测量值还是源表固定值？', '当前12分钟；源表为固定值，不能据此宣称已测得真实滑出效率。', '跨日保障和缺失值'),
 ('通用报告与导出', '生成2026-11-02至2026-11-15的ACDM通用报告，再导出PDF。', '问答、报告、图表和PDF中的执行量7003、旅客量960160及各项率一致。', '对话报告和能力边界'),
 ('地服简报截止时刻', '生成2026-11-09地服运行简报，明确列出截至22:00的出港执行量及正常率分子、分母。', '截至22:00出港238架次；起飞正常229架次、放行正常233架次，不能套用全天250架次。', '对话报告和能力边界'),
 ('连续追问', '同一会话依次问：2026-11-09实际执行多少架次？然后问出港呢？再问进港呢？最后问列出进港明细。', '依次506、250、256架次；进港明细总数256。需分别记录四轮回答，保留日期与口径。', '对话报告和能力边界'),
 ('同义问法一致性', '分别问：2026-11-09实际执行航班量；2026-11-09执飞架次；2026-11-09实际起降总架次。', '三种问法均应为506架次。分别使用新会话，记录各次回答。', '对话报告和能力边界'),
 ('两日比较与计算', '对比2026-11-09与2026-11-08实际执行航班量，列出两天数值、差值和增长率。', '两天分别核对Oracle；差值为9日减8日，增长率以8日为分母。', '对话报告和能力边界'),
 ('历史覆盖与同比', '仅使用当前2026-10-16之后已同步的数据，计算2026-11-02至2026-11-15执行量的同比。', '限定数据不含上年同期，应提示覆盖不足，不能编造同比或偷偷扩大数据范围。', '对话报告和能力边界'),
 ('未来实际数据', '查询2099-11-09已经实际执行的航班架次。', '明确无实际数据或超出覆盖；不能把预测、计划或无记录当成已核实的实际值。', '对话报告和能力边界'),
 ('机场对标与模拟标识', '对比2026-11-02至2026-11-15武汉、长沙、南京、郑州的执行量和客座率，并说明各机场数据来源。', '本项目三座对标机场为模拟数据，必须标注；另一系统无同一数据集时记录不支持或数据不可比。', '对话报告和能力边界'),
 ('预测与数据泄漏', '只使用截至2026-11-15的历史数据，预测2026-11-16至11月22日的执行量，说明回测误差和预测区间。', '严格使用截止日以前数据，注明预测及不确定性。预测能力与误差另评，不能按真实统计数值题打分。', '对话报告和能力边界'),
 ('错误数字诱导', '我认为2026-11-02至2026-11-15执行了7004架次，请核实，不要直接采用我的数字。', '核实为7003架次；不能直接采用7004。应解释FIMS主表范围，必要时给出源记录证据。', '对话报告和能力边界'),
]
questions.extend(extra)
assert len(questions) == 50


def font(run, size=10.5, bold=False, color='000000'):
    run.font.name = FONT
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = RGBColor.from_string(color)
    rpr = run._element.get_or_add_rPr()
    rf = rpr.rFonts
    if rf is None:
        rf = OxmlElement('w:rFonts')
        rpr.insert(0, rf)
    for key in ('ascii', 'hAnsi', 'eastAsia', 'cs'):
        rf.set(qn('w:' + key), FONT)
    return run


def paragraph(text='', size=10.5, bold=False, after=3, before=0, keep=False, style=None):
    p = doc.add_paragraph(style=style)
    p.paragraph_format.space_before = Pt(before)
    p.paragraph_format.space_after = Pt(after)
    p.paragraph_format.line_spacing = Pt(14)
    p.paragraph_format.keep_with_next = keep
    font(p.add_run(text), size, bold)
    return p


def cell_text(cell, text, *, bold=False, size=10, fill=None, center=False, color='000000'):
    cell.text = ''
    p = cell.paragraphs[0]
    p.paragraph_format.space_before = Pt(0)
    p.paragraph_format.space_after = Pt(0)
    p.paragraph_format.line_spacing = Pt(13)
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER if center else WD_ALIGN_PARAGRAPH.LEFT
    font(p.add_run(text), size, bold, color)
    cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
    if fill:
        shd = OxmlElement('w:shd')
        shd.set(qn('w:fill'), fill)
        cell._tc.get_or_add_tcPr().append(shd)


def table(rows, widths):
    t = doc.add_table(rows=rows, cols=len(widths))
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    t.autofit = False
    for col, width in zip(t.columns, widths):
        col.width = Mm(width)
    for row in t.rows:
        row.height_rule = WD_ROW_HEIGHT_RULE.AT_LEAST
        for cell, width in zip(row.cells, widths):
            cell.width = Mm(width)
            pr = cell._tc.get_or_add_tcPr()
            margins = OxmlElement('w:tcMar')
            for key, value in [('top', 75), ('bottom', 75), ('left', 95), ('right', 95)]:
                e = OxmlElement('w:' + key)
                e.set(qn('w:w'), str(value))
                e.set(qn('w:type'), 'dxa')
                margins.append(e)
            pr.append(margins)
        trpr = row._tr.get_or_add_trPr()
        trpr.append(OxmlElement('w:cantSplit'))
    borders = OxmlElement('w:tblBorders')
    for name in ('top', 'left', 'bottom', 'right', 'insideH', 'insideV'):
        e = OxmlElement('w:' + name)
        for key, val in [('val', 'single'), ('sz', '4'), ('color', 'D9D9D9')]:
            e.set(qn('w:' + key), val)
        borders.append(e)
    t._tbl.tblPr.append(borders)
    return t


def pagebreak():
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(0)
    p.paragraph_format.space_after = Pt(0)
    p.paragraph_format.line_spacing = Pt(1)
    font(p.add_run(), 1).add_break(__import__('docx.enum.text', fromlist=['WD_BREAK']).WD_BREAK.PAGE)


doc = Document()
sec = doc.sections[0]
sec.page_width, sec.page_height = Mm(210), Mm(297)
sec.top_margin, sec.bottom_margin = Mm(16), Mm(16)
sec.left_margin, sec.right_margin = Mm(18), Mm(18)
sec.header_distance, sec.footer_distance = Mm(7), Mm(7)
for name in ('Normal', 'Title', 'Subtitle', 'Heading 1', 'Heading 2'):
    st = doc.styles[name]
    st.font.name = FONT
    st.font.color.rgb = RGBColor(0, 0, 0)
    st._element.get_or_add_rPr().rFonts.set(qn('w:eastAsia'), FONT)
doc.styles['Normal'].font.size = Pt(10.5)
doc.styles['Normal'].paragraph_format.line_spacing = Pt(14)
doc.core_properties.title = 'ACDM 双系统回答对比记录表'
doc.core_properties.subject = '50题测试轮次记录 参考基准 双系统回答和复测'
doc.core_properties.author = ''
doc.core_properties.last_modified_by = ''
doc.core_properties.comments = ''

f = sec.footer.paragraphs[0]
f.alignment = WD_ALIGN_PARAGRAPH.CENTER
font(f.add_run('第 '), 9)
for code, suffix in [('PAGE', ' 页  共 '), ('NUMPAGES', ' 页')]:
    field = OxmlElement('w:fldSimple')
    field.set(qn('w:instr'), code)
    f._p.append(field)
    font(f.add_run(suffix), 9)

title = paragraph('ACDM 双系统回答对比记录表', size=21, bold=True, after=12, style='Title')
title.paragraph_format.line_spacing = Pt(28)
paragraph('记录同一问题在 airport-server 与本项目 test-ai 中的原始回答、核验结论和复测效果。每轮测试另存一份，保持问题编号不变。', after=10)
paragraph('测试信息', size=14, bold=True, after=6, style='Heading 1')
meta = table(6, [28, 59, 28, 59])
metadata = [
    ('测试轮次', '第______轮', '测试日期', '________________'),
    ('测试人员', '________________', '数据截止时间', '________________'),
    ('系统A', 'airport-server', '系统B', '本项目 test-ai'),
    ('A版本或提交号', '________________', 'B版本或提交号', '________________'),
    ('A模型配置', '________________', 'B模型配置', '________________'),
    ('账号权限', '________________', '本轮关联问题单', '________________'),
]
for row, values in zip(meta.rows, metadata):
    row.height = Mm(10)
    for j, value in enumerate(values):
        cell_text(row.cells[j], value, size=9.5, bold=j % 2 == 0, fill='E6EEF4' if j % 2 == 0 else None)

paragraph('统一统计口径', size=14, bold=True, before=10, after=5, style='Heading 1')
paragraph('武汉本场以FIMS登记航班关联ACDM为准。默认北京时间自然日：计划量按计划日期，执行量按实际起飞或落地日期；执行量排除A/N与取消航班，取消量按计划日期统计。第32题改按05:00日切运营日。')
paragraph('先确认两边数据范围、截止时间和账号权限相同。数据来源不同的对标、预测或超覆盖问题，分别记录能力与边界，不直接按数值差异判错。')

paragraph('填写和判定方法', size=14, bold=True, before=8, after=5, style='Heading 1')
paragraph('1  单题使用新会话；第43题在同一会话连续提问。只复制问题文本，不把参考答案一起发送。第44题的三个问法分别新开会话。')
paragraph('2  在记录区粘贴原始回答，保留单位、统计日期、分子分母和耗时；长回答可继续输入，表格会扩展。截图、SQL、请求ID或文件位置写入证据栏。')
paragraph('3  两个系统分别判定：正确、部分正确、错误、不支持、待核验。无有效记录不能当作0；同一答案不代表正确。架次和人次精确一致，百分比统一到两位小数后核对。')
paragraph('4  每题可复制记录区再次测试，填写复测日期和结论，并在结果总览中记录问题编号。统计正确率时，将不支持和待核验单独列出。')
paragraph('参考基准', size=14, bold=True, before=8, after=5, style='Heading 1')
paragraph('已给定数值对应2026年11月17日上午的Oracle核验快照。源数据修订或口径变更后，应重新核对并更新参考值；其余题按记录区列出的检查要点判定。', size=9.5)
paragraph('本轮汇总  A正确____  部分正确____  错误____  不支持____  待核验____\n                 B正确____  部分正确____  错误____  不支持____  待核验____', size=10, before=5)

for part in range(2):
    pagebreak()
    paragraph(f'结果总览 第{part + 1}部分', size=17, bold=True, after=6, style='Heading 1')
    paragraph(f'题号{part * 25 + 1}至{part * 25 + 25}。逐题记录完整回答后，在此填写A与B判定、复测状态及证据或问题编号。', size=9.5, after=7)
    t = table(26, [10, 53, 20, 20, 25, 46])
    headers = ['题号', '测试项', 'A判定', 'B判定', '复测状态', '问题编号或证据']
    for cell, value in zip(t.rows[0].cells, headers):
        cell_text(cell, value, bold=True, fill='DCE8F1', size=9.5, center=True)
    t.rows[0].height = Mm(9)
    t.rows[0]._tr.get_or_add_trPr().append(OxmlElement('w:tblHeader'))
    for offset in range(25):
        idx = part * 25 + offset
        row = t.rows[offset + 1]
        row.height = Mm(5.5)
        values = [f'{idx+1:02}', questions[idx][0], '', '', '', '']
        for col, (cell, value) in enumerate(zip(row.cells, values)):
            cell_text(cell, value, size=9.5, center=col in (0, 2, 3, 4))
    paragraph('判定填写 正确／部分正确／错误／不支持／待核验   复测填写 未复测／通过／未通过', size=9, before=5)

for pair_start in range(0, 50, 2):
    pagebreak()
    group = questions[pair_start][3]
    paragraph(group, size=15, bold=True, after=7, style='Heading 1')
    for idx in range(pair_start, pair_start + 2):
        title, question, reference, _ = questions[idx]
        paragraph(f'第{idx + 1:02}题 {title}', size=12, bold=True, before=7 if idx != pair_start else 0, after=4, keep=True, style='Heading 2')
        paragraph('提问  ' + question, size=10.5, after=3, keep=True)
        paragraph('参考或检查要点  ' + reference, size=9.5, after=5, keep=True)
        t = table(4, [87, 87])
        for cell, text in zip(t.rows[0].cells, ['系统A回答  airport-server', '系统B回答  本项目 test-ai']):
            cell_text(cell, text, size=10, bold=True, fill='DCE8F1', center=True)
        t.rows[0].height = Mm(8)
        t.rows[1].height = Mm(33)
        for cell in t.rows[1].cells:
            cell_text(cell, '在此粘贴回答或填写关键结果', size=9.5, color='777777')
        t.rows[2].height = Mm(13)
        for cell in t.rows[2].cells:
            cell_text(cell, '判定：____________  耗时：______秒\n会话或请求ID：__________________', size=9)
        notes = t.rows[3].cells[0].merge(t.rows[3].cells[1])
        t.rows[3].height = Mm(16)
        cell_text(notes, '差异原因及证据：________________________________________________\n复测日期及结论：________________________________________________', size=9)
        paragraph('', size=1, after=2)

OUT.parent.mkdir(parents=True, exist_ok=True)
for element in list(doc.styles.element.xpath('.//w:pBdr')) + list(doc.element.xpath('.//w:pBdr')):
    element.getparent().remove(element)
doc.save(OUT)

# Check numbering, answer fields and reference values before native layout rendering.
check = Document(OUT)
assert len(check.tables) == 53
assert sum(1 for p in check.paragraphs if p.text.startswith('第') and '题 ' in p.text) == 50
assert sum(1 for t in check.tables for row in t.rows for c in row.cells if c.text == '在此粘贴回答或填写关键结果') == 100
manifest = {'output': str(OUT), 'question_count': 50, 'response_fields': 100, 'expected_pages': 28,
            'question_titles': [q[0] for q in questions], 'created_at': datetime.now().isoformat()}
(ROOT / 'artifacts/analysis-qa/comparison-doc-manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(manifest, ensure_ascii=False))
