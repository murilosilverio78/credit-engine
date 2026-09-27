"""Pytest collection settings for the backend suite."""

import os


for key in (
    "ANTHROPIC_API_KEY",
    "PORTAL_TRANSPARENCIA_TOKEN",
    "SECRET_KEY",
    "TWOCAPTCHA_API_KEY",
    "RESEND_API_KEY",
):
    os.environ.setdefault(key, "test")
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://user:pass@localhost/test",
)
os.environ.setdefault(
    "SUPABASE_SERVICE_KEY",
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJyb2xlIjoic2VydmljZV9yb2xlIiwiaXNzIjoic3VwYWJhc2UifQ."
    "testsignature",
)
os.environ.setdefault("SUPABASE_URL", "http://localhost")

# These files are executable API probes: they perform live HTTP requests and
# write JSON output at import time. Keep them available for manual diagnostics
# without treating them as automated tests.
collect_ignore = [
    "test_acordos_leniencia.py",
    "test_brasil_api.py",
    "test_ceis.py",
    "test_cepim.py",
    "test_cnep.py",
    "test_contratos.py",
    "test_pessoa_juridica.py",
    "test_recursos_recebidos.py",
]
