from leadsource.normalize import normalize_email, normalize_phone


class TestNormalizePhone:
    def test_various_formats_collapse_to_one_e164_key(self):
        variants = [
            "(770) 555-1234",
            "770-555-1234",
            "770.555.1234",
            "7705551234",
            "+1 770 555 1234",
            "  +17705551234 ",
        ]
        results = {normalize_phone(v) for v in variants}
        assert results == {"+17705551234"}

    def test_invalid_or_junk_returns_none(self):
        for bad in [None, "", "   ", "abc", "123", "555"]:
            assert normalize_phone(bad) is None

    def test_respects_explicit_country_code(self):
        # A +44 UK number should not be coerced to US.
        assert normalize_phone("+44 20 7946 0958") == "+442079460958"


class TestNormalizeEmail:
    def test_lowercases_and_trims(self):
        assert normalize_email("  John.Doe@Example.COM ") == "john.doe@example.com"

    def test_rejects_malformed(self):
        for bad in [None, "", "no-at-sign", "two@@at.com", "no@domain"]:
            assert normalize_email(bad) is None
