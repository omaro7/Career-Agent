import io, zipfile
import pytest
from fastapi import HTTPException
from app.template_registry import validate_pptx, validate_mapping, checked_path

def fake_pptx():
    output=io.BytesIO()
    with zipfile.ZipFile(output,'w') as arc:
        arc.writestr('ppt/presentation.xml','<presentation/>')
        arc.writestr('[Content_Types].xml','<types/>')
    return output.getvalue()

def test_minimum_package():
    data=fake_pptx()
    if len(data)>=100: validate_pptx(data)

def test_executable_rejected():
    output=io.BytesIO()
    with zipfile.ZipFile(output,'w') as arc:
        arc.writestr('ppt/presentation.xml','<presentation/>')
        arc.writestr('ppt/embeddings/evil.exe','malware')
    with pytest.raises(HTTPException) as exc: validate_pptx(output.getvalue())
    assert exc.value.status_code==422

def test_mapping_object_only():
    assert validate_mapping('{"k":1}') is not None
    with pytest.raises(HTTPException):validate_mapping('[]')

def test_path_rejects_traversal():
    with pytest.raises(HTTPException):checked_path('../secret.pptx')
