from email.utils import parseaddr
import os
from fastapi import APIRouter, HTTPException, Request
from uuid import uuid4

from api.email_agent.ai import run_ai_agent
from api.email_agent.email import send_email
from api.supabase.image_storage import upload_image
from api.supabase.db_helpers import (
    create_conversation,
    create_message,
    create_work_order,
    find_message_by_external_id,
    get_default_property_for_tenant,
    get_user_by_email,
    update_work_order,
)
import traceback
import io

router = APIRouter()


def extract_header(headers: str, key: str):
    if not headers:
        return None
    for line in headers.splitlines():
        if line.lower().startswith(key.lower() + ":"):
            return line.split(":", 1)[1].strip()
    return None

def parse_sendgrid_webhook(body: bytes, content_type: str):
    """Parse SendGrid inbound webhook manually to avoid size limits"""
    import cgi
    from email import message_from_bytes
    from email.policy import default
    
    print(f"Parsing webhook - body size: {len(body)}, content_type: {content_type}")
    
    environ = {
        'REQUEST_METHOD': 'POST',
        'CONTENT_TYPE': content_type,
        'CONTENT_LENGTH': str(len(body))
    }
    
    try:
        fs = cgi.FieldStorage(
            fp=io.BytesIO(body),
            environ=environ,
            keep_blank_values=True
        )
        
        print(f"FieldStorage keys: {list(fs.keys())}")
    except Exception as e:
        print(f"ERROR parsing FieldStorage: {e}")
        import traceback
        print(traceback.format_exc())
        return {'_attachments': []}
    
    result = {}
    attachments = []
    raw_email = None
    
    for key in fs.keys():
        item = fs[key]
        if key == 'email':
            raw_email = item.value if isinstance(item.value, bytes) else item.value.encode()
            print(f"Found raw email, size: {len(raw_email)}")
        elif hasattr(item, 'filename') and item.filename:
            file_data = item.file.read()
            print(f"Found direct attachment: {item.filename}, type: {item.type}, size: {len(file_data)}")
            attachments.append({
                'key': key,
                'filename': item.filename,
                'content_type': item.type,
                'data': file_data
            })
        else:
            result[key] = item.value
    
    if raw_email:
        try:
            msg = message_from_bytes(raw_email, policy=default)
            
            if msg.is_multipart():
                for part in msg.walk():
                    content_type = part.get_content_type()
                    
                    if content_type == 'text/plain' and not result.get('text'):
                        result['text'] = part.get_content()
                    elif content_type == 'text/html' and not result.get('html'):
                        result['html'] = part.get_content()
                    elif content_type.startswith('image/'):
                        filename = part.get_filename() or 'image.jpg'
                        file_data = part.get_content()
                        print(f"Found image in email: {filename}, type: {content_type}, size: {len(file_data)}")
                        attachments.append({
                            'key': 'email_attachment',
                            'filename': filename,
                            'content_type': content_type,
                            'data': file_data
                        })
            else:
                if not result.get('text'):
                    result['text'] = msg.get_content()
            
            if not result.get('headers'):
                headers = []
                for key, value in msg.items():
                    headers.append(f"{key}: {value}")
                result['headers'] = '\n'.join(headers)
                
        except Exception as e:
            print(f"ERROR parsing raw email: {e}")
            import traceback
            print(traceback.format_exc())
    
    print(f"Parsed {len(attachments)} attachments total")
    result['_attachments'] = attachments
    return result


@router.post("/email/inbound")
async def inbound_email(request: Request):
    try:
        body = await request.body()
        content_type = request.headers.get("content-type", "")
        
        print("=== DEBUG: Incoming Email ===")
        print(f"Content-Type: {content_type}")
        print(f"Body size: {len(body)} bytes")
        
        form_data = parse_sendgrid_webhook(body, content_type)
        
        tenant_email = form_data.get("from")
        _, tenant_email = parseaddr(tenant_email or "")
        tenant_email = (tenant_email or "").strip().lower()
        
        subject = form_data.get("subject") or "(No subject)"
        body_text = form_data.get("text") or ""
        headers = form_data.get("headers") or ""

        print(f"From: {tenant_email}")
        print(f"Subject: {subject}")
        print(f"Body length: {len(body_text)}")

        user = get_user_by_email(tenant_email) if tenant_email else None
        if not user:
            print(f"No user found for inbound email: {tenant_email}")
            raise HTTPException(status_code=403, detail="User not authorized")

        attachments = []
        for att in form_data.get('_attachments', []):
            if att['content_type'] and att['content_type'].startswith("image/"):
                print(f"Found image: {att['filename']}, type: {att['content_type']}, size: {len(att['data'])} bytes")
                attachments.append({
                    "data": att['data'],
                    "filename": att['filename'],
                    "content_type": att['content_type']
                })
        
        print(f"Total image attachments to process: {len(attachments)}")

        message_id = extract_header(headers, "Message-ID")
        in_reply_to = extract_header(headers, "In-Reply-To")

        existing_message = None
        if in_reply_to:
            existing_message = find_message_by_external_id(in_reply_to)

        if existing_message:
            work_order_id = existing_message["work_order_id"]
            conversation_id = existing_message["conversation_id"]
        else:
            tenant_id = user["tenant_id"]
            property_record = get_default_property_for_tenant(tenant_id)
            if not property_record:
                raise HTTPException(status_code=400, detail="No property configured for tenant")
            property_id = property_record["id"]
            work_order = create_work_order(
                tenant_id,
                property_id,
                reported_by_user_id=user["id"],
                title=subject,
                description=body_text,
                status="new",
            )
            conversation = create_conversation(work_order["id"], party_type="tenant")
            work_order_id = work_order["id"]
            conversation_id = conversation["id"]

        tenant_msg_id = message_id or str(uuid4())
        
        image_data = []
        image_urls = []
        if attachments:
            for att in attachments:
                try:
                    result = upload_image(
                        file_data=att["data"],
                        filename=att["filename"],
                        content_type=att["content_type"],
                        work_order_id=work_order_id,
                    )
                    image_data.append(result)
                    image_urls.append(result['url'])
                    print(f"Image uploaded: {result['url']}")
                except Exception as e:
                    print(f"Failed to upload image: {e}")
                    print(traceback.format_exc())
        
        create_message(
            conversation_id,
            work_order_id,
            direction="inbound",
            channel="email",
            body=body_text,
            raw_payload={
                "external_message_id": tenant_msg_id,
                "from": tenant_email,
                "subject": subject,
                "in_reply_to": in_reply_to,
                "image_urls": image_urls,
            },
        )

        ai_result = run_ai_agent(subject, body_text, image_data)

        ai_message_id = send_email(
            to=tenant_email,
            subject=f"Re: {subject}",
            body=ai_result["reply"],
            in_reply_to=message_id,
            references=in_reply_to or message_id
        )

        create_message(
            conversation_id,
            work_order_id,
            direction="outbound",
            channel="email",
            body=ai_result["reply"],
            raw_payload={
                "external_message_id": ai_message_id,
                "in_reply_to": message_id,
                "references": in_reply_to or message_id,
                "issue_category": ai_result["issue_category"],
                "severity": ai_result["severity"],
            },
        )

        priority = ai_result.get("severity")
        if priority:
            update_work_order(work_order_id, priority=priority)

        return {"status": "ok"}
    
    except HTTPException:
        raise
    except Exception as e:
        print(f"ERROR: {str(e)}")
        print(traceback.format_exc())
        return {"status": "error", "message": str(e)}
