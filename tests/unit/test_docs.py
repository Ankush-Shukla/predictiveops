"""Keep documentation honest: links resolve, referenced scripts exist, key warnings stay present."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCS = [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md"))]


def test_relative_links_resolve():
    for doc in DOCS:
        for target in re.findall(r"\]\(([^)#\s]+)(?:#[^)]*)?\)", doc.read_text()):
            if target.startswith(("http://", "https://", "mailto:")):
                continue
            assert (doc.parent / target).exists(), f"{doc.name}: broken link {target}"


def test_referenced_scripts_and_files_exist():
    text = "\n".join(d.read_text() for d in DOCS)
    pattern = r"\b((?:scripts|docs|tests/e2e|monitoring|prometheus|docker)/[\w./-]+\.\w+)\b"
    for path in set(re.findall(pattern, text)):
        assert (ROOT / path).exists(), f"referenced but missing: {path}"


def test_readme_keeps_the_honesty_statements():
    readme = (ROOT / "README.md").read_text()
    for must in ("synthetic", "not** evidence", "docker compose up", "Known limitations", "off by default"):
        assert must in readme, must
    assert "docs/evaluation.md" in readme and "requirements_traceability.md" in readme


def test_evaluation_doc_states_scope_and_reproduction():
    ev = (ROOT / "docs" / "evaluation.md").read_text()
    assert "SYNTHETIC" in ev and "scripts/evaluate_robustness.py" in ev and "Cannot:" in ev
