import time

from app.core.database import supabase


_CACHE_TTL = 60
_cache = {"params": None, "matrix": None, "ts": 0.0}


def _load_from_db():
    params_rows = supabase.table("pricing_parameters").select("key,value").execute().data or []
    matrix_rows = supabase.table("pricing_rating_matrix").select("*").execute().data or []
    params = {row["key"]: float(row["value"]) for row in params_rows}
    matrix = {}
    for row in matrix_rows:
        matrix[row["rating"]] = {
            "pd_mult": float(row["pd_mult"]),
            "lgd_mult": float(row["lgd_mult"]),
            "bond_cobertura": float(row["bond_cobertura"]),
            "bond_premio_aa": (
                float(row["bond_premio_aa"])
                if row["bond_premio_aa"] is not None
                else None
            ),
            "recusa": bool(row["recusa"]),
            "perfil": row.get("perfil"),
        }
    return params, matrix


def get_pricing_config(force_reload: bool = False):
    now = time.time()
    if (
        force_reload
        or _cache["params"] is None
        or (now - _cache["ts"]) > _CACHE_TTL
    ):
        params, matrix = _load_from_db()
        _cache.update({"params": params, "matrix": matrix, "ts": now})
    return _cache["params"], _cache["matrix"]


def invalidate_cache():
    _cache["ts"] = 0.0
