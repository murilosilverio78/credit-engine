import pytest

from tests.fakes.postgrest import APIError, Postgrest


def test_production_schema_fake_rejects_unknown_column():
    database = Postgrest({"operations": [{"id": "op"}]})

    with pytest.raises(APIError) as error:
        database.table("operations").select("id,pct_max_contrato").execute()

    assert error.value.code == "42703"
    assert error.value.message == "column operations.pct_max_contrato does not exist"
