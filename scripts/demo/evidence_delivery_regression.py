"""Replay an encrypted real query snapshot without touching live management state.

Default: capture requests with a deterministic model stub (no external calls).
--live-model: test the active provider; sends the same sanitized evidence already
queried by the app. Report contains counts/names only, never keys or HR details.
"""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
import sqlite3
from unittest.mock import patch

from cryptography.fernet import Fernet

from app import core
from app.config import ROOT, settings
from app.evidence_transport import dumps, restore_batches
from app.execution import ExecutionTrace
from app.llm import chat, model_context
from app.metadata import save_snapshot
from app.ontology import Catalog


def read_snapshot(question):
    supplied = os.environ.get('HBASK_ADMIN_SECRET_KEY')
    cipher = Fernet(supplied.encode() if supplied else (settings.admin_directory / 'secret.key').read_bytes())
    # Do not construct AdminStore: startup maintenance mutates active messages.
    with sqlite3.connect((settings.admin_directory / 'management.sqlite3').as_uri() + '?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        snapshot = None
        for row in db.execute('SELECT question,result FROM messages WHERE result IS NOT NULL ORDER BY created_at DESC LIMIT 300'):
            if cipher.decrypt(row['question'].encode()).decode() == question:
                snapshot = json.loads(cipher.decrypt(row['result'].encode()).decode())
                break
        if snapshot is None:
            raise ValueError('未找到该问题的成功查询快照')
        active = db.execute("SELECT value FROM preferences WHERE key='active_model'").fetchone()
        model = db.execute('SELECT * FROM models WHERE id=? AND enabled=1',
                           (json.loads(active[0]) if active else None,)).fetchone()
        provider = {'url': model['url'], 'model': model['model'],
                    'key': cipher.decrypt(model['secret'].encode()).decode() if model['secret'] else ''} if model else None
    return snapshot, provider


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--question', default='查找信科公司李姓员工情况')
    parser.add_argument('--live-model', action='store_true')
    args = parser.parse_args()
    result, provider = read_snapshot(args.question)
    if args.live_model and not provider:
        raise ValueError('没有可用的启用模型')
    catalog = Catalog(json.loads(settings.schema_path.read_text(encoding='utf-8')),
                      json.loads(settings.ontology_path.read_text(encoding='utf-8')), settings)
    evidence, captured, report = result['evidence'], [], {}
    trace = ExecutionTrace(lambda event: print(json.dumps({
        'batch': event['step']['id'], 'state': event['step']['state']}, ensure_ascii=False), flush=True))

    def capture(system, user, **kwargs):
        captured.append(json.loads(user)['evidence'])
        return chat(system, user, **kwargs) if args.live_model else '{"claims":[]}'

    provider = provider if args.live_model else {'url': 'https://example.invalid', 'model': 'stub', 'key': 'stub'}
    with model_context(provider), patch.object(core, 'chat', side_effect=capture), \
            patch.object(core, 'model_available', return_value=True):
        claims, answer, validation = core.answer(catalog, result['plan'], evidence,
            result['main_total'], True, args.question, trace, report)
    unique = {b['batch']['index']: b for b in captured}
    retry_unchanged = all(b == unique[b['batch']['index']] for b in captured)
    facts, edges = restore_batches([unique[i] for i in sorted(unique)])
    fact_equal = {f['id']: f for f in facts} == {f['id']: f for f in evidence['facts']}
    edge_equal = sorted(dumps(e) for e in edges) == sorted(dumps(e) for e in evidence['relationships'])
    name_field = catalog.entities[result['plan']['subject']]['bindings'].get('name')
    names = [str(f['properties'][name_field]['value']) for f in evidence['facts']
             if f['table'] == result['plan']['subject'] and name_field in f['properties']]
    names_equal = all(name in answer for name in names)
    output = {'verified_at': datetime.now(timezone.utc).isoformat(),
              'mode': 'live-model-snapshot-replay' if args.live_model else 'stub-model-snapshot-replay',
              'question': args.question, 'snapshot_queried_at': evidence['queried_at'],
              'database': catalog.schema['database'], 'main_total': result['main_total'],
              'original_fact_count': len(evidence['facts']), 'original_relationship_count': len(evidence['relationships']),
              'lossless_actual_request_payloads': fact_equal and edge_equal,
              'retry_payloads_unchanged': retry_unchanged, 'request_attempts': len(captured),
              'all_primary_names_in_answer': names_equal, 'primary_names': names,
              'model_delivery': deepcopy(report), 'answer_validation': validation,
              'claim_count': len(claims), 'summary': claims[0]['text'] if claims else answer,
              'all_passed': fact_equal and edge_equal and retry_unchanged and names_equal and report['complete_queried_data']}
    path = ROOT / 'artifacts/evaluations' / ('evidence_delivery_live.json' if args.live_model else 'evidence_delivery_regression.json')
    save_snapshot(path, output)
    print(json.dumps(output, ensure_ascii=False), flush=True)
    if not output['all_passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
