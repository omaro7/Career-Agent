"""Run after installing API dependencies: python -m unittest test_local_actor.py"""
import os
import unittest
from unittest.mock import patch
from fastapi import HTTPException
from app.routers.career_documents import require_actor


class LocalActorTest(unittest.TestCase):
    def test_disabled_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(HTTPException) as ctx:
                require_actor()
            self.assertEqual(ctx.exception.status_code, 503)

    def test_explicit_single_user_mode(self):
        with patch.dict(os.environ, {'CAREER_ACCESS_MODE': 'local_single_user', 'CAREER_LOCAL_ACTOR': 'local-user'}, clear=True):
            self.assertEqual(require_actor(), 'local-user')

    def test_invalid_actor(self):
        with patch.dict(os.environ, {'CAREER_ACCESS_MODE': 'local_single_user', 'CAREER_LOCAL_ACTOR': ' '}, clear=True):
            with self.assertRaises(HTTPException) as ctx:
                require_actor()
            self.assertEqual(ctx.exception.status_code, 503)


if __name__ == '__main__':
    unittest.main()
