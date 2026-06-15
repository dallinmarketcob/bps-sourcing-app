import pytest

from leadsource.readers.pestroutes import PestRoutesClient, PestRoutesError


def test_id_param_pads_single_id():
    # PestRoutes quirk: a get with exactly one id returns nothing, so pad to two.
    assert PestRoutesClient._id_param(["253317"]) == "253317,253317"
    assert PestRoutesClient._id_param("5") == "5,5"
    assert PestRoutesClient._id_param(["1", "2", "3"]) == "1,2,3"


def test_update_refuses_empty_fields():
    # An update that omits the field would BLANK it; guard against that.
    c = PestRoutesClient("https://x/api", "k", "t")
    with pytest.raises(PestRoutesError):
        c.update_subscription("253317", {"sourceID": None})
    with pytest.raises(PestRoutesError):
        c.update_subscription("253317", {})
    c.close()