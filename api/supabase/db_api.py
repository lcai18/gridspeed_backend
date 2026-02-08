from __future__ import annotations

import os
import secrets
from datetime import timedelta, timezone, datetime
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel

from api.email_agent.email import send_email
from api.supabase.db_helpers import (
    SupabaseError,
    approve_pending_account,
    create_auth_user,
    create_conversation,
    create_occupancy,
    create_media_asset,
    create_message,
    create_pending_account,
    create_property,
    create_unit,
    create_user,
    create_workspace,
    create_work_order,
    generate_magic_link,
    get_unit,
    get_approved_pending_account_by_email,
    get_auth_user_from_access_token,
    get_pending_account,
    get_pending_account_by_email,
    get_property,
    get_user_by_auth_user_id,
    get_user,
    get_work_order,
    get_workspace,
    list_conversations,
    list_occupancies,
    list_media_assets_for_message,
    list_media_assets_for_work_order,
    list_messages_for_conversation,
    list_messages_for_work_order,
    list_pending_accounts,
    list_properties,
    list_property_zip_codes,
    list_units_table_rows,
    list_units,
    list_users,
    list_work_orders,
    update_work_order,
)

router = APIRouter()
admin_auth = HTTPBasic()


# ----------------------------
# Admin auth (basic auth)
# ----------------------------

def require_admin_auth(credentials: HTTPBasicCredentials = Depends(admin_auth)) -> None:
    username = os.getenv("ADMIN_USERNAME")
    password = os.getenv("ADMIN_PASSWORD")
    if not username or not password:
        raise HTTPException(status_code=500, detail="Admin basic auth is not configured")
    is_username_valid = secrets.compare_digest(credentials.username, username)
    is_password_valid = secrets.compare_digest(credentials.password, password)
    if not (is_username_valid and is_password_valid):
        raise HTTPException(
            status_code=401,
            detail="Unauthorized",
            headers={"WWW-Authenticate": "Basic"},
        )


# ----------------------------
# App auth (Supabase JWT)
# ----------------------------

class AuthContext(BaseModel):
    auth_user_id: str
    email: str | None = None
    user: dict
    workspace: dict


def _extract_bearer_token(authorization: str | None) -> str:
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(status_code=401, detail="Invalid Authorization header")
    token = parts[1].strip()
    if not token:
        raise HTTPException(status_code=401, detail="Invalid Authorization token")
    return token


def require_auth(authorization: str | None = Header(default=None)) -> AuthContext:
    """
    Verifies Supabase JWT, maps auth user -> app user -> workspace.
    """
    token = _extract_bearer_token(authorization)
    try:
        auth_user = get_auth_user_from_access_token(token)
    except SupabaseError as e:
        raise HTTPException(status_code=401, detail=str(e))

    auth_user_id = auth_user.get("id")
    email = auth_user.get("email")
    if not auth_user_id:
        raise HTTPException(status_code=401, detail="Invalid auth user (no id)")


    app_user = get_user_by_auth_user_id(auth_user_id)
    if app_user is None:
        raise HTTPException(
            status_code=403,
            detail="No app user found for this login. Did an admin approve the account?",
        )

    workspace_id = app_user.get("workspace_id")
    if not workspace_id:
        raise HTTPException(status_code=500, detail="App user has no workspace_id")

    workspace = get_workspace(workspace_id)
    if workspace is None:
        raise HTTPException(status_code=500, detail="Workspace not found")

    return AuthContext(
        auth_user_id=auth_user_id,
        email=email,
        user=app_user,
        workspace=workspace,
    )


def _ensure_property_in_workspace(ctx: AuthContext, property_id: str) -> dict:
    prop = get_property(property_id)
    if prop is None:
        raise HTTPException(status_code=404, detail="Property not found")
    if prop.get("workspace_id") != ctx.workspace.get("id"):
        raise HTTPException(status_code=403, detail="Forbidden")
    return prop


def _ensure_work_order_in_workspace(ctx: AuthContext, work_order_id: str) -> dict:
    wo = get_work_order(work_order_id)
    if wo is None:
        raise HTTPException(status_code=404, detail="Work order not found")

    prop_id = wo.get("property_id")
    if not prop_id:
        raise HTTPException(status_code=500, detail="Work order missing property_id")

    prop = get_property(prop_id)
    if prop is None:
        raise HTTPException(status_code=500, detail="Work order property not found")

    if prop.get("workspace_id") != ctx.workspace.get("id"):
        raise HTTPException(status_code=403, detail="Forbidden")
    return wo


def _ensure_unit_in_workspace(ctx: AuthContext, unit_id: str) -> dict:
    unit = get_unit(unit_id)
    if unit is None:
        raise HTTPException(status_code=404, detail="Unit not found")
    property_id = unit.get("property_id")
    if not property_id:
        raise HTTPException(status_code=500, detail="Unit missing property_id")
    _ensure_property_in_workspace(ctx, property_id)
    return unit


def _ensure_user_in_workspace(ctx: AuthContext, user_id: str) -> dict:
    user = get_user(user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    if user.get("workspace_id") != ctx.workspace.get("id"):
        raise HTTPException(status_code=403, detail="Forbidden")
    return user


# ----------------------------
# Request models
# ----------------------------

class UserCreate(BaseModel):
    full_name: str | None = None
    phone: str | None = None
    email: str | None = None
    role: str | None = None  # pm_admin | staff | resident


class PropertyCreate(BaseModel):
    address: str | None = None
    zip_code: str | None = None


class UnitCreate(BaseModel):
    unit_label: str


class WorkOrderCreate(BaseModel):
    property_id: str
    unit_id: str | None = None
    reported_by_user_id: str | None = None
    title: str | None = None
    description: str | None = None
    priority: str | None = None
    status: str | None = None


class WorkOrderUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    priority: str | None = None
    status: str | None = None


class ConversationCreate(BaseModel):
    party_type: str
    party_user_id: str | None = None


class MessageCreate(BaseModel):
    work_order_id: str
    direction: str
    channel: str
    sender_user_id: str | None = None
    recipient_user_id: str | None = None
    body: str | None = None
    raw_payload: dict | None = None


class MediaAssetCreate(BaseModel):
    message_id: str | None = None
    uploaded_by_user_id: str | None = None
    media_type: str | None = None
    file_name: str | None = None
    content_type: str | None = None
    byte_size: int | None = None
    width: int | None = None
    height: int | None = None
    storage_path: str
    public_url: str | None = None


class OccupancyCreate(BaseModel):
    unit_id: str
    user_id: str
    start_at: datetime | None = None
    end_at: datetime | None = None


class PendingAccountCreate(BaseModel):
    email: str
    full_name: str | None = None


class PendingAccountApprove(BaseModel):
    # If approving a waitlist entry (no workspace yet), admin provides workspace_name
    workspace_name: str | None = None
    full_name: str | None = None


class MagicLinkRequest(BaseModel):
    email: str
    redirect_to: str | None = None


class MagicLinkEmailRequest(BaseModel):
    email: str
    redirect_to: str | None = None
    subject: str | None = None


class TenantUser(BaseModel):
    id: str
    full_name: str | None = None
    email: str | None = None
    phone: str | None = None


class WorkOrderSummary(BaseModel):
    id: str
    title: str | None = None
    status: str | None = None
    updated_at: datetime | None = None
    created_at: datetime | None = None
    reported_by_user_id: str | None = None


class UnitsTableRow(BaseModel):
    property_id: str
    property_address: str | None = None
    unit_id: str
    unit_label: str | None = None
    tenant_user: TenantUser | None = None
    manager_name: str
    maintenance_requests: str
    last_updated_at: datetime | None = None
    has_open_request: bool | None = None
    latest_work_orders: list[WorkOrderSummary] | None = None


# ----------------------------
# Auth/session
# ----------------------------

@router.get("/me")
async def me(ctx: AuthContext = Depends(require_auth)):
    return {"user": ctx.user, "workspace": ctx.workspace}


# ----------------------------
# Users (workspace-scoped)
# ----------------------------

@router.get("/users")
async def list_users_endpoint(ctx: AuthContext = Depends(require_auth)):
    return list_users(ctx.workspace["id"])


@router.post("/users")
async def create_user_endpoint(payload: UserCreate, ctx: AuthContext = Depends(require_auth)):
    # NOTE: for now, allow any authenticated user to create.
    # Later: restrict to pm_admin/staff via ctx.user["role"].
    return create_user(
        ctx.workspace["id"],
        full_name=payload.full_name,
        phone=payload.phone,
        email=payload.email,
        role=payload.role or "resident",
    )


# ----------------------------
# Properties (workspace-scoped)
# ----------------------------

# NOTE: The property table UI should use /dashboard/units-table instead of
# stitching /properties, /occupancies, /properties/{id}/units, and /work-orders.
@router.get("/properties")
async def list_properties_endpoint(ctx: AuthContext = Depends(require_auth)):
    return list_properties(ctx.workspace["id"])




@router.get("/properties/zip-codes")
async def list_property_zip_codes_endpoint(ctx: AuthContext = Depends(require_auth)):
    return list_property_zip_codes(ctx.workspace["id"])

@router.post("/properties")
async def create_property_endpoint(payload: PropertyCreate, ctx: AuthContext = Depends(require_auth)):
    return create_property(
        ctx.workspace["id"],
        address=payload.address,
        zip_code=payload.zip_code,
    )


# ----------------------------
# Units (scoped by property -> workspace)
# ----------------------------

@router.get("/properties/{property_id}/units")
async def list_units_endpoint(property_id: str, ctx: AuthContext = Depends(require_auth)):
    _ensure_property_in_workspace(ctx, property_id)
    return list_units(property_id)


@router.post("/properties/{property_id}/units")
async def create_unit_endpoint(property_id: str, payload: UnitCreate, ctx: AuthContext = Depends(require_auth)):
    _ensure_property_in_workspace(ctx, property_id)
    return create_unit(property_id, payload.unit_label)


# ----------------------------
# Occupancies (workspace-scoped)
# ----------------------------

@router.get("/occupancies")
async def list_occupancies_endpoint(
    unit_id: str | None = None,
    user_id: str | None = None,
    ctx: AuthContext = Depends(require_auth),
):
    if unit_id:
        _ensure_unit_in_workspace(ctx, unit_id)
    if user_id:
        _ensure_user_in_workspace(ctx, user_id)
    return list_occupancies(ctx.workspace["id"], unit_id=unit_id, user_id=user_id)


@router.post("/occupancies")
async def create_occupancy_endpoint(payload: OccupancyCreate, ctx: AuthContext = Depends(require_auth)):
    _ensure_unit_in_workspace(ctx, payload.unit_id)
    _ensure_user_in_workspace(ctx, payload.user_id)
    return create_occupancy(
        ctx.workspace["id"],
        unit_id=payload.unit_id,
        user_id=payload.user_id,
        start_at=payload.start_at,
        end_at=payload.end_at,
    )


# ----------------------------
# Dashboard (hydrated table)
# ----------------------------

@router.get("/dashboard/units-table", response_model=list[UnitsTableRow])
async def list_units_table_rows_endpoint(ctx: AuthContext = Depends(require_auth)):
    return list_units_table_rows(ctx.workspace["id"])


# ----------------------------
# Work orders (workspace derived via property)
# ----------------------------

@router.get("/work-orders")
async def list_work_orders_endpoint(ctx: AuthContext = Depends(require_auth)):
    return list_work_orders(ctx.workspace["id"])


@router.post("/work-orders")
async def create_work_order_endpoint(payload: WorkOrderCreate, ctx: AuthContext = Depends(require_auth)):
    _ensure_property_in_workspace(ctx, payload.property_id)
    return create_work_order(
        payload.property_id,
        unit_id=payload.unit_id,
        reported_by_user_id=payload.reported_by_user_id,
        title=payload.title,
        description=payload.description,
        priority=payload.priority,
        status=payload.status or "new",
    )


@router.get("/work-orders/{work_order_id}")
async def get_work_order_endpoint(work_order_id: str, ctx: AuthContext = Depends(require_auth)):
    return _ensure_work_order_in_workspace(ctx, work_order_id)


@router.patch("/work-orders/{work_order_id}")
async def update_work_order_endpoint(
    work_order_id: str,
    payload: WorkOrderUpdate,
    ctx: AuthContext = Depends(require_auth),
):
    _ensure_work_order_in_workspace(ctx, work_order_id)
    return update_work_order(
        work_order_id,
        title=payload.title,
        description=payload.description,
        priority=payload.priority,
        status=payload.status,
    )


# ----------------------------
# Conversations & messages
# ----------------------------

@router.post("/work-orders/{work_order_id}/conversations")
async def create_conversation_endpoint(
    work_order_id: str, payload: ConversationCreate, ctx: AuthContext = Depends(require_auth)
):
    _ensure_work_order_in_workspace(ctx, work_order_id)
    return create_conversation(
        work_order_id,
        party_type=payload.party_type,
        party_user_id=payload.party_user_id,
    )


@router.get("/work-orders/{work_order_id}/conversations")
async def list_conversations_endpoint(work_order_id: str, ctx: AuthContext = Depends(require_auth)):
    _ensure_work_order_in_workspace(ctx, work_order_id)
    return list_conversations(work_order_id)


@router.post("/conversations/{conversation_id}/messages")
async def create_message_endpoint(
    conversation_id: str, payload: MessageCreate, ctx: AuthContext = Depends(require_auth)
):
    # Ensure work order is in workspace (and we only let messages be created for that WO)
    _ensure_work_order_in_workspace(ctx, payload.work_order_id)
    return create_message(
        conversation_id,
        payload.work_order_id,
        direction=payload.direction,
        channel=payload.channel,
        sender_user_id=payload.sender_user_id,
        recipient_user_id=payload.recipient_user_id,
        body=payload.body,
        raw_payload=payload.raw_payload,
    )


@router.get("/conversations/{conversation_id}/messages")
async def list_messages_for_conversation_endpoint(conversation_id: str, ctx: AuthContext = Depends(require_auth)):
    # NOTE: without a conversation->work_order lookup helper, we can't enforce here easily.
    # MVP approach: rely on frontend using correct IDs + enforce on write paths.
    # Better: add db_helpers.get_conversation(conversation_id) and enforce via work_order_id.
    return list_messages_for_conversation(conversation_id)


@router.get("/work-orders/{work_order_id}/messages")
async def list_messages_for_work_order_endpoint(work_order_id: str, ctx: AuthContext = Depends(require_auth)):
    _ensure_work_order_in_workspace(ctx, work_order_id)
    return list_messages_for_work_order(work_order_id)


# ----------------------------
# Media assets
# ----------------------------

@router.post("/work-orders/{work_order_id}/media-assets")
async def create_media_asset_endpoint(
    work_order_id: str, payload: MediaAssetCreate, ctx: AuthContext = Depends(require_auth)
):
    _ensure_work_order_in_workspace(ctx, work_order_id)
    return create_media_asset(
        work_order_id,
        message_id=payload.message_id,
        uploaded_by_user_id=payload.uploaded_by_user_id,
        media_type=payload.media_type or "image",
        file_name=payload.file_name,
        content_type=payload.content_type,
        byte_size=payload.byte_size,
        width=payload.width,
        height=payload.height,
        storage_path=payload.storage_path,
        public_url=payload.public_url,
    )


@router.get("/work-orders/{work_order_id}/media-assets")
async def list_media_assets_for_work_order_endpoint(work_order_id: str, ctx: AuthContext = Depends(require_auth)):
    _ensure_work_order_in_workspace(ctx, work_order_id)
    return list_media_assets_for_work_order(work_order_id)


@router.get("/messages/{message_id}/media-assets")
async def list_media_assets_for_message_endpoint(message_id: str, ctx: AuthContext = Depends(require_auth)):
    # NOTE: same limitation as conversation messages (need message->work_order lookup for strict enforcement).
    return list_media_assets_for_message(message_id)


# ----------------------------
# Waitlist / Pending accounts
# ----------------------------

@router.post("/auth/pending-accounts")
async def create_pending_account_endpoint(payload: PendingAccountCreate):
    """
    Public: submit waitlist entry (workspace_id NULL).
    """
    existing = get_pending_account_by_email(payload.email, workspace_id=None)
    if existing:
        return existing
    return create_pending_account(payload.email, full_name=payload.full_name, workspace_id=None)


@router.get("/admin/pending-accounts")
async def list_pending_accounts_endpoint(
    status: str | None = None,
    _: None = Depends(require_admin_auth),
):
    return list_pending_accounts(status=status)


@router.post("/admin/pending-accounts/{pending_id}/approve")
async def approve_pending_account_endpoint(
    pending_id: str,
    payload: PendingAccountApprove,
    _: None = Depends(require_admin_auth),
):
    pending = get_pending_account(pending_id)
    if pending is None:
        raise HTTPException(status_code=404, detail="Pending account not found")

    # Determine/ensure workspace
    workspace_id = pending.get("workspace_id")
    if not workspace_id:
        if not payload.workspace_name:
            raise HTTPException(
                status_code=400,
                detail="workspace_name is required when approving a waitlist entry",
            )
        ws = create_workspace(payload.workspace_name)
        workspace_id = ws["id"]

    # Create Supabase Auth user
    auth_user = create_auth_user(pending["email"])
    auth_user_id = auth_user.get("id") if isinstance(auth_user, dict) else None
    if not auth_user_id:
        raise HTTPException(status_code=500, detail="Failed to create auth user")

    # Create app user row mapped to auth_user_id
    db_user = create_user(
        workspace_id,
        full_name=payload.full_name or pending.get("full_name"),
        email=pending["email"],
        role="pm_admin",
        auth_user_id=auth_user_id,
    )

    # Mark approved (also stores workspace_id + auth_user_id)
    updated_pending = approve_pending_account(
        pending_id,
        auth_user_id=auth_user_id,
        workspace_id=workspace_id,
    )
    return {"pending_account": updated_pending, "auth_user": auth_user, "db_user": db_user}


# ----------------------------
# Magic links (admin utility or internal)
# ----------------------------

def _ensure_approved_account(email: str) -> dict:
    approved = get_approved_pending_account_by_email(email)
    if not approved:
        raise HTTPException(status_code=403, detail="User is not approved")
    if not approved.get("auth_user_id"):
        raise HTTPException(status_code=500, detail="Approved account missing auth_user_id")
    return approved


@router.post("/auth/send-magic-link")
async def send_magic_link_endpoint(payload: MagicLinkEmailRequest):
    _ensure_approved_account(payload.email)
    link = generate_magic_link(payload.email, redirect_to=payload.redirect_to)
    subject = payload.subject or "Your sign-in link"
    body = (
        "Use the link below to sign in. If you did not request this, you can ignore it.\n\n"
        f"{link}"
    )
    send_email(to=payload.email, subject=subject, body=body)

    return {"status": "sent"}
