from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from api.email_agent.email import send_email
from api.supabase.db_helpers import (
    approve_pending_account,
    create_conversation,
    create_message,
    create_media_asset,
    create_pending_account,
    create_property,
    create_tenant,
    create_auth_user,
    create_unit,
    create_user,
    create_work_order,
    generate_magic_link,
    get_pending_account,
    get_pending_account_by_email,
    get_tenant,
    get_work_order,
    list_conversations,
    list_media_assets_for_message,
    list_media_assets_for_work_order,
    list_messages_for_conversation,
    list_messages_for_work_order,
    list_pending_accounts,
    list_properties,
    list_units,
    list_users,
    list_work_orders,
)

router = APIRouter()


class TenantCreate(BaseModel):
    name: str


class UserCreate(BaseModel):
    full_name: str | None = None
    phone: str | None = None
    email: str | None = None


class PropertyCreate(BaseModel):
    address: str | None = None


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


class PendingAccountCreate(BaseModel):
    email: str
    full_name: str | None = None


class PendingAccountApprove(BaseModel):
    tenant_id: str
    full_name: str | None = None


class MagicLinkRequest(BaseModel):
    email: str
    redirect_to: str | None = None


class MagicLinkEmailRequest(BaseModel):
    email: str
    redirect_to: str | None = None
    subject: str | None = None


@router.post("/tenants")
async def create_tenant_endpoint(payload: TenantCreate):
    return create_tenant(payload.name)


@router.get("/tenants/{tenant_id}")
async def get_tenant_endpoint(tenant_id: str):
    tenant = get_tenant(tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return tenant


@router.post("/tenants/{tenant_id}/users")
async def create_user_endpoint(tenant_id: str, payload: UserCreate):
    return create_user(
        tenant_id,
        full_name=payload.full_name,
        phone=payload.phone,
        email=payload.email,
    )


@router.get("/tenants/{tenant_id}/users")
async def list_users_endpoint(tenant_id: str):
    return list_users(tenant_id)


@router.post("/tenants/{tenant_id}/properties")
async def create_property_endpoint(tenant_id: str, payload: PropertyCreate):
    return create_property(tenant_id, address=payload.address)


@router.get("/tenants/{tenant_id}/properties")
async def list_properties_endpoint(tenant_id: str):
    return list_properties(tenant_id)


@router.post("/properties/{property_id}/units")
async def create_unit_endpoint(property_id: str, payload: UnitCreate):
    return create_unit(property_id, payload.unit_label)


@router.get("/properties/{property_id}/units")
async def list_units_endpoint(property_id: str):
    return list_units(property_id)


@router.post("/tenants/{tenant_id}/work-orders")
async def create_work_order_endpoint(tenant_id: str, payload: WorkOrderCreate):
    return create_work_order(
        tenant_id,
        payload.property_id,
        unit_id=payload.unit_id,
        reported_by_user_id=payload.reported_by_user_id,
        title=payload.title,
        description=payload.description,
        priority=payload.priority,
        status=payload.status or "new",
    )


@router.get("/tenants/{tenant_id}/work-orders")
async def list_work_orders_endpoint(tenant_id: str):
    return list_work_orders(tenant_id)


@router.get("/work-orders/{work_order_id}")
async def get_work_order_endpoint(work_order_id: str):
    work_order = get_work_order(work_order_id)
    if work_order is None:
        raise HTTPException(status_code=404, detail="Work order not found")
    return work_order


@router.post("/work-orders/{work_order_id}/conversations")
async def create_conversation_endpoint(work_order_id: str, payload: ConversationCreate):
    return create_conversation(
        work_order_id,
        party_type=payload.party_type,
        party_user_id=payload.party_user_id,
    )


@router.get("/work-orders/{work_order_id}/conversations")
async def list_conversations_endpoint(work_order_id: str):
    return list_conversations(work_order_id)


@router.post("/conversations/{conversation_id}/messages")
async def create_message_endpoint(conversation_id: str, payload: MessageCreate):
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
async def list_messages_for_conversation_endpoint(conversation_id: str):
    return list_messages_for_conversation(conversation_id)


@router.get("/work-orders/{work_order_id}/messages")
async def list_messages_for_work_order_endpoint(work_order_id: str):
    return list_messages_for_work_order(work_order_id)


@router.post("/work-orders/{work_order_id}/media-assets")
async def create_media_asset_endpoint(work_order_id: str, payload: MediaAssetCreate):
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
async def list_media_assets_for_work_order_endpoint(work_order_id: str):
    return list_media_assets_for_work_order(work_order_id)


@router.get("/messages/{message_id}/media-assets")
async def list_media_assets_for_message_endpoint(message_id: str):
    return list_media_assets_for_message(message_id)


@router.post("/auth/pending-accounts")
async def create_pending_account_endpoint(payload: PendingAccountCreate):
    existing = get_pending_account_by_email(payload.email)
    if existing:
        return existing
    return create_pending_account(payload.email, full_name=payload.full_name)


@router.get("/auth/pending-accounts")
async def list_pending_accounts_endpoint(status: str | None = None):
    return list_pending_accounts(status=status)


@router.post("/auth/pending-accounts/{pending_id}/approve")
async def approve_pending_account_endpoint(
    pending_id: str, payload: PendingAccountApprove
):
    pending = get_pending_account(pending_id)
    if pending is None:
        raise HTTPException(status_code=404, detail="Pending account not found")
    auth_user = create_auth_user(pending["email"])
    auth_user_id = auth_user.get("id") if isinstance(auth_user, dict) else None
    if not auth_user_id:
        raise HTTPException(status_code=500, detail="Failed to create auth user")
    db_user = create_user(
        payload.tenant_id,
        full_name=payload.full_name or pending.get("full_name"),
        email=pending["email"],
    )
    approve_pending_account(pending_id, auth_user_id=auth_user_id)
    return {"pending_account": pending, "auth_user": auth_user, "db_user": db_user}


@router.post("/auth/magic-link")
async def magic_link_endpoint(payload: MagicLinkRequest):
    link = generate_magic_link(payload.email, redirect_to=payload.redirect_to)
    return {"action_link": link}


@router.post("/auth/send-magic-link")
async def send_magic_link_endpoint(payload: MagicLinkEmailRequest):
    link = generate_magic_link(payload.email, redirect_to=payload.redirect_to)
    subject = payload.subject or "Your sign-in link"
    body = (
        "Use the link below to sign in. If you did not request this, you can ignore it.\n\n"
        f"{link}"
    )
    send_email(to=payload.email, subject=subject, body=body)
    return {"status": "sent"}
