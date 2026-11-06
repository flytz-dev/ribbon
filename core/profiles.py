"""Document profiles: how to find the registration code and amount in each layout.

A profile describes one model of payment slip and its master (reference) list:

- which text is a registration code or an amount (regular expressions);
- which characters Tesseract may return for each field;
- optionally, where on the page each field is (a region, as fractions of the page);
- how lines of the master list pair codes with amounts.

Codes and amounts are reduced to canonical forms, so the slip and the list may print the
same value differently: 0000000000113640 on the slip and 11364-0 in the list are both
113640. The built-in profile "padrao" reproduces the original RIBBON rules exactly;
other profiles are JSON files in the profiles/ folder.
"""

import json
import os
import re
from dataclasses import dataclass

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROFILES_DIR = os.environ.get("RIBBON_PROFILES_DIR") or os.path.join(BASE_DIR, "profiles")
DEFAULT_PROFILE_ID = "padrao"

PAIRINGS = ("position", "line")
SCOPES = ("word", "line")

# Separators OCR sometimes returns in place of a comma or a period.
_ODD_SEPARATORS = str.maketrans({"‚": ",", "’": ",", "`": ",", "´": ",", "·": "."})
_ALLOWED_CHARACTERS = re.compile(r"^[0-9A-Za-z.,/:\-]+$")
_PROFILE_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")


class ProfileError(ValueError):
    """A profile definition is incomplete or invalid."""


def _compile(pattern, where):
    if not isinstance(pattern, str) or not pattern:
        raise ProfileError(f"{where}: a regular expression is required.")
    try:
        return re.compile(pattern)
    except re.error as e:
        raise ProfileError(f"{where}: invalid regular expression ({e}).") from e


def _region(value, where):
    """A region is [left, top, right, bottom], each a fraction of the page from 0 to 1."""
    if value is None:
        return None
    if not (isinstance(value, (list, tuple)) and len(value) == 4
            and all(isinstance(v, (int, float)) for v in value)):
        raise ProfileError(f"{where}: region must be [left, top, right, bottom].")
    left, top, right, bottom = (float(v) for v in value)
    if not (0 <= left < right <= 1 and 0 <= top < bottom <= 1):
        raise ProfileError(f"{where}: region values must go from 0 to 1, left < right and top < bottom.")
    return (left, top, right, bottom)


def _allowed(value, where):
    if not isinstance(value, str) or not _ALLOWED_CHARACTERS.match(value):
        raise ProfileError(f"{where}: allowed characters must be letters, digits, or . , / : -")
    return "".join(dict.fromkeys(value))  # drop repeats, keep order


def _span_text(match, group):
    """The text of the named group when the pattern defines it, else the whole match."""
    if group in match.re.groupindex and match.group(group) is not None:
        return match.group(group)
    return match.group(0)


@dataclass(frozen=True)
class CodeRule:
    """How to find a registration code and reduce it to its canonical form."""
    pattern: re.Pattern
    valid: re.Pattern | None = None   # the canonical code must match fully to be accepted
    strip_leading_zeros: bool = False

    def canonical(self, match):
        text = re.sub(r"[^0-9A-Za-z]", "", _span_text(match, "code")).upper()
        if self.strip_leading_zeros:
            text = text.lstrip("0") or "0"
        if not text or (self.valid and not self.valid.fullmatch(text)):
            return None
        return text


@dataclass(frozen=True)
class AmountRule:
    """How to find an amount and write it as 1.234,56."""
    pattern: re.Pattern

    def canonical(self, match):
        text = _span_text(match, "amount")
        separator = re.search(r"[.,](\d{2})$", text)
        if not separator:
            return None
        integer = re.sub(r"\D", "", text[:separator.start()])
        if not integer:
            return None
        # Group thousands without int(), so leading zeros read by OCR stay visible.
        groups = []
        while len(integer) > 3:
            groups.insert(0, integer[-3:])
            integer = integer[:-3]
        groups.insert(0, integer)
        return ".".join(groups) + "," + separator.group(1)


@dataclass(frozen=True)
class SlipField:
    """One field on the slip: its rule, the characters OCR may return, and where it is."""
    rule: CodeRule | AmountRule
    allowed: str
    region: tuple | None = None


@dataclass(frozen=True)
class Profile:
    id: str
    name: str
    description: str
    reference_code: CodeRule
    reference_amount: AmountRule
    pairing: str
    slip_code: SlipField
    slip_amount: SlipField
    scope: str            # "word": match one OCR word at a time; "line": match whole lines
    comparison: re.Pattern | None   # canonical codes outside this pattern never match
    numeric_mode: bool = False
    blacklist: str = ""
    built_in: bool = False

    # ---------- canonical values ----------

    def code_from_text(self, text, rule):
        for match in rule.pattern.finditer(text or ""):
            code = rule.canonical(match)
            if code:
                return code
        return None

    def normalize_amount(self, text):
        """The first amount in text, in canonical form, or None."""
        if not text:
            return None
        normalized = text.translate(_ODD_SEPARATORS)
        for match in self.slip_amount.rule.pattern.finditer(normalized):
            amount = self.slip_amount.rule.canonical(match)
            if amount:
                return amount
        return None

    def comparable(self, code):
        """The key two codes are compared by, or "" when the code cannot be compared."""
        if not code or code in ("0", "N/A"):
            return ""
        if self.comparison and not self.comparison.fullmatch(code):
            return ""
        return code

    # ---------- Tesseract ----------

    def tesseract_config(self, allowed, single_line=False):
        options = [f"--psm {7 if single_line else 6}", f"-c tessedit_char_whitelist={allowed}"]
        if self.numeric_mode and allowed.strip("0123456789.,") == "":
            options.append("-c classify_bln_numeric_mode=1")
        if self.blacklist:
            options.append(f"-c tessedit_char_blacklist={self.blacklist}")
        return " ".join(options)

    @property
    def page_allowed(self):
        """Characters for a whole-page OCR pass: every field that has no region of its own."""
        fields = [f for f in (self.slip_code, self.slip_amount) if f.region is None]
        return "".join(dict.fromkeys("".join(f.allowed for f in fields)))

    # ---------- master list ----------

    def reference_entries(self, page_text):
        """Pair codes with amounts on one page of the master list."""
        if self.pairing == "line":
            return self._entries_by_line(page_text)
        codes = [c for c in (self.reference_code.canonical(m) for m in self.reference_code.pattern.finditer(page_text)) if c]
        amounts = [a for a in (self.reference_amount.canonical(m) for m in self.reference_amount.pattern.finditer(page_text)) if a]
        return [
            {"code": codes[k] if k < len(codes) else "N/A", "amount": amounts[k] if k < len(amounts) else "N/A"}
            for k in range(max(len(codes), len(amounts)))
        ]

    def _entries_by_line(self, page_text):
        entries = []
        for line in page_text.splitlines():
            for match in self.reference_code.pattern.finditer(line):
                code = self.reference_code.canonical(match)
                if not code:
                    continue
                rest = line[:match.start()] + " " + line[match.end():]
                amount = None
                for amount_match in self.reference_amount.pattern.finditer(rest):
                    amount = self.reference_amount.canonical(amount_match)
                    if amount:
                        break
                entries.append({"code": code, "amount": amount or "N/A"})
                break
        return entries

    def summary(self):
        return {"id": self.id, "name": self.name, "description": self.description, "built_in": self.built_in}


# ==========================================
# Building and loading profiles
# ==========================================

def build_profile(data, built_in=False):
    """Validate a profile definition (a dict, as stored in JSON) and build it."""
    if not isinstance(data, dict):
        raise ProfileError("A profile must be a JSON object.")
    profile_id = data.get("id")
    if not isinstance(profile_id, str) or not _PROFILE_ID.match(profile_id):
        raise ProfileError("id: use lowercase letters, digits, and hyphens (up to 40).")
    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ProfileError("name: a profile needs a name.")

    reference = data.get("reference") or {}
    slip = data.get("slip") or {}
    code = slip.get("code") or {}
    amount = slip.get("amount") or {}

    pairing = reference.get("pairing", "line")
    if pairing not in PAIRINGS:
        raise ProfileError(f"reference.pairing: use one of {', '.join(PAIRINGS)}.")
    scope = slip.get("scope", "line")
    if scope not in SCOPES:
        raise ProfileError(f"slip.scope: use one of {', '.join(SCOPES)}.")

    def optional(pattern, where):
        return _compile(pattern, where) if pattern else None

    return Profile(
        id=profile_id,
        name=name.strip(),
        description=str(data.get("description") or ""),
        reference_code=CodeRule(
            pattern=_compile(reference.get("code"), "reference.code"),
            valid=optional(reference.get("code_valid"), "reference.code_valid"),
        ),
        reference_amount=AmountRule(pattern=_compile(reference.get("amount"), "reference.amount")),
        pairing=pairing,
        slip_code=SlipField(
            rule=CodeRule(
                pattern=_compile(code.get("pattern"), "slip.code.pattern"),
                valid=optional(code.get("valid"), "slip.code.valid"),
                strip_leading_zeros=bool(code.get("strip_leading_zeros", False)),
            ),
            allowed=_allowed(code.get("allowed", "0123456789"), "slip.code.allowed"),
            region=_region(code.get("region"), "slip.code.region"),
        ),
        slip_amount=SlipField(
            rule=AmountRule(pattern=_compile(amount.get("pattern"), "slip.amount.pattern")),
            allowed=_allowed(amount.get("allowed", "0123456789.,"), "slip.amount.allowed"),
            region=_region(amount.get("region"), "slip.amount.region"),
        ),
        scope=scope,
        comparison=optional(data.get("comparison"), "comparison"),
        numeric_mode=bool(slip.get("numeric_mode", False)),
        blacklist=_allowed(slip["blacklist"], "slip.blacklist") if slip.get("blacklist") else "",
        built_in=built_in,
    )


# The original RIBBON rules, kept exactly:
# - slip: a 16-digit numeric line whose value, without leading zeros, has 4 to 6 digits
#   ending in 0; the first such word on the page is the code;
# - amounts from 10,00 to 999,99; the first one on the page is the amount;
# - master list: codes written as 11364-0, paired with amounts by position on each page;
# - codes compare only when they have 5 or 6 digits ending in 0.
DEFAULT_DEFINITION = {
    "id": DEFAULT_PROFILE_ID,
    "name": "Guia padrão (matrícula com -0)",
    "description": "Linha numérica de 16 dígitos na guia; lista mestre com matrículas como 11364-0 "
                   "e valores de 10,00 a 999,99.",
    "reference": {
        "code": r"\b\d{4,5}-0\b",
        "amount": r"\b\d{2,3},\d{2}\b",
        "pairing": "position",
    },
    "slip": {
        "scope": "word",
        "numeric_mode": True,
        "blacklist": "IlOo",
        "code": {
            "pattern": r"\b\d{16}\b",
            "valid": r"\d{3,5}0",
            "strip_leading_zeros": True,
            "allowed": "0123456789,.",
        },
        "amount": {
            "pattern": r"\b\d{2,3}[.,]\d{2}\b",
            "allowed": "0123456789,.",
        },
    },
    "comparison": r"\d{4,5}0",
}

DEFAULT_PROFILE = build_profile(DEFAULT_DEFINITION, built_in=True)


def _profile_files():
    if not os.path.isdir(PROFILES_DIR):
        return []
    return sorted(
        os.path.join(PROFILES_DIR, name) for name in os.listdir(PROFILES_DIR) if name.endswith(".json")
    )


def load_profiles():
    """All usable profiles, the built-in one first. Invalid files are skipped with a message."""
    profiles = {DEFAULT_PROFILE_ID: DEFAULT_PROFILE}
    for path in _profile_files():
        try:
            with open(path, encoding="utf-8") as file:
                profile = build_profile(json.load(file))
        except (OSError, json.JSONDecodeError, ProfileError) as e:
            print(f"[profiles] skipping {os.path.basename(path)}: {e}")
            continue
        if profile.id in profiles:
            print(f"[profiles] skipping {os.path.basename(path)}: the id '{profile.id}' is already used.")
            continue
        profiles[profile.id] = profile
    return profiles


def get_profile(profile_id=None):
    """The profile with this id; None or "" means the built-in profile."""
    if not profile_id or profile_id == DEFAULT_PROFILE_ID:
        return DEFAULT_PROFILE
    profile = load_profiles().get(profile_id)
    if profile is None:
        raise KeyError(profile_id)
    return profile


def _profile_path(profile_id):
    if not isinstance(profile_id, str) or not _PROFILE_ID.match(profile_id):
        raise KeyError(profile_id)
    return os.path.join(PROFILES_DIR, f"{profile_id}.json")


def load_definition(profile_id):
    """The stored definition (a dict) of a profile, for viewing or editing."""
    if profile_id == DEFAULT_PROFILE_ID:
        return json.loads(json.dumps(DEFAULT_DEFINITION))
    path = _profile_path(profile_id)
    if not os.path.isfile(path):
        raise KeyError(profile_id)
    with open(path, encoding="utf-8") as file:
        return json.load(file)


def save_profile(data):
    """Validate and store a profile as profiles/<id>.json; returns the built profile."""
    profile = build_profile(data)
    if profile.id == DEFAULT_PROFILE_ID:
        raise ProfileError("id: 'padrao' is reserved for the built-in profile.")
    os.makedirs(PROFILES_DIR, exist_ok=True)
    with open(_profile_path(profile.id), "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)
        file.write("\n")
    return profile


def delete_profile(profile_id):
    if profile_id == DEFAULT_PROFILE_ID:
        raise ProfileError("The built-in profile cannot be deleted.")
    path = _profile_path(profile_id)
    if not os.path.isfile(path):
        raise KeyError(profile_id)
    os.remove(path)


# ==========================================
# Suggesting a profile from examples
# ==========================================

# Letters OCR confuses with digits; on the slip either one is accepted.
_CONFUSABLE = {"Z": "[Z2]", "S": "[S5]", "O": "[O0]", "B": "[B8]", "I": "[I1]", "G": "[G6]", "T": "[T7]"}
_SEPARATORS = ".,/:-"

# Brazilian amounts, with or without the thousands separator: 76,82 · 1.000,00 · 123766,01.
REFERENCE_AMOUNT = r"(?<![\d.,])\d{1,3}(?:\.?\d{3})*,\d{2}(?!\d)"
SLIP_AMOUNT = r"(?<!\d)\d{1,3}(?:[.\s]?\d{3})*[.,]\d{2}(?!\d)"


def _literal(text, tolerant):
    """Turn the fixed parts of an example into a pattern; tolerant parts accept OCR slips."""
    parts = []
    for character in text:
        if character.isdigit():
            parts.append(r"\d")
        elif character.isalpha():
            letter = character.upper()
            parts.append(_CONFUSABLE.get(letter, re.escape(letter)) if tolerant else re.escape(character))
        elif character.isspace():
            parts.append(r"\s?")
        else:
            parts.append(rf"\s?{re.escape(character)}?\s?" if tolerant else re.escape(character))
    return "".join(parts)


def suggest_definition(name, code_example, profile_id=None):
    """Build a profile definition from one registration code as printed in the master list.

    The longest run of digits is the key compared between slip and list (54700 in
    54700-Z); the characters around it locate the code. On the slip, separators are
    optional and letters also accept the digit OCR confuses them with (Z or 2)."""
    code = (code_example or "").strip()
    runs = list(re.finditer(r"\d+", code))
    if not runs:
        raise ProfileError("code example: type a code as it appears in the master list, with its digits.")
    key = max(runs, key=lambda match: len(match.group()))
    size = len(key.group())
    lowest, highest = max(1, size - 1), size + 1
    before, after = code[:key.start()], code[key.end():]
    group = rf"(?P<code>\d{{{lowest},{highest}}})"
    edge_before, edge_after = r"(?<![0-9A-Za-z])", r"(?![0-9A-Za-z])"

    allowed = "0123456789" + "".join(
        dict.fromkeys(c.upper() for c in before + after if c.isalpha() or c in _SEPARATORS)
    )
    # A letter accepted as its look-alike digit needs no extra character.
    slug = re.sub(r"[^a-z0-9]+", "-", (profile_id or name or "").lower()).strip("-")[:40] or "novo-modelo"
    return {
        "id": slug,
        "name": (name or "").strip() or code,
        "description": f"Código como {code}; valores no formato 1.234,56.",
        "reference": {
            "code": edge_before + _literal(before, False) + group + _literal(after, False) + edge_after,
            "amount": REFERENCE_AMOUNT,
            "pairing": "line",
        },
        "slip": {
            "scope": "line",
            "code": {
                "pattern": edge_before + _literal(before, True) + group + _literal(after, True) + edge_after,
                "allowed": allowed,
                "region": None,
            },
            "amount": {"pattern": SLIP_AMOUNT, "allowed": "0123456789.,", "region": None},
        },
        "comparison": rf"\d{{{lowest},{highest}}}",
    }
