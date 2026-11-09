"""元数据差异比对测试：验证 compare_schema_to_configs 对表、字段增删改差异的捕获。"""
from app.metadata_diff import compare_schema_to_configs


def _make_db_schema(tables):
    """构造模拟 DB schema。"""
    return {
        'database': 'test_db',
        'tables': [
            {
                'table': name,
                'name': name,
                'schema': 'public',
                'comment': '',
                'columns': [{'name': col, 'type': typ, 'nullable': True, 'comment': ''}
                            for col, typ in cols.items()],
                'primary_key': [],
                'est_rows': 0,
            }
            for name, cols in tables.items()
        ],
        'foreign_keys': [],
    }


def _make_metrics_schema(cubes):
    """构造模拟 metrics_schema。"""
    result = {'cubes': {}}
    for cube_name, table, measures, dims, time_dims in cubes:
        result['cubes'][cube_name] = {
            'sql_table': table,
            'measures': {m: {'sql': m_col, 'type': 'sum'} for m, m_col in measures.items()},
            'dimensions': {d: {'sql': d_col} for d, d_col in dims.items()},
            'time_dimensions': {t: {'sql': t_col, 'granularities': ['month']}
                                for t, t_col in time_dims.items()},
        }
    return result


def _make_field_graph(nodes):
    """构造模拟 field_graph。"""
    return {
        'field_nodes': [
            {'key': f'{table}.{col}', 'table': table, 'col': col, 'type': typ}
            for table, col, typ in nodes
        ],
    }


def _make_ontology(entities):
    """构造模拟 business_ontology。"""
    result = {'entities': {}}
    for table, bindings, identity_fields, attributes, metric_fields in entities:
        result['entities'][table] = {
            'bindings': dict(bindings),
            'identity_fields': list(identity_fields),
            'attributes': {a: {'column': a} for a in attributes},
            'metrics': {f'm_{f}': {'field': f} for f in metric_fields},
        }
    return result


class TestNewTablesDetection:
    """数据库新增了表但配置中未纳入。"""

    def test_new_table_detected(self):
        db = _make_db_schema({'contract_main': {'id': 'int', 'enterprise_scc': 'varchar'},
                              'new_table': {'id': 'int'}})
        metrics = _make_metrics_schema([('contracts', 'contract_main',
                                          {'total': 'lump_sum_amount_wan'}, {}, {})])
        graph = _make_field_graph([('contract_main', 'lump_sum_amount_wan', 'numeric')])
        ontology = _make_ontology([('contract_main', {}, [], ['lump_sum_amount_wan'], [])])

        result = compare_schema_to_configs(db, metrics, graph, ontology)
        assert 'new_table' in result['new_tables']
        assert result['summary']['new_tables'] >= 1


class TestNewColumnsDetection:
    """数据库新增了字段但配置中未纳入。"""

    def test_new_column_detected(self):
        db = _make_db_schema({
            'contract_main': {
                'id': 'int', 'enterprise_scc': 'varchar',
                'lump_sum_amount_wan': 'numeric',
                'new_column': 'text',  # 配置中不存在
            },
        })
        metrics = _make_metrics_schema([('contracts', 'contract_main',
                                          {'total': 'lump_sum_amount_wan'}, {}, {})])
        graph = _make_field_graph([('contract_main', 'lump_sum_amount_wan', 'numeric')])
        ontology = _make_ontology([('contract_main', {}, [], ['lump_sum_amount_wan'], [])])

        result = compare_schema_to_configs(db, metrics, graph, ontology)
        new_cols = [c for c in result['new_columns'] if c['table'] == 'contract_main']
        assert any(c['column'] == 'new_column' for c in new_cols)

    def test_existing_column_not_flagged(self):
        db = _make_db_schema({
            'contract_main': {'enterprise_scc': 'varchar', 'lump_sum_amount_wan': 'numeric'},
        })
        metrics = _make_metrics_schema([('contracts', 'contract_main',
                                          {'total': 'lump_sum_amount_wan'}, {'ent': 'enterprise_scc'}, {})])
        graph = _make_field_graph([('contract_main', 'enterprise_scc', 'varchar')])
        ontology = _make_ontology([('contract_main', {'ent_scc': 'enterprise_scc'},
                                    ['enterprise_scc'], ['enterprise_scc'], [])])

        result = compare_schema_to_configs(db, metrics, graph, ontology)
        new_cols = [c for c in result['new_columns'] if c['table'] == 'contract_main']
        # enterprise_scc and lump_sum_amount_wan are both in config, so no new columns
        assert len(new_cols) == 0


class TestTypeChangeDetection:
    """字段类型发生变更。"""

    def test_type_change_detected(self):
        db = _make_db_schema({
            'contract_main': {'lump_sum_amount_wan': 'numeric'},
        })
        metrics = _make_metrics_schema([('contracts', 'contract_main',
                                          {'total': 'lump_sum_amount_wan'}, {}, {})])
        graph = _make_field_graph([('contract_main', 'lump_sum_amount_wan', 'varchar')])  # 配置是 varchar
        ontology = _make_ontology([('contract_main', {}, [], ['lump_sum_amount_wan'], [])])

        result = compare_schema_to_configs(db, metrics, graph, ontology)
        changes = [c for c in result['type_changes']
                    if c['table'] == 'contract_main' and c['column'] == 'lump_sum_amount_wan']
        assert len(changes) == 1
        assert 'varchar' in changes[0]['config_type']
        assert 'numeric' in changes[0]['db_type']

    def test_type_change_with_length_normalized(self):
        db = _make_db_schema({
            'contract_main': {'enterprise_scc': 'varchar(64)'},
        })
        metrics = _make_metrics_schema([])
        graph = _make_field_graph([('contract_main', 'enterprise_scc', 'varchar')])
        ontology = _make_ontology([('contract_main', {}, ['enterprise_scc'], ['enterprise_scc'], [])])

        result = compare_schema_to_configs(db, metrics, graph, ontology)
        # varchar(64) normalizes to varchar, matches config varchar → no change
        changes = [c for c in result['type_changes']
                    if c['table'] == 'contract_main' and c['column'] == 'enterprise_scc']
        assert len(changes) == 0


class TestOrphanedConfigDetection:
    """本地配置中存在但数据库已删除的表/字段。"""

    def test_orphaned_table_detected(self):
        db = _make_db_schema({'contract_main': {'id': 'int'}})
        metrics = _make_metrics_schema([('contracts', 'contract_main',
                                          {'total': 'lump_sum_amount_wan'}, {}, {}),
                                         ('deleted_cube', 'deleted_table',
                                          {'total': 'amount'}, {}, {})])
        graph = _make_field_graph([('contract_main', 'id', 'int')])
        ontology = _make_ontology([('contract_main', {}, ['id'], ['id'], [])])

        result = compare_schema_to_configs(db, metrics, graph, ontology)
        assert 'deleted_table' in result['orphaned_tables']

    def test_orphaned_column_detected(self):
        db = _make_db_schema({
            'contract_main': {'id': 'int', 'enterprise_scc': 'varchar'},
        })
        metrics = _make_metrics_schema([('contracts', 'contract_main',
                                          {'total': 'lump_sum_amount_wan'},  # 列不存在于 DB
                                          {'ent': 'enterprise_scc'}, {})])
        graph = _make_field_graph([('contract_main', 'enterprise_scc', 'varchar')])
        ontology = _make_ontology([('contract_main', {}, ['enterprise_scc'], ['enterprise_scc'], [])])

        result = compare_schema_to_configs(db, metrics, graph, ontology)
        orphaned_cols = [c for c in result['orphaned_columns']
                         if c['table'] == 'contract_main']
        assert any(c['column'] == 'lump_sum_amount_wan' for c in orphaned_cols)
        assert any('metrics_schema' in c['source'] for c in orphaned_cols)

    def test_orphaned_column_source_tracking(self):
        db = _make_db_schema({'contract_main': {'id': 'int'}})
        metrics = _make_metrics_schema([('contracts', 'contract_main', {'total': 'amount'}, {}, {})])
        graph = _make_field_graph([('contract_main', 'deleted_col', 'varchar')])
        ontology = _make_ontology([('contract_main', {'bind': 'bind_col'},
                                    ['identity_col'], ['attr_col'], ['metric_col'])])

        result = compare_schema_to_configs(db, metrics, graph, ontology)
        orphaned = {c['column']: c['source'] for c in result['orphaned_columns']
                     if c['table'] == 'contract_main'}
        assert 'deleted_col' in orphaned
        assert 'field_graph' in orphaned['deleted_col']
        assert 'bind_col' in orphaned
        assert 'business_ontology' in orphaned['bind_col']


class TestSummaryIntegrity:
    """返回的 summary 计数与实际差异列表一致。"""

    def test_summary_counts_match(self):
        db = _make_db_schema({
            'contract_main': {'id': 'int', 'enterprise_scc': 'varchar', 'lump_sum_amount_wan': 'numeric'},
            'new_table': {'id': 'int'},
        })
        metrics = _make_metrics_schema([('contracts', 'contract_main',
                                          {'total': 'lump_sum_amount_wan'}, {'ent': 'enterprise_scc'}, {})])
        graph = _make_field_graph([('contract_main', 'enterprise_scc', 'varchar'),
                                   ('contract_main', 'lump_sum_amount_wan', 'numeric')])
        ontology = _make_ontology([('contract_main', {'ent_scc': 'enterprise_scc'},
                                    ['enterprise_scc'], ['enterprise_scc', 'lump_sum_amount_wan'], [])])

        result = compare_schema_to_configs(db, metrics, graph, ontology)
        s = result['summary']
        assert s['new_tables'] == len(result['new_tables'])
        assert s['new_columns'] == len(result['new_columns'])
        assert s['type_changes'] == len(result['type_changes'])
        assert s['orphaned_tables'] == len(result['orphaned_tables'])
        assert s['orphaned_columns'] == len(result['orphaned_columns'])

    def test_no_differences_clean_db(self):
        db = _make_db_schema({
            'contract_main': {'enterprise_scc': 'varchar', 'lump_sum_amount_wan': 'numeric'},
        })
        metrics = _make_metrics_schema([('contracts', 'contract_main',
                                          {'total': 'lump_sum_amount_wan'}, {'ent': 'enterprise_scc'}, {})])
        graph = _make_field_graph([('contract_main', 'enterprise_scc', 'varchar'),
                                   ('contract_main', 'lump_sum_amount_wan', 'numeric')])
        ontology = _make_ontology([('contract_main', {'ent_scc': 'enterprise_scc'},
                                    ['enterprise_scc'], ['enterprise_scc', 'lump_sum_amount_wan'], [])])

        result = compare_schema_to_configs(db, metrics, graph, ontology)
        s = result['summary']
        assert s['new_tables'] == 0
        assert s['new_columns'] == 0
        assert s['type_changes'] == 0
        assert s['orphaned_tables'] == 0
        assert s['orphaned_columns'] == 0
