"""
tests/fixtures/README.md
========================
Place a real (or generated) PPTX here:

    tests/fixtures/sample.pptx

The integration test will automatically pick it up.
If missing, the note-extraction test is skipped gracefully.

To generate a minimal test PPTX (requires python-pptx):

    python - <<'EOF'
    from pptx import Presentation
    from pptx.util import Inches
    prs = Presentation()
    blank = prs.slide_layouts[5]
    for i in range(1, 4):
        slide = prs.slides.add_slide(blank)
        tf = slide.shapes.add_textbox(Inches(1), Inches(1), Inches(8), Inches(2))
        tf.text_frame.text = f"Slide {i} title"
        notes = slide.notes_slide.notes_text_frame
        notes.text = f"Speaker notes for slide {i}."
    prs.save("tests/fixtures/sample.pptx")
    print("sample.pptx created")
    EOF
"""
