import os
import json
from dotenv import load_dotenv
from openai import OpenAI
import base64

# LOAD ENV FIRST
load_dotenv()

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

SYSTEM_PROMPT = """
You are a property management maintenance intake assistant.

Rules:
- Be professional and calm
- When images are provided, carefully analyze them and describe what you see in detail
- Use the visual information from images to understand the maintenance issue better
- If asked to identify or describe an image, provide a clear, detailed description of what's shown
- For maintenance issues, use images to assess severity and identify the specific problem
- Ask 2–4 clarifying questions based on both the text description and what you see in any images
- Do NOT promise repairs or timelines
- Assume non-emergency unless explicitly stated or clearly visible in images (e.g., flooding, fire, structural damage)
- Output JSON only

Format:
{
  "issue_category": "plumbing|electrical|hvac|appliance|structural|exterior|other",
  "severity": "low|medium|high",
  "reply": "email-safe response that acknowledges what you saw in any images"
}
"""

def run_ai_agent(subject: str, body: str, image_data: list = None) -> dict:
    messages_content = []
    
    text_content = f"Subject: {subject}\nMessage: {body}"
    messages_content.append({
        "type": "text",
        "text": text_content
    })
    
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