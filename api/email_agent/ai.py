import os
import json
from dotenv import load_dotenv
from openai import OpenAI
import base64

# LOAD ENV FIRST
load_dotenv()

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

RECENT_TURNS_TO_KEEP = 4

SYSTEM_PROMPT = """
You are an AI maintenance intake assistant for a property management company.

Your job is to gather information from tenants about maintenance issues and determine the appropriate next step.

PRIMARY GOAL
- First gather enough information to understand the issue.
- Only then determine whether a vendor should be dispatched.
- Do not recommend vendor dispatch too early unless the issue is clearly urgent or obviously requires professional repair.

GENERAL BEHAVIOR
- Be calm, professional, and concise.
- Do not promise repairs, timelines, approvals, or reimbursement.
- Assume the tenant is not technically trained.
- Assume non-emergency unless explicitly stated or clearly visible.

IMAGE HANDLING
- If images are provided, analyze them carefully.
- Describe what you can clearly observe.
- Use the image to improve diagnosis.
- If the image is blurry, incomplete, or ambiguous, state that clearly.

TRIAGE PRINCIPLES
There are 3 possible next states:

1. MORE_INFO_NEEDED
Use this when there is not enough information to determine whether a vendor is needed.
Examples:
- "My sink is broken"
- "AC not working" with no details
- blurry image without enough context

2. SELF_HELP_POSSIBLE
Use this when the issue may be resolved by a simple tenant action and there is no clear sign a vendor is needed yet.
Examples:
- thermostat may be set incorrectly
- breaker may need reset
- appliance may be unplugged
- light bulb may need replacement

3. VENDOR_LIKELY_NEEDED
Use this only when the issue likely requires professional repair, tools, parts, or inspection.
Examples:
- active leak
- repeated drain backup
- exposed wiring
- sparking outlet
- broken appliance with clear malfunction
- damaged door, wall, ceiling, or window
- HVAC failure not resolved by basic checks

SEVERITY
Assess severity independently from dispatch status.

LOW
- minor inconvenience
- cosmetic or non-urgent issue
- little risk of damage or safety concern

MEDIUM
- affects normal use or comfort
- repair is likely needed but does not appear urgent

HIGH
- safety concern, active leak, significant property damage risk, or major loss of habitability

INTAKE QUESTIONS
- Ask 2 to 4 short, specific clarifying questions when more information is needed.
- Ask only the most useful questions for diagnosis.
- Base the questions on both the tenant's text and any image provided.
- Do not ask unnecessary repeated questions if the tenant already answered them.

DISPATCH DECISION RULES
- If information is incomplete, set needs_more_info = true and dispatch_recommendation = "not_yet".
- If a simple tenant troubleshooting step should be tried first, set needs_more_info = false and dispatch_recommendation = "no".
- If enough information has been collected and professional repair is likely needed, set needs_more_info = false and dispatch_recommendation = "yes".
- Only recommend dispatch early when the issue is clearly severe or obviously requires a vendor from the initial message or image.

OUTPUT FORMAT

Return JSON only.

{
  "issue_category": "plumbing|electrical|hvac|appliance|structural|exterior|other",
  "severity": "low|medium|high",
  "needs_more_info": true,
  "dispatch_recommendation": "not_yet|yes|no",
  "likely_trade": "plumber|electrician|hvac_technician|appliance_technician|handyman|roofer|locksmith|other",
  "summary": "brief internal summary of the issue and current triage status",
  "reply": "email-safe reply to the tenant that acknowledges what was reported or shown in the image and asks any necessary questions"
}

REPLY STYLE
- Friendly and helpful
- Reference anything visible in images if provided
- Do NOT include internal reasoning
- Ask questions as bullet points if possible
"""

def run_ai_agent(
    subject: str,
    body: str,
    image_data: list = None,
    recent_history: list | None = None,
    rolling_summary: str | None = None,
) -> dict:
    messages_content = []
    
    text_content = f"Subject: {subject}\nMessage: {body}"
    messages_content.append({
        "type": "text",
        "text": text_content
    })

    if rolling_summary:
        messages_content.append(
            {
                "type": "text",
                "text": f"Rolling summary of older conversation context:\n{rolling_summary}",
            }
        )

    if recent_history:
        history_lines = []
        for item in recent_history:
            direction = item.get("direction", "unknown")
            history_subject = item.get("subject")
            history_body = item.get("body") or ""
            if history_subject:
                history_lines.append(
                    f"- {direction} | subject={history_subject} | body={history_body}"
                )
            else:
                history_lines.append(f"- {direction} | body={history_body}")

        messages_content.append(
            {
                "type": "text",
                "text": f"Most recent {RECENT_TURNS_TO_KEEP} email turns:\n" + "\n".join(history_lines),
            }
        )
    
    if image_data:
        print(f"Adding {len(image_data)} images to AI request")
        for i, img in enumerate(image_data):
            base64_preview = img['base64'][:50] + "..." if len(img['base64']) > 50 else img['base64']
            print(f"Image {i+1}: content_type={img['content_type']}, base64_length={len(img['base64'])}, preview={base64_preview}")
            messages_content.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:{img['content_type']};base64,{img['base64']}",
                    "detail": "high"
                }
            })
    
    print(f"Sending request to OpenAI with {len(messages_content)} content items")
    
    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": messages_content
                }
            ],
            temperature=0.2,
            max_tokens=1000
        )
        
        print(f"OpenAI response received")
    except Exception as e:
        print(f"ERROR calling OpenAI: {e}")
        import traceback
        print(traceback.format_exc())
        raise

    content = response.choices[0].message.content
    
    print("=" * 50)
    print("RAW AI RESPONSE:")
    print(content)
    print("=" * 50)

    try:
        parsed = json.loads(content)
        print("✓ Successfully parsed JSON directly")
        return parsed
    except json.JSONDecodeError as e:
        print(f"✗ Direct JSON parse failed: {e}")
        import re
        json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', content, re.DOTALL)
        if json_match:
            try:
                parsed = json.loads(json_match.group(1))
                print("Successfully extracted JSON from code block")
                return parsed
            except json.JSONDecodeError:
                print("Failed to parse JSON from code block")
        
        json_match = re.search(r'\{.*\}', content, re.DOTALL)
        if json_match:
            try:
                parsed = json.loads(json_match.group(0))
                print("Successfully extracted JSON from response")
                return parsed
            except json.JSONDecodeError:
                print("Failed to parse extracted JSON")
        
        print("✗ All JSON parsing attempts failed, using fallback")
        return {
            "issue_category": "unknown",
            "severity": "unknown",
            "reply": (
                "Thanks for reaching out. We've received your maintenance request "
                "and will follow up shortly."
            )
        }


def condense_rolling_summary(
    previous_summary: str | None,
    overflow_lines: list[str],
) -> str:
    """Create an AI-condensed rolling summary for older email turns."""
    summary_seed = (previous_summary or "").strip()
    if not overflow_lines and summary_seed:
        return summary_seed
    if not overflow_lines:
        return ""

    prompt_lines = [
        "Create an AI-condensed rolling summary of this email thread.",
        "Keep it concise and preserve key commitments, dates, and next actions.",
        "Return plain text only.",
    ]
    if summary_seed:
        prompt_lines.append(f"Current rolling summary:\n{summary_seed}")
    prompt_lines.append("New older turns to fold in:")
    prompt_lines.extend(f"- {line}" for line in overflow_lines)

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {
                "role": "system",
                "content": "You produce concise, accurate rolling summaries for email threads.",
            },
            {"role": "user", "content": "\n".join(prompt_lines)},
        ],
        temperature=0.2,
        max_tokens=400,
    )

    return (response.choices[0].message.content or "").strip()
