# -*- coding: utf-8 -*-
"""Comprehensive Aviation Domain 76-Question Test Suite (WUH / ACDM)."""

import json
import os
import sys
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import unittest
from datetime import datetime
from unittest.mock import patch

from app.agent.coordinator import Coordinator
from app.config import load_settings
from app.ontology import Catalog
from app.planner import Planner
from app.query import Compiler


TEST_QUESTIONS = [
    # --- 1. 放行正常率、起飞正常率与放行延误 (On-Time Performance & Clearance Delay) ---
    {"id": "A01", "domain": "放行正常与放行延误", "question": "昨天武汉天河机场放行正常率是多少？"},
    {"id": "A02", "domain": "放行正常与放行延误", "question": "本月天河机场出港航班放行延误架次是多少？"},
    {"id": "A03", "domain": "放行正常与放行延误", "question": "昨天放行延误架次"},
    {"id": "A04", "domain": "放行正常与放行延误", "question": "昨天放行正常率"},
    {"id": "A05", "domain": "放行正常与放行延误", "question": "本周放行延误架次是多少？"},
    {"id": "A06", "domain": "放行正常与放行延误", "question": "昨天放行延误1-5分钟的短延误架次是多少？"},
    {"id": "A07", "domain": "放行正常与放行延误", "question": "昨天短延误架次是多少？"},
    {"id": "A08", "domain": "放行正常与放行延误", "question": "本月天河机场起飞延误架次是多少？"},
    {"id": "A09", "domain": "放行正常与放行延误", "question": "昨天起飞正常率是多少？"},
    {"id": "A10", "domain": "放行正常与放行延误", "question": "昨天出港平均延误时长是多少分钟？"},
    {"id": "A11", "domain": "放行正常与放行延误", "question": "昨天进港平均延误时长是多少分钟？"},
    {"id": "A12", "domain": "放行正常与放行延误", "question": "昨天放行延误的航班有哪些？"},
    {"id": "A13", "domain": "放行正常与放行延误", "question": "昨天放行延误时间最长的5个航班是哪些？"},
    {"id": "A14", "domain": "放行正常与放行延误", "question": "本周放行延误前10个航班明细"},
    {"id": "A15", "domain": "放行正常与放行延误", "question": "昨天放行延误时间最长的航班是哪班？"},

    # --- 2. 延误原因与异常原因主分类统计 (Delay & Abnormal Reason Breakdown) ---
    {"id": "B01", "domain": "延误原因与异常原因分类", "question": "本月武汉天河机场延误原因中主分类的占比是多少？按异常原因分类展示"},
    {"id": "B02", "domain": "延误原因与异常原因分类", "question": "昨天延误原因分布情况"},
    {"id": "B03", "domain": "延误原因与异常原因分类", "question": "本月延误原因分布"},
    {"id": "B04", "domain": "延误原因与异常原因分类", "question": "昨天异常原因分类排名"},
    {"id": "B05", "domain": "延误原因与异常原因分类", "question": "昨天各延误原因架次排行"},
    {"id": "B06", "domain": "延误原因与异常原因分类", "question": "本月航班延误总架次是多少？"},
    {"id": "B07", "domain": "延误原因与异常原因分类", "question": "昨天出港延误架次是多少？"},
    {"id": "B08", "domain": "延误原因与异常原因分类", "question": "昨天进港延误架次是多少？"},
    {"id": "B09", "domain": "延误原因与异常原因分类", "question": "昨天全口径延误架次"},
    {"id": "B10", "domain": "延误原因与异常原因分类", "question": "昨天延误总架次是多少？"},
    {"id": "B11", "domain": "延误原因与异常原因分类", "question": "本月按异常原因统计航班延误架次"},
    {"id": "B12", "domain": "延误原因与异常原因分类", "question": "昨天延误原因分类展示"},

    # --- 3. 机位、靠桥率、远机位与外保障 (Stands & Bridge Operations) ---
    {"id": "C01", "domain": "机位靠桥与外保障", "question": "昨天武汉天河机场全量靠桥率是多少？"},
    {"id": "C02", "domain": "机位靠桥与外保障", "question": "本月天河机场全量靠桥率是多少？"},
    {"id": "C03", "domain": "机位靠桥与外保障", "question": "昨天进港靠桥率是多少？"},
    {"id": "C04", "domain": "机位靠桥与外保障", "question": "本月靠桥率是多少？"},
    {"id": "C05", "domain": "机位靠桥与外保障", "question": "昨天远机位外保障航班架次是多少？"},
    {"id": "C06", "domain": "机位靠桥与外保障", "question": "昨天外保障架次是多少？"},
    {"id": "C07", "domain": "机位靠桥与外保障", "question": "本月外保障架次是多少？"},
    {"id": "C08", "domain": "机位靠桥与外保障", "question": "昨天远机位架次是多少？"},
    {"id": "C09", "domain": "机位靠桥与外保障", "question": "本月武汉天河机场外保障航班按天展示并给出近机位与远机位占比"},
    {"id": "C10", "domain": "机位靠桥与外保障", "question": "昨天近机位靠桥架次是多少？"},
    {"id": "C11", "domain": "机位靠桥与外保障", "question": "昨天登机桥使用总次数是多少？"},
    {"id": "C12", "domain": "机位靠桥与外保障", "question": "昨天登机桥使用了多少次？"},
    {"id": "C13", "domain": "机位靠桥与外保障", "question": "本月按机位类型统计航班量"},
    {"id": "C14", "domain": "机位靠桥与外保障", "question": "昨天近机位与远机位航班量分别是多少？"},
    {"id": "C15", "domain": "机位靠桥与外保障", "question": "本月每天的靠桥率趋势"},

    # --- 4. A-CDM 里程碑、地面过站与保障节点 (Milestones, Turnaround & Nodes) ---
    {"id": "D01", "domain": "ACDM里程碑与过站节点", "question": "昨天过站保障平均时长是多少分钟？"},
    {"id": "D02", "domain": "ACDM里程碑与过站节点", "question": "昨天过站配对放行正常率是多少？"},
    {"id": "D03", "domain": "ACDM里程碑与过站节点", "question": "昨天地服放行正常率是多少？"},
    {"id": "D04", "domain": "ACDM里程碑与过站节点", "question": "昨天保障节点实测录入总量是多少？"},
    {"id": "D05", "domain": "ACDM里程碑与过站节点", "question": "昨天保障节点录入量是多少？"},
    {"id": "D06", "domain": "ACDM里程碑与过站节点", "question": "昨天保障节点录入去重航班数是多少？"},
    {"id": "D07", "domain": "ACDM里程碑与过站节点", "question": "昨天按保障节点统计实测录入量"},
    {"id": "D08", "domain": "ACDM里程碑与过站节点", "question": "昨天各保障节点实测录入排名"},
    {"id": "D09", "domain": "ACDM里程碑与过站节点", "question": "CZ3101昨天的过站保障诊断"},
    {"id": "D10", "domain": "ACDM里程碑与过站节点", "question": "查询MU2501昨天的过站保障"},
    {"id": "D11", "domain": "ACDM里程碑与过站节点", "question": "昨天CZ3101保障时序图"},
    {"id": "D12", "domain": "ACDM里程碑与过站节点", "question": "昨天在地面存放多久？"},

    # --- 5. 航司、机型、航站楼、属性与高峰小时 (Airline, Craft, Terminal & Peak Hours) ---
    {"id": "E01", "domain": "航司与运控运行体征", "question": "昨天南航出港实际执行了多少班？"},
    {"id": "E02", "domain": "航司与运控运行体征", "question": "昨天东航实际执行架次是多少？"},
    {"id": "E03", "domain": "航司与运控运行体征", "question": "昨天各航司执行架次排名"},
    {"id": "E04", "domain": "航司与运控运行体征", "question": "昨天实际执行架次排名前10的航司"},
    {"id": "E05", "domain": "航司与运控运行体征", "question": "昨天T3航站楼实际执行架次是多少？"},
    {"id": "E06", "domain": "航司与运控运行体征", "question": "昨天各航站楼航班量分布"},
    {"id": "E07", "domain": "航司与运控运行体征", "question": "昨天高峰小时是哪个？"},
    {"id": "E08", "domain": "航司与运控运行体征", "question": "昨天各小时起降架次趋势"},
    {"id": "E09", "domain": "航司与运控运行体征", "question": "昨天哪个小时起降架次最多？"},
    {"id": "E10", "domain": "航司与运控运行体征", "question": "昨天国内出港实际执行了多少班？"},
    {"id": "E11", "domain": "航司与运控运行体征", "question": "昨天国际出港实际执行架次是多少？"},
    {"id": "E12", "domain": "航司与运控运行体征", "question": "昨天出港旅客吞吐量是多少人？"},
    {"id": "E13", "domain": "航司与运控运行体征", "question": "昨天进港旅客吞吐量是多少人？"},
    {"id": "E14", "domain": "航司与运控运行体征", "question": "昨天计划航班量是多少？"},
    {"id": "E15", "domain": "航司与运控运行体征", "question": "昨天实际执行架次是多少？"},
    {"id": "E16", "domain": "航司与运控运行体征", "question": "昨天取消了多少班？"},
    {"id": "E17", "domain": "航司与运控运行体征", "question": "昨天出港取消了多少班？"},
    {"id": "E18", "domain": "航司与运控运行体征", "question": "昨天最晚实际起飞的出港航班是哪一班？"},
    {"id": "E19", "domain": "航司与运控运行体征", "question": "昨天最早实际落地的进港航班是哪一班？"},
    {"id": "E20", "domain": "航司与运控运行体征", "question": "查看昨天地服公司运行简报"},
    {"id": "E21", "domain": "航司与运控运行体征", "question": "查看本月A-CDM运行通用报表"},
    {"id": "E22", "domain": "航司与运控运行体征", "question": "查看昨天保障节点与资源看板"},
]


def run_tests():
    with patch.dict("os.environ", {"HBASK_DATA_SOURCE": "ops_ask"}):
        settings = load_settings()
    catalog = Catalog(
        json.loads(settings.schema_path.read_text(encoding="utf-8")),
        json.loads(settings.ontology_path.read_text(encoding="utf-8")),
        settings,
    )
    references = [
        {
            "role": "airline",
            "term": term,
            "value": code,
            "table": "dim_airline",
            "field": "iata_code",
            "column": "airline_code",
        }
        for term, code in Planner._AIRLINE_ALIASES.items()
    ]
    coord = Coordinator(catalog, references=references)
    compiler = Compiler(catalog)

    results = []
    print(f"Executing {len(TEST_QUESTIONS)} Aviation Domain Questions...\n" + "-" * 75)

    for item in TEST_QUESTIONS:
        qid = item["id"]
        domain = item["domain"]
        q = item["question"]
        plan = coord.plan(q)
        outcome = plan.get("outcome")

        # An outcome of None or 'answered' means planner accepted and formed a valid plan/response
        is_pass = outcome in (None, "answered")
        sql_generated = None
        sql_error = None

        if is_pass and outcome is None:
            # Check SQL compilation
            try:
                if plan.get("intent") == "detail":
                    compiled = compiler.detail(plan, [], settings.graph_main_limit, settings.graph_related_limit)
                elif plan.get("intent") in ("count", "aggregate", "metric"):
                    compiled = compiler.aggregate(plan)
                else:
                    compiled = None
                if compiled:
                    sql_generated = compiled.sql
            except Exception as e:
                sql_error = str(e)
                is_pass = False

        res = {
            "id": qid,
            "domain": domain,
            "question": q,
            "passed": is_pass,
            "outcome": outcome,
            "subject": plan.get("subject"),
            "intent": plan.get("intent"),
            "metric": plan.get("metric"),
            "group_by": plan.get("group_by"),
            "limit": plan.get("limit"),
            "order_by": plan.get("order_by"),
            "conditions": plan.get("conditions"),
            "clarification": plan.get("clarification"),
            "answer": plan.get("answer"),
            "sql": sql_generated,
            "sql_error": sql_error,
        }
        results.append(res)

        status_tag = "PASS" if is_pass else "FAIL"
        details = (
            f"subject={res['subject']}, metric={res['metric'] or res['intent']}"
            if is_pass
            else f"reason={res['answer'] or res.get('clarification') or res['sql_error']}"
        )
        print(f"[{qid}] [{status_tag}] {q} -> {details}")

    passed_count = sum(1 for r in results if r["passed"])
    total_count = len(results)
    pass_rate = passed_count / total_count * 100

    print("=" * 75)
    print(f"Total: {total_count} | Passed: {passed_count} | Failed: {total_count - passed_count} | Pass Rate: {pass_rate:.1f}%")
    return results


if __name__ == "__main__":
    run_tests()
