# R.I.B.B.O.N.

**Reconhecimento Inteligente de Boletos Baseado em OCR Numérico** — OCR-assisted verification of financial payment slips.

RIBBON is an undergraduate final-year project (TCC) that compares registration codes and amounts from scanned payment slips (RLC / financial forms) against a reference PDF. It turns a page-by-page manual check into a batch report, with visual evidence showing where each field was read.

The current application is a **local, multi-user prototype** built with Python, FastAPI, SQLite, and plain JavaScript. Source identifiers, documentation, API fields, and report labels are in English; the web interface is in Portuguese. Input documents still follow the original Brazilian registration and monetary formats.

**About the name.** The acronym describes the method: the OCR reads numbers only, accepting digits and decimal separators. It also nods to the ink ribbons that save progress at the typewriters of the *Resident Evil* games, where players collect and read documents as *files*. Here, too, every document read is saved. The project was first called PyConfer; the database file (`pyconfer.db`) and the `PYCONFER_*` environment variables keep that name for compatibility.

## Features

- Sign in with a personal account; each user sees only their own audits.
- Upload a scanned payment-slip PDF and a reference PDF through the web interface.
- Follow results page by page through Server-Sent Events (SSE), with pages read in parallel.
- Inspect annotated previews highlighting the registration code and amount, with Tesseract's confidence and how many image strategies agreed.
- Pair each slip with its reference entry by registration code, so missing, extra, or reordered slips do not shift the rest of the report.
- Compare expected and extracted values, filter discrepancies, and inspect their categories.
- Mark each discrepancy as a real discrepancy (**Divergência real**) or an OCR misread (**Erro do OCR**).
- Review from the keyboard: `J`/`K` move between report rows, `R`/`O` record the verdict, and **Próxima pendente** jumps to the next discrepancy without one. Filter chips narrow the report by category or by pending review, and the digits the OCR read differently are highlighted.
- Start from the **Painel** (dashboard): today's audits and conformance, pages per day for the last week, reviewer verdicts, and processing time per resolution. The interface follows the IBM Carbon Design System and uses IBM Plex when installed, falling back to Segoe UI, so it needs no internet access.
- Compare 200, 300, and 500 DPI in the **Métricas** (metrics) tab: processing time per page, flagged pages, confirmed OCR errors, and confidence.
- Reopen previous audit reports stored in SQLite and download CSV results.
- Run the same verification engine from the command line, and benchmark resolutions against a ground truth.

## How verification works

1. **Read the reference list.** `pypdf` extracts text from the reference PDF and identifies registration codes and amounts.
2. **Render each scanned page.** `pdf2image` and Poppler convert payment slips into images one page at a time. A pool of OCR workers reads several pages at once; results are still reported in page order.
3. **Extract the fields.** Tesseract reads a preprocessed image and returns each word's text, confidence (0–100), and location. If the initial code and amount form a pair present in the reference list, that reading is used immediately.
4. **Build a consensus when needed.** Otherwise the engine runs the remaining seven strategies: a different threshold, sharpening, the original image, 2x zoom, a higher threshold, contrast adjustment, and stroke thickening. The most frequent nonempty code and amount are selected independently. The report records how many strategies agreed and their average Tesseract confidence. Agreement is a useful signal, not a calibrated probability.
5. **Pair, compare, and explain.** The reading is paired with a reference entry (see below), the code suffix is compared exactly, amounts are normalized and compared, and discrepancies are classified. The field locations from the winning strategy are mapped back to the original page for the annotated preview, without another OCR pass.

Code comparison deliberately does not tolerate substitutions such as `6`/`8` or `0`/`9`: different registrations may differ by precisely those digits. A mismatch is presented for human review instead of being silently accepted.

## Matching slips to the reference

Each slip is paired with a reference entry **by its registration code**, wherever that entry is in the list. Each entry is used once.

| Situation | What happens |
|---|---|
| The code is in the list | The slip is compared with that entry (`Matched By` = `CODE`). A swapped or out-of-order slip still matches. |
| A slip is missing from the batch | The following slips still match by code. The unpaired entry is listed under **Lançamentos da consulta sem guia** (reference entries without a slip) and exported as `MISSING SLIP`. |
| The code is unreadable, or is not in the list | The slip is compared with the entry expected at that position: the next unused entry after the previous pairing (`Matched By` = `POSITION`). |
| The code matches an entry already paired | The page is flagged as `DUPLICATE`. |
| Every entry is already paired | The page is reported as `END OF LIST` (an extra slip). |

**When the code is wrong but the amount is right**, the code is either misread by OCR or the slip carries a registration that is not in the list. The slip falls back to the expected position. If its amount matches that entry, the page is still flagged, with the category `LIKELY MISREAD`. The reviewer confirms in the image and records the verdict. The engine never accepts a code it could not read exactly.

A code misread as *another valid registration* in the same batch cannot be detected on its own page. Because each entry is used once, the error still surfaces elsewhere: the real slip for that registration becomes a `DUPLICATE`, or the misread slip's own entry is reported as missing.

## Discrepancy categories

| Report value | Meaning | Suggested review |
|---|---|---|
| `DUPLICATE` | Another page already matched this registration. | Check for a repeated slip, or a code misread as another registration in the batch. |
| `LIKELY MISREAD` | The code is not in the list, but the amount matches the slip expected at this position. | Confirm the code in the image; most likely an OCR error. |
| `UNREAD` | The required field was not extracted. | Inspect scan quality and consider rescanning or changing the DPI. |
| `REVIEW` | A field differs from its reference entry without meeting the categories above. | Compare the highlighted reading with the original slip. |
| `OTHER REGISTRATION` | Produced only by earlier releases, which paired slips by position. | Shown when reopening old reports. |

These categories help prioritize review; they do not prove whether a discrepancy comes from the document or the OCR. That decision is recorded by the reviewer (see [Metrics and review](#metrics-and-review)). Overall report statuses are `OK`, `ERROR` (mismatch), `END OF LIST` (a slip with no reference entry left), and, in CSV exports only, `MISSING SLIP` (a reference entry with no slip).

## Resolution (DPI)

DPI (dots per inch) sets how many pixels each PDF page becomes before Tesseract reads it. The verification rules are identical at every resolution; DPI changes only **processing time** and **the detail available to OCR**.

| DPI | Pixels per A4 page | Relative work | Typical use |
|---|---|---|---|
| 200 | ≈ 3.9 million | 1× | Clean, high-contrast scans; quick checks. |
| 300 | ≈ 8.7 million | ≈ 2.3× | Tesseract's recommended resolution for printed text. |
| 500 | ≈ 24 million | ≈ 6× | Small or faint digits, when the scan itself holds that detail. |

Pixel count grows with the square of the DPI, so time and memory grow quickly. Rendering above the resolution at which the paper was scanned adds pixels but no information. Higher DPI can even make OCR worse: characters become larger than the sizes Tesseract handles best, and the consensus filters (thresholds, the 3-pixel stroke thickening, the 2x zoom) work in pixels. Measure on your own documents before choosing a default; see [Measuring accuracy](#measuring-accuracy).

## Metrics and review

Every page records its rendering and OCR time, whether the consensus ran, how many strategies agreed, and Tesseract's confidence for each field. Every audit records its wall-clock duration.

In the report, select a mismatch and mark it as:

- **Divergência real** (real discrepancy): the document differs from the reference. The app caught a genuine problem.
- **Erro do OCR** (OCR misread): the document is correct; OCR read it wrong.

The **Métricas** tab groups audits by resolution and shows processing time per page, wall time per page, pages that needed the consensus, flagged pages split into real discrepancies, OCR errors, and unreviewed pages, the OCR error rate, and average confidence. The OCR error rate counts reviewed pages only, so it is a lower bound while flagged pages remain unreviewed. Metrics cover all users' audits and contain counts only, never document data.

Processing time is measured on the server for each page. Wall time divides the audit duration by its pages; because pages are read in parallel, it is lower than processing time.

## Measuring accuracy

Reviewer verdicts measure errors among *flagged* pages only. An OCR error that happens to produce the expected value would pass unnoticed. To measure accuracy completely, compare readings with a **ground truth**: a semicolon-separated file listing what is actually printed on each slip.

```text
page;code;amount
1;113640;76,82
2;116110;82,30
```

```bash
python -m tools.benchmark --payment-slips slips.pdf --reference reference.pdf --ground-truth truth.csv
```

The benchmark runs the same documents at 200, 300, and 500 DPI (change with `--dpi`) and reports total and per-page time, consensus pages, code and amount accuracy, real discrepancies caught, false alarms caused by OCR, missed discrepancies, and missing slips found. Add `--output summary.csv` to save the table.

Without private documents, `--samples N` generates synthetic slips, a reference PDF, and their ground truth. Each batch includes a changed amount, a missing slip, and two swapped slips. The slips imitate a 300 DPI office scan, with slight skew, blur, speckles, and JPEG compression. They contain no taxpayer data.

```bash
python -m tools.benchmark --samples 20
```

Example run on 20 synthetic slips (19 pages after the missing one; 12-thread CPU, 4 OCR workers):

| DPI | Total | Work per page | Consensus | Code accuracy | Amount accuracy | Caught | False alarms |
|---|---|---|---|---|---|---|---|
| 200 | 4.4 s | 0.49 s | 2/19 | 100% | 100% | 1/1 | 0 |
| 300 | 4.8 s | 0.97 s | 4/19 | 100% | 100% | 1/1 | 0 |
| 500 | 11.8 s | 2.16 s | 6/19 | 100% | 73.7% | 1/1 | 5 |

On these slips, 500 DPI was slower and *less* accurate: enlarged digits were misread (`76,82` as `16,82`). Synthetic slips are cleaner than real scans, so these numbers illustrate the method rather than predict results on the project's documents. Repeat the benchmark with a ground truth for real batches before drawing conclusions. With `RIBBON_OCR_WORKERS=1`, the same 300 DPI run took 16.0 s instead of 4.8 s.

## Handwritten payment slips

RIBBON is designed for **printed** slips and does not support handwritten ones:

- Tesseract's models are trained on printed text, not handwriting.
- The engine looks for the printed 16-digit numeric line; handwritten slips have no equivalent.
- Exact code comparison, which protects against accepting the wrong registration, would flag nearly every handwritten page.

`python -m tools.benchmark --samples 10 --handwritten` renders the fields with a script font and small random offsets. Even this simplified imitation, far easier than real handwriting, produced 33–44% code accuracy and 0–22% amount accuracy, and every page was flagged. Supporting handwriting would require a handwriting-recognition model (for example TrOCR, or a cloud service such as Google Cloud Vision or Azure AI Vision) and a different extraction strategy. This is future work, outside the scope of the current engine.

## Users and sign-in

The interface opens with a sign-in screen. **Criar conta** (create account) registers a new user directly. Usernames have 3–32 letters, digits, dots, hyphens, or underscores; passwords need at least 8 characters.

- Passwords are stored as salted PBKDF2-SHA256 hashes.
- A session is a random token in an `HttpOnly`, `SameSite=Lax` cookie, valid for 12 hours. The database stores only the token's SHA-256 digest, and signing out revokes it.
- Each audit belongs to the user who started it. Other users receive `404 Not Found` for it, as if it did not exist.
- Audits created before accounts existed have no owner and remain visible to every user.

Registration is open to anyone who can reach the server, and there are no roles or password resets. This suits a prototype on a trusted network; a shared deployment needs administrator-managed accounts, HTTPS, and rate limiting.

## Requirements

- **Python 3.10 or later**; compatibility also depends on the pinned packages in [requirements.txt](requirements.txt).
- **Git** to clone the repository.
- **Tesseract OCR**, including its English language data (`eng`).
- **Poppler**, including its PDF information and rendering utilities.

Tesseract and Poppler are external programs, not Python packages. Install them separately. Windows installation resources: [Tesseract](https://github.com/UB-Mannheim/tesseract/wiki) and [Poppler binaries](https://github.com/oschwartz10612/poppler-windows/releases/).

## Installation

```bash
git clone https://github.com/1Flytz/TCC.git
cd TCC
python -m venv .venv
```

Activate the environment using the command for your shell:

| Shell | Command |
|---|---|
| Windows PowerShell | `.\.venv\Scripts\Activate.ps1` |
| Windows Command Prompt | `.venv\Scripts\activate.bat` |
| macOS / Linux | `source .venv/bin/activate` |

```bash
python -m pip install -r requirements.txt
```

Create the virtual environment on your own machine; environments copied from another computer may contain paths to an unavailable Python installation.

## Running on another Windows PC

Three scripts in the project folder install and start RIBBON on Windows without editing code. They need no administrator rights when the tools come from a prepared USB drive. Their messages are in Portuguese.

| Script | Where to run | What it does |
|---|---|---|
| `prepare-usb.bat E:\RIBBON` | A PC where RIBBON already works | Copies the project files (without `.venv`, PDFs, or `pyconfer.db`) and a `vendor\` folder with the Python installer, the Python packages, Tesseract, and Poppler. About 175 MB. |
| `setup.bat` | The other PC, once | Finds or installs Python (per user), creates `.venv`, installs the packages (from `vendor\wheels` when present, otherwise from the internet), finds Tesseract and Poppler, and reads one synthetic slip as a test. Prints the commands to start the server. |
| `start-server.bat` | The other PC, each time | Starts the server on the first free port from 8000 and opens the browser. Add `-Lan` to accept other computers on the network, `-NoBrowser` to skip the browser. |

Without a prepared drive, `setup.bat` installs from the internet, using `winget` for Python, Tesseract, and Poppler when they are missing; the Tesseract installer may ask for administrator rights. The packages in `vendor\wheels` fit only the Python version they were downloaded with, which is why the drive also carries that Python installer.

If the PC's policy blocks PowerShell scripts, run the steps by hand: `python -m venv .venv`, `.\.venv\Scripts\python.exe -m pip install -r requirements.txt`, set `TESSERACT_CMD` and `POPPLER_PATH` (below) to the copies in `vendor\`, then `.\.venv\Scripts\python.exe -m uvicorn api.main:app`. `python -m tools.check_install` verifies an installation from any shell.

## Configuration

| Environment variable | Default | Purpose |
|---|---|---|
| `TESSERACT_CMD` | `tesseract` on `PATH`, else `C:\Program Files\Tesseract-OCR\tesseract.exe` | Tesseract executable. |
| `POPPLER_PATH` | Poppler on `PATH`, else `C:\poppler\Library\bin` if it exists | Directory containing Poppler utilities. |
| `RIBBON_DB` (or `PYCONFER_DB`) | `pyconfer.db` in the project root | SQLite file for audits, users, and sessions. |
| `RIBBON_OCR_WORKERS` (or `PYCONFER_OCR_WORKERS`) | Half the CPU threads, between 1 and 4 | Pages read in parallel, shared by all users' audits. |

For custom Windows locations, set these before starting the application:

```powershell
$env:TESSERACT_CMD = "C:\tools\Tesseract-OCR\tesseract.exe"
$env:POPPLER_PATH = "C:\tools\poppler\Library\bin"
$env:RIBBON_DB = "C:\data\pyconfer.db"
```

Ensure the database's parent directory exists. Tools installed on the system `PATH` are found automatically on Windows, macOS, and Linux; the Windows installer locations are used only as a last resort. If a configured Tesseract file or Poppler directory does not exist, the engine falls back to `PATH`. Use your shell's `export` syntax for custom environment variables on macOS and Linux. The application reads environment variables directly; it does not load `.env` files.

## Using the web application

```bash
python -m uvicorn api.main:app --reload
```

Open [localhost:8000](http://localhost:8000), sign in or create an account, select both PDFs, choose a resolution, and click **Iniciar auditoria** (start audit). The results panel displays progress, active DPI, elapsed time, seconds per page, and matching and mismatching page counts. Select a result to inspect the expected values, OCR confidence, and annotated image, and to record your review. Download the CSV when processing ends.

The default is **500 DPI**. The interface explains what each resolution changes; see [Resolution (DPI)](#resolution-dpi). The appropriate setting depends on scan quality; a higher DPI is not a guarantee of better recognition.

Keep the audit page open while processing. The live stream uses a bounded queue; if it stays full for 60 seconds without consumption, the worker marks the audit as abandoned. Previously saved rows remain in the history.

## Using the command line

```bash
python auditor.py --payment-slips docs/payment_slips.pdf --reference docs/reference.pdf
```

| Option | Purpose | Default |
|---|---|---|
| `--payment-slips` | Scanned payment-slip PDF. | `docs/payment_slips.pdf` in the project root |
| `--reference` | Reference PDF. | `docs/reference.pdf` in the project root |
| `--output` | Output CSV path. | `Final_Report.csv` in the project root |
| `--dpi` | Rendering resolution. | `500` |
| `--tesseract` | Tesseract executable. | Configured engine value |
| `--poppler` | Poppler binary directory. | Configured engine value |

The CLI runs the same engine without annotated previews and writes a semicolon-separated CSV using Latin-1 encoding. Reference entries without a slip are appended as `MISSING SLIP` rows. Web CSV exports also use semicolons, with UTF-8 text. The CLI does not save audits to SQLite; persistence is handled by the API layer.

## Document profiles

Everything that depends on the layout of a slip or a master list lives in a **document profile**; the rest of the engine (rendering, the eight strategies, voting, matching, categories, review, and metrics) is the same for every layout. A profile defines:

| Part | What it sets |
|---|---|
| Master list | The code and amount patterns, and whether codes and amounts are paired **by line** or by their order on each page. |
| Slip fields | The code and amount patterns, the characters Tesseract may return for each, and optionally a **region** of the page (fractions from 0 to 1). |
| Comparison | Which canonical codes can be compared. |

Codes and amounts are reduced to canonical forms before they are compared: `0000000000113640` on a slip and `11364-0` in the list are both `113640`; `123766,01` and `123.766,01` are both `123.766,01`. A field with a region is read from its own crop, so other numbers on the slip (fees, totals, dates) never reach the match.

The built-in profile **`padrao`** reproduces the original rules exactly; benchmarks before and after the change give identical results. Other profiles are JSON files in [profiles/](profiles/), such as [exemplo-codigo-z.json](profiles/exemplo-codigo-z.json) for codes like `54700-Z` and amounts above `1.000,00`, and [guia-municipal.json](profiles/guia-municipal.json), created in the assistant from the code `MAT-80621` and two drawn regions for the municipal practice layout (`python -m tools.seed_demo --skip-audits` writes its sample PDFs, including a crooked copy, to `demo/novo-modelo-municipal/`).

**Creating a profile without writing patterns.** In **Modelos de documento**, the assistant asks for one code copied from the master list (`54700-Z`) and builds the patterns from it: the longest run of digits becomes the compared key, the characters around it locate the code, and on the slip separators become optional and letters also accept the digit OCR confuses them with (`Z` or `2`). A sample master list shows how many entries the profile finds; a sample slip is rendered so the code and amount regions can be drawn with the mouse and tested with the eight strategies before saving. Each audit then picks its profile in the upload form.

The synthetic `codigo-z` samples print a late fee above the amount, so they show why regions matter: read as a whole page, the fee is taken for the amount; with regions, the amount is read correctly.

```bash
python -m tools.benchmark --samples 10 --profile exemplo-codigo-z --style codigo-z
```

## Input format and current limitations

The built-in profile targets the project's reference document layout; other layouts need a profile of their own (see [Document profiles](#document-profiles)).

- The reference PDF must contain extractable text. It is not processed with OCR.
- In the built-in profile, reference codes follow a pattern of four or five digits followed by `-0`; amounts have two or three integer digits (up to 999,99), and codes and amounts are paired by their order within each reference page, so an extra total or a missing value misaligns that page's pairs. Profiles can pair by line instead. Payment slips themselves are matched by code, so their order does not matter (see [Matching slips to the reference](#matching-slips-to-the-reference)).
- Only printed slips are supported; see [Handwritten payment slips](#handwritten-payment-slips).
- In the built-in profile, scanned code extraction expects a 16-digit numeric sequence that becomes a four-to-six-digit code ending in zero after leading zeros are removed.
- Amounts are read in the Brazilian format (comma for decimals). Profiles use regular expressions supplied by the people who configure them; they run on the local server only.
- Missing or inaccurate OCR coordinates can prevent a field from being highlighted, even when a reading is available.

PDFs are excluded from version control because working documents contain taxpayer data. Supply your own PDFs through the interface, or create a local `docs/` folder for CLI inputs. The clone does not include the reference dataset.

## API

Interactive API documentation is available at [localhost:8000/docs](http://localhost:8000/docs) while the server is running. Routes, upload fields, JSON keys, and status values use English names.

| Method | Route | Purpose |
|---|---|---|
| `POST` | `/api/v1/auth/register` | Create an account from JSON `username` and `password`, and sign in. |
| `POST` | `/api/v1/auth/login` | Sign in; sets the session cookie. |
| `POST` | `/api/v1/auth/logout` | Revoke the session. |
| `GET` | `/api/v1/auth/me` | Return the signed-in `username`. |
| `POST` | `/api/v1/audits` | Upload multipart fields `payment_slips` and `reference`; optional `dpi` query parameter (100–600) defaults to 500. Returns `job_id` and `message`. |
| `GET` | `/api/v1/audits` | List the user's saved audits, newest first, with `dpi` and `duration_seconds`; `limit` defaults to 50. |
| `GET` | `/api/v1/audits/{job_id}/events` | Consume the live SSE stream. |
| `GET` | `/api/v1/audits/{job_id}` | Retrieve audit status and counts. |
| `GET` | `/api/v1/audits/{job_id}/pages` | Retrieve report rows as JSON, including historical audits. |
| `GET` | `/api/v1/audits/{job_id}/missing` | Reference entries that no slip was paired with. |
| `PUT` | `/api/v1/audits/{job_id}/pages/{page}/review` | Record a review: JSON `verdict` of `DOCUMENT`, `OCR`, or `null` to clear it. |
| `GET` | `/api/v1/audits/{job_id}/report.csv` | Download the available report rows, then missing slips, as CSV. |
| `PUT` | `/api/v1/audits/{job_id}/pages/{page}/correction` | The reviewer's corrected code and/or amount for a flagged page (picked from any strategy's reading or typed); sets the verdict and keeps the OCR reading. Both null undoes it. |
| `GET` | `/api/v1/audits/{job_id}/report.html` | Printable A4 report (discrepancies, verdicts with reviewer and date, missing slips); the browser saves it as PDF. |
| `GET` | `/api/v1/metrics` | Timing, flagged pages, reviews, and confidence for each resolution. |
| `GET` | `/api/v1/export.zip` | The user's audits, pages, and missing slips as Portuguese CSV files for Power BI or Excel, with a data dictionary (`LEIA-ME.txt`). |
| `GET` | `/api/v1/bi/overview?scope=all\|mine&days=N` | Indicators and chart data for the BI panel on the dashboard (`days=0` for the whole history); counts only, no document data. Charts use Chart.js, shipped in `static/vendor`. |
| `GET` | `/api/v1/profiles` | Document profiles; audits take `?profile=<id>` (default `padrao`). |
| `GET`, `DELETE` | `/api/v1/profiles/{id}` | Read a profile definition, or delete a custom profile. |
| `POST` | `/api/v1/profiles` | Save a profile definition as `profiles/<id>.json`. |
| `POST` | `/api/v1/profiles/suggest` | Build a starting definition from one code as printed in the master list. |
| `POST` | `/api/v1/profiles/reference-preview`, `/api/v1/profiles/slip-preview` | Test a draft profile on a master list or on one slip (all eight strategies). |

Every route except sign-in and registration requires a session. Audits owned by another user return `404`.

Each report row contains the page, the paired `Reference Line`, expected and read values with their statuses, `Category`, `Matched By` (`CODE`, `POSITION`, or `DUPLICATE`), `Code Confidence` and `Amount Confidence` (0–100), `Code Votes` and `Amount Votes` (strategies agreeing), `Strategies Run` (1 or 8), `Render ms`, `OCR ms`, and `Review`.

Each SSE message contains a JSON object with a `type` field:

| `type` | Content |
|---|---|
| `start` | `total_pages`, `reference_count`, `dpi`, and `workers`. |
| `page` | Page number, total page count, and report row in `row`; live previews may include `image`, `strategy`, `code_found`, and `amount_found`. |
| `error` | Error description in `message`. |
| `end` | `total_pages`, `model` (currently `"Tesseract OCR"`), `elapsed_seconds`, and `missing` (reference entries without a slip). |

Audit states are `processing`, `completed`, `error`, and `abandoned`. The browser currently displays resolution from `start.dpi`; it does not display `end.model`. SSE is a live consumption channel, not a persisted event replay or broadcast system.

The API sets no CORS policy: the interface is served from the same origin, and a permissive policy would let other websites use a signed-in user's cookie.

## Persistence and history

SQLite stores audits (`audit`), report rows with timing, confidence, and reviews (`page`), reference entries without a slip (`missing_slip`), accounts (`app_user`), and sessions (`session`). The API saves each processed page, allowing earlier results to survive an interruption or server restart. Old reports can be reopened and exported even after their live in-memory entry is removed. Startup adds the new columns to databases created by earlier releases without changing existing rows.

Annotated images, original uploaded PDFs, and the extracted reference list are not retained in the database. Temporary uploads are removed when the worker exits normally or through its cleanup handler. Historical views therefore contain report data without the live annotated images.

Persisted partial results are not an automatic resume mechanism. An abrupt server shutdown can leave an audit marked as processing; restart recovery is not implemented.

## Upgrading from the Portuguese codebase

This refactor changes the public names used by scripts and API clients. The bundled
web interface uses the new contract. External integrations must update their routes,
upload fields, JSON keys, status comparisons, and CSV column names; the previous
API names and command-line flags are not aliases.

| Previous name | Current name |
|---|---|
| `conferidor.py` | `auditor.py` |
| `core/armazenamento.py` | `core/storage.py` |
| `--boletos`, `--consulta`, `--saida` | `--payment-slips`, `--reference`, `--output` |
| `/api/v1/auditorias` | `/api/v1/audits` |
| `/eventos`, `/paginas`, `/relatorio.csv` | `/events`, `/pages`, `/report.csv` |
| Upload fields `boletos`, `consulta` | `payment_slips`, `reference` |
| Event keys `tipo`, `linha`, `imagem` | `type`, `row`, `image` |
| Event types `inicio`, `pagina`, `erro`, `fim` | `start`, `page`, `error`, `end` |
| `Relatorio_Final_Python.csv` | `Final_Report.csv` |

Before starting the new server against an existing database, stop the old server
and back up the database. Startup migrates the legacy tables, columns, statuses,
and discrepancy categories in one transaction. Existing audit IDs, timestamps,
document filenames, codes, and monetary values are preserved. Repeated startup is
safe; conflicting old and new schemas stop the migration instead of merging or
discarding records. Older versions must use the backup rather than the migrated file.

Legacy Portuguese strings remain in `core/migrations.py` and its tests solely to
identify existing data. Input PDFs are not renamed: select them in the interface or
pass their existing paths explicitly to the CLI. Brazilian decimal separators and
registration-code rules are unchanged by the English translation.

## Tests

```bash
python -m pytest
```

The suite covers comparison rules, reference matching, OCR consensus and confidence, normalization, discrepancy classification, reference parsing, sign-in and data isolation, reviews and metrics, synthetic samples and benchmark scoring, API validation and lifecycle behavior, and SQLite persistence and migrations. Tests use synthetic or mocked inputs and a temporary database; they do not require the private PDFs or an installed OCR toolchain. The test configuration always selects a disposable database, even if `RIBBON_DB` or `PYCONFER_DB` is set.

These tests do not establish OCR accuracy on real scans or validate the full browser workflow. Use `python -m tools.benchmark` with a ground truth for accuracy, and a manual browser run for the interface.

If Node.js is available, run the front-end contract smoke test with
`node --test tests/frontend.test.cjs`. It executes the browser script against a
simulated DOM and event stream without third-party packages. Node.js is optional
for development checks and is not required to run RIBBON.

## Project structure

```text
TCC/
├── core/
│   ├── engine.py          # PDF extraction, OCR, consensus, matching, and visual evidence
│   ├── profiles.py        # Document profiles: patterns, regions, and the built-in layout
│   ├── storage.py         # SQLite audits, users, sessions, reviews, and metrics
│   ├── samples.py         # Synthetic slips and ground truth for demos and benchmarks
│   └── migrations.py      # Upgrade legacy history to the English schema
├── profiles/              # Custom document profiles (JSON), created by the assistant or by hand
├── api/
│   ├── main.py            # REST API, worker lifecycle, SSE, and static files
│   └── auth.py            # Accounts, password hashing, and session cookies
├── tools/
│   ├── benchmark.py       # Compare resolutions and measure accuracy
│   └── check_install.py   # Verify packages, Tesseract, Poppler, and one OCR pass
├── scripts/windows/       # PowerShell behind setup.bat, start-server.bat, prepare-usb.bat
├── static/                # HTML, CSS, and plain JavaScript; no build step
├── tests/                 # Engine, API, persistence, and front-end tests
├── docs/                  # Optional local PDF inputs; not included in the clone
├── auditor.py             # Command-line entry point
├── requirements.txt       # Pinned Python dependencies
├── pytest.ini             # Test configuration
├── CONTRIBUTING.md        # Contribution workflow and team roadmap
└── README.md
```

Both the CLI and API consume the engine's event generator. The API owns persistence and exposes the JSON contract consumed by the browser. The engine does not depend on FastAPI, SQLite, or the front end.

## Contributing and next steps

See [CONTRIBUTING.md](CONTRIBUTING.md) for team responsibilities and the next phase. Sign-in, metrics, and recorded reviews now exist in their first form; shared database infrastructure, roles, and a real load test remain planned.
