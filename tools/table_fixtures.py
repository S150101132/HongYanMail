"""T2 表格: writes the acceptance samples to tests/fixtures/tables/ — clipboard HTML shaped like what
Excel, WPS, Word and a browser put on the clipboard (synthetic data only), and expected.json with
what the layout must find in each. Run: python -m tools.table_fixtures
"""
import json
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / 'tests' / 'fixtures' / 'tables'
EXCEL_TD = ('td{padding-top:1px;padding-right:1px;padding-left:1px;mso-ignore:padding;color:black;font-size:11.0pt;font-weight:400;'
            'font-style:normal;text-decoration:none;font-family:等线;mso-generic-font-family:auto;mso-font-charset:134;'
            'mso-number-format:General;text-align:general;vertical-align:bottom;border:none;mso-background-source:auto;'
            'mso-pattern:auto;mso-protection:locked visible;white-space:nowrap;mso-rotate:0;}')


def cell(v):
    return v if isinstance(v, dict) else {'t': v}


def excel(rows, classes='', widths=None, program='Excel'):
    """rows: [[text | {t, cls, num, cs, rs, style}]]; classes: extra CSS rules."""
    meta = ('<meta name=ProgId content=Excel.Sheet><meta name=Generator content="Microsoft Excel 15">' if program == 'Excel'
            else '<meta name=ProgId content=Et.Document><meta name=Generator content="WPS Office ET">')
    cols = ''.join(f'<col width={w} style="width:{w * 0.75}pt">' for w in (widths or [])) or '<col width=72>'
    body = []
    for row in rows:
        tds = []
        for v in map(cell, row):
            attrs = ''
            if v.get('cls'):
                attrs += f' class={v["cls"]}'
            if v.get('cs'):
                attrs += f' colspan={v["cs"]}'
            if v.get('rs'):
                attrs += f' rowspan={v["rs"]}'
            if v.get('num') is not None:
                attrs += f' align=right x:num="{v["num"]}"'
            elif v.get('str'):
                attrs += ' x:str'
            if v.get('style'):
                attrs += f" style='{v['style']}'"
            tds.append(f'<td{attrs}>{v["t"]}</td>')
        body.append('<tr height=19 style="height:14.25pt">' + ''.join(tds) + '</tr>')
    width = sum(widths or [72])
    return (f'<html xmlns:o="urn:schemas-microsoft-com:office:office" xmlns:x="urn:schemas-microsoft-com:office:excel" '
            f'xmlns="http://www.w3.org/TR/REC-html40"><head><meta http-equiv=Content-Type content="text/html; charset=utf-8">{meta}'
            f'<style><!--table{{mso-displayed-decimal-separator:"\\.";mso-displayed-thousand-separator:"\\,";}}\n{EXCEL_TD}\n{classes}--></style></head>'
            f'<body link="#0563C1" vlink="#954F72"><table border=0 cellpadding=0 cellspacing=0 width={width} '
            f"style='border-collapse:collapse;width:{width * 0.75}pt'>{cols}<!--StartFragment-->{''.join(body)}<!--EndFragment--></table></body></html>")


HEAD = '.xl65{font-weight:700;background:#D9E1F2;mso-pattern:black none;border:.5pt solid windowtext;}\n'
GRID = '.xl66{border:.5pt solid windowtext;}\n'
DATE = '.xl67{mso-number-format:"yyyy\\/m\\/d";border:.5pt solid windowtext;}\n'
MONEY = '.xl68{mso-number-format:"\\#\\,\\#\\#0\\.00";border:.5pt solid windowtext;}\n'
PCT = '.xl69{mso-number-format:0%;border:.5pt solid windowtext;}\n'
TEXT = '.xl70{mso-number-format:"\\@";border:.5pt solid windowtext;}\n'
H = lambda t, **k: {'t': t, 'cls': 'xl65', **k}
G = lambda t, **k: {'t': t, 'cls': 'xl66', **k}


def fixtures():
    f = {}
    # 1 普通 Excel 数据清单
    people = [('张伟', '研发部', '2023/3/15', '00123', '138 0013 8001'), ('王芳', '市场部', '2022/11/2', '00124', '139 0013 9002'),
              ('李娜', '财务部', '2024/1/8', '00131', '137 0013 7003'), ('刘洋', '研发部', '2021/7/19', '00142', '136 0013 6004'),
              ('陈静', '人事部', '2025/5/6', '00150', '135 0013 5005')]
    f['01-list'] = (excel([[H('姓名'), H('部门'), H('入职日期'), H('工号'), H('联系电话')]] +
                          [[G(n), G(d), {'t': day, 'cls': 'xl67', 'num': 45000 + i}, {'t': no, 'cls': 'xl70', 'str': True}, G(tel)] for i, (n, d, day, no, tel) in enumerate(people)],
                          HEAD + GRID + DATE + TEXT, [64, 72, 88, 64, 120]),
                    {'roles': 'head data data data data data', 'types': ['text', 'text', 'date', 'id', 'id']})
    # 2 多行表头和合并单元格（标题行 + 分组表头）
    f['02-multihead'] = (excel([[{'t': '2026 年 10 月 差旅费用汇总', 'cls': 'xl71', 'cs': 6}],
                                [H('序号', rs=2), H('出差人', rs=2), H('目的地', rs=2), H('费用（元）', cs=2), H('备注', rs=2)],
                                [H('交通'), H('住宿')],
                                [G('1', num=1), G('张伟'), G('利雅得'), {'t': '3,250.00', 'cls': 'xl68', 'num': 3250}, {'t': '1,800.00', 'cls': 'xl68', 'num': 1800}, G('客户现场')],
                                [G('2', num=2), G('王芳'), G('吉达'), {'t': '2,980.00', 'cls': 'xl68', 'num': 2980}, {'t': '2,400.00', 'cls': 'xl68', 'num': 2400}, G('')],
                                [G('3', num=3), G('李娜'), G('达曼'), {'t': '1,560.00', 'cls': 'xl68', 'num': 1560}, {'t': '960.00', 'cls': 'xl68', 'num': 960}, G('培训')],
                                [{'t': '合计', 'cls': 'xl72', 'cs': 3}, {'t': '7,790.00', 'cls': 'xl73', 'num': 7790}, {'t': '5,160.00', 'cls': 'xl73', 'num': 5160}, {'t': '', 'cls': 'xl72'}]],
                               HEAD + GRID + MONEY + '.xl71{font-size:14.0pt;font-weight:700;text-align:center;}\n'
                               '.xl72{font-weight:700;border-top:1.5pt solid windowtext;border-bottom:.5pt solid windowtext;}\n'
                               '.xl73{font-weight:700;mso-number-format:"\\#\\,\\#\\#0\\.00";border-top:1.5pt solid windowtext;}\n', [48, 64, 64, 88, 88, 96]),
                         {'roles': 'title head head data data data total', 'types': ['index', 'text', 'text', 'money', 'money', 'text']})
    # 3 第一行不是表头
    f['03-noheader'] = (excel([[G('利雅得'), G('NG-RUH-01'), G('在线')], [G('吉达'), G('NG-JED-02'), G('在线')], [G('达曼'), G('NG-DMM-03'), G('维护中')],
                               [G('麦地那'), G('NG-MED-04'), G('在线')]], GRID, [72, 96, 64]),
                        {'roles': 'data data data data', 'types': ['text', 'id', 'status']})
    # 4 金额、百分比、日期、单位混合
    f['04-mixed'] = (excel([[H('设备型号'), H('数量'), H('单价'), H('完成率'), H('到货日期'), H('备注')],
                            [G('NE40E-X8'), G('23 台'), G('SAR 2,500'), {'t': '15%', 'cls': 'xl69', 'num': 0.15}, {'t': '2026/10/12', 'cls': 'xl67', 'num': 46307}, G('')],
                            [G('S5735-L48'), G('120 台'), G('SAR 1,180'), {'t': '100%', 'cls': 'xl69', 'num': 1}, {'t': '2026/9/30', 'cls': 'xl67', 'num': 46295}, G('已签收')],
                            [G('AR6280'), G('8 台'), G('SAR 9,600'), G('N/A'), G('待定'), G('海关清关中')],
                            [G('OLT MA5800'), G('2 套'), G('SAR 48,000'), {'t': '60%', 'cls': 'xl69', 'num': 0.6}, {'t': '2026/11/20', 'cls': 'xl67', 'num': 46346}, G('')]],
                           HEAD + GRID + DATE + PCT, [96, 56, 88, 64, 88, 96]),
                     {'roles': 'head data data data data', 'types': ['id', 'quantity', 'money', 'percent', 'date', 'text']})
    # 5 小计、总计的统计表（分组）
    rows = [[H('区域'), H('站点'), H('告警数'), H('处理率')]]
    for region, sites in (('中区', (('RUH-01', 12, '92%'), ('RUH-02', 7, '100%'))), ('西区', (('JED-01', 21, '86%'), ('JED-02', 5, '80%'), ('MED-01', 3, '100%')))):
        rows.append([{'t': region, 'cls': 'xl74', 'cs': 4}])
        for site, n, rate in sites:
            rows.append([G(''), G(site), G(str(n), num=n), G(rate)])
        rows.append([G('小计'), G(''), G(str(sum(s[1] for s in sites)), num=sum(s[1] for s in sites)), G('')])
    rows.append([{'t': '总计', 'cls': 'xl72'}, {'t': '', 'cls': 'xl72'}, {'t': '48', 'cls': 'xl72', 'num': 48}, {'t': '90%', 'cls': 'xl72'}])
    f['05-subtotals'] = (excel(rows, HEAD + GRID + '.xl74{font-weight:700;background:#F2F2F2;border:.5pt solid windowtext;}\n'
                               '.xl72{font-weight:700;border-top:1.5pt solid windowtext;}\n', [64, 88, 64, 64]),
                         {'roles': 'head group data data subtotal group data data data subtotal total', 'types': ['empty', 'id', 'number', 'percent']})
    # 6 大量文字说明
    f['06-longtext'] = (excel([[H('编号'), H('问题'), H('现象描述'), H('负责人'), H('状态')],
                               [G('INC-2026-0412'), G('VPN 掉线'), G('现场工程师使用 VPN 接入客户网络后约 20 分钟自动断开，重新拨号后恢复，怀疑客户防火墙会话超时设置过短，已提供抓包，等待客户网络组确认。'), G('张伟'), G('处理中')],
                               [G('INC-2026-0418'), G('报表导出慢'), G('月度报表导出超过 5 分钟，数据量约 30 万行，需要评估分批导出或后台生成后邮件通知的方案。'), G('王芳'), G('待评估')],
                               [G('INC-2026-0421'), G('证书过期'), G('网管平台 HTTPS 证书将于 11 月 15 日过期，已提交续期申请。'), G('李娜'), G('处理中')]],
                              HEAD + GRID, [96, 88, 300, 64, 64]),
                        {'roles': 'head data data data', 'types': ['id', 'text', 'longtext', 'text', 'status']})
    # 7 列数较多的宽表
    months = ['1 月', '2 月', '3 月', '4 月', '5 月', '6 月', '7 月', '8 月', '9 月', '10 月', '11 月', '12 月']
    f['07-wide'] = (excel([[H('指标')] + [H(m) for m in months] + [H('全年')]] +
                          [[G(name)] + [G(f'{v + i * 13:,}', num=v + i * 13) for i in range(12)] + [G(f'{sum(v + i * 13 for i in range(12)):,}', num=sum(v + i * 13 for i in range(12)))]
                           for name, v in (('新增用户', 12480), ('活跃用户', 98200), ('流失用户', 3120), ('工单数', 860))], HEAD + GRID, [88] + [64] * 13),
                    {'roles': 'head data data data data', 'types': ['text'] + ['number'] * 13, 'overflow': True})
    # 8 有业务颜色标记（状态色 + Excel 表格样式的灰色斑马纹）
    zebra = '.xl75{background:#F2F2F2;border:.5pt solid windowtext;}\n'
    status = {'正常': '.xl76{background:#C6EFCE;color:#006100;border:.5pt solid windowtext;}\n', '异常': '.xl77{background:#FFC7CE;color:#9C0006;border:.5pt solid windowtext;}\n',
              '待处理': '.xl78{background:#FFEB9C;color:#9C5700;border:.5pt solid windowtext;}\n'}
    cls = {'正常': 'xl76', '异常': 'xl77', '待处理': 'xl78'}
    sites = (('RUH-01', '正常', '99.98%'), ('RUH-02', '异常', '97.20%'), ('JED-01', '正常', '99.95%'), ('JED-02', '待处理', '99.10%'), ('DMM-01', '正常', '99.99%'), ('MED-01', '异常', '95.40%'))
    f['08-colors'] = (excel([[H('站点'), H('状态'), H('可用率'), H('说明')]] +
                            [[{'t': s, 'cls': 'xl75' if i % 2 else 'xl66'}, {'t': st, 'cls': cls[st]}, {'t': r, 'cls': 'xl75' if i % 2 else 'xl66'},
                              {'t': '电源模块告警' if st == '异常' else '', 'cls': 'xl75' if i % 2 else 'xl66', **({'style': 'color:red;font-weight:700'} if st == '异常' else {})}]
                             for i, (s, st, r) in enumerate(sites)], HEAD + GRID + zebra + ''.join(status.values()), [72, 64, 72, 120]),
                      {'roles': 'head data data data data data data', 'types': ['id', 'status', 'percent', 'text'], 'keep': ['#c6efce', '#ffc7ce', '#ffeb9c', '#9c0006'], 'drop': ['#f2f2f2']})
    # 9 中英文混合（WPS 表格）
    f['09-cn-en'] = (excel([[H('Item 项目'), H('Owner 负责人'), H('Due 截止'), H('Status 状态'), H('Budget (USD)')],
                            [G('Core network upgrade 核心网升级'), G('Zhang Wei'), {'t': '2026-11-30', 'cls': 'xl67', 'num': 46356}, G('In progress'), G('$120,000')],
                            [G('Billing migration 计费迁移'), G('Wang Fang'), {'t': '2026-12-15', 'cls': 'xl67', 'num': 46371}, G('Planned'), G('$85,500')],
                            [G('NOC training 网管培训'), G('Li Na'), {'t': '2026-11-10', 'cls': 'xl67', 'num': 46336}, G('Done'), G('$12,000')]],
                           HEAD + GRID + DATE, [200, 96, 88, 88, 96], program='WPS'),
                     {'roles': 'head data data data', 'types': ['longtext', 'text', 'date', 'text', 'money']})
    # 10 大型表格及复杂分组（150 行 + 分组 + 小计 + 总计）
    rows = [[{'t': '2026 年 Q3 各站点工单统计', 'cls': 'xl71', 'cs': 5}], [H('序号'), H('站点'), H('工单号'), H('处理时长（小时）'), H('结论')]]
    n, total = 0, 0
    for g in range(6):
        rows.append([{'t': f'第 {g + 1} 片区', 'cls': 'xl74', 'cs': 5}])
        sub = 0
        for i in range(24):
            n += 1
            hours = round(1.5 + (n * 7 % 23) / 2, 1)
            sub += hours
            rows.append([G(str(n), num=n), G(f'SITE-{g + 1:02d}{i + 1:02d}'), G(f'WO{20260700 + n:08d}'), G(f'{hours}', num=hours), G('已关闭' if n % 5 else '转二线')])
        total += sub
        rows.append([G('小计'), G(''), G(''), G(f'{sub:.1f}', num=round(sub, 1)), G('')])
    rows.append([{'t': '总计', 'cls': 'xl72'}, {'t': '', 'cls': 'xl72'}, {'t': '', 'cls': 'xl72'}, {'t': f'{total:.1f}', 'cls': 'xl72', 'num': round(total, 1)}, {'t': '', 'cls': 'xl72'}])
    rows.append([{'t': '注：处理时长按工单创建到关闭计算，不含客户确认时间。', 'cls': 'xl79', 'cs': 5}])
    roles = ['title', 'head'] + (['group'] + ['data'] * 24 + ['subtotal']) * 6 + ['total', 'note']
    f['10-large'] = (excel(rows, HEAD + GRID + '.xl71{font-size:14.0pt;font-weight:700;}\n.xl74{font-weight:700;background:#F2F2F2;border:.5pt solid windowtext;}\n'
                           '.xl72{font-weight:700;border-top:1.5pt solid windowtext;}\n.xl79{color:#7F7F7F;}\n', [48, 96, 96, 88, 72]),
                     {'roles': ' '.join(roles), 'types': ['index', 'id', 'id', 'number', 'status']})
    # 11 用户手动调整过格式的表格（编辑器里已有的表：手动对齐、行角色、底色、样式）
    f['11-manual'] = ('<table data-hy-table="1" data-hy-theme="report"><tbody>'
                      '<tr><td>项目</td><td>金额</td><td>说明</td></tr>'
                      '<tr><td>住宿</td><td data-hy-al="center">1,800.00</td><td data-hy-fill="#fff4d6">客户现场附近</td></tr>'
                      '<tr><td>交通</td><td data-hy-al="center">3,250.00</td><td>往返机票</td></tr>'
                      '<tr data-hy-role="total"><td>共计</td><td data-hy-al="center">5,050.00</td><td></td></tr></tbody></table>',
                      {'roles': 'head data data total', 'types': ['text', 'money', 'text'], 'manual': True})
    # 12 网页里复制的表格（浏览器带着计算后的样式）→ 检查发出去的 HTML
    f['12-web'] = ('<html><body><!--StartFragment--><table style="color: rgb(36, 41, 47); font-family: -apple-system, Segoe UI; font-size: 14px; border-collapse: collapse; width: 100%;">'
                   '<thead><tr><th style="padding: 6px 13px; border: 1px solid rgb(208, 215, 222); font-weight: 600;">Option</th>'
                   '<th style="padding: 6px 13px; border: 1px solid rgb(208, 215, 222); font-weight: 600;">Default</th>'
                   '<th style="padding: 6px 13px; border: 1px solid rgb(208, 215, 222); font-weight: 600;">Description</th></tr></thead><tbody>'
                   '<tr><td style="padding: 6px 13px; border: 1px solid rgb(208, 215, 222);"><code>timeout</code></td><td style="padding: 6px 13px; border: 1px solid rgb(208, 215, 222);">30</td>'
                   '<td style="padding: 6px 13px; border: 1px solid rgb(208, 215, 222);">Seconds to wait for the server before the request fails and is retried once.</td></tr>'
                   '<tr style="background-color: rgb(246, 248, 250);"><td style="padding: 6px 13px; border: 1px solid rgb(208, 215, 222);"><code>retries</code></td>'
                   '<td style="padding: 6px 13px; border: 1px solid rgb(208, 215, 222);">2</td><td style="padding: 6px 13px; border: 1px solid rgb(208, 215, 222);">How many times a failed request is sent again.</td></tr>'
                   '<tr><td style="padding: 6px 13px; border: 1px solid rgb(208, 215, 222);"><code>proxy</code></td><td style="padding: 6px 13px; border: 1px solid rgb(208, 215, 222);">none</td>'
                   '<td style="padding: 6px 13px; border: 1px solid rgb(208, 215, 222);">HTTP proxy address, for example http://10.0.0.1:8080.</td></tr>'
                   '</tbody></table><!--EndFragment--></body></html>',
                   {'roles': 'head data data data', 'types': ['text', 'number', 'longtext'], 'mail': True})
    return f


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    expected = {}
    for name, (html, want) in fixtures().items():
        (OUT / f'{name}.html').write_text(html, encoding='utf-8')
        expected[name] = want
    (OUT / 'expected.json').write_text(json.dumps(expected, ensure_ascii=False, indent=1), encoding='utf-8')
    print(f'{len(expected)} samples -> {OUT}')


if __name__ == '__main__':
    main()
