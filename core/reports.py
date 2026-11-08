"""Documents built from the audit history: a data export for Power BI and a printable report.

Both are in Portuguese, the language of the people who use them. The export is a .zip of
CSV files in the Brazilian format (";" between columns, decimal comma, UTF-8), which Power BI
and Excel in Portuguese read without any conversion. The report is an HTML page laid out for
A4 paper; the browser prints it or saves it as PDF."""

import csv
import html
import io
import re
import zipfile
from datetime import datetime

# ==========================================
# Labels shared by the export and the report
# ==========================================

AUDIT_STATUS = {
    "processing": "Em andamento",
    "completed": "Concluída",
    "error": "Erro",
    "abandoned": "Interrompida",
}

RESULT = {"OK": "Conforme", "ERROR": "Divergente", "END OF LIST": "Fim da lista"}

CATEGORY = {
    "DUPLICATE": "Duplicada",
    "LIKELY MISREAD": "Provável erro de leitura",
    "UNREAD": "Não lido",
    "REVIEW": "Verificar",
    "OTHER REGISTRATION": "Outro cadastro",
}

MATCHED_BY = {"CODE": "Código", "AMOUNT": "Valor", "POSITION": "Posição",
              "SIMILAR CODE": "Código parecido", "DUPLICATE": "Código repetido"}

VERDICT = {"DOCUMENT": "Divergência real", "OCR": "Erro do OCR"}
PENDING = "Pendente"

_AMOUNT = re.compile(r"\d{1,3}(?:\.\d{3})*,\d{2}")


def amount_number(text):
    """'1.234,56' -> '1234,56' (a number Power BI reads in Portuguese); unreadable -> ''."""
    if not text or not _AMOUNT.fullmatch(text):
        return ""
    return text.replace(".", "")


def decimal(value, places=2):
    return "" if value is None else f"{value:.{places}f}".replace(".", ",")


def yes_no(condition):
    return "Sim" if condition else "Não"


def verdict_label(page):
    """The reviewer's verdict; flagged pages without one are pending, matching pages have none."""
    if page.get("review"):
        return VERDICT.get(page["review"], page["review"])
    return PENDING if page.get("overall_status") == "ERROR" else ""


def _datetime(text):
    try:
        return datetime.fromisoformat(text) if text else None
    except ValueError:
        return None


def _iso(text):
    moment = _datetime(text)
    return moment.strftime("%Y-%m-%d %H:%M:%S") if moment else ""


def _br(text):
    moment = _datetime(text)
    return moment.strftime("%d/%m/%Y %H:%M") if moment else ""


def page_counts(pages):
    flagged = [page for page in pages if page.get("overall_status") == "ERROR"]
    return {
        "pages": len(pages),
        "matched": sum(page.get("overall_status") == "OK" for page in pages),
        "flagged": len(flagged),
        "document": sum(page.get("review") == "DOCUMENT" for page in flagged),
        "ocr": sum(page.get("review") == "OCR" for page in flagged),
        "pending": sum(not page.get("review") for page in flagged),
    }


# ==========================================
# Power BI export
# ==========================================

AUDIT_COLUMNS = (
    "id_auditoria", "data_hora", "data", "usuario", "modelo_id", "modelo", "dpi", "arquivo_guias",
    "arquivo_lista", "situacao", "paginas", "conformes", "divergentes", "sem_guia",
    "divergencias_reais", "erros_ocr", "pendentes", "duracao_segundos", "segundos_por_pagina",
)

PAGE_COLUMNS = (
    "id_auditoria", "pagina", "linha_lista", "codigo_lista", "codigo_lido", "codigo_confere",
    "valor_lista", "valor_lido", "valor_confere", "resultado", "categoria", "pareada_por",
    "confianca_codigo", "confianca_valor", "votos_codigo", "votos_valor", "estrategias_executadas",
    "usou_consenso", "tempo_imagem_ms", "tempo_ocr_ms", "tempo_total_s", "parecer", "revisor",
    "data_parecer",
)

MISSING_COLUMNS = ("id_auditoria", "linha_lista", "codigo", "valor")


def _audit_row(audit, pages, profile_names):
    counts = page_counts(pages)
    duration = audit.get("duration_seconds")
    profile_id = audit.get("profile") or "padrao"
    moment = _datetime(audit.get("created_at"))
    return {
        "id_auditoria": audit["job_id"],
        "data_hora": _iso(audit.get("created_at")),
        "data": moment.strftime("%Y-%m-%d") if moment else "",
        "usuario": audit.get("owner") or "",
        "modelo_id": profile_id,
        "modelo": profile_names.get(profile_id, profile_id),
        "dpi": audit.get("dpi") or "",
        "arquivo_guias": audit.get("payment_slips_file") or "",
        "arquivo_lista": audit.get("reference_file") or "",
        "situacao": AUDIT_STATUS.get(audit.get("status"), audit.get("status") or ""),
        "paginas": counts["pages"],
        "conformes": counts["matched"],
        "divergentes": counts["flagged"],
        "sem_guia": audit.get("missing_slips") or 0,
        "divergencias_reais": counts["document"],
        "erros_ocr": counts["ocr"],
        "pendentes": counts["pending"],
        "duracao_segundos": decimal(duration, 1),
        "segundos_por_pagina": decimal(duration / counts["pages"]) if duration and counts["pages"] else "",
    }


def _page_row(page):
    render, ocr = page.get("render_ms"), page.get("ocr_ms")
    total = (render or 0) + ocr if ocr is not None else None
    reviewed = bool(page.get("review"))

    def number(value):
        return "" if value is None else value

    return {
        "id_auditoria": page["job_id"],
        "pagina": page["page"],
        "linha_lista": number(page.get("reference_line")),
        "codigo_lista": page.get("reference_code") or "",
        "codigo_lido": page.get("ocr_code") or "",
        "codigo_confere": yes_no(page.get("code_status") == "OK"),
        "valor_lista": amount_number(page.get("reference_amount")),
        "valor_lido": amount_number(page.get("ocr_amount")),
        "valor_confere": yes_no(page.get("amount_status") == "OK"),
        "resultado": RESULT.get(page.get("overall_status"), page.get("overall_status") or ""),
        "categoria": CATEGORY.get(page.get("category"), page.get("category") or ""),
        "pareada_por": MATCHED_BY.get(page.get("matched_by"), page.get("matched_by") or ""),
        "confianca_codigo": number(page.get("code_confidence")),
        "confianca_valor": number(page.get("amount_confidence")),
        "votos_codigo": number(page.get("code_votes")),
        "votos_valor": number(page.get("amount_votes")),
        "estrategias_executadas": number(page.get("strategies_run")),
        "usou_consenso": yes_no((page.get("strategies_run") or 0) > 1),
        "tempo_imagem_ms": number(render),
        "tempo_ocr_ms": number(ocr),
        "tempo_total_s": decimal(total / 1000) if total is not None else "",
        "parecer": verdict_label(page),
        "revisor": (page.get("reviewed_by") or "") if reviewed else "",
        "data_parecer": _iso(page.get("reviewed_at")) if reviewed else "",
    }


def _missing_row(entry):
    return {
        "id_auditoria": entry["job_id"],
        "linha_lista": entry["reference_line"],
        "codigo": entry.get("code") or "",
        "valor": amount_number(entry.get("amount")),
    }


def _csv(columns, rows):
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns, delimiter=";", lineterminator="\r\n")
    writer.writeheader()
    writer.writerows(rows)
    # The byte-order mark makes Excel open the file as UTF-8, keeping the accents.
    return "﻿" + buffer.getvalue()


def export_files(tables, profile_names):
    """The export's files as {name: text}."""
    pages_by_audit = {}
    for page in tables["pages"]:
        pages_by_audit.setdefault(page["job_id"], []).append(page)
    audits = [_audit_row(audit, pages_by_audit.get(audit["job_id"], []), profile_names)
              for audit in tables["audits"]]
    return {
        "auditorias.csv": _csv(AUDIT_COLUMNS, audits),
        "paginas.csv": _csv(PAGE_COLUMNS, [_page_row(page) for page in tables["pages"]]),
        "lancamentos_sem_guia.csv": _csv(MISSING_COLUMNS, [_missing_row(entry) for entry in tables["missing"]]),
        "LEIA-ME.txt": DATA_DICTIONARY,
    }


def export_zip(tables, profile_names) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, text in export_files(tables, profile_names).items():
            archive.writestr(name, text.encode("utf-8"))
    return buffer.getvalue()


DATA_DICTIONARY = """\
RIBBON - Dados para análise (Power BI ou Excel)
================================================

Arquivos
--------
auditorias.csv            uma linha por auditoria (um lote de guias conferido)
paginas.csv               uma linha por guia lida (página do PDF de guias)
lancamentos_sem_guia.csv  lançamentos da lista mestre para os quais nenhuma guia chegou

Os três se ligam pela coluna id_auditoria. No Power BI, crie as relações:
  auditorias[id_auditoria]  1 ---> *  paginas[id_auditoria]
  auditorias[id_auditoria]  1 ---> *  lancamentos_sem_guia[id_auditoria]

Formato
-------
- Separador de colunas: ponto e vírgula (;). Codificação: UTF-8.
- Números decimais com vírgula (1234,56), sem separador de milhar.
- Datas no formato AAAA-MM-DD HH:MM:SS.
- O Power BI em português lê tudo sem ajuste. Se o Power BI estiver em inglês, em
  "Transformar dados", troque o tipo das colunas decimais com
  "Alterar tipo > Usando localidade > Decimal > Português (Brasil)".
- Contém só as auditorias do usuário que exportou.
- Para atualizar o painel: exporte de novo, substitua os arquivos na mesma pasta e
  clique em "Atualizar" no Power BI.

auditorias.csv
--------------
id_auditoria         identificador da auditoria
data_hora            quando a auditoria começou
data                 só o dia (bom para o eixo de tempo)
usuario              quem rodou a auditoria
modelo_id, modelo    modelo de documento usado (id e nome)
dpi                  resolução usada para transformar cada página em imagem (200, 300 ou 500)
arquivo_guias        nome do PDF de guias enviado
arquivo_lista        nome do PDF da lista mestre enviado
situacao             Concluída, Em andamento, Erro ou Interrompida
paginas              guias lidas
conformes            guias em que código e valor conferem com a lista
divergentes          guias marcadas para revisão
sem_guia             lançamentos da lista sem guia correspondente
divergencias_reais   divergentes em que o revisor confirmou que o documento difere da lista
erros_ocr            divergentes em que o revisor apontou erro de leitura do OCR
pendentes            divergentes ainda sem parecer
duracao_segundos     tempo total da leitura do lote
segundos_por_pagina  duracao_segundos / paginas

paginas.csv
-----------
id_auditoria            auditoria a que a guia pertence
pagina                  número da página no PDF de guias
linha_lista             linha da lista mestre pareada com a guia
codigo_lista            código (matrícula) esperado pela lista
codigo_lido             código lido pelo OCR
codigo_confere          Sim ou Não
valor_lista             valor esperado pela lista (número)
valor_lido              valor lido pelo OCR (número; vazio se não lido)
valor_confere           Sim ou Não
resultado               Conforme, Divergente ou Fim da lista (guia a mais, sem linha na lista)
categoria               só nas divergentes: Duplicada, Provável erro de leitura, Não lido ou Verificar
pareada_por             como a guia foi ligada à linha: Código, Valor, Posição ou Código repetido
confianca_codigo        confiança do Tesseract na leitura do código (0 a 100)
confianca_valor         confiança do Tesseract na leitura do valor (0 a 100)
votos_codigo            quantas estratégias de imagem concordaram no código
votos_valor             quantas estratégias de imagem concordaram no valor
estrategias_executadas  1 quando a primeira leitura bastou; 8 quando precisou do consenso
usou_consenso           Sim quando precisou das 8 estratégias
tempo_imagem_ms         tempo para transformar a página em imagem
tempo_ocr_ms            tempo de leitura do OCR
tempo_total_s           soma dos dois, em segundos
parecer                 Divergência real, Erro do OCR, Pendente (divergente sem parecer) ou vazio (conforme)
revisor                 quem deu o parecer
data_parecer            quando o parecer foi dado

lancamentos_sem_guia.csv
------------------------
id_auditoria  auditoria
linha_lista   linha da lista mestre
codigo        código esperado
valor         valor esperado (número)

Sugestões de medidas (DAX)
--------------------------
Taxa de conformidade = DIVIDE(SUM(auditorias[conformes]), SUM(auditorias[paginas]))
Taxa de erro do OCR  = DIVIDE(SUM(auditorias[erros_ocr]), SUM(auditorias[paginas]))
Guias por dia        = SUM(auditorias[paginas])   (com auditorias[data] no eixo)
Pendências           = SUM(auditorias[pendentes])
"""


# ==========================================
# Printable audit report
# ==========================================

def _e(value):
    return html.escape("" if value is None else str(value))


def _comparison(expected, read, matches):
    if matches:
        return f"<span class='mono'>{_e(read or expected)}</span>"
    return (f"<span class='mono muted'>{_e(expected or '—')}</span> → "
            f"<span class='mono bad'>{_e(read or 'não lido')}</span>")


def _page_rows(pages, with_review):
    rows = []
    for page in pages:
        flagged = page.get("overall_status") == "ERROR"
        cells = [
            f"<td class='num'>{_e(page['page'])}</td>",
            f"<td class='num'>{_e(page.get('reference_line') or '—')}</td>",
            f"<td>{_comparison(page.get('reference_code'), page.get('ocr_code'), page.get('code_status') == 'OK')}</td>",
            f"<td>{_comparison(page.get('reference_amount'), page.get('ocr_amount'), page.get('amount_status') == 'OK')}</td>",
        ]
        if with_review:
            cells += [
                f"<td>{_e(CATEGORY.get(page.get('category'), page.get('category') or ''))}</td>",
                f"<td><strong>{_e(verdict_label(page))}</strong></td>",
                f"<td>{_e(page.get('reviewed_by') or '') if page.get('review') else ''}<br><span class='muted'>{_e(_br(page.get('reviewed_at'))) if page.get('review') else ''}</span></td>",
            ]
        else:
            status = RESULT.get(page.get("overall_status"), page.get("overall_status") or "")
            cells += [
                f"<td><span class='tag {'tag-bad' if flagged else 'tag-ok'}'>{_e(status)}</span></td>",
                f"<td>{_e(MATCHED_BY.get(page.get('matched_by'), ''))}</td>",
            ]
        rows.append(f"<tr>{''.join(cells)}</tr>")
    return "".join(rows)


def render_audit_report(summary, pages, missing, profile_name, generated_at) -> str:
    """The audit as an A4 HTML page: summary, discrepancies with verdicts, missing slips, all pages."""
    counts = page_counts(pages)
    flagged = [page for page in pages if page.get("overall_status") == "ERROR"]
    duration = summary.get("duration_seconds")

    if not flagged and not missing:
        conclusion = "Todas as guias conferem com a lista mestre."
        conclusion_class = "ok"
    else:
        parts = []
        if flagged:
            parts.append(f"{counts['flagged']} guia(s) divergente(s)")
        if missing:
            parts.append(f"{len(missing)} lançamento(s) sem guia")
        conclusion = " e ".join(parts) + "."
        if flagged:
            conclusion += (f" {counts['pending']} divergência(s) aguardando parecer." if counts["pending"]
                           else " Todas as divergências têm parecer.")
        conclusion_class = "bad" if counts["pending"] else "warn"

    details = [
        ("Data", _br(summary.get("created_at"))),
        ("Usuário", summary.get("owner") or "—"),
        ("Modelo de documento", profile_name),
        ("Resolução", f"{summary.get('dpi')} DPI" if summary.get("dpi") else "—"),
        ("PDF das guias", summary.get("payment_slips_file") or "—"),
        ("PDF da lista mestre", summary.get("reference_file") or "—"),
        ("Tempo de leitura", f"{decimal(duration, 1)} s" if duration else "—"),
    ]
    tiles = [
        ("Guias lidas", counts["pages"], ""),
        ("Conformes", counts["matched"], "ok"),
        ("Divergentes", counts["flagged"], "bad" if counts["flagged"] else ""),
        ("Sem guia", len(missing), "bad" if missing else ""),
        ("Divergências reais", counts["document"], ""),
        ("Erros do OCR", counts["ocr"], ""),
        ("Aguardando parecer", counts["pending"], "bad" if counts["pending"] else ""),
    ]

    flagged_section = (
        "<p class='muted'>Nenhuma divergência.</p>" if not flagged else
        "<table><thead><tr><th>Pág.</th><th>Linha</th><th>Código (lista → lido)</th>"
        "<th>Valor (lista → lido)</th><th>Categoria</th><th>Parecer</th><th>Revisor</th></tr></thead>"
        f"<tbody>{_page_rows(flagged, True)}</tbody></table>"
    )
    missing_section = (
        "<p class='muted'>Todos os lançamentos da lista têm guia.</p>" if not missing else
        "<table><thead><tr><th>Linha</th><th>Código</th><th>Valor</th></tr></thead><tbody>"
        + "".join(f"<tr><td class='num'>{_e(entry['line'])}</td><td class='mono'>{_e(entry.get('code'))}</td>"
                  f"<td class='mono'>{_e(entry.get('amount'))}</td></tr>" for entry in missing)
        + "</tbody></table>"
    )

    return f"""<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Relatório da auditoria {_e(summary['job_id'])} — RIBBON</title>
<style>
  @page {{ size: A4; margin: 14mm 12mm; }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; background: #f4f4f4; color: #161616;
         font: 13px/1.45 "IBM Plex Sans", "Segoe UI", Arial, sans-serif; }}
  .sheet {{ max-width: 210mm; margin: 24px auto; background: #fff; padding: 14mm 12mm; }}
  .toolbar {{ max-width: 210mm; margin: 16px auto 0; display: flex; gap: 8px; justify-content: flex-end; }}
  .toolbar button {{ font: inherit; border: 0; padding: 10px 16px; background: #0f62fe; color: #fff; cursor: pointer; }}
  .toolbar button.secondary {{ background: #393939; }}
  header {{ display: flex; justify-content: space-between; align-items: flex-end;
           border-bottom: 2px solid #161616; padding-bottom: 8px; margin-bottom: 16px; }}
  .brand {{ font-size: 12px; letter-spacing: .32em; font-weight: 600; }}
  h1 {{ font-size: 22px; font-weight: 400; margin: 4px 0 0; }}
  h2 {{ font-size: 15px; font-weight: 600; margin: 22px 0 8px; }}
  .id {{ text-align: right; font-size: 12px; color: #525252; }}
  dl {{ display: grid; grid-template-columns: max-content 1fr; gap: 2px 16px; margin: 0; }}
  dt {{ color: #525252; }} dd {{ margin: 0; overflow-wrap: anywhere; }}
  .tiles {{ display: grid; grid-template-columns: repeat(7, 1fr); gap: 1px; background: #e0e0e0;
           border: 1px solid #e0e0e0; margin-top: 16px; }}
  .tile {{ background: #fff; padding: 8px; }}
  .tile span {{ display: block; font-size: 11px; color: #525252; min-height: 2.6em; }}
  .tile strong {{ font-size: 22px; font-weight: 400; }}
  .tile.ok strong {{ color: #198038; }} .tile.bad strong {{ color: #da1e28; }}
  .conclusion {{ margin-top: 12px; padding: 10px 12px; border-left: 4px solid #0f62fe; background: #edf5ff; }}
  .conclusion.ok {{ border-color: #24a148; background: #defbe6; }}
  .conclusion.warn {{ border-color: #f1c21b; background: #fcf4d6; }}
  .conclusion.bad {{ border-color: #da1e28; background: #fff1f1; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 12px; }}
  th {{ text-align: left; background: #e0e0e0; font-weight: 600; padding: 6px 8px; }}
  td {{ padding: 5px 8px; border-bottom: 1px solid #e0e0e0; vertical-align: top; }}
  tr {{ break-inside: avoid; }}
  .num {{ text-align: right; width: 3.5em; }}
  .mono {{ font-family: "IBM Plex Mono", Consolas, monospace; }}
  .muted {{ color: #6f6f6f; }} .bad {{ color: #da1e28; font-weight: 600; }}
  .tag {{ display: inline-block; padding: 0 8px; border-radius: 12px; font-size: 11px; }}
  .tag-ok {{ background: #a7f0ba; color: #0e6027; }} .tag-bad {{ background: #ffd7d9; color: #a2191f; }}
  .signatures {{ display: grid; grid-template-columns: 2fr 1fr; gap: 32px; margin-top: 40px; break-inside: avoid; }}
  .signatures div {{ border-top: 1px solid #161616; padding-top: 4px; font-size: 12px; color: #525252; }}
  footer {{ margin-top: 24px; font-size: 11px; color: #6f6f6f; }}
  @media (max-width: 720px) {{ .tiles {{ grid-template-columns: repeat(2, 1fr); }} .sheet {{ padding: 16px; }} }}
  @media print {{
    body {{ background: #fff; }} .toolbar {{ display: none; }}
    .sheet {{ margin: 0; padding: 0; max-width: none; }}
    * {{ -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
  }}
</style>
</head>
<body>
<div class="toolbar">
  <button type="button" class="secondary" onclick="window.close()">Fechar</button>
  <button type="button" onclick="window.print()">Imprimir ou salvar em PDF</button>
</div>
<main class="sheet">
  <header>
    <div><div class="brand">R.I.B.B.O.N.</div><h1>Relatório de auditoria</h1></div>
    <div class="id">Auditoria<br><span class="mono">{_e(summary['job_id'])}</span></div>
  </header>

  <dl>{''.join(f'<dt>{_e(label)}</dt><dd>{_e(value)}</dd>' for label, value in details)}</dl>

  <div class="tiles">{''.join(f"<div class='tile {cls}'><span>{_e(label)}</span><strong>{value}</strong></div>" for label, value, cls in tiles)}</div>
  <p class="conclusion {conclusion_class}"><strong>Resultado:</strong> {_e(conclusion)}</p>

  <h2>Divergências e pareceres</h2>
  {flagged_section}

  <h2>Lançamentos da lista sem guia</h2>
  {missing_section}

  <h2>Todas as guias</h2>
  <table><thead><tr><th>Pág.</th><th>Linha</th><th>Código</th><th>Valor</th><th>Resultado</th><th>Pareada por</th></tr></thead>
  <tbody>{_page_rows(pages, False)}</tbody></table>

  <div class="signatures"><div>Conferido por</div><div>Data</div></div>

  <footer>Gerado pelo RIBBON em {_e(generated_at.strftime('%d/%m/%Y %H:%M'))}.
  Nenhuma leitura que difere da lista é aceita sem parecer de uma pessoa.</footer>
</main>
</body>
</html>"""
