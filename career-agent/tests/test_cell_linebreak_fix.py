"""Regression checks for leftover a:br elements in PowerPoint template cells."""
from copy import deepcopy
from io import BytesIO

from pptx import Presentation
from pptx.oxml.ns import qn
from pptx.util import Inches

from career_pptx import _cell_unchanged_style


def test_old_line_break_removed_and_first_run_style_retained():
    pres = Presentation()
    slide = pres.slides.add_slide(pres.slide_layouts[6])
    table = slide.shapes.add_table(1, 1, Inches(1), Inches(1), Inches(6), Inches(1)).table
    cell = table.cell(0, 0)
    paragraph = cell.text_frame.paragraphs[0]
    paragraph.alignment = 2  # centered, matching client template
    run = paragraph.add_run()
    run.text = 'OLD DATES'
    run.font.bold = True
    paragraph.add_line_break()
    paragraph.add_run().text = '(OLD MONTHS)'
    assert len(paragraph._p.findall(qn('a:br'))) == 1
    _cell_unchanged_style(cell, '2026.12 ~ 2027.12\n(11개월)')
    paragraph = cell.text_frame.paragraphs[0]
    assert len(cell.text_frame.paragraphs) == 1
    assert len(paragraph._p.findall(qn('a:br'))) == 0
    assert len(paragraph.runs) == 1
    assert paragraph.runs[0].font.bold is True
    assert paragraph.alignment == 2
    assert cell.text == '2026.12 ~ 2027.12\n(11개월)'
    buf = BytesIO()
    pres.save(buf)
    reread = Presentation(BytesIO(buf.getvalue()))
    reread_cell = next(s for s in reread.slides[0].shapes if s.has_table).table.cell(0, 0)
    assert reread_cell.text == cell.text
    assert len(reread_cell.text_frame.paragraphs[0]._p.findall(qn('a:br'))) == 0


def test_extra_paragraphs_removed():
    pres = Presentation()
    slide = pres.slides.add_slide(pres.slide_layouts[6])
    cell = slide.shapes.add_table(1, 1, Inches(1), Inches(1), Inches(6), Inches(1)).table.cell(0, 0)
    cell.text_frame.paragraphs[0].text = 'OLD'
    cell.text_frame.add_paragraph().text = ''
    cell.text_frame.add_paragraph().text = ' '
    _cell_unchanged_style(cell, '2026.12 ~ 2027.12\n(11개월)')
    assert len(cell.text_frame.paragraphs) == 1
    assert cell.text == '2026.12 ~ 2027.12\n(11개월)'
