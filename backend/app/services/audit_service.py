"""
AuditService: trilha de auditoria imutável (append-only).
Registra toda ação relevante do sistema: decisões, overrides, uploads, erros.
"""
from typing import Optional
from app.core.database import supabase
import structlog

logger = structlog.get_logger()


class AuditService:

    def log(
        self,
        operation_id: Optional[str],
        action: str,
        payload: Optional[dict] = None,
        actor_id: Optional[str] = None,
        actor_type: str = "system",
        ip_address: Optional[str] = None,
        override_reason: Optional[str] = None,
        previous_value: Optional[dict] = None,
        new_value: Optional[dict] = None,
    ):
        """
        Registra evento no audit trail.
        Operação append-only — não há update ou delete no banco (rule SQL).
        """
        data = {
            "action": action,
            "actor_type": actor_type,
        }

        if operation_id:
            data["operation_id"] = operation_id
        if payload:
            data["payload"] = payload
        if actor_id:
            data["actor_id"] = actor_id
        if ip_address:
            data["ip_address"] = ip_address
        if override_reason:
            data["override_reason"] = override_reason
        if previous_value:
            data["previous_value"] = previous_value
        if new_value:
            data["new_value"] = new_value

        try:
            supabase.table("audit_trail").insert(data).execute()
        except Exception as e:
            # Nunca deixar falha de auditoria derrubar o fluxo principal
            logger.error("audit.failed", action=action, error=str(e))

    def log_override(
        self,
        operation_id: str,
        actor_id: str,
        field: str,
        previous_value,
        new_value,
        reason: str,
        ip_address: Optional[str] = None,
    ):
        """Registra override manual com justificativa obrigatória."""
        self.log(
            operation_id=operation_id,
            action="override_applied",
            actor_id=actor_id,
            actor_type="analyst",
            ip_address=ip_address,
            override_reason=reason,
            previous_value={field: previous_value},
            new_value={field: new_value},
            payload={"field": field},
        )

    def get_trail(self, operation_id: str) -> list[dict]:
        """Retorna trilha completa de uma operação, ordenada por tempo."""
        result = supabase.table("audit_trail")\
            .select("action, actor_type, payload, override_reason, previous_value, new_value, created_at")\
            .eq("operation_id", operation_id)\
            .order("created_at", desc=False)\
            .execute()
        return result.data
