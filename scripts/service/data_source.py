"""Persist a source switch without replacing either source's original files."""
import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('source', choices=['hbairport','acdm','ops_ask'])
    source = parser.parse_args().source
    if source != 'hbairport' and not (ROOT/'data'/source/'profile.json').exists():
        parser.error('Missing data source profile: ' + source)
    path = ROOT/'config.json'
    config = json.loads(path.read_text(encoding='utf-8'))
    config['data_source'] = source
    temporary = ROOT/'config.source.tmp'
    temporary.write_text(json.dumps(config,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    temporary.replace(path)
    print('Data source: ' + source)
