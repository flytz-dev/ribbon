"""RIBBON command-line entry point.

Consume the shared OCR engine and export its report to CSV."""

import argparse
import os

import pandas as pd

from core import engine, profiles


def run_audit(payment_slips_path, reference_path, output_path=None, dpi=engine.DEFAULT_DPI, profile=None):
    output_path = output_path or os.path.join(engine.BASE_DIR, 'Final_Report.csv')
    report, missing = [], []

    print(f"Starting PDF rendering (DPI {dpi}, {engine.OCR_WORKERS} OCR workers)...")
    for event in engine.stream_audit(payment_slips_path, reference_path, dpi=dpi, include_image=False, profile=profile):
        if event["type"] == "page":
            report.append(event["row"])
        elif event["type"] == "end":
            missing = event["missing"]
            print(f"Read {len(report)} pages in {event['elapsed_seconds']:.1f} s.")
            for entry in missing:
                print(f"   [MISSING SLIP] Reference line {entry['line']}: {entry['code']} - {entry['amount']}")

    rows = report + engine.missing_report_rows(missing)
    pd.DataFrame(rows, columns=engine.REPORT_COLUMNS).to_csv(
        output_path, index=False, sep=';', encoding='latin1'
    )
    print(f"Audit completed! '{output_path}' generated.")
    return report


def main():
    parser = argparse.ArgumentParser(description="Compare payment-slip codes and amounts against the reference PDF.")
    parser.add_argument('--payment-slips', default=engine.PAYMENT_SLIPS_PATH, help="PDF containing scanned payment slips.")
    parser.add_argument('--reference', default=engine.REFERENCE_PDF_PATH, help="Reference PDF (master list).")
    parser.add_argument('--output', default=None, help="Output CSV path.")
    parser.add_argument('--dpi', type=int, default=engine.DEFAULT_DPI, help="PDF rendering resolution.")
    parser.add_argument('--tesseract', default=engine.TESSERACT_PATH, help="Tesseract executable.")
    parser.add_argument('--poppler', default=engine.POPPLER_PATH, help="Poppler binary directory.")
    parser.add_argument('--profile', default=profiles.DEFAULT_PROFILE_ID, help="Document profile id.")
    args = parser.parse_args()

    try:
        profile = profiles.get_profile(args.profile)
    except KeyError:
        parser.error(f"unknown profile: {args.profile}")
    engine.configure_paths(args.tesseract, args.poppler)
    run_audit(args.payment_slips, args.reference, args.output, args.dpi, profile)


if __name__ == "__main__":
    main()
