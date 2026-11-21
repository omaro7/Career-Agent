"""Worker contracts consumed by React template management, no private source files required."""
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient
from pptx import Presentation
from pptx.util import Inches
import app


def create_unmapped(path: Path):
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    shape = slide.shapes.add_table(3, 5, Inches(0.5), Inches(1), Inches(9), Inches(2))
    for col, title in enumerate(['사업명', '참여기간', '담당업무', '발주처', '비고']):
        shape.table.cell(0, col).text = title
    shape.table.cell(1, 0).text = 'SECRET_SAMPLE_NAME_ONLY_FOR_TEST'
    prs.save(path)


def test_inspection_lists_unmapped_and_masks_values(monkeypatch):
    with TemporaryDirectory() as d:
        directory = Path(d)
        create_unmapped(directory / 'customer-demo.pptx')
        monkeypatch.setenv('CAREER_PPTX_TEMPLATE_DIR', d)
        client = TestClient(app.app)
        response = client.get('/pptx/templates')
        assert response.status_code == 200
        assert len(response.json()['templates']) == 1
        template = response.json()['templates'][0]
        assert template['templateId'] == 'customer-demo.pptx'
        assert template['status'] == 'NEEDS_MAPPING'
        inspection = client.get('/pptx/templates/customer-demo.pptx/inspect')
        assert inspection.status_code == 200
        assert inspection.json()['slides']
        assert 'SECRET_SAMPLE_NAME_ONLY_FOR_TEST' not in inspection.text
        assert client.get('/pptx/templates/not-in-folder.pptx/inspect').status_code == 404
        assert client.get('/pptx/templates/..%2Fetc%2Fpasswd/inspect').status_code in (404, 422)
