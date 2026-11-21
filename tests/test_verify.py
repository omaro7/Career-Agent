import importlib.util
import pathlib
import sys
import types
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
FAKE = types.ModuleType('migrate_mysql')
FAKE.TABLES = ['tenant', 'profile_import', 'career_project']
FAKE.JSON_COLS = {'profile_import': {'parsed_json'}}
FAKE.BOOL_COLS = {'career_project': {'is_current'}}
FAKE.required = lambda *args: ''
sys.modules['migrate_mysql'] = FAKE
for n in ('pymysql', 'psycopg'):
    if n not in sys.modules:
        sys.modules[n] = types.ModuleType(n)
sys.modules['psycopg'].sql = types.SimpleNamespace()
spec = importlib.util.spec_from_file_location('verify_migration_v2', ROOT/'career-db'/'migrations'/'verify_migration_v2.py')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

class TestVerify(unittest.TestCase):
    def test_pk_keyed_order_independent(self):
        a=[{'tenant_id':'a','tenant_name':'A'},{'tenant_id':'b','tenant_name':'B'}]
        self.assertTrue(mod.compare_rows(a,list(reversed(a)),'tenant')['pass'])
    def test_string_zeros_not_auto_json(self):
        self.assertEqual(mod.normalize('0001','tenant','tenant_name'),'0001')
    def test_json_typed_only(self):
        self.assertEqual(mod.normalize('{"x":1}','profile_import','parsed_json'),{'x':1})
    def test_boolean_normalization(self):
        a=[{'project_id':'one','is_current':1}]
        b=[{'project_id':'one','is_current':True}]
        self.assertTrue(mod.compare_rows(a,b,'career_project')['pass'])
    def test_detect_changes(self):
        a=[{'tenant_id':'a','tenant_name':'A'}]
        b=[{'tenant_id':'a','tenant_name':'B'}]
        self.assertEqual(mod.compare_rows(a,b,'tenant')['different_count'],1)

if __name__=='__main__': unittest.main()
