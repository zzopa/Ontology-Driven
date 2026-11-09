import unittest
from app.agent.follow_up import FollowUpNormalizer
from app.ontology import Catalog
from app.planner import Planner
from fixtures import fixture_catalog


class FollowUpNormalizerTests(unittest.TestCase):
    def setUp(self):
        self.catalog = fixture_catalog()
        self.planner = Planner(
            self.catalog,
            name_index={'comp_employee': {'张浩', '李康平', '王唯'}},
            enterprise_names={'信科公司': '91420116MACTDG7E9W', '中天智航': '91420111MA4KT4K41P'}
        )
        self.normalizer = FollowUpNormalizer()

    def test_detects_follow_up_questions(self):
        prev = {'subject': 'contract_main', 'intent': 'count', 'conditions': []}
        self.assertTrue(self.normalizer.is_follow_up('中天智航呢？', prev))
        self.assertTrue(self.normalizer.is_follow_up('那去年呢？', prev))
        self.assertTrue(self.normalizer.is_follow_up('有哪些合同？', prev))
        self.assertTrue(self.normalizer.is_follow_up('看下明细', prev))
        self.assertFalse(self.normalizer.is_follow_up('查询2026年信科公司的全部采购合同', prev))
        self.assertFalse(self.normalizer.is_follow_up('中天智航呢？', None))

    def test_company_slot_replacement(self):
        prev_plan = {
            'subject': 'contract_main',
            'intent': 'count',
            'disp': '合同',
            'conditions': [
                {'col': 'enterprise_scc', 'op': '=', 'value': '91420116MACTDG7E9W'},
                {'col': 'signing_at', 'op': 'prefix', 'value': '2026'}
            ]
        }
        res = self.normalizer.apply('中天智航呢？', prev_plan, self.catalog, self.planner)
        self.assertIsNotNone(res)
        self.assertEqual(res['subject'], 'contract_main')
        self.assertEqual(res['intent'], 'count')
        company_cond = next(c for c in res['conditions'] if c['col'] == 'enterprise_scc')
        self.assertEqual(company_cond['value'], '91420111MA4KT4K41P')
        time_cond = next(c for c in res['conditions'] if c['col'] == 'signing_at')
        self.assertEqual(time_cond['value'], '2026')

    def test_intent_switch_from_count_to_detail(self):
        prev_plan = {
            'subject': 'contract_main',
            'intent': 'count',
            'disp': '合同',
            'conditions': [
                {'col': 'signing_at', 'op': 'prefix', 'value': '2026'}
            ]
        }
        res = self.normalizer.apply('有哪些合同？', prev_plan, self.catalog, self.planner)
        self.assertIsNotNone(res)
        self.assertEqual(res['intent'], 'detail')
        self.assertEqual(res['conditions'], prev_plan['conditions'])

    def test_time_period_replacement(self):
        prev_plan = {
            'subject': 'contract_main',
            'intent': 'sum',
            'disp': '合同',
            'conditions': [
                {'table': 'contract_main', 'col': 'signing_at', 'op': 'prefix', 'value': '2026'}
            ]
        }
        res = self.normalizer.apply('那去年呢？', prev_plan, self.catalog, self.planner)
        self.assertIsNotNone(res)
        self.assertEqual(res['intent'], 'sum')
        time_cond = next(c for c in res['conditions'] if c['col'] == 'signing_at')
        self.assertEqual(time_cond['value'], '2025')


if __name__ == '__main__':
    unittest.main()
