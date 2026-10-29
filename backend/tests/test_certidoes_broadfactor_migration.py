from pathlib import Path


ROOT = Path(__file__).parents[2]
MIGRATION = ROOT / "infra" / "supabase" / "migrations" / "070_certidoes_broadfactor.sql"
CONFIG = ROOT / "infra" / "supabase" / "certidoes_broadfactor_config.sql"


def test_certificate_cache_migration_has_cache_and_lineage_per_document_hash():
    sql = " ".join(MIGRATION.read_text(encoding="utf-8").split())

    assert "CREATE TABLE IF NOT EXISTS certidoes_broadfactor" in sql
    assert "PRIMARY KEY (tipo, cnpj, sha256)" in sql
    assert "CREATE TABLE IF NOT EXISTS certidoes_broadfactor_origens" in sql
    assert "REFERENCES documentos_cliente(id) ON DELETE CASCADE" in sql
    assert "ALTER TABLE certidoes_broadfactor ENABLE ROW LEVEL SECURITY" in sql


def test_capture_configuration_is_a_manual_update_not_a_migration_default():
    sql = " ".join(CONFIG.read_text(encoding="utf-8").split())

    assert sql.startswith("-- PROPOSTA MANUAL: aplicar no SQL Editor antes do deploy.")
    assert "UPDATE documentos_captura_config" in sql
    for certificate_type in ("CND_FEDERAL", "CND_ESTADUAL", "CND_MUNICIPAL"):
        assert certificate_type in sql
