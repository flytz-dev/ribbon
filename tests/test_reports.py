"""Test the Power BI export and the printable audit report."""

import csv
import io
import zipfile
from datetime import datetime

from core import reports


def _page(page, status="OK", review=None, **extra):
    row = {
        "job_id": "job1", "page": page, "reference_line": page, "reference_code": "113640",
        "ocr_code": "113640", "code_status": "OK", "reference_amount": "1.234,56", "ocr_amount": "1.234,56",
        "amount_status": "OK", "overall_status": status, "category": "", "matched_by": "CODE",
        "code_confidence": 91, "amount_confidence": 88, "code_votes": 1, "amount_votes": 1,
        "strategies_run": 1, "render_ms": 400, "ocr_ms": 600, "review": review,
        "reviewed_by": None, "reviewed_at": None,
    }
    row.update(extra)
    return row


AUDIT = {
    "job_id": "job1", "created_at": "2026-11-07T09:30:00", "status": "completed", "dpi": 300,
    "payment_slips_file": "guias.pdf", "reference_file": "lista.pdf", "owner": "ana",
    "duration_seconds": 12.0, "profile": None, "missing_slips": 1,
}

TABLES = {
    "audits": [AUDIT],
    "pages": [
        _page(1),
        _page(2, "ERROR", "DOCUMENT", ocr_amount="1.284,56", amount_status="MISMATCH", category="REVIEW",
              reviewed_by="ana", reviewed_at="2026-11-07T10:00:00"),
        _page(3, "ERROR", None, ocr_code="", code_status="MISMATCH", category="UNREAD", strategies_run=8),
    ],
    "missing": [{"job_id": "job1", "reference_line": 4, "code": "118870", "amount": "76,82"}],
}


def _read(text):
    assert text.startswith("﻿")
    return list(csv.DictReader(io.StringIO(text[1:]), delimiter=";"))


def test_amounts_become_numbers_with_a_decimal_comma():
    assert reports.amount_number("1.234,56") == "1234,56"
    assert reports.amount_number("76,82") == "76,82"
    assert reports.amount_number("") == ""
    assert reports.amount_number(None) == ""
    assert reports.amount_number("7?,82") == ""


def test_export_has_three_tables_and_a_data_dictionary():
    archive = zipfile.ZipFile(io.BytesIO(reports.export_zip(TABLES, {"padrao": "Guia padrão"})))
    assert sorted(archive.namelist()) == ["LEIA-ME.txt", "auditorias.csv", "lancamentos_sem_guia.csv", "paginas.csv"]

    dictionary = reports.export_files(TABLES, {})["LEIA-ME.txt"]
    for column in reports.PAGE_COLUMNS + reports.AUDIT_COLUMNS + reports.MISSING_COLUMNS:
        assert column in dictionary, column


def test_audit_row_counts_pages_and_verdicts():
    audit = _read(reports.export_files(TABLES, {"padrao": "Guia padrão"})["auditorias.csv"])[0]

    assert audit["data_hora"] == "2026-11-07 09:30:00"
    assert audit["data"] == "2026-11-07"
    assert audit["modelo"] == "Guia padrão"
    assert audit["situacao"] == "Concluída"
    assert (audit["paginas"], audit["conformes"], audit["divergentes"], audit["sem_guia"]) == ("3", "1", "2", "1")
    assert (audit["divergencias_reais"], audit["erros_ocr"], audit["pendentes"]) == ("1", "0", "1")
    assert audit["duracao_segundos"] == "12,0"
    assert audit["segundos_por_pagina"] == "4,00"


def test_page_rows_are_in_portuguese():
    pages = _read(reports.export_files(TABLES, {})["paginas.csv"])

    assert pages[0]["resultado"] == "Conforme"
    assert pages[0]["parecer"] == ""
    assert pages[0]["valor_lista"] == "1234,56"
    assert pages[0]["tempo_total_s"] == "1,00"
    assert pages[1]["parecer"] == "Divergência real"
    assert pages[1]["revisor"] == "ana"
    assert pages[1]["data_parecer"] == "2026-11-07 10:00:00"
    assert pages[1]["valor_confere"] == "Não"
    assert pages[2]["parecer"] == "Pendente"
    assert pages[2]["categoria"] == "Não lido"
    assert pages[2]["usou_consenso"] == "Sim"


def test_missing_slips_are_exported():
    missing = _read(reports.export_files(TABLES, {})["lancamentos_sem_guia.csv"])
    assert missing == [{"id_auditoria": "job1", "linha_lista": "4", "codigo": "118870", "valor": "76,82"}]


def _report(pages, missing):
    return reports.render_audit_report(AUDIT, pages, missing, "Guia padrão", datetime(2026, 11, 7, 11, 0))


def test_report_lists_discrepancies_verdicts_and_missing_slips():
    page = _report(TABLES["pages"], [{"line": 4, "code": "118870", "amount": "76,82"}])

    assert "Relatório de auditoria" in page
    assert "Guia padrão" in page and "300 DPI" in page and "guias.pdf" in page
    assert "Divergência real" in page and "Pendente" in page
    assert "1.284,56" in page
    assert "07/11/2026 10:00" in page
    assert "118870" in page
    assert "2 guia(s) divergente(s) e 1 lançamento(s) sem guia" in page
    assert "1 divergência(s) aguardando parecer" in page
    assert "window.print()" in page


def test_report_of_a_clean_batch_says_so():
    page = _report([_page(1), _page(2)], [])
    assert "Todas as guias conferem com a lista mestre." in page
    assert "Nenhuma divergência." in page


def test_report_escapes_file_names():
    summary = {**AUDIT, "payment_slips_file": "<script>x</script>.pdf"}
    page = reports.render_audit_report(summary, [], [], "Guia padrão", datetime(2026, 11, 7))
    assert "<script>x</script>" not in page


def test_export_and_report_carry_the_correction():
    pages = [_page(1, "ERROR", "OCR", ocr_amount="16,82", amount_status="MISMATCH", category="REVIEW",
                   corrected_amount="1.234,56", corrected_by="ana", corrected_at="2026-11-07T10:30:00")]
    tables = {"audits": [AUDIT], "pages": pages, "missing": []}
    files = reports.export_files(tables, {})

    page = _read(files["paginas.csv"])[0]
    assert (page["valor_corrigido"], page["corrigido_por"], page["data_correcao"]) == (
        "1234,56", "ana", "2026-11-07 10:30:00")
    assert page["codigo_corrigido"] == ""
    assert _read(files["auditorias.csv"])[0]["corrigidas"] == "1"

    html = _report(pages, [])
    assert "corrigido: <span class='mono'>1.234,56</span>" in html
