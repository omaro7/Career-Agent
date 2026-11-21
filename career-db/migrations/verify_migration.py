'''Read-only verification: compare MySQL/PG table rows with deterministic per-row SHA256.
Runs AFTER migrate_mysql.py --apply. No DB writes. Secrets read from environment.
'''
import hashlib, json, os, sys
from datetime import date,datetime
from decimal import Decimal
import pymysql,psycopg
from psycopg import sql
from migrate_mysql import TABLES, required

PKS={t:'template_id' if t in ('resume_template','career_pptx_template_registry') else {
 'tenant':'tenant_id','employee':'employee_id','profile_import':'import_id',
 'employee_education':'education_id','employee_certification':'certification_id',
 'career_project':'project_id','profile_validation_issue':'issue_id',
 'vector_index_outbox':'event_id'}[t] for t in TABLES}

def normalized(v):
    if isinstance(v,(date,datetime)): return v.isoformat(sep=' ') if isinstance(v,datetime) else v.isoformat()
    if isinstance(v,Decimal): return str(v)
    if isinstance(v,bytes): return v.decode('utf8')
    if isinstance(v,str):
        try: return json.loads(v)
        except ValueError: return v.rstrip() if len(v)==36 else v
    if isinstance(v,dict): return {k: normalized(x) for k,x in v.items()}
    if isinstance(v,list): return [normalized(x) for x in v]
    return v

def canonical(row):
    return json.dumps({k:normalized(v) for k,v in row.items()},sort_keys=True,ensure_ascii=False,default=str,separators=(',',':'))

def main():
    my=pymysql.connect(host=required('MYSQL_SOURCE','HOST'),port=int(os.getenv('MYSQL_SOURCE_PORT','3306')),
        user=required('MYSQL_SOURCE','USER'),password=required('MYSQL_SOURCE','PASSWORD'),
        database=required('MYSQL_SOURCE','DB'),cursorclass=pymysql.cursors.DictCursor,charset='utf8mb4')
    pg=psycopg.connect(host=required('POSTGRES_TARGET','HOST'),port=int(os.getenv('POSTGRES_TARGET_PORT','15432')),
        dbname=required('POSTGRES_TARGET','DB'),user=required('POSTGRES_TARGET','USER'),
        password=required('POSTGRES_TARGET','PASSWORD'))
    issues=[]
    try:
        with my.cursor() as mc,pg.cursor() as pc:
            for table in TABLES:
                pk=PKS[table]
                mc.execute(f'SELECT * FROM `{table}` ORDER BY `{pk}`')
                pc.execute(sql.SQL('SELECT * FROM {} ORDER BY {}').format(sql.Identifier(table),sql.Identifier(pk)))
                a=mc.fetchall();b=pc.fetchall(); cols=[d.name for d in pc.description]
                target=[dict(zip(cols,row)) for row in b]
                keys_a={str(row[pk]).strip() for row in a};keys_b={str(row[pk]).strip() for row in target}
                mismatches=[]
                for x,y in zip(a,target):
                    # SQL boolean in MySQL is int 0/1; normalize for field equality
                    for column in ('is_active','is_current','duplicate_suspected'):
                        if column in x: x[column]=bool(x[column])
                    ca,cb=canonical(x),canonical(y)
                    if ca!=cb:
                        mismatches.append(str(x[pk]))
                print(f'{table}: mysql={len(a)} postgres={len(target)} missing={len(keys_a-keys_b)} extra={len(keys_b-keys_a)} row_mismatch={len(mismatches)}')
                if len(a)!=len(target) or keys_a!=keys_b or mismatches: issues.append(table)
    finally: my.close();pg.close()
    if issues:
        print('VERIFY_FAILED:',','.join(issues));sys.exit(1)
    print('VERIFY_PASS: all 10 tables matched')
if __name__=='__main__':main()
