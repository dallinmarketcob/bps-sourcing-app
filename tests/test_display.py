from leadsource.display import friendly_source

SRC2PROV = {"Source 11": "DoLead", "Source 144": "Facebook Inbound"}


def test_meta_form_vs_facebook_call():
    # Source 144 splits by channel in reports (same sourceID in PestRoutes).
    assert friendly_source("Source 144", "meta", SRC2PROV) == "Meta Form Lead (S144)"
    assert friendly_source("Source 144", "gmail", SRC2PROV) == "Meta Form Lead (S144)"
    assert friendly_source("Source 144", "genesys", SRC2PROV) == "Facebook Inbound Call (S144)"


def test_other_sources_use_provider_name():
    assert friendly_source("Source 11", "gmail", SRC2PROV) == "DoLead (Source 11)"
    assert friendly_source("Source 999", "gmail", SRC2PROV) == "Source 999"
    assert friendly_source("", None, SRC2PROV) == ""
