import os
import base64
import datetime
from typing import List, Dict
from dotenv import load_dotenv
import google.generativeai as genai
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
import requests

# Load environment variables
load_dotenv()

# Constants
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

SUMMARIZATION_PROMPT = """
You are a highly efficient personal assistant. Your goal is to produce a high-fidelity "Briefing" for the user.

### TONE & STYLE:
- **Direct & Personal**: Speak directly to "You" (the user). No third-person generalisations (e.g., skip "for points-heavy travelers").
- **Concise & Analytical**: No fluff. No conversational filler.
- **Strict Formatting**: Use **Bold** for section headers and key terms only. Do NOT use italics. Do NOT use "Summarised for" or other meta-noise.
- **High Fidelity**: Every point in "The Bottom Line" MUST have a corresponding breakdown in the briefing.

### STRUCTURE:
1. **The Bottom Line**: 3-4 bullet points summarizing the absolute must-know context for you today. **NEVER** mention system/infrastructure alerts (GitHub, Security, etc.) here. This is for content only.
2. **⚡ Action Plan**: A consolidated list of next steps for you. Label each as `[URGENT ⚡]` or `[FYI ☕]`.

3. **📁 Categorized Briefing**: Group your emails by Topic (e.g., **Tech & AI**, **Markets**, **Lifestyle**).
   Each entry must include:
   - **Subject**: [Title]
   - **Core Argument**: 1-2 sentences on the central claim.
   - **Key Data**: Bulleted list of specific stats, examples, or quotes.
   - **Caveat**: A single crisp sentence on the main limitation or uncertainty.
   - **Reading Time**: "X min read" for the full source.

### NOISE FILTERING:
- Ignore all authentication codes, OTPs, or trivial notifications.
- System/Infra alerts (GitHub, Security, etc.) are handled separately- DO NOT include them in the summary.
"""

# Configure Gemini
genai.configure(api_key=GEMINI_API_KEY)
# Use gemini-1.5-flash (via gemini-flash-latest alias) to avoid 2.0-flash quota exhaustion
model = genai.GenerativeModel('gemini-flash-latest')

def get_gmail_service(token_path: str):
    """Builds and returns a Gmail service object with debug logging."""
    if not os.path.exists(token_path):
        print(f"❌ ERROR: File '{token_path}' does not exist.")
        return None
    
    file_size = os.path.getsize(token_path)
    if file_size == 0:
        print(f"❌ ERROR: File '{token_path}' is EMPTY.")
        return None
        
    try:
        creds = Credentials.from_authorized_user_file(token_path)
        return build('gmail', 'v1', credentials=creds)
    except Exception as e:
        print(f"❌ ERROR: Failed to load credentials from {token_path}: {str(e)}")
        return None

def fetch_recent_emails(service, max_results=15) -> List[Dict]:
    """Fetches emails from the last 24 hours."""
    now = datetime.datetime.now(datetime.timezone.utc)
    yesterday = now - datetime.timedelta(days=1)
    query = f"after:{int(yesterday.timestamp())}"
    
    try:
        results = service.users().messages().list(userId='me', q=query, maxResults=max_results).execute()
        messages = results.get('messages', [])
        
        email_data = []
        for msg in messages:
            full_msg = service.users().messages().get(userId='me', id=msg['id']).execute()
            payload = full_msg.get('payload', {})
            headers = payload.get('headers', [])
            
            subject = next((h['value'] for h in headers if h['name'] == 'Subject'), 'No Subject')
            sender = next((h['value'] for h in headers if h['name'] == 'From'), 'Unknown Sender')
            
            # Smart pre-filtering of system noise
            noise_keywords = ['security alert', 'github', 'sign-in', 'verification code', 'otp', 'unsubscribe', 'tos', 'privacy']
            is_system = any(kw in subject.lower() or kw in sender.lower() for kw in noise_keywords)
            
            # Extract plain text body
            body = ""
            if 'parts' in payload:
                for part in payload['parts']:
                    if part['mimeType'] == 'text/plain':
                        body = base64.urlsafe_b64decode(part['body'].get('data', '')).decode('utf-8')
                        break
            elif 'body' in payload:
                body = base64.urlsafe_b64decode(payload['body'].get('data', '')).decode('utf-8')

            email_data.append({
                'id': msg['id'],
                'subject': subject,
                'sender': sender,
                'body': body,
                'is_system': is_system
            })
        return email_data
    except HttpError as error:
        print(f"An error occurred: {error}")
        return []

def summarize_emails(emails: List[Dict]) -> str:
    """Summarizes a list of emails using Gemini."""
    if not emails:
        return "No new emails found."
    
    content_emails = [e for e in emails if not e['is_system']]
    system_emails = [e for e in emails if e['is_system']]
    
    combined_text = ""
    for idx, email in enumerate(content_emails):
        combined_text += f"\n--- Email {idx+1} ---\n"
        combined_text += f"From: {email['sender']}\n"
        combined_text += f"Subject: {email['subject']}\n"
        combined_text += f"Content: {email['body'][:6000]}\n"

    full_prompt = f"{SUMMARIZATION_PROMPT}\n\nHere are the emails:\n{combined_text}"
    
    response = model.generate_content(full_prompt)
    digest = response.text
    
    # Add Service Status footer if system emails exist (minimalist)
    if system_emails:
        digest += "\n\n**🛠 SERVICE HEALTH**\n"
        digest += f"Ignored {len(system_emails)} automated alerts (GitHub, Security, etc.).\n"
            
    return digest

def send_telegram_message(text: str):
    """Sends a message to Telegram with smart chunking."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    
    # Smart chunking: Split by paragraphs to avoid cutting mid-sentence
    max_len = 4096
    paragraphs = text.split('\n\n')
    current_chunk = ""
    
    chunks = []
    for p in paragraphs:
        if len(current_chunk) + len(p) + 2 > max_len:
            chunks.append(current_chunk.strip())
            current_chunk = p + "\n\n"
        else:
            current_chunk += p + "\n\n"
    if current_chunk:
        chunks.append(current_chunk.strip())

    for idx, chunk in enumerate(chunks):
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": chunk, "parse_mode": "Markdown"}
        print(f"Sending part {idx+1}/{len(chunks)} to Telegram...")
        response = requests.post(url, json=payload)
        if response.status_code != 200:
            # Fallback if Markdown fails
            payload.pop("parse_mode")
            requests.post(url, json=payload)

def main():
    tokens = ['token1.json', 'token2.json', 'token3.json']
    all_emails = []
    
    for idx, token_path in enumerate(tokens):
        service = get_gmail_service(token_path)
        if not service: continue
        
        print(f"Fetching Account {idx+1}...")
        emails = fetch_recent_emails(service)
        all_emails.extend(emails)

    if not all_emails:
        send_telegram_message("📧 **Daily Email Digest**\n\nNo important emails found today.")
        return

    print(f"Processing {len(all_emails)} emails...")
    final_digest = summarize_emails(all_emails)
    
    header = f"📧 **Daily Email Digest - {datetime.date.today()}**\n\n"
    final_digest = header + final_digest
    send_telegram_message(final_digest)
    print("Done!")

if __name__ == "__main__":
    main()
