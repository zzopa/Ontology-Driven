import json
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    b = p.chromium.launch()
    page = b.new_page(viewport={'width': 1280, 'height': 850})
    
    page.route('**/api/auth/status', lambda route: route.fulfill(
        status=200,
        content_type='application/json',
        body=json.dumps({
            'initialized': True,
            'user': {
                'id': 'u1',
                'username': 'admin',
                'display_name': '运营指挥中心',
                'can_manage': True,
                'source': 'hbairport01',
                'roles': ['admin'],
                'role_names': ['管理员']
            }
        })
    ))
    
    page.route('**/api/status', lambda route: route.fulfill(
        status=200,
        content_type='application/json',
        body=json.dumps({
            'ready': True,
            'data_source': 'ops_ask',
            'coverage': {
                'start': '2026-10-16',
                'end_exclusive': '2026-11-17',
                'exported_at': '2026-11-16'
            }
        })
    ))
    
    page.route('**/api/reports?*', lambda route: route.fulfill(
        status=200,
        content_type='application/json',
        body=json.dumps({
            'ok': True,
            'items': [
                {
                    'id': 'rep001',
                    'title': '《A-CDM 通用报告（日报）》· 2026-11-10',
                    'kind': 'general',
                    'status': 'completed',
                    'created_at': '2026-11-16T16:07:15+08:00',
                    'attempts': 1
                }
            ],
            'total': 1,
            'worker_active': True
        })
    ))

    page.route('**/api/report-subscriptions', lambda route: route.fulfill(
        status=200,
        content_type='application/json',
        body=json.dumps({
            'ok': True,
            'items': [
                {
                    'id': 'sub001',
                    'kind': 'general',
                    'period': 'day',
                    'send_time': '08:00',
                    'status': 'active',
                    'include_benchmarks': 1
                },
                {
                    'id': 'sub002',
                    'kind': 'ground_briefing',
                    'period': 'day',
                    'send_time': '22:30',
                    'status': 'paused',
                    'include_benchmarks': 0
                }
            ]
        })
    ))

    page.route('**/api/notifications?*', lambda route: route.fulfill(
        status=200,
        content_type='application/json',
        body=json.dumps({
            'ok': True,
            'unread': 1,
            'total': 2,
            'items': [
                {
                    'id': 'not001',
                    'report_id': 'rep001',
                    'title': '《A-CDM 通用报告（日报）》· 2026-11-10 已生成完毕',
                    'is_read': 0,
                    'created_at': '2026-11-16T16:07:20+08:00'
                },
                {
                    'id': 'not002',
                    'report_id': 'rep001',
                    'title': '《地服公司运行简报》· 2026-11-09 已生成完毕',
                    'is_read': 1,
                    'created_at': '2026-11-15T23:00:00+08:00'
                }
            ]
        })
    ))

    page.goto('http://localhost:3000')
    page.wait_for_timeout(1000)

    btn = page.get_by_role('button', name='报告中心')
    if btn.count() > 0:
        btn.click()
        page.wait_for_timeout(800)

        # Tab 2: 定时订阅
        page.get_by_role('tab', name='定时订阅').click()
        page.wait_for_timeout(500)
        page.screenshot(path='artifacts/analysis-qa/new-tab-subscriptions-ui.png')
        print('Captured new-tab-subscriptions-ui.png!')

        # Tab 3: 通知中心
        page.get_by_role('tab', name='通知中心').click()
        page.wait_for_timeout(500)
        page.screenshot(path='artifacts/analysis-qa/new-tab-notifications-ui.png')
        print('Captured new-tab-notifications-ui.png!')

    b.close()
