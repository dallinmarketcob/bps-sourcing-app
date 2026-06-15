from leadsource.readers.source_maps import load_source_map_csv

# Mirrors the real master sheet schema: canonical "Source N", a friendly
# provider/channel, and a DNIS -- with the same messiness (mixed formats,
# many-to-one, no-DNIS rows).
SAMPLE = """Pestroutes Source,Provider / Channel,DNIS
Source 1,Aragon Fresno,628-232-0597
Source 11,DoLead,951-356-6354
Source 11,DoLead Form,951 963 9018
Source 24,Velynx,9519639015
Source 52,GBPs,626-515-8438
Source 52,GBPs,503-506-0881
Source 55,Website Forms,
Source 23,Pestnet,833-999-3128
"""


def _load(tmp_path, text=SAMPLE):
    p = tmp_path / "sourcing.csv"
    p.write_text(text, encoding="utf-8")
    return load_source_map_csv(p)


def test_dnis_resolves_to_canonical_source(tmp_path):
    m = _load(tmp_path)
    assert m.source_for_dnis("628-232-0597") == "Source 1"
    assert m.source_for_dnis("(951) 963-9015") == "Source 24"
    assert m.source_for_dnis("833-999-3128") == "Source 23"


def test_provider_name_resolves_to_canonical_source(tmp_path):
    # Gmail/Meta leads name a provider; it must resolve to the same canonical key.
    m = _load(tmp_path)
    assert m.source_for_provider("Pestnet") == "Source 23"
    assert m.source_for_provider("  aragon fresno ") == "Source 1"


def test_many_numbers_and_aliases_one_source(tmp_path):
    m = _load(tmp_path)
    assert m.source_for_dnis("626-515-8438") == "Source 52"
    assert m.source_for_dnis("503-506-0881") == "Source 52"
    # DoLead and "DoLead Form" are both Source 11.
    assert m.source_for_provider("DoLead") == "Source 11"
    assert m.source_for_provider("DoLead Form") == "Source 11"


def test_source_to_provider_display_name(tmp_path):
    m = _load(tmp_path)
    assert m.provider_for_source("Source 1") == "Aragon Fresno"


def test_no_dnis_row_still_maps_provider(tmp_path):
    # Website Forms has no tracking number but must still resolve by name.
    m = _load(tmp_path)
    assert m.source_for_provider("Website Forms") == "Source 55"
    assert m.source_for_dnis("999-999-9999") is None


def test_blank_source_with_dnis_is_flagged(tmp_path):
    text = "Pestroutes Source,Provider / Channel,DNIS\n,Mystery,833-778-0053\n"
    m = _load(tmp_path, text)
    assert any("blank Pestroutes Source" in w for w in m.warnings)
    assert m.source_for_dnis("833-778-0053") is None
