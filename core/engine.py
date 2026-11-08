"""RIBBON OCR engine.

The CLI and API share the same reference extraction, consensus, and audit stream.
The engine is independent of persistence and HTTP interfaces."""

import base64
import io
import os
import re
import shutil
import threading
import time
from collections import Counter, deque
from concurrent.futures import ThreadPoolExecutor

import pdf2image
import pypdf
import pytesseract
from PIL import ImageDraw, ImageEnhance, ImageFilter, ImageOps

from core.profiles import DEFAULT_PROFILE

# ==========================================
# External tool and input paths
# ==========================================
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAYMENT_SLIPS_PATH = os.path.join(BASE_DIR, 'docs', 'payment_slips.pdf')
REFERENCE_PDF_PATH = os.path.join(BASE_DIR, 'docs', 'reference.pdf')

# Default Windows installer locations, used only when nothing better is available.
_WINDOWS_TESSERACT = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
_WINDOWS_POPPLER = r"C:\poppler\Library\bin"


def default_tesseract_path() -> str:
    """Prefer TESSERACT_CMD, then PATH, then the default Windows installer location."""
    configured = os.environ.get('TESSERACT_CMD')
    if configured:
        return configured
    found = shutil.which('tesseract')
    if found:
        return found
    return _WINDOWS_TESSERACT if os.path.isfile(_WINDOWS_TESSERACT) else 'tesseract'


def default_poppler_path() -> str:
    """Prefer POPPLER_PATH; an empty value lets pdf2image search PATH."""
    configured = os.environ.get('POPPLER_PATH')
    if configured:
        return configured
    if shutil.which('pdftoppm'):
        return ''
    return _WINDOWS_POPPLER if os.path.isdir(_WINDOWS_POPPLER) else ''


TESSERACT_PATH = default_tesseract_path()
POPPLER_PATH = default_poppler_path()

DEFAULT_DPI = 500
# Whole-page Tesseract options of the built-in profile; each profile builds its own.
TESSERACT_CONFIG = DEFAULT_PROFILE.tesseract_config(DEFAULT_PROFILE.page_allowed)

# Pages are read in parallel by a pool shared across audits, so concurrent users
# divide the same CPU budget instead of each starting their own workers.
# PYCONFER_OCR_WORKERS, from the project's first name, is still accepted.
OCR_WORKERS = max(1, int(
    os.environ.get('RIBBON_OCR_WORKERS') or os.environ.get('PYCONFER_OCR_WORKERS')
    or min(4, max(1, (os.cpu_count() or 2) // 2))
))

# Each Tesseract process would otherwise start one OpenMP thread per core, which
# oversubscribes the CPU once several pages are read at the same time.
os.environ.setdefault('OMP_THREAD_LIMIT', '1')


def configure_paths(tess_path: str, poppler_bin: str):
    """Configure external tools, falling back to executables on PATH when needed."""
    global TESSERACT_PATH, POPPLER_PATH
    TESSERACT_PATH = tess_path
    POPPLER_PATH = poppler_bin
    pytesseract.pytesseract.tesseract_cmd = tess_path if os.path.isfile(tess_path) else 'tesseract'
    if POPPLER_PATH and os.path.isdir(POPPLER_PATH) and POPPLER_PATH not in os.environ.get("PATH", ""):
        os.environ["PATH"] += os.pathsep + POPPLER_PATH


# Configure tools at import time for both API and CLI execution.
configure_paths(TESSERACT_PATH, POPPLER_PATH)


# ==========================================
# Field extraction and comparison
# ==========================================

def extract_reference_list(reference_path, profile=None):
    """Read reference codes and amounts, pairing them as the document profile says."""
    profile = profile or DEFAULT_PROFILE
    print(f"Reading codes and amounts from the reference PDF (profile: {profile.id})...")
    entries = []
    try:
        reader = pypdf.PdfReader(reference_path)
        for page in reader.pages:
            text = page.extract_text()
            if not text: continue
            entries.extend(profile.reference_entries(text))
    except Exception as e:
        print(f"Failed to read reference PDF: {e}")
    return entries


def code_status(read_code: str, expected_code: str, profile=None):
    """Compare canonical registration codes exactly.

    Digit substitutions can identify a different real registration, so OCR ambiguity
    must remain visible for human review instead of being silently accepted."""
    profile = profile or DEFAULT_PROFILE
    read = profile.comparable(read_code)
    return "OK" if read and read == profile.comparable(expected_code) else "MISMATCH"


def normalize_amount(extracted_text: str, profile=None):
    """Normalize an OCR amount to the report format, 1.234,56, or return None."""
    return (profile or DEFAULT_PROFILE).normalize_amount(extracted_text)


def _confidence(value):
    """Convert a Tesseract word confidence (0-100, or -1 for non-words) to an integer."""
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return None
    return round(confidence) if confidence >= 0 else None


def _word_box(data, k, offset):
    dx, dy = offset
    return (data["left"][k] + dx, data["top"][k] + dy,
            data["left"][k] + data["width"][k] + dx, data["top"][k] + data["height"][k] + dy)


def _union(boxes):
    return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))


def _ocr_lines(data):
    """Group OCR words into lines, in reading order: [(text, [(word index, start, end)])].

    Words are grouped by where they are on the image, not by Tesseract's block numbers:
    in sparse mode (psm 11) each piece of 474.780,65 can come back as its own block.
    Pieces split at a separator are joined without a space, so the amount is whole again."""
    words = []
    for k, word in enumerate(data.get("text", [])):
        word = (word or "").strip()
        if word:
            top, height = data["top"][k], max(1, data["height"][k])
            words.append((k, word, data["left"][k], top + height / 2, height))

    lines = []  # each: [center, height, [(k, word, left)]]
    for k, word, left, center, height in sorted(words, key=lambda w: w[3]):
        for line in lines:
            if abs(line[0] - center) <= max(line[1], height) / 2:
                line[2].append((k, word, left))
                break
        else:
            lines.append([center, height, [(k, word, left)]])

    result = []
    for _center, _height, members in lines:
        text, spans = "", []
        for k, word, _left in sorted(members, key=lambda m: m[2]):
            if text and not (text[-1] in ".," or word[0] in ".,"):
                text += " "
            spans.append((k, len(text), len(text) + len(word)))
            text += word
        result.append((text, spans))
    return result


def _find_by_word(data, profile, fields, result, offset):
    """Original RIBBON matching: one OCR word at a time, the first match wins."""
    code_rule = profile.slip_code.rule
    for k, word in enumerate(data.get("text", [])):
        word = (word or "").strip()
        if not word:
            continue
        box = _word_box(data, k, offset)

        if "code" in fields and result["code"] is None:
            matches = list(code_rule.pattern.finditer(word))
            if matches:
                code = next((c for c in map(code_rule.canonical, matches) if c), None)
                if code:
                    result.update(code=code, code_conf=_confidence(data["conf"][k]), code_box=box)
                # A word shaped like a code is never read as an amount.
                continue

        if "amount" in fields and result["amount"] is None:
            amount = profile.normalize_amount(word)
            if amount:
                result.update(amount=amount, amount_conf=_confidence(data["conf"][k]), amount_box=box)


def _find_by_line(data, profile, fields, result, offset):
    """Match whole OCR lines, so a code split into words (54700 - Z) is still found."""
    lines = _ocr_lines(data)
    used = set()
    for field in ("code", "amount"):
        if field not in fields or result[field] is not None:
            continue
        rule = profile.slip_code.rule if field == "code" else profile.slip_amount.rule
        for text, spans in lines:
            searchable = text if field == "code" else text.translate(str.maketrans("‚’`´·", ",,,,."))
            for match in rule.pattern.finditer(searchable):
                value = rule.canonical(match)
                members = [k for k, start, end in spans if start < match.end() and end > match.start()]
                if not value or not members or used.intersection(members):
                    continue
                confidences = [c for c in (_confidence(data["conf"][k]) for k in members) if c is not None]
                result.update({
                    field: value,
                    f"{field}_conf": min(confidences) if confidences else None,
                    f"{field}_box": _union([_word_box(data, k, offset) for k in members]),
                })
                used.update(members)
                break
            if result[field] is not None:
                break


def _find_fields(data, profile, fields, result, offset=(0, 0)):
    finder = _find_by_word if profile.scope == "word" else _find_by_line
    finder(data, profile, fields, result, offset)


# White margin around a cropped field; Tesseract misreads text that touches the image edge.
REGION_PADDING = 24


def _crop_region(img, region):
    """Crop a fractional region with a white margin; returns the crop and the offset
    that maps crop coordinates back onto img."""
    left, top, right, bottom = region
    box = (int(left * img.width), int(top * img.height), int(right * img.width), int(bottom * img.height))
    crop = ImageOps.expand(img.crop(box), border=REGION_PADDING, fill=255)
    return crop, (box[0] - REGION_PADDING, box[1] - REGION_PADDING)


def extract_image_fields(img_param, profile=None):
    """Extract the registration code and amount with Tesseract.

    Fields without a region are read in one whole-page image_to_data call, which also
    returns each word's confidence and location, so previews need no second OCR pass.
    A field with a region is read from that crop alone, with its own allowed characters,
    which keeps other numbers on the page (fees, totals, dates) out of the match."""
    profile = profile or DEFAULT_PROFILE
    result = {
        "code": None, "amount": None,
        "code_conf": None, "amount_conf": None,
        "code_box": None, "amount_box": None,
    }
    slip_fields = {"code": profile.slip_code, "amount": profile.slip_amount}

    page_fields = [name for name, field in slip_fields.items() if field.region is None]
    if page_fields:
        try:
            data = pytesseract.image_to_data(
                img_param, lang='eng', config=profile.tesseract_config(profile.page_allowed),
                output_type=pytesseract.Output.DICT,
            )
        except Exception:
            data = None
        if data:
            _find_fields(data, profile, page_fields, result)

    for name, field in slip_fields.items():
        if field.region is None:
            continue
        # Each attempt runs only when the previous one found nothing, so regions that already
        # read well keep their result. Measured on the samples:
        # 1. the drawn region as one block of text (psm 6): best for a clean field;
        # 2. the same region as sparse text (psm 11): recovers fields next to labels or titles;
        # 3. the region with a margin, as sparse text: recovers fields moved out by a crooked scan.
        attempts = (
            (field.region, False),
            (field.region, True),
            (_widened(field.region, REGION_SEARCH_MARGIN), True),
        )
        for region, sparse in attempts:
            try:
                crop, offset = _crop_region(img_param, region)
                data = pytesseract.image_to_data(
                    crop, lang='eng', config=profile.tesseract_config(field.allowed, sparse=sparse),
                    output_type=pytesseract.Output.DICT,
                )
            except Exception:
                continue
            _find_fields(data, profile, [name], result, offset)
            if result[name] is not None:
                break
    return result


# Second search around a region whose field was not found, as a fraction of the page.
# Measured on crooked samples: it recovers fields moved up to 5% out of the region.
REGION_SEARCH_MARGIN = 0.05


def _widened(region, margin):
    left, top, right, bottom = region
    return (max(0.0, left - margin), max(0.0, top - margin), min(1.0, right + margin), min(1.0, bottom + margin))


# ==========================================
# Image preprocessing and consensus
# ==========================================

# Keep strategies reusable so winning fields can be located for previews.
STRATEGIES = [
    ("1. Default", lambda img: img.point(lambda x: 0 if x < 140 else 255, '1')),
    ("2. Dark", lambda img: img.point(lambda x: 0 if x < 180 else 255, '1')),
    ("3. Sharpness", lambda img: ImageEnhance.Sharpness(img).enhance(2.5).point(lambda x: 0 if x < 160 else 255, '1')),
    ("4. Original", lambda img: img),
    ("5. Zoom 2x", lambda img: img.resize((img.width * 2, img.height * 2)).point(lambda x: 0 if x < 160 else 255, '1')),
    ("6. High Threshold", lambda img: img.point(lambda x: 0 if x < 210 else 255, '1')),
    ("7. Contrast", lambda img: ImageEnhance.Contrast(img).enhance(3.0).convert('1')),
    ("8. Thicken", lambda img: img.filter(ImageFilter.MinFilter(3)).point(lambda x: 0 if x < 140 else 255, '1')),
]

# Sentinel readings representing missing OCR data.
EMPTY_CODE = "0"
EMPTY_AMOUNT = "0,00"


def _read_with_strategy(img_gray, index, profile=None):
    """Apply one strategy and map the field locations back to the original page."""
    processed_image = STRATEGIES[index][1](img_gray)
    fields = extract_image_fields(processed_image, profile)
    factor = img_gray.width / processed_image.width
    for key in ("code_box", "amount_box"):
        if fields.get(key):
            fields[key] = tuple(v * factor for v in fields[key])
    return fields


def vote_readings(readings):
    """Choose the most frequent nonempty code and amount independently.

    readings is a list of (strategy index, fields). Votes count the strategies that
    agree with the winner; confidence averages Tesseract's score across them."""
    result = {
        "strategies_run": len(readings),
        # What every strategy read, so the reviewer can pick a minority reading by hand.
        "readings": [
            {"strategy": STRATEGIES[index][0], "code": fields.get("code") or "", "amount": fields.get("amount") or ""}
            for index, fields in readings
        ],
    }
    for field, empty in (("code", EMPTY_CODE), ("amount", EMPTY_AMOUNT)):
        candidates = [(index, fields) for index, fields in readings if fields.get(field)]
        if not candidates:
            result.update({
                field: empty, f"{field}_votes": 0, f"{field}_strategy": None,
                f"{field}_conf": None, f"{field}_box": None,
            })
            continue

        winner, votes = Counter(fields[field] for _index, fields in candidates).most_common(1)[0]
        supporters = [(index, fields) for index, fields in candidates if fields[field] == winner]
        confidences = [fields[f"{field}_conf"] for _index, fields in supporters
                       if fields.get(f"{field}_conf") is not None]
        source_index, source = supporters[0]
        result.update({
            field: winner,
            f"{field}_votes": votes,
            f"{field}_strategy": source_index,
            f"{field}_conf": round(sum(confidences) / len(confidences)) if confidences else None,
            f"{field}_box": source.get(f"{field}_box"),
        })
    return result


def read_page(img_gray, is_known, profile=None):
    """Read a page, voting across eight image strategies when needed.

    The first strategy is accepted when its code and amount form a pair present in
    the reference list (is_known). Otherwise the remaining strategies run and vote.
    The check does not depend on which slip is expected next, so pages can be read
    in parallel and matched to the reference afterwards, in order."""
    readings = [(0, _read_with_strategy(img_gray, 0, profile))]
    first = readings[0][1]

    if not (first.get("code") and first.get("amount") and is_known(first["code"], first["amount"])):
        for index in range(1, len(STRATEGIES)):
            try:
                readings.append((index, _read_with_strategy(img_gray, index, profile)))
            except Exception:
                pass

    return vote_readings(readings)


# ==========================================
# Matching pages to the reference list
# ==========================================

MATCHED_BY_CODE = "CODE"
MATCHED_BY_AMOUNT = "AMOUNT"
MATCHED_BY_POSITION = "POSITION"
MATCHED_BY_SIMILAR_CODE = "SIMILAR CODE"
MATCHED_DUPLICATE = "DUPLICATE"


def _one_character_apart(first, second):
    """Same length and exactly one differing character: the typical single-digit misread."""
    return len(first) == len(second) and sum(a != b for a, b in zip(first, second)) == 1


class ReferenceMatcher:
    """Pair each slip with a reference entry by registration code, in page order.

    A slip whose code appears in the list is paired with that entry, wherever it is,
    so a missing, extra, or out-of-order slip no longer shifts every following page.
    When the code is unreadable or absent from the list, the slip falls back to the
    next unused entry after the last pairing: the position where it was expected.
    If that entry's amount differs from the slip's while exactly one unused entry has
    the slip's amount, the slip is paired with that entry instead, so a slip after a
    missing one is not compared with the wrong line. Such slips are still flagged,
    because their code did not match. Each entry is used once; entries never paired
    are reported as missing slips.

    A code that belongs to an entry already paired is usually a repeated slip. But one
    misread digit can also turn a slip's code into another code of the same list: then
    the slip's own entry would be reported missing as well, one error shown twice. So
    when exactly one unused entry has the slip's amount and a code that differs from the
    reading in a single character, the slip is paired with it (SIMILAR CODE) and still
    flagged; otherwise it stays a DUPLICATE."""

    def __init__(self, entries, profile=None):
        self.entries = entries
        self.profile = profile or DEFAULT_PROFILE
        self.used = [False] * len(entries)
        self.cursor = 0
        self._by_suffix = {}
        self._pairs = set()
        for index, entry in enumerate(entries):
            suffix = self._suffix(entry["code"])
            if suffix:
                self._by_suffix.setdefault(suffix, []).append(index)
                self._pairs.add((suffix, entry["amount"]))

    def _suffix(self, code):
        """The key codes are matched by: the canonical code, when the profile can compare it."""
        return self.profile.comparable(code)

    def _matchable(self, index):
        return not self.used[index] and bool(self._suffix(self.entries[index]["code"]))

    def is_known(self, code, amount):
        """Whether a code and amount pair appears in the list. Stateless and thread-safe."""
        return (self._suffix(code), amount) in self._pairs

    def _next_unused(self, start):
        for index in range(start, len(self.entries)):
            if self._matchable(index):
                return index
        return None

    def _take(self, index):
        self.used[index] = True
        self.cursor = index + 1

    def _by_amount(self, amount, expected):
        """The single unused entry with this amount, when the expected entry does not have it."""
        if amount in _EMPTY_AMOUNT_READINGS or amount == "N/A":
            return None
        if expected is not None and self.entries[expected]["amount"] == amount:
            return None
        same = [index for index in range(len(self.entries))
                if self._matchable(index) and self.entries[index]["amount"] == amount]
        return same[0] if len(same) == 1 else None

    def _similar_code(self, code, amount):
        """The single unused entry with this amount whose code differs from the reading in one character."""
        read = self._suffix(code)
        if not read or amount in _EMPTY_AMOUNT_READINGS or amount == "N/A":
            return None
        close = [
            index for index in range(len(self.entries))
            if self._matchable(index) and self.entries[index]["amount"] == amount
            and _one_character_apart(read, self._suffix(self.entries[index]["code"]))
        ]
        return close[0] if len(close) == 1 else None

    def match(self, code, amount=None):
        """Return (entry index or None, how it was matched)."""
        candidates = self._by_suffix.get(self._suffix(code), [])
        unused = [index for index in candidates if not self.used[index]]
        if unused:
            # With repeated codes, prefer the next occurrence after the last pairing.
            ahead = [index for index in unused if index >= self.cursor]
            index = ahead[0] if ahead else unused[0]
            self._take(index)
            return index, MATCHED_BY_CODE
        if candidates:
            similar = self._similar_code(code, amount)
            if similar is not None:
                self._take(similar)
                return similar, MATCHED_BY_SIMILAR_CODE
            # Every entry with this code was already paired: the slip is a repeat.
            return candidates[0], MATCHED_DUPLICATE

        index = self._next_unused(self.cursor)
        if index is None:
            index = self._next_unused(0)
        by_amount = self._by_amount(amount, index)
        if by_amount is not None:
            self._take(by_amount)
            return by_amount, MATCHED_BY_AMOUNT
        if index is None:
            return None, MATCHED_BY_POSITION
        self._take(index)
        return index, MATCHED_BY_POSITION

    def missing(self):
        """Entries that no slip was paired with."""
        return [
            {"line": index + 1, "code": entry["code"], "amount": entry["amount"]}
            for index, entry in enumerate(self.entries)
            if self._matchable(index)
        ]


def compare_reading(reading, entry, matched_by, profile=None):
    """Compare a reading with its paired entry and return code, amount, and overall status."""
    if entry is None:
        return "MISMATCH", "MISMATCH", "END OF LIST"

    code_check = code_status(reading["code"], entry["code"], profile)
    amount_check = "OK" if reading["amount"] == entry["amount"] else "MISMATCH"
    mismatch = code_check != "OK" or amount_check != "OK" or matched_by == MATCHED_DUPLICATE
    return code_check, amount_check, "ERROR" if mismatch else "OK"


# ==========================================
# Highlight OCR evidence
# ==========================================

CODE_COLOR = "#2563eb"
AMOUNT_COLOR = "#16a34a"


def _scale_box(box, scale):
    return tuple(int(v * scale) for v in box)


def _pad_box(box, factor=0.45, minimum=4.0):
    """Pad the rectangle so its border does not obscure the text."""
    x0, y0, x1, y1 = box
    margin = max(minimum, (y1 - y0) * factor)
    return (x0 - margin, y0 - margin, x1 + margin, y1 + margin)


def generate_annotated_image(img_gray, result, max_width=1400):
    """Highlight the extracted fields on the original page.

    Field locations come from the OCR reading itself, already mapped back to the
    original page, so the preview costs no additional Tesseract call."""
    highlights = []
    for field_type, color in (("code", CODE_COLOR), ("amount", AMOUNT_COLOR)):
        box = result.get(f"{field_type}_box")
        if box:
            highlights.append((_pad_box(box), color))

    scale = min(1.0, max_width / img_gray.width)
    preview = img_gray.convert("RGB")
    if scale < 1.0:
        preview = preview.resize((int(img_gray.width * scale), int(img_gray.height * scale)))

    drawing = ImageDraw.Draw(preview)
    thickness = max(2, preview.width // 500)
    for box, color in highlights:
        drawing.rectangle(_scale_box(box, scale), outline=color, width=thickness)

    buffer = io.BytesIO()
    preview.save(buffer, format="JPEG", quality=70, optimize=True)
    return {
        "image": "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii"),
        "code_found": any(color == CODE_COLOR for _box, color in highlights),
        "amount_found": any(color == AMOUNT_COLOR for _box, color in highlights),
    }


# ==========================================
# Audit event stream
# ==========================================

REPORT_COLUMNS = [
    "Page", "Reference Line", "Code (Reference PDF)", "Code (OCR Slips)", "Code Status",
    "Amount (Reference PDF)", "Amount (OCR Slips)", "Amount Status", "Overall Status",
    "Category", "Matched By", "Code Confidence", "Amount Confidence",
    "Code Votes", "Amount Votes", "Strategies Run", "Render ms", "OCR ms", "Review",
    "Corrected Code", "Corrected Amount", "Corrected By", "Corrected At",
]

# Page rows also carry each strategy's reading; the CSV leaves it out.
ROW_COLUMNS = REPORT_COLUMNS + ["Readings"]

# Discrepancy categories for human review.
CATEGORY_DUPLICATE = "DUPLICATE"
CATEGORY_LIKELY_MISREAD = "LIKELY MISREAD"
CATEGORY_UNREAD = "UNREAD"
CATEGORY_REVIEW = "REVIEW"
# Produced by releases that matched slips by position only; kept to display old reports.
CATEGORY_OTHER_REGISTRATION = "OTHER REGISTRATION"

# Status of reference entries that no slip was paired with.
STATUS_MISSING_SLIP = "MISSING SLIP"

_EMPTY_CODE_READINGS = {"", EMPTY_CODE, None}
_EMPTY_AMOUNT_READINGS = {"", EMPTY_AMOUNT, None}


def classify_discrepancy(result, matched_by):
    """Classify a mismatch to prioritize human review.

    LIKELY MISREAD covers a code found nowhere in the list while the amount matches
    the slip expected at that position: the code was probably misread. Categories
    cannot prove whether the OCR or the document is wrong; the reviewer decides."""
    if result["overall_status"] != "ERROR":
        return ""

    if matched_by == MATCHED_DUPLICATE:
        return CATEGORY_DUPLICATE

    if result["code_status"] != "OK":
        if result["code"] in _EMPTY_CODE_READINGS:
            return CATEGORY_UNREAD
        fallback = (MATCHED_BY_POSITION, MATCHED_BY_AMOUNT, MATCHED_BY_SIMILAR_CODE)
        if matched_by in fallback and result["amount_status"] == "OK":
            return CATEGORY_LIKELY_MISREAD
        return CATEGORY_REVIEW

    if result["amount"] in _EMPTY_AMOUNT_READINGS:
        return CATEGORY_UNREAD
    return CATEGORY_REVIEW


def missing_report_rows(missing):
    """Represent reference entries without a slip as report rows for CSV export."""
    return [
        {
            "Page": None,
            "Reference Line": entry["line"],
            "Code (Reference PDF)": entry["code"],
            "Amount (Reference PDF)": entry["amount"],
            "Overall Status": STATUS_MISSING_SLIP,
        }
        for entry in missing
    ]


def _poppler_kwargs():
    # Without a configured directory, pdf2image searches PATH.
    return {"poppler_path": POPPLER_PATH} if (POPPLER_PATH and os.path.isdir(POPPLER_PATH)) else {}


_POOL = None
_POOL_LOCK = threading.Lock()


def _ocr_pool():
    global _POOL
    with _POOL_LOCK:
        if _POOL is None:
            _POOL = ThreadPoolExecutor(max_workers=OCR_WORKERS, thread_name_prefix="ribbon-ocr")
        return _POOL


def _process_page(payment_slips_path, number, dpi, is_known, include_image, profile=None):
    """Render and read one page; runs on a pool worker."""
    started = time.perf_counter()
    pages = pdf2image.convert_from_path(
        payment_slips_path, dpi=dpi, first_page=number, last_page=number, **_poppler_kwargs()
    )
    if not pages:
        return None
    rendered = time.perf_counter()

    img_gray = pages[0].convert('L')
    reading = read_page(img_gray, is_known, profile)
    finished = time.perf_counter()

    page = {
        "number": number,
        "reading": reading,
        "render_ms": round((rendered - started) * 1000),
        "ocr_ms": round((finished - rendered) * 1000),
        "preview": None,
    }
    if include_image:
        try:
            page["preview"] = generate_annotated_image(img_gray, reading)
        except Exception as e:
            page["preview"] = {"image_error": str(e)}
    return page


def _process_pages(payment_slips_path, total_pages, dpi, is_known, include_image, profile=None):
    """Yield processed pages in order while pool workers read the following pages."""
    lookahead = OCR_WORKERS * 2
    pending = deque()
    next_page = 1
    try:
        while pending or next_page <= total_pages:
            while next_page <= total_pages and len(pending) < lookahead:
                pending.append(_ocr_pool().submit(
                    _process_page, payment_slips_path, next_page, dpi, is_known, include_image, profile
                ))
                next_page += 1
            yield pending.popleft().result()
    finally:
        # Abandoned audits release queued pages; pages already being read finish and are dropped.
        for future in pending:
            future.cancel()


def stream_audit(payment_slips_path, reference_path, dpi=DEFAULT_DPI, include_image=True, profile=None):
    """Yield start, page, and end events, reading pages in parallel and reporting them in order."""
    profile = profile or DEFAULT_PROFILE
    started = time.perf_counter()
    reference_list = extract_reference_list(reference_path, profile)
    matcher = ReferenceMatcher(reference_list, profile)

    info = pdf2image.pdfinfo_from_path(payment_slips_path, **_poppler_kwargs())
    total_pages = info["Pages"]

    yield {
        "type": "start",
        "total_pages": total_pages,
        "reference_count": len(reference_list),
        "dpi": dpi,
        "workers": OCR_WORKERS,
        "profile": profile.id,
    }

    for page in _process_pages(payment_slips_path, total_pages, dpi, matcher.is_known, include_image, profile):
        if page is None:
            continue
        number = page["number"]
        reading = page["reading"]

        index, matched_by = matcher.match(reading["code"], reading["amount"])
        entry = reference_list[index] if index is not None else None
        code_check, amount_check, overall_status = compare_reading(reading, entry, matched_by, profile)
        result = {**reading, "code_status": code_check, "amount_status": amount_check,
                  "overall_status": overall_status}
        category = classify_discrepancy(result, matched_by)

        expected_code = entry["code"] if entry else "N/A"
        expected_amount = entry["amount"] if entry else "N/A"
        if overall_status == "ERROR":
            print(f"   [MISMATCH/{category}] Page {number} | Expected: {expected_code} - {expected_amount} | Read: {reading['code']} - {reading['amount']}")

        row = {
            "Page": number,
            "Reference Line": index + 1 if index is not None else None,
            "Code (Reference PDF)": expected_code,
            "Code (OCR Slips)": reading["code"],
            "Code Status": code_check,
            "Amount (Reference PDF)": expected_amount,
            "Amount (OCR Slips)": reading["amount"],
            "Amount Status": amount_check,
            "Overall Status": overall_status,
            "Category": category,
            "Matched By": matched_by,
            "Code Confidence": reading.get("code_conf"),
            "Amount Confidence": reading.get("amount_conf"),
            "Code Votes": reading.get("code_votes"),
            "Amount Votes": reading.get("amount_votes"),
            "Strategies Run": reading.get("strategies_run"),
            "Render ms": page["render_ms"],
            "OCR ms": page["ocr_ms"],
            "Review": "",
            "Corrected Code": "",
            "Corrected Amount": "",
            "Corrected By": "",
            "Corrected At": "",
            "Readings": reading.get("readings", []),
        }

        event = {"type": "page", "page": number, "total_pages": total_pages, "row": row}

        if include_image:
            strategy_index = reading.get("code_strategy")
            if strategy_index is None:
                strategy_index = reading.get("amount_strategy")
            event["strategy"] = STRATEGIES[strategy_index][0] if strategy_index is not None else None
            event.update(page["preview"] or {})

        yield event

    yield {
        "type": "end",
        "total_pages": total_pages,
        "model": "Tesseract OCR",
        "elapsed_seconds": round(time.perf_counter() - started, 2),
        "missing": matcher.missing(),
    }
