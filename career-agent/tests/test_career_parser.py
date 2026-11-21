import unittest
from datetime import date
from career_parser import excel_date, age_on, normalize_text

class ParserDateTest(unittest.TestCase):
    def test_age_after_birthday(self):
        self.assertEqual(age_on(date(2000, 11, 6), date(2026, 11, 16)), 26)
    def test_age_before_birthday(self):
        self.assertEqual(age_on(date(2000, 11, 6), date(2026, 10, 16)), 25)
    def test_date_serial(self):
        self.assertEqual(excel_date(36836), date(2000, 11, 6))
    def test_full_iso(self):
        self.assertEqual(excel_date('2025.05.14'), date(2025, 5, 14))
    def test_no_month_only(self):
        with self.assertRaises(ValueError):
            excel_date('2025.05')
    def test_normalize(self):
        self.assertEqual(normalize_text('홍 길동\n 테스트'), '홍 길동 테스트')

if __name__ == '__main__':
    unittest.main()
