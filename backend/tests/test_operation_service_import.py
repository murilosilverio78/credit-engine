from app.services.operation_service import OperationService


def test_operation_service_annotations_are_deferred():
    assert OperationService._attach_quote_stage.__annotations__["items"] == "list[dict]"
