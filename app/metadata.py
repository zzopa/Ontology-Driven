"""Read PostgreSQL metadata and validate configurable relation definitions."""
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
import os
import tempfile


def table_key(schema, table):
    return table if schema == 'public' else schema + '.' + table


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     default=str).encode('utf-8')).hexdigest()


def read_schema(conn, schemas=('public',)):
    cur = conn.cursor()
    try:
        cur.execute('SELECT current_database()')
        database = cur.fetchone()[0]
        cur.execute("""
            SELECT n.nspname, c.relname, GREATEST(c.reltuples::bigint, 0),
                   obj_description(c.oid, 'pg_class')
            FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = ANY(%s::text[]) AND c.relkind IN ('r', 'p')
            ORDER BY n.nspname, c.relname
        """, (list(schemas),))
        tables = [{'schema': ns, 'table': table_key(ns, name), 'name': name,
                   'comment': comment or '', 'columns': [], 'primary_key': [],
                   'unique_keys': [], 'unique_constraints': [], 'est_rows': rows}
                  for ns, name, rows, comment in cur.fetchall()]
        by_name = {item['table']: item for item in tables}
        cur.execute("""
            SELECT n.nspname, c.relname, a.attname,
                   format_type(a.atttypid, a.atttypmod), NOT a.attnotnull,
                   pg_get_expr(d.adbin, d.adrelid), col_description(c.oid, a.attnum)
            FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            JOIN pg_attribute a ON a.attrelid = c.oid
                AND a.attnum > 0 AND NOT a.attisdropped
            LEFT JOIN pg_attrdef d ON d.adrelid = c.oid AND d.adnum = a.attnum
            WHERE n.nspname = ANY(%s::text[]) AND c.relkind IN ('r', 'p')
            ORDER BY n.nspname, c.relname, a.attnum
        """, (list(schemas),))
        for ns, table, name, typ, nullable, default, comment in cur.fetchall():
            by_name[table_key(ns, table)]['columns'].append({
                'name': name, 'type': typ, 'nullable': nullable,
                'default': default, 'comment': comment or ''})
        cur.execute("""
            SELECT n.nspname, c.relname, con.conname, con.contype,
                   array_agg(a.attname ORDER BY key.pos)
            FROM pg_constraint con JOIN pg_class c ON c.oid = con.conrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            JOIN LATERAL unnest(con.conkey) WITH ORDINALITY AS key(attnum, pos) ON TRUE
            JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = key.attnum
            WHERE n.nspname = ANY(%s::text[]) AND con.contype IN ('p', 'u')
            GROUP BY n.nspname, c.relname, con.conname, con.contype
            ORDER BY n.nspname, c.relname, con.conname
        """, (list(schemas),))
        for ns, name, constraint, kind, columns in cur.fetchall():
            table = by_name[table_key(ns, name)]
            if kind == 'p':
                table['primary_key'] = list(columns)
            else:
                table['unique_keys'].extend(columns)
            table['unique_constraints'].append(
                {'name': constraint, 'columns': list(columns), 'primary': kind == 'p'})
        cur.execute("""
            SELECT n.nspname, c.relname, rn.nspname, rc.relname, con.conname,
                   array_agg(a.attname ORDER BY key.pos),
                   array_agg(ra.attname ORDER BY key.pos)
            FROM pg_constraint con JOIN pg_class c ON c.oid = con.conrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            JOIN pg_class rc ON rc.oid = con.confrelid
            JOIN pg_namespace rn ON rn.oid = rc.relnamespace
            JOIN LATERAL unnest(con.conkey) WITH ORDINALITY AS key(attnum, pos) ON TRUE
            JOIN LATERAL unnest(con.confkey) WITH ORDINALITY AS ref(attnum, pos)
                ON ref.pos = key.pos
            JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = key.attnum
            JOIN pg_attribute ra ON ra.attrelid = rc.oid AND ra.attnum = ref.attnum
            WHERE n.nspname = ANY(%s::text[]) AND rn.nspname = ANY(%s::text[])
              AND con.contype = 'f'
            GROUP BY n.nspname, c.relname, rn.nspname, rc.relname, con.conname
            ORDER BY n.nspname, c.relname, con.conname
        """, (list(schemas), list(schemas)))
        foreign_keys = [
            {'table': table_key(ns, table), 'columns': list(cols), 'column': cols[0],
             'ref_table': table_key(rns, rtable), 'ref_columns': list(rcols),
             'ref_column': rcols[0], 'constraint': name}
            for ns, table, rns, rtable, name, cols, rcols in cur.fetchall()]
    finally:
        cur.close()
    if not tables:
        raise RuntimeError('配置的 schema 中没有可访问的业务表')
    schema = {'database': database, 'schemas': list(schemas),
              'tables': tables, 'foreign_keys': foreign_keys}
    schema['fingerprint'] = fingerprint({
        'database': database, 'tables': [
            {k: v for k, v in table.items() if k != 'est_rows'} for table in tables],
        'foreign_keys': foreign_keys})
    schema['refreshed_at'] = datetime.now(timezone.utc).isoformat()
    return schema


def _bare_table(name, tables):
    if name in tables:
        return name
    if name.startswith('public.') and name[7:] in tables:
        return name[7:]
    return name


def build_graph(schema, logical_specs, relationship_hints=()):
    tables = {item['table']: item for item in schema['tables']}
    columns = {name: {col['name']: col for col in item['columns']}
               for name, item in tables.items()}
    primary = {name: set(item.get('primary_key', [])) for name, item in tables.items()}
    links, logical, skipped, skipped_hints, hints = [], [], [], [], 0
    seen, fk_targets = set(), defaultdict(list)

    def unique(table, cols):
        groups = [primary[table]] + [
            set(x['columns']) for x in tables[table].get('unique_constraints', [])]
        return any(group and group.issubset(set(cols)) for group in groups)

    def add(spec, kind):
        nonlocal hints
        if spec.get('status') in ('disabled', 'candidate', 'invalid'):
            return
        pairs = spec.get('pairs') or [(spec['source'], spec['target'])]
        endpoints = [(a.rsplit('.', 1), b.rsplit('.', 1)) for a, b in pairs]
        ft = _bare_table(endpoints[0][0][0], tables)
        tt = _bare_table(endpoints[0][1][0], tables)
        valid = all(_bare_table(a[0], tables) == ft and
                    _bare_table(b[0], tables) == tt and
                    a[1] in columns.get(ft, {}) and b[1] in columns.get(tt, {})
                    for a, b in endpoints)
        if not valid:
            (skipped_hints if kind == 'hint' else skipped).append(
                {'source': pairs[0][0], 'target': pairs[0][1],
                 'reason': '连接表或字段不存在'})
            return
        pair_columns = [(a[1], b[1]) for a, b in endpoints]
        key = tuple(sorted((ft + '.' + a, tt + '.' + b) for a, b in pair_columns))
        reverse = tuple(sorted((b, a) for a, b in key))
        if key in seen or reverse in seen:
            return
        seen.add(key)
        left_unique = unique(ft, [a for a, _ in pair_columns])
        right_unique = unique(tt, [b for _, b in pair_columns])
        cardinality = ('one_to_one' if left_unique and right_unique else
                       'many_to_one' if right_unique else
                       'one_to_many' if left_unique else 'unknown')
        item = {'source': ft + '.' + pair_columns[0][0],
                'target': tt + '.' + pair_columns[0][1],
                'from_table': ft, 'to_table': tt,
                'pairs': [list(p) for p in pair_columns],
                'type': 'fk' if kind == 'fk' else 'logical',
                'provenance': 'database_constraint' if kind == 'fk' else kind,
                'status': spec.get('status', 'configured'),
                'role': spec.get('role', ''),
                'cardinality': cardinality,
                'note': spec.get('note', '历史关系提示，当前库未声明约束' if kind == 'hint' else ''),
                'validation': 'constraint' if kind == 'fk' else 'schema_only'}
        if kind == 'fk':
            links.append(item)
            for a, b in pair_columns:
                fk_targets[ft + '.' + a].append(tt + '.' + b)
        else:
            logical.append(item)
            hints += kind == 'hint'

    for fk in schema['foreign_keys']:
        ft, tt = _bare_table(fk['table'], tables), _bare_table(fk['ref_table'], tables)
        cols = fk.get('columns', [fk['column']])
        ref_cols = fk.get('ref_columns', [fk['ref_column']])
        add({'pairs': [(ft + '.' + a, tt + '.' + b) for a, b in zip(cols, ref_cols)]}, 'fk')
    for spec in relationship_hints:
        add(spec, 'hint')
    for spec in logical_specs:
        add(spec, 'configured')

    cards, nodes = [], []
    for name, table in tables.items():
        fields = []
        for col in table['columns']:
            key = name + '.' + col['name']
            targets = fk_targets.get(key, [])
            meta = {'col': col['name'], 'type': col['type'], 'nullable': col['nullable'],
                    'comment': col.get('comment', ''), 'pk': col['name'] in primary[name],
                    'fk': bool(targets), 'fk_target': targets[0] if targets else ''}
            fields.append(meta)
            nodes.append({'key': key, 'table': name, **meta})
        cards.append({'table': name, 'comment': table.get('comment', ''),
                      'rows': table.get('est_rows', 0), 'fields': fields,
                      'n_pk': len(primary[name])})
    return {'database': schema.get('database'),
            'fingerprint': schema.get('fingerprint'),
            'refreshed_at': schema.get('refreshed_at'),
            'field_nodes': nodes, 'cards': cards,
            'links': links, 'logical_links': logical, 'links_all': links + logical,
            'stats': {'tables': len(tables), 'fields': len(nodes), 'fks': len(links),
                      'relationship_hints': hints, 'logical_links': len(logical) - hints,
                      'skipped_relationship_hints': skipped_hints,
                      'skipped_logical_links': skipped}}


def save_snapshot(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=path.parent,
                                         prefix=path.name + '.', suffix='.tmp',
                                         delete=False) as output:
            temporary = output.name
            json.dump(value, output, ensure_ascii=False, indent=1, default=str)
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)
