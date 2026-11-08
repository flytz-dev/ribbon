
"""Indicadores de Business Intelligence do R.I.B.B.O.N."""

from collections import Counter

from fastapi import APIRouter, Depends, Query

from api import auth
from core import storage

router = APIRouter(prefix="/api/v1/bi", tags=["Business Intelligence"])


@router.get("/overview")
def overview(
    scope: str = Query("all", pattern="^(all|mine)$"),
    days: int = Query(30, ge=0, le=36500),
    username: str = Depends(auth.current_user),
):
    """Dados agregados para o painel de BI.

    scope=all: todos os usuários reais.
    scope=mine: somente o usuário autenticado.
    days=0: histórico completo.
    """
    conditions = [
        "COALESCE((SELECT u.simulated FROM app_user u "
        "WHERE u.username = a.owner), 0) = 0"
    ]
    params = []

    if scope == "mine":
        conditions.append("a.owner = ?")
        params.append(username)

    if days:
        conditions.append(
            "datetime(a.created_at) >= datetime('now', ?)"
        )
        params.append(f"-{days} days")

    where = " AND ".join(conditions)

    with storage._connection() as conn:
        audits = [
            dict(row)
            for row in conn.execute(
                f"""SELECT a.job_id, a.created_at, a.owner,
                           a.status, a.dpi, a.profile,
                           a.duration_seconds
                    FROM audit a
                    WHERE {where}
                    ORDER BY a.created_at DESC, a.rowid DESC""",
                params,
            )
        ]

        pages = [
            dict(row)
            for row in conn.execute(
                f"""SELECT p.overall_status, p.review, p.category,
                           p.ocr_ms, p.render_ms,
                           p.code_confidence, p.amount_confidence,
                           p.strategies_run
                    FROM page p
                    JOIN audit a ON a.job_id = p.job_id
                    WHERE {where}""",
                params,
            )
        ]

        missing = conn.execute(
            f"""SELECT COUNT(*)
                FROM missing_slip m
                JOIN audit a ON a.job_id = m.job_id
                WHERE {where}""",
            params,
        ).fetchone()[0]

    def average(values):
        numbers = [float(v) for v in values if v is not None]
        return round(sum(numbers) / len(numbers), 2) if numbers else None

    processed = len(pages)
    conforming = sum(p["overall_status"] == "OK" for p in pages)
    flagged = sum(p["overall_status"] == "ERROR" for p in pages)
    real = sum(
        p["overall_status"] == "ERROR" and p["review"] == "DOCUMENT"
        for p in pages
    )
    ocr_errors = sum(
        p["overall_status"] == "ERROR" and p["review"] == "OCR"
        for p in pages
    )
    pending = sum(
        p["overall_status"] == "ERROR" and not p["review"]
        for p in pages
    )

    by_date = Counter(
        a["created_at"][:10] for a in audits if a["created_at"]
    )
    by_profile = Counter(
        a["profile"] or "Padrão" for a in audits
    )
    by_dpi = Counter(
        str(a["dpi"]) if a["dpi"] is not None else "Não informado"
        for a in audits
    )
    by_category = Counter(
        p["category"] or "Não categorizada"
        for p in pages if p["overall_status"] == "ERROR"
    )
    by_user = Counter(
        a["owner"] or "Sem usuário associado" for a in audits
    )

    completed = [
        a for a in audits if a["duration_seconds"] is not None
    ]
    total_duration = sum(
        float(a["duration_seconds"]) for a in completed
    )

    return {
        "scope": scope,
        "period_days": days or None,
        "summary": {
            "audits": len(audits),
            "processed_pages": processed,
            "conforming_pages": conforming,
            "flagged_pages": flagged,
            "conformity_rate": (
                round(100 * conforming / processed, 2)
                if processed else None
            ),
            "document_discrepancies": real,
            "ocr_errors": ocr_errors,
            "pending_review": pending,
            "missing_slips": missing,
            "average_ocr_ms": average(p["ocr_ms"] for p in pages),
            "average_render_ms": average(p["render_ms"] for p in pages),
            "average_code_confidence": average(
                p["code_confidence"] for p in pages
            ),
            "average_amount_confidence": average(
                p["amount_confidence"] for p in pages
            ),
            "consensus_pages": sum(
                (p["strategies_run"] or 0) > 1 for p in pages
            ),
            "wall_seconds_per_page": (
                round(total_duration / processed, 2)
                if completed and processed else None
            ),
        },
        "audits_by_date": [
            {"date": day, "count": count}
            for day, count in sorted(by_date.items())
        ],
        "audits_by_profile": [
            {"name": name, "count": count}
            for name, count in by_profile.most_common()
        ],
        "audits_by_dpi": [
            {"dpi": dpi, "count": count}
            for dpi, count in by_dpi.most_common()
        ],
        "discrepancies_by_category": [
            {"name": name, "count": count}
            for name, count in by_category.most_common()
        ],
        "audits_by_user": [
            {"name": name, "count": count}
            for name, count in by_user.most_common()
        ] if scope == "all" else [],
    }
