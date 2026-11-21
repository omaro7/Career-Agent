"""Copy data from legacy MySQL 8.4 to NEW PostgreSQL 17, without altering the source.
Safeguards: read-only MySQL transaction, empty-target requirement, single PostgreSQL transaction.
Run ONLY after schema initialization and manual backup. Requires mysqlclient credentials separately.
"""
import argparse
import json
import os
import sys
from datetime import date,datetime
from decimal import Decimal
from pathlib import Path
import pymysql
import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

TABLES = [
 'tenant','employee','profile_import','employee_education','employee_certification',
 'career_project','profile_validation_issue','vector_index_outbox','resume_template',
 'career_pptx_template_registry'
]
JSON_COLS={'profile_import':{'parsed_json'},'resume_template':{'template_config'}}
BOOL_COLS={
 'employee_education':{'is_current'},'employee_certification':{'is_current'},
 'career_project':{'duplicate_suspected','is_current'},
 'resume_template':{'is_active'},'career_pptx_template_registry':{'is_active'}
}


def required(prefix,key):
    var=f'{prefix}_{key}'
    if not os.environ.get(var): raise RuntimeError(f'Missing {var}')
    return os.environ[var]


def run(apply):
    mysql=pymysql.connect(host=required('MYSQL_SOURCE','HOST'),port=int(os.getenv('MYSQL_SOURCE_PORT','3306')),
        user=required('MYSQL_SOURCE','USER'),password=required('MYSQL_SOURCE','PASSWORD'),
        database=required('MYSQL_SOURCE','DB'),charset='utf8mb4',cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,read_timeout=60)
    pg=psycopg.connect(host=required('POSTGRES_TARGET','HOST'),port=int(os.getenv('POSTGRES_TARGET_PORT','15432')),
        dbname=required('POSTGRES_TARGET','DB'),user=required('POSTGRES_TARGET','USER'),
        password=required('POSTGRES_TARGET','PASSWORD'),autocommit=False)
    try:
        with mysql.cursor() as mc:
            mc.execute('SET TRANSACTION READ ONLY')
            mc.execute('START TRANSACTION WITH CONSISTENT SNAPSHOT')
        report={}
        with pg.cursor() as cur, mysql.cursor() as mc:
            # No writes to PG until explicitly --apply.
            for table in TABLES:
                cur.execute(sql.SQL('SELECT COUNT(*) FROM {}').format(sql.Identifier(table)))
                dest=cur.fetchone()[0]
                mc.execute(f'SELECT COUNT(*) AS c FROM `{table}`')
                source=mc.fetchone()['c']
                report[table]={'mysql':source,'postgres_before':dest}
                if dest: raise RuntimeError(f'Destination table {table} is not empty. Aborting without writes.')
            print(json.dumps({'mode':'APPLY' if apply else 'DRY_RUN','counts':report},ensure_ascii=False,indent=2))
            if not apply:
                pg.rollback();mysql.rollback();return
            for table in TABLES:
                mc.execute(f'SELECT * FROM `{table}`')
                batch=mc.fetchmany(200)
                inserted=0
                while batch:
                    keys=list(batch[0].keys())
                    query=sql.SQL('INSERT INTO {} ({}) VALUES ({})').format(
                        sql.Identifier(table),sql.SQL(',').join(map(sql.Identifier,keys)),
                        sql.SQL(',').join(sql.Placeholder() for _ in keys))
                    data=[]
                    for row in batch:
                        vals=[]
                        for k in keys:
                            v=row[k]
                            if v is not None and k in JSON_COLS.get(table,set()):
                                v=Jsonb(json.loads(v) if isinstance(v,(str,bytes,bytearray)) else v)
                            elif v is not None and k in BOOL_COLS.get(table,set()): v=bool(v)
                            vals.append(v)
                        data.append(tuple(vals))
                    cur.executemany(query,data)
                    inserted+=len(data)
                    batch=mc.fetchmany(200)
                cur.execute(sql.SQL('SELECT COUNT(*) FROM {}').format(sql.Identifier(table)))
                after=cur.fetchone()[0]
                if after!=report[table]['mysql'] or after!=inserted:
                    raise RuntimeError(f'Count mismatch in {table}: source={report[table]["mysql"]} target={after}')
                report[table]['postgres_after']=after
            # PostgreSQL commit is atomic; MySQL source never written.
            pg.commit();mysql.rollback()
            print(json.dumps({'result':'COMMITTED','counts':report},ensure_ascii=False,indent=2))
    except Exception:
        pg.rollback();mysql.rollback();raise
    finally:
        pg.close();mysql.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--apply',action='store_true',help='Actually copy data; otherwise verify connections and counts only')
    a=parser.parse_args()
    run(a.apply)
