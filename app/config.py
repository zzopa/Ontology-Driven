"""从 JSON 配置和环境变量加载运行参数。"""
from dataclasses import dataclass
from ipaddress import IPv4Network, ip_network
from pathlib import Path
import json
import os


ROOT = Path(__file__).resolve().parents[1]


def _merge(base, override):
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge(base[key], value)
        else:
            base[key] = value
    return base


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    lan_cidr: IPv4Network
    embed_parent_origins: tuple
    db: dict
    llm_url: str
    llm_model: str
    llm_key: str
    graph_path: Path
    schema_path: Path
    ontology_path: Path
    memory_path: Path
    page_path: Path
    page_size: int
    drill_ttl_seconds: int
    graph_main_limit: int
    graph_related_limit: int
    schemas: tuple
    excluded_prefixes: tuple
    excluded_suffixes: tuple
    query_timeout_ms: int
    max_depth: int
    max_related_tables: int
    preflight: bool
    evidence_budget: int
    relation_probe_timeout_ms: int
    relation_probe_budget_seconds: int
    admin_directory: Path
    admin_session_hours: int
    retention_days: int
    data_source: str = 'hbairport'
    identity_db: dict | None = None
    external_models_enabled: bool = True
    permitted_model_origins: tuple = ()
    evidence_workers: int = 3
    evidence_timeout_seconds: int = 120
    evidence_retries: int = 1
    evidence_enable_thinking: bool = False
    answer_composition_enabled: bool = True
    answer_composition_budget: int = 18000
    answer_composition_timeout_seconds: int = 20
    max_evidence_batches: int = 0
    metadata_views: tuple = ()
    oracle: dict | None = None
    sync_service: dict | None = None
    coordinator_enabled: bool = False


def load_settings():
    with (ROOT / 'config.json').open(encoding='utf-8') as source:
        config = json.load(source)
    local = ROOT / 'config.local.json'
    if local.exists():
        with local.open(encoding='utf-8') as source:
            _merge(config, json.load(source))

    identity_database = config['database'].copy()
    identity_database['password'] = os.environ.get('HBASK_DB_PASSWORD', identity_database.get('password', ''))
    data_source = os.environ.get('HBASK_DATA_SOURCE', config.get('data_source', 'hbairport'))
    if data_source not in ('hbairport', 'acdm', 'ops_ask'):
        raise ValueError('数据源必须为 hbairport、acdm 或 ops_ask')
    if data_source != 'hbairport':
        with (ROOT / 'data' / data_source / 'profile.json').open(encoding='utf-8') as source:
            _merge(config, json.load(source))
        if local.exists():
            with local.open(encoding='utf-8') as source:
                _merge(config, json.load(source))

    server = config['server']
    database = config['database'].copy()
    llm = config['llm']
    paths = config['files']
    query = config['query']
    database['password'] = os.environ.get('HBASK_DB_PASSWORD', database.get('password', ''))
    llm_key = os.environ.get('HBASK_LLM_API_KEY', llm.get('api_key', ''))
    ora_cfg = config.get('oracle', {}).copy()
    oracle_cfg = {
        'host': os.environ.get('HBASK_ORACLE_HOST', ora_cfg.get('host', '172.26.78.133')),
        'port': int(os.environ.get('HBASK_ORACLE_PORT', ora_cfg.get('port', 1521))),
        'service_name': os.environ.get('HBASK_ORACLE_SERVICE', ora_cfg.get('service_name', 'datacache')),
        'user': os.environ.get('HBASK_ORACLE_USER', ora_cfg.get('user', '')),
        'password': os.environ.get('HBASK_ORACLE_PASSWORD', ora_cfg.get('password', '')),
        'airport_code': os.environ.get('HBASK_SYNC_AIRPORT_CODE', ora_cfg.get('airport_code', 'WUH')),
        'acdm_schema': ora_cfg.get('acdm_schema', 'ACDM'),
        'acdm_table': ora_cfg.get('acdm_table', 'TB_DYN_FLIGHT'),
        'fims_schema': ora_cfg.get('fims_schema', 'FIMS'),
        'fims_table': ora_cfg.get('fims_table', 'TB_DYN_FLIGHT'),
        'fims_share_table': ora_cfg.get('fims_share_table', 'TB_DYN_SHARE_FLIGHTS'),
    }
    sync_cfg = config.get('sync_service', {}).copy()
    sync_service = {
        'oracle_host': oracle_cfg['host'],
        'oracle_port': oracle_cfg['port'],
        'fetch_size': max(100, min(5000, int(sync_cfg.get('fetch_size', 1000)))),
        'batch_size': max(50, min(5000, int(sync_cfg.get('batch_size', 500)))),
        'lookback_minutes': max(1, min(1440, int(sync_cfg.get('lookback_minutes', 15)))),
        'dep_off_in_values': sync_cfg.get('dep_off_in_values', '1,D,O,出'),
        'arr_off_in_values': sync_cfg.get('arr_off_in_values', '0,A,I,进'),
        'source_system_acdm': sync_cfg.get('source_system_acdm', 'ACDM_ORACLE'),
        'source_system_fims': sync_cfg.get('source_system_fims', 'FIMS_ORACLE'),
        'source_system_production': sync_cfg.get('source_system_production', 'FORMFILL_ORACLE'),
        'backup_container': os.environ.get('HBASK_PG_BACKUP_CONTAINER', sync_cfg.get('backup_container', 'lightrag-pgvector')),
        'backup_dir': str(ROOT / sync_cfg.get('backup_dir', 'artifacts/backups')),
    }

    def file_path(name):
        return ROOT / paths[name]

    return Settings(
        host=os.environ.get('HBASK_HOST', os.environ.get('ASK_WEB_HOST', server['host'])),
        port=int(os.environ.get('HBASK_PORT', server['port'])),
        lan_cidr=ip_network(os.environ.get(
            'HBASK_LAN_CIDR', os.environ.get('ASK_WEB_LAN_CIDR', server['lan_cidr'])),
            strict=False),
        embed_parent_origins=tuple(server.get('embed_parent_origins', [])),
        db=database,
        llm_url=llm['url'], llm_model=llm['model'], llm_key=llm_key,
        graph_path=file_path('graph'), schema_path=file_path('schema'),
        ontology_path=file_path('ontology'), memory_path=file_path('memory'),
        page_path=file_path('page'),
        page_size=int(query['page_size']),
        drill_ttl_seconds=int(query['drill_ttl_seconds']),
        graph_main_limit=int(query['graph_main_limit']),
        graph_related_limit=int(query['graph_related_limit']),
        schemas=tuple(config.get('metadata', {}).get('schemas', ['public'])),
        metadata_views=tuple(config.get('metadata', {}).get('views', [])),
        excluded_prefixes=tuple(config.get('metadata', {}).get('excluded_prefixes', [])),
        excluded_suffixes=tuple(config.get('metadata', {}).get('excluded_suffixes', [])),
        query_timeout_ms=int(query.get('timeout_ms', 15000)),
        max_depth=int(query.get('max_depth', 3)),
        max_related_tables=int(query.get('max_related_tables', 12)),
        preflight=bool(query.get('preflight', True)),
        evidence_budget=int(query.get('evidence_budget', 45000)),
        relation_probe_timeout_ms=int(query.get('relation_probe_timeout_ms',2000)),
        relation_probe_budget_seconds=int(query.get('relation_probe_budget_seconds',30)),
        admin_directory=Path(os.environ.get('HBASK_ADMIN_DIR', str(ROOT / config.get('admin', {}).get('directory', 'data/admin')))),
        admin_session_hours=int(config.get('admin', {}).get('session_hours', 12)),
        retention_days=int(config.get('admin', {}).get('retention_days', 90)),
        data_source=data_source, identity_db=identity_database,
        external_models_enabled=data_source == 'hbairport',
        permitted_model_origins=tuple(
            x.strip() for x in os.environ['HBASK_PERMITTED_MODEL_ORIGINS'].split(',') if x.strip()
        ) if os.environ.get('HBASK_PERMITTED_MODEL_ORIGINS') else tuple(config.get('llm',{}).get('permitted_origins',[])),
        evidence_workers=max(1,min(4,int(query.get('evidence_workers',3)))),
        evidence_timeout_seconds=max(10,min(300,int(query.get('evidence_timeout_seconds',120)))),
        evidence_retries=max(0,min(2,int(query.get('evidence_retries',1)))),
        evidence_enable_thinking=bool(query.get('evidence_enable_thinking',False)),
        answer_composition_enabled=bool(query.get('answer_composition_enabled',True)),
        answer_composition_budget=max(2048,min(100000,int(query.get('answer_composition_budget',18000)))),
        answer_composition_timeout_seconds=max(5,min(60,int(query.get('answer_composition_timeout_seconds',20)))),
        max_evidence_batches=max(0, int(os.environ.get('HBASK_MAX_EVIDENCE_BATCHES', query.get('max_evidence_batches', 0)))),
        oracle=oracle_cfg,
        sync_service=sync_service,
        coordinator_enabled=bool(query.get('coordinator_enabled', False)),
    )


settings = load_settings()


def db_options(database=None):
    """为历史脚本返回独立连接参数，保留其指定的数据库名。"""
    options = settings.db.copy()
    if database:
        options['database'] = database
    return options
