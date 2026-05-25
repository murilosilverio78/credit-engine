"""
OverrideService: gerencia solicitações e revisões de alteração manual de crédito.
"""
from datetime import datetime, timezone
from typing import Any, Optional

from app.core.database import supabase
from app.services.audit_service import AuditService


audit = AuditService()


class OverrideService:
    FIELD_MAP = {
        "rating": "rating",
        "score": "score",
        "taxa": "taxa_sugerida",
        "limite": "limite_aprovado",
        "status_operacao": "status",
    }

    def _normalize_value(self, override_type: str, value: Any) -> Any:
        if override_type not in self.FIELD_MAP:
            raise ValueError("Tipo de override inválido")

        if override_type == "rating":
            rating = str(value).upper()
            if rating not in {"A", "B", "C", "D", "E"}:
                raise ValueError("Rating deve ser A, B, C, D ou E")
            return rating

        if override_type in {"score", "taxa", "limite"}:
            try:
                return float(value)
            except (TypeError, ValueError):
                raise ValueError("Novo valor deve ser numérico") from None

        status = str(value)
        valid_statuses = {
            "pending",
            "running",
            "waiting_upload",
            "completed",
            "expired",
            "error",
        }
        if status not in valid_statuses:
            raise ValueError("Status da operação inválido")
        return status

    def _required_alcada(self, override_type: str, previous_value: Any, new_value: Any) -> str:
        if override_type == "rating":
            return "committee" if new_value in {"D", "E"} else "analyst"

        if override_type == "score":
            if previous_value is None:
                return "committee"
            return "committee" if abs(float(new_value) - float(previous_value)) > 15 else "analyst"

        if override_type == "taxa":
            if previous_value is None:
                return "committee"
            return "committee" if abs(float(new_value) - float(previous_value)) > 0.02 else "analyst"

        if override_type == "limite":
            return "committee" if float(new_value) > 500_000 else "analyst"

        return "committee"

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

    async def create(
        self,
        operation_id: str,
        override_type: str,
        previous_value: Any,
        new_value: Any,
        justificativa: str,
        requested_by: str,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> dict:
        operation = self._get_operation(operation_id)
        if not operation:
            raise LookupError("Operação não encontrada")

        normalized_previous_value = self._normalize_value(override_type, previous_value)
        normalized_value = self._normalize_value(override_type, new_value)
        alcada = self._required_alcada(override_type, normalized_previous_value, normalized_value)
        status = "approved" if alcada == "analyst" else "pending"
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
            "requested_by": requested_by,
            "alcada_required": alcada,
            "status": status,
            "score_no_momento": operation.get("score"),
            "componentes_snap": snapshots.data,
            "ip_address": ip_address,
            "user_agent": user_agent,
        }
        if alcada == "analyst":
            data["reviewed_at"] = now
            data["review_comment"] = "Auto-aprovado por alçada analyst."

        result = supabase.table("credit_overrides").insert(data).execute()
        override = result.data[0]

        audit.log(
            operation_id=operation_id,
            action="override_applied",
            actor_id=requested_by,
            actor_type="analyst",
            ip_address=ip_address,
            override_reason=justificativa,
            previous_value={override_type: normalized_previous_value},
            new_value={override_type: normalized_value},
            payload={
                "event": "override_requested",
                "override_id": override.get("id"),
                "alcada_required": alcada,
                "status": status,
            },
        )

        if alcada == "analyst":
            self._apply_override(operation_id, override_type, normalized_value)

        return override

    async def review(
        self,
        operation_id: str,
        override_id: str,
        status: str,
        reviewed_by: str,
        review_comment: Optional[str] = None,
        ip_address: Optional[str] = None,
    ) -> dict:
        if status not in {"approved", "rejected"}:
            raise ValueError("Status da revisão deve ser approved ou rejected")

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
            raise LookupError("Override não encontrado")
        if override.get("status") != "pending":
            raise ValueError("Somente overrides pendentes podem ser revisados")
        if override.get("requested_by") == reviewed_by:
            raise ValueError("O revisor deve ser diferente do solicitante")

        updated = {
            "status": status,
            "reviewed_by": reviewed_by,
            "reviewed_at": datetime.now(timezone.utc).isoformat(),
            "review_comment": review_comment,
        }
        result = supabase.table("credit_overrides")\
            .update(updated)\
            .eq("id", override_id)\
            .eq("operation_id", operation_id)\
            .execute()

        if status == "approved":
            self._apply_override(
                operation_id,
                override["override_type"],
                override["new_value"],
            )

        audit.log(
            operation_id=operation_id,
            action="override_applied",
            actor_id=reviewed_by,
            actor_type="analyst",
            ip_address=ip_address,
            override_reason=override.get("justificativa"),
            previous_value={override["override_type"]: override.get("previous_value")},
            new_value={override["override_type"]: override.get("new_value")},
            payload={
                "event": "override_reviewed",
                "override_id": override_id,
                "status": status,
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
