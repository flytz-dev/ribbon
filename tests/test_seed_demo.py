"""The demo seed labels reviews from the ground truth and refuses to mix with real history."""

import pytest

from core import storage
from tools import seed_demo


def _row(code, amount, page=1):
    return {"Page": page, "Code (OCR Slips)": code, "Amount (OCR Slips)": amount}


def test_reading_what_is_printed_is_a_real_discrepancy():
    truth = {1: {"code": "113640", "amount": "99,99"}}
    assert seed_demo._verdict(_row("113640", "99,99"), truth) == storage.REVIEW_DOCUMENT


def test_reading_something_else_is_an_ocr_error():
    truth = {1: {"code": "113640", "amount": "76,82"}}
    assert seed_demo._verdict(_row("118640", "76,82"), truth) == storage.REVIEW_OCR
    assert seed_demo._verdict(_row("113640", "76,82", page=2), truth) is None


def test_scenarios_are_described_in_portuguese():
    assert seed_demo._in_portuguese("Slip for reference line 3 is missing").startswith("Falta a guia da linha 3")
    assert "páginas 6 e 7" in seed_demo._in_portuguese("Slips on pages 6 and 7 are swapped")


def test_existing_history_is_not_touched_without_force():
    storage.register_audit("real", "2026-11-06T10:00:00", 300, "a.pdf", "b.pdf", owner="admin")
    with pytest.raises(SystemExit):
        seed_demo.main([])


def test_large_batch_writes_only_its_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(seed_demo, "DEMO_DIR", str(tmp_path))

    assert seed_demo.main(["--lote-grande", "12"]) == 0

    folder = tmp_path / "lote-12"
    assert sorted(path.name for path in folder.iterdir()) == ["LEIA-ME.txt", "gabarito.csv", "guias.pdf", "lista-mestre.pdf"]
    assert "lista mestre com 12 linhas; 11 guias" in (folder / "LEIA-ME.txt").read_text(encoding="utf-8")
    assert [path.name for path in tmp_path.iterdir()] == ["lote-12"]
    assert storage.list_audits() == []


def test_large_batch_needs_enough_slips():
    with pytest.raises(SystemExit):
        seed_demo.main(["--lote-grande", "3"])
