from __future__ import annotations
import os
from pathlib import Path
from fastapi.testclient import TestClient
import app as worker_app
from test_career_pptx import data

def test_stage4_endpoint(monkeypatch):
    path = Path(os.getenv('BWG_TEST_PPTX_TEMPLATE', Path(__file__).resolve().parents[2] / 'templates' / 'customer-profile.pptx'))
    if not path.is_file():
        import pytest
        pytest.skip('Client template is not placed in templates/')
    monkeypatch.setattr(worker_app, 'TEMPLATE_PATH', path)
    client = TestClient(worker_app.app)
    response = client.post('/render-customer-pptx', json=data(19))
    assert response.status_code == 200, response.text[:500]
    assert response.headers['x-pptx-page-count'] == '2'
    assert response.headers['x-pptx-project-count'] == '19'
    assert response.content[:2] == b'PK'


def test_reject_empty_project():
    client = TestClient(worker_app.app)
    response = client.post('/render-customer-pptx', json=data(0))
    assert response.status_code in (422, 503)
