from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from api.supabase.supabase_client import get_supabase


class SupabaseError(RuntimeError):
    """Raised when Supabase operations fail or return unexpected results."""


def _expect_single(response: Any, *, context: str) -> dict:
    data = getattr(response, "data", None)
    if not data:
        raise SupabaseError(f"No data returned for {context}.")
    return data[0]


def _maybe_single(response: Any) -> dict | None:
    data = getattr(response, "data", None)
    if not data:
        return None
    return data[0]


def create_tenant(name: str) -> dict:
    sb = get_supabase()
    response = sb.table("tenants").insert({"name": name}).execute()
    return _expect_single(response, context="create_tenant")


def get_tenant(tenant_id: str) -> dict | None:
    sb = get_supabase()
    response = sb.table("tenants").select("*").eq("id", tenant_id).execute()
    return _maybe_single(response)


def create_user(
    tenant_id: str,
    *,
    full_name: str | None = None,
    phone: str | None = None,
    email: str | None = None,
) -> dict:
    sb = get_supabase()
    payload = {
        "tenant_id": tenant_id,
        "full_name": full_name,
        "phone": phone,
        "email": email,
    }
    response = sb.table("users").insert(payload).execute()
    return _expect_single(response, context="create_user")


def list_users(tenant_id: str) -> list[dict]:
    sb = get_supabase()
    response = (
        sb.table("users")
        .select("*")
        .eq("tenant_id", tenant_id)
        .order("created_at", desc=False)
        .execute()
    )
    return response.data or []


def get_user_by_email(email: str) -> dict | None:
    sb = get_supabase()
    #debug
    allusers = sb.table("users").select("*").execute()
    print(f"All users in DB: {allusers.data}")
    response = (
        sb.table("users")
        .select("*")
        .ilike("email", email)
        .limit(1)
        .execute()
    )
    return _maybe_single(response)


def create_pending_account(
    email: str,
    *,
    full_name: str | None = None,
) -> dict:
    sb = get_supabase()
    payload = {
        "email": email,
        "full_name": full_name,
        "status": "pending",
    }
    response = sb.table("pending_accounts").insert(payload).execute()
    return _expect_single(response, context="create_pending_account")


def get_pending_account(pending_id: str) -> dict | None:
    sb = get_supabase()
    response = sb.table("pending_accounts").select("*").eq("id", pending_id).execute()
    return _maybe_single(response)


def get_pending_account_by_email(email: str) -> dict | None:
    sb = get_supabase()
    response = (
        sb.table("pending_accounts")
        .select("*")
        .ilike("email", email)
        .limit(1)
        .execute()
    )
    return _maybe_single(response)


def list_pending_accounts(status: str | None = None) -> list[dict]:
    sb = get_supabase()
    query = sb.table("pending_accounts").select("*")
    if status:
        query = query.eq("status", status)
    response = query.order("created_at", desc=False).execute()
    return response.data or []


def approve_pending_account(pending_id: str, *, auth_user_id: str) -> dict:
    sb = get_supabase()
    payload = {
        "status": "approved",
        "auth_user_id": auth_user_id,
        "approved_at": datetime.now(timezone.utc).isoformat(),
    }
    response = (
        sb.table("pending_accounts")
        .update(payload)
        .eq("id", pending_id)
        .execute()
    )
    return _expect_single(response, context="approve_pending_account")


def create_auth_user(email: str) -> dict:
    sb = get_supabase()
    response = sb.auth.admin.create_user(
        {
            "email": email,
            "email_confirm": True,
        }
    )
    data = getattr(response, "user", None)
    if data is None:
        data = getattr(response, "data", None)
    if isinstance(data, dict):
        return data
    if hasattr(data, "dict"):
        return data.dict()
    return data or {}


def generate_magic_link(email: str, redirect_to: str | None = None) -> str:
    sb = get_supabase()
    payload = {"type": "magiclink", "email": email}
    if redirect_to:
        payload["redirect_to"] = redirect_to
    response = sb.auth.admin.generate_link(payload)
    data = getattr(response, "data", None) or response
    if isinstance(data, dict):
        action_link = data.get("action_link") or data.get("actionLink")
        if action_link:
            return action_link
    raise SupabaseError("No action link returned from generate_link.")


def get_default_property_for_tenant(tenant_id: str) -> dict | None:
    sb = get_supabase()
    response = (
        sb.table("properties")
        .select("*")
        .eq("tenant_id", tenant_id)
        .order("created_at", desc=False)
        .limit(1)
        .execute()
    )
    return _maybe_single(response)


def create_property(tenant_id: str, *, address: str | None = None) -> dict:
    sb = get_supabase()
    response = (
        sb.table("properties")
        .insert({"tenant_id": tenant_id, "address": address})
        .execute()
    )
    return _expect_single(response, context="create_property")


def list_properties(tenant_id: str) -> list[dict]:
    sb = get_supabase()
    response = (
        sb.table("properties")
        .select("*")
        .eq("tenant_id", tenant_id)
        .order("created_at", desc=False)
        .execute()
    )
    return response.data or []


def create_unit(property_id: str, unit_label: str) -> dict:
    sb = get_supabase()
    response = (
        sb.table("units")
        .insert({"property_id": property_id, "unit_label": unit_label})
        .execute()
    )
    return _expect_single(response, context="create_unit")


def list_units(property_id: str) -> list[dict]:
    sb = get_supabase()
    response = (
        sb.table("units")
        .select("*")
        .eq("property_id", property_id)
        .order("unit_label", desc=False)
        .execute()
    )
    return response.data or []


def create_work_order(
    tenant_id: str,
    property_id: str,
    *,
    unit_id: str | None = None,
    reported_by_user_id: str | None = None,
    title: str | None = None,
    description: str | None = None,
    priority: str | None = None,
    status: str = "new",
) -> dict:
    sb = get_supabase()
    payload = {
        "tenant_id": tenant_id,
        "property_id": property_id,
        "unit_id": unit_id,
        "reported_by_user_id": reported_by_user_id,
        "title": title,
        "description": description,
        "priority": priority,
        "status": status,
    }
    response = sb.table("work_orders").insert(payload).execute()
    return _expect_single(response, context="create_work_order")


def get_work_order(work_order_id: str) -> dict | None:
    sb = get_supabase()
    response = sb.table("work_orders").select("*").eq("id", work_order_id).execute()
    return _maybe_single(response)


def update_work_order(
    work_order_id: str,
    *,
    title: str | None = None,
    description: str | None = None,
    priority: str | None = None,
    status: str | None = None,
) -> dict:
    payload = {
        "title": title,
        "description": description,
        "priority": priority,
        "status": status,
    }
    updates = {key: value for key, value in payload.items() if value is not None}
    if not updates:
        return get_work_order(work_order_id) or {}
    sb = get_supabase()
    response = sb.table("work_orders").update(updates).eq("id", work_order_id).execute()
    return _expect_single(response, context="update_work_order")


def list_work_orders(tenant_id: str) -> list[dict]:
    sb = get_supabase()
    response = (
        sb.table("work_orders")
        .select("*")
        .eq("tenant_id", tenant_id)
        .order("created_at", desc=True)
        .execute()
    )
    return response.data or []


def create_conversation(
    work_order_id: str,
    *,
    party_type: str,
    party_user_id: str | None = None,
) -> dict:
    sb = get_supabase()
    payload = {
        "work_order_id": work_order_id,
        "party_type": party_type,
        "party_user_id": party_user_id,
    }
    response = sb.table("conversations").insert(payload).execute()
    return _expect_single(response, context="create_conversation")


def list_conversations(work_order_id: str) -> list[dict]:
    sb = get_supabase()
    response = (
        sb.table("conversations")
        .select("*")
        .eq("work_order_id", work_order_id)
        .order("created_at", desc=False)
        .execute()
    )
    return response.data or []


def create_message(
    conversation_id: str,
    work_order_id: str,
    *,
    direction: str,
    channel: str,
    sender_user_id: str | None = None,
    recipient_user_id: str | None = None,
    body: str | None = None,
    raw_payload: dict | None = None,
) -> dict:
    sb = get_supabase()
    payload = {
        "conversation_id": conversation_id,
        "work_order_id": work_order_id,
        "direction": direction,
        "channel": channel,
        "sender_user_id": sender_user_id,
        "recipient_user_id": recipient_user_id,
        "body": body,
        "raw_payload": raw_payload,
    }
    response = sb.table("messages").insert(payload).execute()
    return _expect_single(response, context="create_message")


def list_messages_for_conversation(conversation_id: str) -> list[dict]:
    sb = get_supabase()
    response = (
        sb.table("messages")
        .select("*")
        .eq("conversation_id", conversation_id)
        .order("created_at", desc=False)
        .execute()
    )
    return response.data or []

def find_message_by_external_id(external_message_id: str) -> dict | None:
    sb = get_supabase()
    response = (
        sb.table("messages")
        .select("*")
        .contains("raw_payload", {"external_message_id": external_message_id})
        .limit(1)
        .execute()
    )
    return _maybe_single(response)


def list_messages_for_work_order(work_order_id: str) -> list[dict]:
    sb = get_supabase()
    response = (
        sb.table("messages")
        .select("*")
        .eq("work_order_id", work_order_id)
        .order("created_at", desc=False)
        .execute()
    )
    return response.data or []


def create_media_asset(
    work_order_id: str,
    *,
    storage_path: str,
    message_id: str | None = None,
    uploaded_by_user_id: str | None = None,
    media_type: str = "image",
    file_name: str | None = None,
    content_type: str | None = None,
    byte_size: int | None = None,
    width: int | None = None,
    height: int | None = None,
    public_url: str | None = None,
) -> dict:
    sb = get_supabase()
    payload = {
        "work_order_id": work_order_id,
        "message_id": message_id,
        "uploaded_by_user_id": uploaded_by_user_id,
        "media_type": media_type,
        "file_name": file_name,
        "content_type": content_type,
        "byte_size": byte_size,
        "width": width,
        "height": height,
        "storage_path": storage_path,
        "public_url": public_url,
    }
    response = sb.table("media_assets").insert(payload).execute()
    return _expect_single(response, context="create_media_asset")


def list_media_assets_for_work_order(work_order_id: str) -> list[dict]:
    sb = get_supabase()
    response = (
        sb.table("media_assets")
        .select("*")
        .eq("work_order_id", work_order_id)
        .order("created_at", desc=False)
        .execute()
    )
    return response.data or []


def list_media_assets_for_message(message_id: str) -> list[dict]:
    sb = get_supabase()
    response = (
        sb.table("media_assets")
        .select("*")
        .eq("message_id", message_id)
        .order("created_at", desc=False)
        .execute()
    )
    return response.data or []
