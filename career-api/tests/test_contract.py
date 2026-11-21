"""Fast unit checks of API validation contracts (no live DB needed)."""
import pytest
from fastapi import HTTPException
from app.pptx import rate_normalize,limit

def test_rate_normalization():
    assert rate_normalize('70')=='70%'
    assert rate_normalize('70 %')=='70%'
    with pytest.raises(HTTPException): rate_normalize('105')

def test_required_fields():
    with pytest.raises(HTTPException): limit('','고객사',100,True)
