"""Pytest collection settings for the backend suite."""

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
