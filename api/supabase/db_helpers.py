from __future__ import annotations
from dotenv import load_dotenv
from datetime import datetime, timedelta, timezone
import base64
import hashlib
import hmac
import json
import os
import time
from typing import Any, Iterable

from api.supabase.supabase_client import get_supabase

load_dotenv()

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


def _coerce_user_id(resp: Any) -> str | None:
    """
    supabase-py has returned different shapes over time:
    - response.user.id
    - response.data.user.id
    - response.user (dict)
    """
    user = getattr(resp, "user", None)
    if user is None:
        data = getattr(resp, "data", None)
        if isinstance(data, dict):
            user = data.get("user")
        elif hasattr(data, "user"):
            user = data.user

    if user is None:
        return None

    if isinstance(user, dict):
        return user.get("id")

    if hasattr(user, "id"):
        return user.id

    if hasattr(user, "dict"):
        d = user.dict()
        return d.get("id")

    return None


# ----------------------------
# Auth helpers (server-side)
# ----------------------------

def get_auth_user_from_access_token(access_token: str) -> dict:
    """
    Verify the access token with Supabase and return a user dict-like object.
    """
    sb = get_supabase()
    resp = sb.auth.get_user(access_token)
    user = getattr(resp, "user", None) or getattr(resp, "data", None)
    if user is None:
        raise SupabaseError("Invalid auth token (no user returned).")
    # normalize to dict
    if isinstance(user, dict):
        return user
    if hasattr(user, "dict"):
        return user.dict()
    return {"id": getattr(user, "id", None), "email": getattr(user, "email", None)}


# ----------------------------
# Workspaces
# ----------------------------

def create_workspace(name: str) -> dict:
    sb = get_supabase()
    resp = sb.table("workspaces").insert({"name": name}).execute()
    return _expect_single(resp, context="create_workspace")


def get_workspace(workspace_id: str) -> dict | None:
    sb = get_supabase()
    resp = sb.table("workspaces").select("*").eq("id", workspace_id).limit(1).execute()
    return _maybe_single(resp)


# ----------------------------
# Users (PM staff + residents)
# ----------------------------

def create_user(
    workspace_id: str,
    *,
    full_name: str | None = None,
    phone: str | None = None,
    email: str | None = None,
    role: str = "resident",
    auth_user_id: str | None = None,
) -> dict:
    sb = get_supabase()
    payload = {
        "workspace_id": workspace_id,
        "full_name": full_name,
        "phone": phone,
        "email": email,
        "role": role,
        "auth_user_id": auth_user_id,
    }
    resp = sb.table("users").insert(payload).execute()
    return _expect_single(resp, context="create_user")


def get_user(user_id: str) -> dict | None:
    sb = get_supabase()
    resp = sb.table("users").select("*").eq("id", user_id).limit(1).execute()
    return _maybe_single(resp)


def get_user_by_auth_user_id(auth_user_id: str) -> dict | None:
    sb = get_supabase()
    resp = (
        sb.table("users")
        .select("*")
        .eq("auth_user_id", auth_user_id)
        .limit(1)
        .execute()
    )
    return _maybe_single(resp)


def list_users(workspace_id: str) -> list[dict]:
    sb = get_supabase()
    resp = (
        sb.table("users")
        .select("*")
        .eq("workspace_id", workspace_id)
        .order("created_at", desc=False)
        .execute()
    )
    return resp.data or []


# ----------------------------
# Pending accounts (waitlist/invites)
# ----------------------------

def create_pending_account(
    email: str,
    *,
    full_name: str | None = None,
    workspace_id: str | None = None,
) -> dict:
    sb = get_supabase()
    payload = {
        "email": email,
        "full_name": full_name,
        "workspace_id": workspace_id,  # NULL => waitlist entry
        "status": "pending",
    }
    resp = sb.table("pending_accounts").insert(payload).execute()
    return _expect_single(resp, context="create_pending_account")


def get_pending_account(pending_id: str) -> dict | None:
    sb = get_supabase()
    resp = (
        sb.table("pending_accounts")
        .select("*")
        .eq("id", pending_id)
        .limit(1)
        .execute()
    )
    return _maybe_single(resp)


def get_pending_account_by_email(
    email: str, *, workspace_id: str | None = None
) -> dict | None:
    sb = get_supabase()
    q = sb.table("pending_accounts").select("*").ilike("email", email).limit(1)
    if workspace_id is None:
        q = q.is_("workspace_id", "null")
    else:
        q = q.eq("workspace_id", workspace_id)
    resp = q.execute()
    return _maybe_single(resp)


def list_pending_accounts(
    status: str | None = None,
    *,
    workspace_id: str | None = None,
) -> list[dict]:
    sb = get_supabase()
    q = sb.table("pending_accounts").select("*")
    if status:
        q = q.eq("status", status)
    if workspace_id is None:
        # show all by default
        pass
    else:
        q = q.eq("workspace_id", workspace_id)
    resp = q.order("created_at", desc=False).execute()
    return resp.data or []


def approve_pending_account(
    pending_id: str,
    *,
    auth_user_id: str,
    workspace_id: str,
) -> dict:
    sb = get_supabase()
    payload = {
        "status": "approved",
        "auth_user_id": auth_user_id,
        "workspace_id": workspace_id,
        "approved_at": datetime.now(timezone.utc).isoformat(),
    }
    resp = (
        sb.table("pending_accounts")
        .update(payload)
        .eq("id", pending_id)
        .execute()
    )
    return _expect_single(resp, context="approve_pending_account")


def get_approved_pending_account_by_email(email: str) -> dict | None:
    sb = get_supabase()
    resp = (
        sb.table("pending_accounts")
        .select("*")
        .ilike("email", email)
        .eq("status", "approved")
        .limit(1)
        .execute()
    )
    return _maybe_single(resp)


# ----------------------------
# Supabase Auth Admin helpers
# ----------------------------

def create_auth_user(email: str) -> dict:
    sb = get_supabase()
    resp = sb.auth.admin.create_user(
        {
            "email": email,
            "email_confirm": True,
        }
    )
    # normalize best-effort
    user_id = _coerce_user_id(resp)
    user = getattr(resp, "user", None) or getattr(resp, "data", None) or {}
    if isinstance(user, dict):
        if user_id and "id" not in user:
            user["id"] = user_id
        return user
    if hasattr(user, "dict"):
        d = user.dict()
        if user_id and "id" not in d:
            d["id"] = user_id
        return d
    return {"id": user_id}


def generate_magic_link(email: str, redirect_to: str | None = None) -> str:
    sb = get_supabase()
    payload = {"type": "magiclink", "email": email}
    if redirect_to:
        payload['options'] = {"redirect_to": redirect_to}
    resp = sb.auth.admin.generate_link(payload)
    
    data = getattr(resp, "data", None) or resp
    action_link = getattr(getattr(data, "properties", None), "action_link", None)
    print(action_link)
    if not action_link:
        raise SupabaseError(f"No action link returned. resp={resp!r}")
    return action_link





# ----------------------------
# Properties & units
# ----------------------------

def create_property(
    workspace_id: str,
    *,
    address: str | None = None,
    zip_code: str | None = None,
) -> dict:
    sb = get_supabase()
    resp = (
        sb.table("properties")
        .insert({
            "workspace_id": workspace_id,
            "address": address,
            "zip_code": zip_code,
        })
        .execute()
    )
    return _expect_single(resp, context="create_property")


def get_property(property_id: str) -> dict | None:
    sb = get_supabase()
    resp = sb.table("properties").select("*").eq("id", property_id).limit(1).execute()
    return _maybe_single(resp)


def update_property(
    property_id: str,
    *,
    address: str | None = None,
    zip_code: str | None = None,
) -> dict:
    payload = {
        "address": address,
        "zip_code": zip_code,
    }
    updates = {k: v for k, v in payload.items() if v is not None}
    if not updates:
        return get_property(property_id) or {}
    sb = get_supabase()
    resp = sb.table("properties").update(updates).eq("id", property_id).execute()
    return _expect_single(resp, context="update_property")


def list_properties(workspace_id: str) -> list[dict]:
    sb = get_supabase()
    resp = (
        sb.table("properties")
        .select("*")
        .eq("workspace_id", workspace_id)
        .order("created_at", desc=False)
        .execute()
    )
    print(resp.data)
    return resp.data or []


def list_property_zip_codes(workspace_id: str) -> list[str]:
    sb = get_supabase()
    resp = (
        sb.table("properties")
        .select("zip_code")
        .eq("workspace_id", workspace_id)
        .not_.is_("zip_code", "null")
        .execute()
    )
    rows = resp.data or []
    zip_codes = {row.get("zip_code") for row in rows if row.get("zip_code")}
    return sorted(zip_codes)


def delete_work_orders_for_property(property_id: str) -> int:
    sb = get_supabase()
    existing = (
        sb.table("work_orders")
        .select("id")
        .eq("property_id", property_id)
        .execute()
    )
    work_order_rows = existing.data or []
    if not work_order_rows:
        return 0

    work_order_ids = [row.get("id") for row in work_order_rows if row.get("id")]
    if not work_order_ids:
        return 0

    sb.table("work_orders").delete().in_("id", work_order_ids).execute()
    return len(work_order_ids)


def delete_property(property_id: str) -> dict | None:
    sb = get_supabase()
    resp = sb.table("properties").delete().eq("id", property_id).execute()
    return _maybe_single(resp)


def list_resident_user_ids_for_property(property_id: str, workspace_id: str) -> list[str]:
    sb = get_supabase()
    occ_resp = (
        sb.table("occupancies")
        .select("user_id, units!inner(property_id)")
        .eq("units.property_id", property_id)
        .execute()
    )
    occupancy_rows = occ_resp.data or []
    candidate_user_ids = sorted({row.get("user_id") for row in occupancy_rows if row.get("user_id")})
    if not candidate_user_ids:
        return []

    users_resp = (
        sb.table("users")
        .select("id")
        .eq("workspace_id", workspace_id)
        .eq("role", "resident")
        .in_("id", candidate_user_ids)
        .execute()
    )
    resident_user_ids = {row.get("id") for row in (users_resp.data or []) if row.get("id")}
    if not resident_user_ids:
        return []

    deletable_user_ids: list[str] = []
    for user_id in sorted(resident_user_ids):
        other_occupancy_resp = (
            sb.table("occupancies")
            .select("id, units!inner(property_id)")
            .eq("user_id", user_id)
            .neq("units.property_id", property_id)
            .limit(1)
            .execute()
        )
        if not (other_occupancy_resp.data or []):
            deletable_user_ids.append(user_id)
    return deletable_user_ids


def delete_users_by_ids(user_ids: list[str]) -> int:
    if not user_ids:
        return 0
    sb = get_supabase()
    resp = sb.table("users").delete().in_("id", user_ids).execute()
    deleted_rows = resp.data or []
    return len(deleted_rows)


def create_unit(property_id: str, unit_label: str) -> dict:
    sb = get_supabase()
    resp = (
        sb.table("units")
        .insert({"property_id": property_id, "unit_label": unit_label})
        .execute()
    )
    return _expect_single(resp, context="create_unit")


def list_units(property_id: str) -> list[dict]:
    sb = get_supabase()
    resp = (
        sb.table("units")
        .select("*")
        .eq("property_id", property_id)
        .order("unit_label", desc=False)
        .execute()
    )
    return resp.data or []


def get_unit(unit_id: str) -> dict | None:
    sb = get_supabase()
    resp = sb.table("units").select("*").eq("id", unit_id).limit(1).execute()
    return _maybe_single(resp)


def update_unit(
    unit_id: str,
    *,
    unit_label: str | None = None,
) -> dict:
    if unit_label is None:
        return get_unit(unit_id) or {}
    sb = get_supabase()
    resp = sb.table("units").update({"unit_label": unit_label}).eq("id", unit_id).execute()
    return _expect_single(resp, context="update_unit")


# ----------------------------
# Work orders (workspace derived via property)
# ----------------------------

def create_work_order(
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
        "property_id": property_id,
        "unit_id": unit_id,
        "reported_by_user_id": reported_by_user_id,
        "title": title,
        "description": description,
        "priority": priority,
        "status": status,
    }
    resp = sb.table("work_orders").insert(payload).execute()
    return _expect_single(resp, context="create_work_order")


def get_work_order(work_order_id: str) -> dict | None:
    sb = get_supabase()
    resp = sb.table("work_orders").select("*").eq("id", work_order_id).limit(1).execute()
    return _maybe_single(resp)


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
    updates = {k: v for k, v in payload.items() if v is not None}
    if not updates:
        return get_work_order(work_order_id) or {}
    sb = get_supabase()
    resp = sb.table("work_orders").update(updates).eq("id", work_order_id).execute()
    return _expect_single(resp, context="update_work_order")


def list_work_orders_for_properties(property_ids: Iterable[str]) -> list[dict]:
    ids = [pid for pid in property_ids if pid]
    if not ids:
        return []
    sb = get_supabase()
    resp = (
        sb.table("work_orders")
        .select("*")
        .in_("property_id", ids)
        .order("created_at", desc=True)
        .execute()
    )
    return resp.data or []


def list_work_orders(workspace_id: str) -> list[dict]:
    sb = get_supabase()
    resp = (
        sb.table("work_orders")
        .select("*, properties!inner(workspace_id)")
        .eq("properties.workspace_id", workspace_id)
        .order("created_at", desc=True)
        .execute()
    )
    rows = resp.data or []
    for row in rows:
        row.pop("properties", None)
    return rows


def list_units_table_rows(workspace_id: str) -> list[dict]:
    sb = get_supabase()
    resp = sb.rpc("list_units_table_rows", {"workspace_id": workspace_id}).execute()
    rows = resp.data or []
    normalized: list[dict] = []
    for row in rows:
        tenant_user = None
        if row.get("tenant_user_id"):
            tenant_user = {
                "id": row.get("tenant_user_id"),
                "full_name": row.get("tenant_full_name"),
                "email": row.get("tenant_email"),
                "phone": row.get("tenant_phone"),
            }
        latest_work_orders = row.get("latest_work_orders") or []
        normalized.append(
            {
                "property_id": row.get("property_id"),
                "property_address": row.get("property_address"),
                "property_zip_code": row.get("property_zip_code"),
                "unit_id": row.get("unit_id"),
                "unit_label": row.get("unit_label"),
                "tenant_user": tenant_user,
                "manager_name": row.get("manager_name"),
                "maintenance_requests": row.get("maintenance_requests") or "",
                "last_updated_at": row.get("last_updated_at"),
                "has_open_request": row.get("has_open_request", False),
                "latest_work_orders": latest_work_orders,
            }
        )
    return normalized


# ----------------------------
# Occupancies
# ----------------------------

def create_occupancy(
    workspace_id: str,
    *,
    unit_id: str,
    user_id: str,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
) -> dict:
    sb = get_supabase()
    payload = {
        "workspace_id": workspace_id,
        "unit_id": unit_id,
        "user_id": user_id,
        "start_at": start_at.isoformat() if isinstance(start_at, datetime) else start_at,
        "end_at": end_at.isoformat() if isinstance(end_at, datetime) else end_at,
    }
    resp = sb.table("occupancies").insert(payload).execute()
    return _expect_single(resp, context="create_occupancy")


def list_occupancies(
    workspace_id: str,
    *,
    unit_id: str | None = None,
    user_id: str | None = None,
) -> list[dict]:
    sb = get_supabase()
    q = sb.table("occupancies").select("*").eq("workspace_id", workspace_id)
    if unit_id:
        q = q.eq("unit_id", unit_id)
    if user_id:
        q = q.eq("user_id", user_id)
    resp = q.order("created_at", desc=False).execute()
    return resp.data or []


# ----------------------------
# Conversations & messages
# ----------------------------

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
    resp = sb.table("conversations").insert(payload).execute()
    return _expect_single(resp, context="create_conversation")


def list_conversations(work_order_id: str) -> list[dict]:
    sb = get_supabase()
    resp = (
        sb.table("conversations")
        .select("*")
        .eq("work_order_id", work_order_id)
        .order("created_at", desc=False)
        .execute()
    )
    return resp.data or []


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
    resp = sb.table("messages").insert(payload).execute()
    return _expect_single(resp, context="create_message")


def list_messages_for_conversation(conversation_id: str) -> list[dict]:
    sb = get_supabase()
    resp = (
        sb.table("messages")
        .select("*")
        .eq("conversation_id", conversation_id)
        .order("created_at", desc=False)
        .execute()
    )
    return resp.data or []


def list_messages_for_work_order(work_order_id: str) -> list[dict]:
    sb = get_supabase()
    resp = (
        sb.table("messages")
        .select("*")
        .eq("work_order_id", work_order_id)
        .order("created_at", desc=False)
        .execute()
    )
    return resp.data or []


def find_message_by_external_id(external_message_id: str) -> dict | None:
    sb = get_supabase()
    resp = (
        sb.table("messages")
        .select("*")
        .contains("raw_payload", {"external_message_id": external_message_id})
        .limit(1)
        .execute()
    )
    return _maybe_single(resp)


# ----------------------------
# Media assets
# ----------------------------

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
    resp = sb.table("media_assets").insert(payload).execute()
    return _expect_single(resp, context="create_media_asset")

#TODO: to be removed
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

#TODO: to be removed
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

def list_media_assets_for_work_order(work_order_id: str) -> list[dict]:
    sb = get_supabase()
    resp = (
        sb.table("media_assets")
        .select("*")
        .eq("work_order_id", work_order_id)
        .order("created_at", desc=False)
        .execute()
    )
    return resp.data or []


def list_media_assets_for_message(message_id: str) -> list[dict]:
    sb = get_supabase()
    resp = (
        sb.table("media_assets")
        .select("*")
        .eq("message_id", message_id)
        .order("created_at", desc=False)
        .execute()
    )
    return resp.data or []
