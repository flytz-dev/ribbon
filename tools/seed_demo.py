"""Prepare a demonstration database: real audits of synthetic batches over the past week.

Every audit is produced by the real engine (Tesseract and the eight strategies) on
synthetic slips, so readings, timings, discrepancies, and metrics are genuine. Reviews
of older audits come from the ground truth: when the OCR read what is printed, the
discrepancy is real (DOCUMENT); otherwise it is an OCR error. Today's discrepancies stay
pending, so the review can be shown live.

It also writes two batches for live demonstrations to demo/ (ignored by git).

    python -m tools.seed_demo                    # refuses to touch a database with audits
    python -m tools.seed_demo --force            # adds to it, after a backup in backups/
    python -m tools.seed_demo --user ana --password "senha-de-teste"
    python -m tools.seed_demo --lote-grande 250  # only writes demo/lote-250 (a long recorded audit)

The account and password are for demonstrations only. Never use them for real data."""

import argparse
import os
import re
import shutil
import sys
import tempfile
import time
import uuid
from datetime import datetime, timedelta

from api import auth
from core import engine, profiles, samples, storage

DEMO_USER = "demonstracao"
DEMO_PASSWORD = "ribbon-demo-2026"

# (days ago, time of day, DPI, slips, seed, style). Styles map to document profiles.
SCHEDULE = [
    (6, "09:10", 500, 12, 101, "padrao"),
    (6, "14:30", 300, 10, 102, "padrao"),
    (5, "10:05", 200, 12, 103, "padrao"),
    (4, "11:20", 300, 14, 104, "padrao"),
    (4, "16:45", 500, 8, 105, "padrao"),
    (3, "09:40", 300, 10, 106, "codigo-z"),
    (2, "13:15", 200, 10, 107, "padrao"),
    (1, "10:00", 300, 14, 108, "padrao"),
    (1, "15:30", 500, 10, 109, "padrao"),
    (0, "08:50", 300, 12, 110, "padrao"),
    (0, "09:40", 300, 8, 111, "codigo-z"),
]
STYLE_PROFILE = {"padrao": profiles.DEFAULT_PROFILE_ID, "codigo-z": "exemplo-codigo-z"}
BATCH_NAMES = {"padrao": "guias_lote{n:02d}.pdf", "codigo-z": "guias_modeloZ_lote{n:02d}.pdf"}

DEMO_DIR = os.path.join(engine.BASE_DIR, "demo")


def _has_audits():
    with storage._connection() as connection:
        return connection.execute("SELECT COUNT(*) FROM audit").fetchone()[0] > 0


def _backup():
    if not os.path.isfile(storage.DATABASE_PATH):
        return None
    folder = os.path.join(engine.BASE_DIR, "backups")
    os.makedirs(folder, exist_ok=True)
    target = os.path.join(folder, f"pyconfer-antes-da-demo-{datetime.now():%Y%m%d-%H%M%S}.db")
    shutil.copy2(storage.DATABASE_PATH, target)
    return target


def _ensure_account(username, password):
    if storage.password_hash(username):
        print(f"Account '{username}' already exists; its password is unchanged.")
        return
    auth.create_account(username, password)
    print(f"Account '{username}' created.")


def _verdict(row, truth):
    """DOCUMENT when the OCR read what is printed (so the slip itself differs), else OCR."""
    printed = truth.get(row["Page"])
    if printed is None:
        return None
    read_correctly = row["Code (OCR Slips)"] == printed["code"] and row["Amount (OCR Slips)"] == printed["amount"]
    return storage.REVIEW_DOCUMENT if read_correctly else storage.REVIEW_OCR


def run_audit(number, days_ago, clock, dpi, slips, seed, style, owner, workdir):
    profile = profiles.get_profile(STYLE_PROFILE[style])
    folder = os.path.join(workdir, f"batch{number:02d}")
    dataset = samples.generate_dataset(folder, slips=slips, seed=seed, style=style)
    truth = samples.load_ground_truth(dataset["ground_truth"])

    hour, minute = (int(part) for part in clock.split(":"))
    created = (datetime.now() - timedelta(days=days_ago)).replace(hour=hour, minute=minute, second=0, microsecond=0)
    job_id = uuid.uuid4().hex[:12]
    file_name = BATCH_NAMES[style].format(n=number)
    storage.register_audit(job_id, created.isoformat(timespec="seconds"), dpi, file_name,
                           f"consulta_{created:%d%m}.pdf", owner, profile.id)

    rows, missing, elapsed = [], [], None
    for event in engine.stream_audit(dataset["payment_slips"], dataset["reference"], dpi=dpi,
                                     include_image=False, profile=profile):
        if event["type"] == "start":
            storage.update_status(job_id, "processing", event["total_pages"])
        elif event["type"] == "page":
            rows.append(event["row"])
        elif event["type"] == "end":
            missing, elapsed = event["missing"], event["elapsed_seconds"]

    reviewed = 0
    for row in rows:
        if days_ago > 0 and row["Overall Status"] == "ERROR":
            row["Review"] = _verdict(row, truth) or ""
            reviewed += bool(row["Review"])
        storage.save_page(job_id, row)
        if row["Review"]:
            reviewed_at = created + timedelta(minutes=15 + row["Page"])
            storage.set_review(job_id, row["Page"], row["Review"], owner, reviewed_at.isoformat(timespec="seconds"))
    storage.finish_audit(job_id, elapsed, missing)
    storage.update_status(job_id, "completed")

    flagged = sum(row["Overall Status"] == "ERROR" for row in rows)
    print(f"  {created:%d/%m %H:%M}  {file_name:28} {dpi} DPI  {len(rows):2} páginas  "
          f"{flagged} sinalizadas ({reviewed} com parecer)  {elapsed:.1f} s")


def _in_portuguese(scenario):
    """Describe a sample scenario for the group, who read the notes in Portuguese."""
    patterns = [
        (r"Slip for reference line (\d+) shows a different amount", r"A guia da linha \1 da consulta mostra outro valor (divergência real)"),
        (r"Slip for reference line (\d+) is missing", r"Falta a guia da linha \1 da consulta (lançamento sem guia)"),
        (r"Slips on pages (\d+) and (\d+) are swapped", r"As guias das páginas \1 e \2 estão fora de ordem (pareadas pelo código)"),
    ]
    for pattern, replacement in patterns:
        if re.fullmatch(pattern, scenario):
            return re.sub(pattern, replacement, scenario)
    return scenario


# How each page of guias-tortas.pdf is moved: (right, down) as fractions of the page, and degrees.
CROOKED_PAGES = [(0, -0.05, 0), (0.08, 0, 0), (0, 0.05, 0), (-0.05, 0, 0), (0, 0, 2),
                 (0, 0, -2), (0.05, -0.04, 0), (-0.04, 0.04, 1), (0.06, 0.03, -1.5)]


def write_crooked_copy(source, target):
    """The same slips, shifted and rotated as if placed carelessly on the scanner."""
    from PIL import Image
    pages = engine.pdf2image.convert_from_path(source, dpi=samples.SCAN_DPI, **engine._poppler_kwargs())
    moved = []
    for index, page in enumerate(pages):
        dx, dy, angle = CROOKED_PAGES[index % len(CROOKED_PAGES)]
        page = page.convert("L")
        canvas = Image.new("L", page.size, 255)
        canvas.paste(page, (round(dx * page.width), round(dy * page.height)))
        moved.append(canvas.rotate(angle, resample=Image.BICUBIC, fillcolor=255) if angle else canvas)
    moved[0].save(target, save_all=True, append_images=moved[1:], resolution=samples.SCAN_DPI)


# Portuguese file names, so the master list and the slips are not confused when uploading.
LIVE_FILE_NAMES = {"reference": "lista-mestre.pdf", "payment_slips": "guias.pdf", "ground_truth": "gabarito.csv"}


def write_live_batches():
    """Batches to upload during the presentation, with their scenarios described."""
    batches = [
        # Ten master-list lines each: ten codes and ten amounts to read.
        ("lote-apresentacao", 10, 2026, "padrao", None),
        ("lote-modelo-z", 10, 2027, "codigo-z", None),
        # The shipped Guia municipal profile reads this layout; the batch also serves to create one again in the assistant.
        ("novo-modelo-municipal", 10, 48153, "municipal",
         "modelo: Guia municipal; para treinar, crie outro em Modelos de documento a partir do código MAT-80621"),
    ]
    lines = [
        "Lotes para a demonstração ao vivo (dados sintéticos, sem dados de contribuintes).",
        "Em cada pasta: lista-mestre.pdf (texto, vai no campo da lista mestre),",
        "guias.pdf (guias digitalizadas) e gabarito.csv (o que está impresso em cada guia).",
        "",
    ]
    for name, slips, seed, style, note in batches:
        folder = os.path.join(DEMO_DIR, name)
        shutil.rmtree(folder, ignore_errors=True)
        dataset = samples.generate_dataset(folder, slips=slips, seed=seed, style=style)
        for key, file_name in LIVE_FILE_NAMES.items():
            os.replace(dataset[key], os.path.join(folder, file_name))
        model = note or f"modelo: {profiles.get_profile(STYLE_PROFILE[style]).name}"
        lines.append(f"{name}/  ({model})")
        lines.append(f"  - lista mestre com {slips} linhas; {dataset['pages']} guias")
        if style == "municipal":
            write_crooked_copy(os.path.join(folder, "guias.pdf"), os.path.join(folder, "guias-tortas.pdf"))
            lines.append("  - guias-tortas.pdf: as mesmas guias, deslocadas até 8% e giradas até 2 graus")
        lines.extend(f"  - {_in_portuguese(item)}" for item in dataset["scenario"])
        lines.append("")
    with open(os.path.join(DEMO_DIR, "LEIA-ME.txt"), "w", encoding="utf-8") as file:
        file.write("\n".join(lines))
    print(f"\nLive-demo batches written to {DEMO_DIR}")
    print("\n".join(lines))


LARGE_BATCH_SEED = 2500


def write_large_batch(slips):
    """A long batch for recording a full audit: the same three planned problems, among many slips."""
    name = f"lote-{slips}"
    folder = os.path.join(DEMO_DIR, name)
    shutil.rmtree(folder, ignore_errors=True)
    dataset = samples.generate_dataset(folder, slips=slips, seed=LARGE_BATCH_SEED, style="padrao")
    for key, file_name in LIVE_FILE_NAMES.items():
        os.replace(dataset[key], os.path.join(folder, file_name))
    lines = [
        f"{name}/  (modelo: {profiles.get_profile(STYLE_PROFILE['padrao']).name})",
        f"  - lista mestre com {slips} linhas; {dataset['pages']} guias",
        *(f"  - {_in_portuguese(item)}" for item in dataset["scenario"]),
        "  - outras divergências que aparecerem são leituras do OCR: confira no gabarito.csv",
    ]
    with open(os.path.join(folder, "LEIA-ME.txt"), "w", encoding="utf-8") as file:
        file.write("\n".join(lines) + "\n")
    print("\n".join(lines))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--user", default=DEMO_USER, help="Account that owns the demo audits.")
    parser.add_argument("--password", default=DEMO_PASSWORD, help="Password for a new demo account (8+ characters).")
    parser.add_argument("--force", action="store_true", help="Add to a database that already has audits (backs it up first).")
    parser.add_argument("--skip-audits", action="store_true", help="Only write the live-demo batches.")
    parser.add_argument("--lote-grande", type=int, metavar="GUIAS",
                        help="Only write demo/lote-GUIAS, a long batch for recording a full audit.")
    args = parser.parse_args(argv)

    if args.lote_grande:
        if args.lote_grande < 10:
            parser.error("--lote-grande needs at least 10 slips")
        write_large_batch(args.lote_grande)
        return 0

    storage.create_schema()
    if not args.skip_audits:
        if _has_audits():
            if not args.force:
                parser.error("the database already has audits; use --force to add the demo (a backup is made first)")
            print(f"Backup: {_backup()}")
        _ensure_account(args.user, args.password)

        print(f"Running {len(SCHEDULE)} real audits with {engine.OCR_WORKERS} OCR workers into {storage.DATABASE_PATH}")
        started = time.perf_counter()
        workdir = tempfile.mkdtemp(prefix="ribbon_demo_")
        try:
            for number, item in enumerate(SCHEDULE, start=1):
                run_audit(number, *item, owner=args.user, workdir=workdir)
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
        print(f"Done in {time.perf_counter() - started:.0f} s.")

    write_live_batches()
    return 0


if __name__ == "__main__":
    sys.exit(main())
