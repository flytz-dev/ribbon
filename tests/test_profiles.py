"""Document profiles: the built-in rules are unchanged and other layouts can be described."""

import json

import pytest
from fastapi.testclient import TestClient

from api import auth, main
from core import engine, profiles, samples

EXAMPLE = "exemplo-codigo-z"


@pytest.fixture
def example():
    return profiles.get_profile(EXAMPLE)


def _definition(**overrides):
    data = json.loads(json.dumps(profiles.DEFAULT_DEFINITION))
    data["id"] = "teste"
    for path, value in overrides.items():
        target = data
        *parents, key = path.split(".")
        for parent in parents:
            target = target[parent]
        target[key] = value
    return data


# ==========================================
# The built-in profile keeps the original rules
# ==========================================

def test_default_tesseract_options_are_unchanged():
    assert engine.TESSERACT_CONFIG == (
        "--psm 6 -c tessedit_char_whitelist=0123456789,. "
        "-c classify_bln_numeric_mode=1 -c tessedit_char_blacklist=IlOo"
    )


def test_default_profile_is_built_in_and_listed_first():
    loaded = profiles.load_profiles()
    assert next(iter(loaded)) == profiles.DEFAULT_PROFILE_ID
    assert profiles.get_profile(None) is profiles.DEFAULT_PROFILE
    assert profiles.DEFAULT_PROFILE.summary()["built_in"] is True


def test_default_amounts_keep_the_original_limit():
    """Known limitation kept on purpose: the original rule reads amounts up to 999,99, and
    finds 000,00 inside 1.000,00. Layouts with larger amounts need a profile of their own."""
    assert engine.normalize_amount("999,99") == "999,99"
    assert engine.normalize_amount("1000,00") is None
    assert engine.normalize_amount("1.000,00") == "000,00"


# ==========================================
# A second layout: codes such as 54700-Z and amounts above 1.000,00
# ==========================================

def test_master_list_lines_pair_code_and_amount(example):
    text = "Registration  Amount due\n54700-Z   123.766,01\n12300-Z\n98100-Z   1.000,00"
    assert example.reference_entries(text) == [
        {"code": "54700", "amount": "123.766,01"},
        {"code": "12300", "amount": "N/A"},
        {"code": "98100", "amount": "1.000,00"},
    ]


@pytest.mark.parametrize("text", ["54700-Z", "54700 - Z", "54700-2", "54700Z"])
def test_slip_code_tolerates_the_shape_but_keeps_the_digits(example, text):
    """Every code ends in -Z, so the suffix only locates the field; Z and 2 are confused by OCR."""
    assert example.code_from_text(text, example.slip_code.rule) == "54700"


@pytest.mark.parametrize("text, expected", [
    ("123.766,01", "123.766,01"),
    ("123766,01", "123.766,01"),     # thousands separator lost by OCR
    ("123 766,01", "123.766,01"),
    ("1.000.00", "1.000,00"),        # decimal comma read as a period
    ("76,82", "76,82"),
])
def test_amounts_are_canonical(example, text, expected):
    assert example.normalize_amount(text) == expected


def test_codes_compare_exactly(example):
    assert engine.code_status("54700", "54700", example) == "OK"
    assert engine.code_status("54100", "54700", example) == "MISMATCH"
    assert engine.code_status("0", "54700", example) == "MISMATCH"


def _ocr_data(*lines):
    """Fake image_to_data output: each line is a list of (word, confidence, left)."""
    data = {name: [] for name in ("text", "conf", "left", "top", "width", "height", "block_num", "par_num", "line_num")}
    for number, words in enumerate(lines, start=1):
        for word, confidence, left in words:
            data["text"].append(word)
            data["conf"].append(confidence)
            data["left"].append(left)
            data["top"].append(10 * number)
            data["width"].append(40)
            data["height"].append(8)
            data["block_num"].append(1)
            data["par_num"].append(1)
            data["line_num"].append(number)
    return data


def test_a_code_split_into_words_is_read_as_one_line(monkeypatch, example):
    """Without regions, matching whole lines finds 54700 - Z even when OCR splits it."""
    whole_page = profiles.build_profile({**json.loads(_example_json()), "slip": {
        **json.loads(_example_json())["slip"],
        "code": {**json.loads(_example_json())["slip"]["code"], "region": None},
        "amount": {**json.loads(_example_json())["slip"]["amount"], "region": None},
    }})
    data = _ocr_data([("54700", 90, 0), ("-", 60, 50), ("Z", 80, 100)], [("1.234,56", 88, 0)])
    monkeypatch.setattr(engine.pytesseract, "image_to_data", lambda *args, **kwargs: data)

    fields = engine.extract_image_fields(object(), whole_page)

    assert fields["code"] == "54700"
    assert fields["code_conf"] == 60, "the weakest word sets the confidence"
    assert fields["code_box"] == (0, 10, 140, 18)
    assert fields["amount"] == "1.234,56"


def test_regions_read_each_field_from_its_own_crop(monkeypatch, example):
    """The late fee printed elsewhere on the page never reaches the amount region."""
    from PIL import Image

    calls = []

    def fake_ocr(image, lang, config, output_type):
        calls.append((image.size, config))
        if "Z" in config:
            return _ocr_data([("54700-Z", 91, 30)])
        return _ocr_data([("123.766,01", 87, 30)])

    monkeypatch.setattr(engine.pytesseract, "image_to_data", fake_ocr)
    page = Image.new("L", (1000, 1000), 255)

    fields = engine.extract_image_fields(page, example)

    assert len(calls) == 2, "two crops, no whole-page pass"
    assert all("--psm 6" in config for _size, config in calls)
    assert fields["code"] == "54700" and fields["amount"] == "123.766,01"
    left, top = int(0.03 * 1000) - engine.REGION_PADDING, int(0.21 * 1000) - engine.REGION_PADDING
    assert fields["code_box"][:2] == (left + 30, top + 10), "boxes map back onto the page"


def _example_json():
    with open(f"{profiles.PROFILES_DIR}/{EXAMPLE}.json", encoding="utf-8") as file:
        return file.read()


def test_synthetic_layout_matches_its_profile(tmp_path, example):
    """The codigo-z samples produce a master list the example profile reads exactly."""
    dataset = samples.generate_dataset(tmp_path, slips=6, seed=1, style="codigo-z")
    entries = engine.extract_reference_list(dataset["reference"], example)
    truth = samples.load_ground_truth(dataset["ground_truth"])

    assert len(entries) == 6
    assert all(entry["amount"].count(",") == 1 and len(entry["amount"]) >= 8 for entry in entries)
    listed = {entry["code"] for entry in entries}
    assert {row["code"] for row in truth.values()} <= listed


# ==========================================
# Validation, files, and the API
# ==========================================

@pytest.mark.parametrize("path, value, message", [
    ("reference.code", "(", "reference.code"),
    ("slip.code.region", [0.5, 0.1, 0.2, 0.3], "region"),
    ("slip.code.allowed", "0-9 \"", "allowed characters"),
    ("reference.pairing", "diagonal", "pairing"),
    ("id", "Com Espaço", "id"),
])
def test_invalid_definitions_are_rejected(path, value, message):
    with pytest.raises(profiles.ProfileError, match=message):
        profiles.build_profile(_definition(**{path: value}))


def test_saved_profiles_are_loaded_and_bad_files_skipped(monkeypatch, tmp_path):
    monkeypatch.setattr(profiles, "PROFILES_DIR", str(tmp_path))
    (tmp_path / "quebrado.json").write_text("{ not json", encoding="utf-8")

    profiles.save_profile(_definition(name="Teste salvo"))

    loaded = profiles.load_profiles()
    assert list(loaded) == ["padrao", "teste"]
    assert loaded["teste"].name == "Teste salvo"
    with pytest.raises(profiles.ProfileError, match="reserved"):
        profiles.save_profile(_definition(id="padrao"))


@pytest.fixture
def client():
    main.app.dependency_overrides[auth.current_user] = lambda: "ana"
    yield TestClient(main.app)
    main.app.dependency_overrides.clear()


def test_api_lists_profiles(client):
    listed = {item["id"]: item for item in client.get("/api/v1/profiles").json()}
    assert listed["padrao"]["built_in"] is True
    assert EXAMPLE in listed


def test_suggestion_from_one_code_reads_the_layout():
    profile = profiles.build_profile(profiles.suggest_definition("Prefeitura 2027", "54700-Z"))
    assert profile.id == "prefeitura-2027"
    assert profile.reference_entries("54700-Z   123.766,01") == [{"code": "54700", "amount": "123.766,01"}]
    assert profile.code_from_text("54700 - 2", profile.slip_code.rule) == "54700"
    assert profile.slip_code.allowed == "0123456789-Z"


def test_suggestion_needs_digits():
    with pytest.raises(profiles.ProfileError, match="code example"):
        profiles.suggest_definition("x", "ABC")


def test_assistant_endpoints_suggest_preview_save_and_delete(client, monkeypatch, tmp_path):
    monkeypatch.setattr(profiles, "PROFILES_DIR", str(tmp_path / "profiles"))
    definition = client.post("/api/v1/profiles/suggest", json={"name": "Modelo Z", "code_example": "54700-Z"}).json()

    dataset = samples.generate_dataset(tmp_path / "data", slips=4, seed=2, style="codigo-z", discrepancies=False)
    with open(dataset["reference"], "rb") as reference:
        preview = client.post(
            "/api/v1/profiles/reference-preview",
            files={"reference": ("lista.pdf", reference, "application/pdf")},
            data={"definition": json.dumps(definition)},
        ).json()
    assert preview["count"] == preview["complete"] == 4

    assert client.post("/api/v1/profiles", json=definition).status_code == 201
    assert client.get("/api/v1/profiles/modelo-z").json()["name"] == "Modelo Z"
    assert client.delete("/api/v1/profiles/modelo-z").status_code == 204
    assert client.get("/api/v1/profiles/modelo-z").status_code == 404
    assert client.delete("/api/v1/profiles/padrao").status_code == 400


def test_slip_preview_reads_with_the_draft_profile(client, monkeypatch):
    from PIL import Image

    monkeypatch.setattr(engine.pdf2image, "convert_from_path", lambda *args, **kwargs: [Image.new("L", (200, 100), 255)])
    monkeypatch.setattr(engine, "read_page", lambda image, is_known, profile: {
        "code": "54700", "amount": "1.234,56", "code_votes": 6, "amount_votes": 8, "strategies_run": 8,
        "code_box": (10, 10, 50, 20), "amount_box": None,
    })
    definition = profiles.suggest_definition("Modelo Z", "54700-Z")

    body = client.post(
        "/api/v1/profiles/slip-preview",
        files={"payment_slips": ("guia.pdf", b"%PDF", "application/pdf")},
        data={"definition": json.dumps(definition)},
    ).json()

    assert body["code"] == "54700" and body["code_votes"] == 6
    assert body["page_image"].startswith("data:image/jpeg;base64,")


def test_invalid_draft_profiles_are_explained(client):
    definition = profiles.suggest_definition("x", "54700-Z")
    definition["slip"]["code"]["region"] = [0.9, 0.1, 0.2, 0.3]
    response = client.post(
        "/api/v1/profiles/slip-preview",
        files={"payment_slips": ("guia.pdf", b"%PDF", "application/pdf")},
        data={"definition": json.dumps(definition)},
    )
    assert response.status_code == 400 and "region" in response.json()["detail"]


def test_api_rejects_an_unknown_profile(client):
    files = {"payment_slips": ("a.pdf", b"%PDF", "application/pdf"), "reference": ("b.pdf", b"%PDF", "application/pdf")}
    response = client.post("/api/v1/audits?dpi=300&profile=nao-existe", files=files)
    assert response.status_code == 400
    assert response.json()["detail"] == "Unknown document profile."
