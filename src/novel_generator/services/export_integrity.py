"""Source-bound content checks shared by production export and qualification."""
from io import BytesIO
from pathlib import Path


def verify_manuscript_exports(run, chapters, artifact_paths):
    """Compare saved exports with the current manuscript's rendered content.

    Word ZIP metadata and formatting can differ without changing the book.
    Compare ordered paragraph text, including table cells, rather than ZIP bytes.
    Exporter layout/formatting behavior has its own regression coverage.
    """
    from docx import Document
    from docx.oxml.ns import qn
    from .exports import render_markdown, render_docx
    result = {}
    try:
        saved = Path(artifact_paths["markdown"]).read_text(encoding="utf-8-sig")
        result["markdown"] = {"matches_current_source": saved.replace("\r\n", "\n") == render_markdown(run.project, chapters)}
    except (OSError, UnicodeError, KeyError) as error:
        result["markdown"] = {"matches_current_source": False, "error": type(error).__name__}
    try:
        expected = BytesIO()
        render_docx(run.project, chapters, expected)
        expected.seek(0)
        def text_rows(document):
            rows = []
            for paragraph in document.element.body.iter(qn("w:p")):
                parts = []
                for node in paragraph.iter():
                    if node.tag == qn("w:t"):
                        parts.append(node.text or "")
                    elif node.tag == qn("w:tab"):
                        parts.append("\t")
                    elif node.tag == qn("w:cr") or (node.tag == qn("w:br") and node.get(qn("w:type")) in {None, "textWrapping"}):
                        parts.append("\n")
                rows.append("".join(parts))
            return rows
        result["docx"] = {"matches_current_source": text_rows(Document(artifact_paths["docx"])) == text_rows(Document(expected))}
    except Exception as error:
        # Malformed ZIP/XML/Word input is failed qualification, not a crashed
        # report that hides the saved manuscript and other diagnostic evidence.
        result["docx"] = {"matches_current_source": False, "error": type(error).__name__}
    return result

