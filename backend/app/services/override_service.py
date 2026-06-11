"""
OverrideService: gerencia solicitacoes e revisoes de alteracao manual de credito.
"""
from datetime import datetime, timezone
from typing import Any, Optional

from app.core.database import supabase
from app.services.audit_service import AuditService
from app.services.pricing_engine import compute_margem_subordinado


audit = AuditService()
SYSTEM_ACTOR_ID = "00000000-0000-0000-0000-000000000001"
ALCADA_HIERARCHY = ["analista", "gerente", "diretor", "comite"]


class OverrideService:
    FIELD_MAP = {
        "taxa": "taxa_sugerida",
    }
    ROLE_ORDER = ("analista", "gerente", "diretor")

    def _normalize_value(self, override_type: str, value: Any) -> Any:
        if override_type not in self.FIELD_MAP:
            raise ValueError("Tipo de override invalido")

        try:
            numeric = float(value)
        except (TypeError, ValueError):
            raise ValueError("Novo valor deve ser numerico") from None
        return numeric / 100 if numeric > 1 else numeric

    def _get_operation(self, operation_id: str) -> Optional[dict]:
        try:
            result = supabase.table("operations")\
                .select("*")\
                .eq("id", operation_id)\
                .single()\
                .execute()
            return result.data
        except Exception:
            return None

    def _apply_override(self, operation_id: str, override_type: str, new_value: Any):
        field = self.FIELD_MAP[override_type]
        supabase.table("operations")\
            .update({field: new_value})\
            .eq("id", operation_id)\
            .execute()

    def _normalize_role(self, role: str) -> str:
        aliases = {
            "analyst": "analista",
            "manager": "gerente",
            "committee": "diretor",
            "comite": "diretor",
        }
        normalized = aliases.get((role or "").lower(), (role or "").lower())
        if normalized not in self.ROLE_ORDER:
            raise ValueError("Alcada solicitante invalida")
        return normalized

    def _prazo_meses(self, prazo_dias: Any) -> int:
        try:
            dias = int(prazo_dias or 0)
        except (TypeError, ValueError):
            dias = 0
        return max(round(dias / 30), 1)

    def _delta_pp_to_fraction(self, value: Any) -> float:
        try:
            return float(value or 0) / 100
        except (TypeError, ValueError):
            return 0.0

    def _fraction_value(self, value: Any) -> float:
        try:
            numeric = float(value or 0)
        except (TypeError, ValueError):
            return 0.0
        return numeric / 100 if numeric > 1 else numeric

    def _fetch_alcada_config(self, role: str) -> dict:
        result = supabase.table("alcada_config")\
            .select("role,delta_maximo_taxa_pp,margem_minima_subordinado")\
            .eq("role", role)\
            .single()\
            .execute()
        if not result.data:
            raise LookupError("Alcada nao encontrada")
        return result.data

    def _taxa_minima_alcada(
        self,
        taxa_sugerida: float,
        valor: float,
        prazo_meses: int,
        rating: str,
        config: dict,
    ) -> float:
        delta_maximo = self._delta_pp_to_fraction(config.get("delta_maximo_taxa_pp"))
        margem_minima = self._fraction_value(config.get("margem_minima_subordinado"))
        piso_delta = max(taxa_sugerida - delta_maximo, 0.0)

        margem_no_piso = compute_margem_subordinado(
            piso_delta,
            valor,
            prazo_meses,
            rating,
        )
        if margem_no_piso >= margem_minima:
            piso_margem = piso_delta
        else:
            margem_na_sugerida = compute_margem_subordinado(
                taxa_sugerida,
                valor,
                prazo_meses,
                rating,
            )
            if margem_na_sugerida < margem_minima:
                piso_margem = taxa_sugerida
            else:
                lo, hi = piso_delta, taxa_sugerida
                for _ in range(80):
                    mid = (lo + hi) / 2
                    margem_mid = compute_margem_subordinado(
                        mid,
                        valor,
                        prazo_meses,
                        rating,
                    )
                    if margem_mid >= margem_minima:
                        hi = mid
                    else:
                        lo = mid
                    if hi - lo < 1e-10:
                        break
                piso_margem = hi

        return max(piso_delta, piso_margem)

    def _passes_alcada(
        self,
        taxa_sugerida: float,
        taxa_proposta: float,
        margem_resultante: float,
        config: dict,
    ) -> tuple[bool, str | None]:
        delta_maximo = self._delta_pp_to_fraction(config.get("delta_maximo_taxa_pp"))
        margem_minima = self._fraction_value(config.get("margem_minima_subordinado"))
        delta = taxa_sugerida - taxa_proposta

        if delta > delta_maximo:
            return False, "delta_excedido"
        if margem_resultante < margem_minima:
            return False, "margem_insuficiente"
        return True, None

    async def _validate_taxa_override(
        self,
        operation_id: str,
        taxa_proposta: float,
        requesting_role: str,
    ) -> dict:
        """
        Returns:
        {
          "approved": bool,
          "alcada_required": "analista"|"gerente"|"diretor",
          "motivo": "delta_excedido"|"margem_insuficiente"|None,
          "margem_resultante": float,
          "taxa_minima_sua_alcada": float,
          "taxa_minima_proximo_nivel": float | None,
        }
        """
        role = self._normalize_role(requesting_role)
        operation = self._get_operation(operation_id)
        if not operation:
            raise LookupError("Operacao nao encontrada")

        taxa_sugerida = self._fraction_value(operation.get("taxa_sugerida"))
        taxa_proposta = self._fraction_value(taxa_proposta)
        valor = float(operation.get("valor_solicitado") or 0)
        prazo_meses = self._prazo_meses(operation.get("prazo_dias"))
        rating = str(operation.get("rating") or "").upper()

        if taxa_sugerida <= 0:
            raise ValueError("Operacao sem taxa sugerida para validar")
        if taxa_proposta <= 0:
            raise ValueError("Taxa proposta deve ser positiva")
        if valor <= 0:
            raise ValueError("Operacao sem valor solicitado para validar")
        if rating not in {"A", "B", "C", "D"}:
            raise ValueError("Rating invalido para validar taxa")

        margem_resultante = compute_margem_subordinado(
            taxa_proposta,
            valor,
            prazo_meses,
            rating,
        )

        role_index = self.ROLE_ORDER.index(role)
        current_config = self._fetch_alcada_config(role)
        passes_current, motivo = self._passes_alcada(
            taxa_sugerida,
            taxa_proposta,
            margem_resultante,
            current_config,
        )

        alcada_required = role
        if not passes_current:
            for candidate in self.ROLE_ORDER[role_index + 1:]:
                candidate_config = self._fetch_alcada_config(candidate)
                passes_candidate, _ = self._passes_alcada(
                    taxa_sugerida,
                    taxa_proposta,
                    margem_resultante,
                    candidate_config,
                )
                if passes_candidate:
                    alcada_required = candidate
                    break
            else:
                alcada_required = "diretor"

        taxa_minima_sua_alcada = self._taxa_minima_alcada(
            taxa_sugerida,
            valor,
            prazo_meses,
            rating,
            current_config,
        )
        next_role = (
            self.ROLE_ORDER[role_index + 1]
            if role_index + 1 < len(self.ROLE_ORDER)
            else None
        )
        taxa_minima_proximo_nivel = None
        if next_role:
            taxa_minima_proximo_nivel = self._taxa_minima_alcada(
                taxa_sugerida,
                valor,
                prazo_meses,
                rating,
                self._fetch_alcada_config(next_role),
            )

        return {
            "approved": passes_current,
            "alcada_required": alcada_required,
            "motivo": None if passes_current else motivo,
            "margem_resultante": margem_resultante,
            "taxa_minima_sua_alcada": taxa_minima_sua_alcada,
            "taxa_minima_proximo_nivel": taxa_minima_proximo_nivel,
        }

    async def create(
        self,
        operation_id: str,
        override_type: str,
        previous_value: Any,
        new_value: Any,
        justificativa: str,
        requested_by: Optional[str] = None,
        requesting_role: str = "analista",
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> dict:
        operation = self._get_operation(operation_id)
        if not operation:
            raise LookupError("Operacao nao encontrada")

        requested_by = requested_by or SYSTEM_ACTOR_ID
        normalized_previous_value = self._normalize_value(override_type, previous_value)
        normalized_value = self._normalize_value(override_type, new_value)
        validation = await self._validate_taxa_override(
            operation_id,
            normalized_value,
            requesting_role,
        )
        alcada = validation["alcada_required"]
        status = "approved" if validation["approved"] else "pending"
        now = datetime.now(timezone.utc).isoformat()

        snapshots = supabase.table("component_snapshots")\
            .select("component, status, parsed_result, score_contrib")\
            .eq("operation_id", operation_id)\
            .execute()

        data = {
            "operation_id": operation_id,
            "override_type": override_type,
            "previous_value": normalized_previous_value,
            "new_value": normalized_value,
            "justificativa": justificativa,
            "alcada_required": alcada,
            "status": status,
            "validation_result": validation,
            "score_no_momento": operation.get("score"),
            "componentes_snap": snapshots.data,
            "ip_address": ip_address,
            "user_agent": user_agent,
        }
        data["requested_by"] = requested_by
        if validation["approved"]:
            data["reviewed_at"] = now
            data["review_comment"] = f"Auto-aprovado por alcada {requesting_role}."

        result = supabase.table("credit_overrides").insert(data).execute()
        override = result.data[0]

        audit.log(
            operation_id=operation_id,
            action="override_applied",
            actor_id=requested_by,
            actor_type=requesting_role,
            ip_address=ip_address,
            override_reason=justificativa,
            previous_value={override_type: normalized_previous_value},
            new_value={override_type: normalized_value},
            payload={
                "event": "override_requested",
                "override_id": override.get("id"),
                "alcada_required": alcada,
                "status": status,
                "validation": validation,
            },
        )

        if validation["approved"]:
            self._apply_override(operation_id, override_type, normalized_value)

        return {"override": override, "validation": validation}

    async def review(
        self,
        operation_id: str,
        override_id: str,
        decision: str,
        reviewed_by: str,
        reviewer_role: Optional[str] = None,
        review_comment: Optional[str] = None,
        ip_address: Optional[str] = None,
    ) -> dict:
        if decision not in {"approved", "rejected"}:
            raise ValueError("Decisao da revisao deve ser approved ou rejected")

        try:
            result = supabase.table("credit_overrides")\
                .select("*")\
                .eq("id", override_id)\
                .eq("operation_id", operation_id)\
                .single()\
                .execute()
            override = result.data
        except Exception:
            override = None

        if not override:
            raise LookupError("Override nao encontrado")
        if override.get("status") != "pending":
            raise ValueError("Somente overrides pendentes podem ser revisados")

        required = override.get("alcada_required", "analista")
        required_idx = (
            ALCADA_HIERARCHY.index(required)
            if required in ALCADA_HIERARCHY
            else 0
        )
        reviewer_idx = (
            ALCADA_HIERARCHY.index(reviewer_role)
            if reviewer_role in ALCADA_HIERARCHY
            else 0
        )
        if reviewer_idx < required_idx:
            raise ValueError(
                f"Alçada insuficiente: override requer '{required}', "
                f"revisor tem '{reviewer_role}'"
            )

        if override.get("requested_by") == reviewed_by:
            raise ValueError("O revisor deve ser diferente do solicitante")

        updated = {
            "status": decision,
            "reviewed_by": reviewed_by,
            "reviewed_at": datetime.now(timezone.utc).isoformat(),
            "review_comment": review_comment,
        }
        result = supabase.table("credit_overrides")\
            .update(updated)\
            .eq("id", override_id)\
            .eq("operation_id", operation_id)\
            .execute()

        if decision == "approved":
            self._apply_override(
                operation_id,
                override["override_type"],
                override["new_value"],
            )

        audit.log(
            operation_id=operation_id,
            action="override_applied",
            actor_id=reviewed_by,
            actor_type=reviewer_role or "analista",
            ip_address=ip_address,
            override_reason=override.get("justificativa"),
            previous_value={override["override_type"]: override.get("previous_value")},
            new_value={override["override_type"]: override.get("new_value")},
            payload={
                "event": "override_reviewed",
                "override_id": override_id,
                "status": decision,
                "review_comment": review_comment,
            },
        )

        return result.data[0]

    async def list_for_operation(self, operation_id: str) -> list[dict]:
        result = supabase.table("credit_overrides")\
            .select("*")\
            .eq("operation_id", operation_id)\
            .order("created_at", desc=True)\
            .execute()
        return result.data

    async def list_pending(self) -> list[dict]:
        result = supabase.table("vw_overrides_pendentes")\
            .select("*")\
            .execute()
        return result.data
