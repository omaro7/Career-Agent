"""Read-only, PK-keyed MySQL->PostgreSQL data audit. Exit nonzero on any mismatch.

Uses the Stage 02-4 migration table/column metadata; does not modify either DB.
"""
import argparse
import hashlib
import json
import os
import sys
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path

import pymysql
import psycopg
from psycopg import sql

from migrate_mysql import TABLES, JSON_COLS, BOOL_COLS, required

PKEYS = {
    'tenant': 'tenant_id', 'employee': 'employee_id',
    'profile_import': 'import_id', 'employee_education': 'education_id',
    'employee_certification': 'certification_id', 'career_project': 'project_id',
    'profile_validation_issue': 'issue_id', 'vector_index_outbox': 'event_id',
    'resume_template': 'template_id', 'career_pptx_template_registry': 'template_id',
}


def normalize(v, table, column):
    if v is None:
        return None
    if column in BOOL_COLS.get(table, set()):
        return bool(v)
    if column in JSON_COLS.get(table, set()):
        if isinstance(v, (bytes, bytearray)):
            v = v.decode('utf-8')
        return json.loads(v) if isinstance(v, str) else v
    if isinstance(v, (date, datetime, time)):
        return v.isoformat()
    if isinstance(v, Decimal):
        return format(v, 'f')
    if isinstance(v, (bytes, bytearray, memoryview)):
        return bytes(v).hex()
    if isinstance(v, str) and column.endswith('_id'):
        # PostgreSQL CHAR(n) pads values; MySQL UUID strings are fixed width.
        return v.rstrip(' ')
    return v


def digest(row, table):
    blob = json.dumps({k: normalize(v, table, k) for k, v in row.items()},
                      ensure_ascii=False, sort_keys=True, separators=(',', ':'), default=str)
    return hashlib.sha256(blob.encode('utf-8')).hexdigest()


def compare_rows(source_rows, target_rows, table, sample=10):
    pk = PKEYS[table]
    source = {str(row[pk]).rstrip(' '): digest(row, table) for row in source_rows}
    target = {str(row[pk]).rstrip(' '): digest(row, table) for row in target_rows}
    absent = sorted(source.keys() - target.keys())
    extra = sorted(target.keys() - source.keys())
    changed = sorted(k for k in source.keys() & target.keys() if source[k] != target[k])
    return {'mysql': len(source_rows), 'postgres': len(target_rows),
            'missing_count': len(absent), 'extra_count': len(extra), 'different_count': len(changed),
            'missing_sample': absent[:sample], 'extra_sample': extra[:sample], 'different_sample': changed[:sample],
            'pass': not (absent or extra or changed) and len(source_rows) == len(target_rows)}


def verify():
    my = pymysql.connect(host=required('MYSQL_SOURCE', 'HOST'),
        port=int(os.getenv('MYSQL_SOURCE_PORT', '3306')),
        user=required('MYSQL_SOURCE', 'USER'), password=required('MYSQL_SOURCE', 'PASSWORD'),
        database=required('MYSQL_SOURCE', 'DB'), charset='utf8mb4',
        cursorclass=pymysql.cursors.DictCursor, autocommit=False)
    pg = psycopg.connect(host=required('POSTGRES_TARGET', 'HOST'),
        port=int(os.getenv('POSTGRES_TARGET_PORT', '15432')),
        user=required('POSTGRES_TARGET', 'USER'), password=required('POSTGRES_TARGET', 'PASSWORD'),
        dbname=required('POSTGRES_TARGET', 'DB'), autocommit=False)
    report = {}
    try:
        with my.cursor() as mc, pg.cursor() as pc:
            mc.execute('SET TRANSACTION READ ONLY')
            mc.execute('START TRANSACTION WITH CONSISTENT SNAPSHOT')
            pc.execute('SET TRANSACTION READ ONLY')
            for table in TABLES:
                pk = PKEYS[table]
                mc.execute(f'SELECT * FROM `{table}` ORDER BY `{pk}`')
                source = mc.fetchall()
                pc.execute(sql.SQL('SELECT * FROM {} ORDER BY {}').format(sql.Identifier(table), sql.Identifier(pk)))
                cols = [d.name for d in pc.description]
                target = [dict(zip(cols, row)) for row in pc.fetchall()]
                report[table] = compare_rows(source, target, table)
                x = report[table]
                print(f'{table}: mysql={x["mysql"]} pg={x["postgres"]} missing={x["missing_count"]} extra={x["extra_count"]} different={x["different_count"]} result={"PASS" if x["pass"] else "FAIL"}')
    finally:
        my.rollback()
        pg.rollback()
        my.close()
        pg.close()
    return report


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--report', default='output/migration-stage02/verify-stage02-4-3.json')
    args = p.parse_args()
    result = verify()
    path = Path(args.report)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'tables': result, 'passed': all(x['pass'] for x in result.values())},
        ensure_ascii=False, indent=2), encoding='utf-8')
    print('AUDIT_PASS' if all(x['pass'] for x in result.values()) else 'AUDIT_FAIL', path)
    return 0 if all(x['pass'] for x in result.values()) else 1

if __name__ == '__main__':
    sys.exit(main())
