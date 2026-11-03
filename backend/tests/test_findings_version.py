from app.services.findings.version import EMITTER_VERSION


def test_emitter_version_bumps_after_cash_flow_rule_changes():
    assert EMITTER_VERSION == "35"
