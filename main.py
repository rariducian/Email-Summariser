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
You are an expert Executive Assistant. Create a Daily Email Digest from the following emails.

NOISE FILTERING (apply before anything else):
- SKIP entirely (do not include): authentication codes, OTPs, password resets, trade/order confirmations (e.g. nabtrade, brokerage receipts), shipping notifications, calendar invites, unsubscribe confirmations, marketing promos with no substantive content.
- ONE-LINER only (include in a "Low Signal" section at the bottom): product update emails, app notifications, event reminders.

FORMAT:
1. **TLDR**: 3–5 sentences covering the most important themes and decisions worth acting on across all emails.
2. **AI TIPS, LEARNINGS & TOOLS**: Any AI-related insights, tools, prompts, or technical developments. Exclude sponsored/promotional mentions.
3. **EMAIL BREAKDOWN**: For each substantive email:
   - **From**: [Sender] | **Subject**: [Title]
   - **Core Argument**: What is the central claim or thesis? (1–2 sentences)
   - **Key Evidence / Data**: Specific data points, stats, named examples, quotes, or research cited. Bullet each one.
   - **Actionable / So What**: What should the reader do or think differently because of this? Any explicit recommendations?
   - **Contrarian / Caveats**: Any pushback, uncertainty, or nuance the author acknowledges (or that's obviously missing)?

4. **LOW SIGNAL** (one-liners only): List any deprioritised emails with a single sentence each.

RULES:
- For newsletters: depth over breadth. Capture the argument, not just the topic.
- Never describe what a publication "covers" — summarise what *this issue* said.
- No category headers (Finance, Tech, etc.)
- Flag if a summary is incomplete due to truncated email content.
"""

# Configure Gemini
genai.configure(api_key=GEMINI_API_KEY)
# Use gemini-1.5-flash (via gemini-flash-latest alias) to avoid 2.0-flash quota exhaustion
model = genai.GenerativeModel('gemini-flash-latest')

def get_gmail_service(token_path: str):
    """Builds and returns a Gmail service object."""
    if not os.path.exists(token_path):
        return None
    creds = Credentials.from_authorized_user_file(token_path)
    return build('gmail', 'v1', credentials=creds)

def fetch_recent_emails(service, max_results=10) -> List[Dict]:
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
            date = next((h['value'] for h in headers if h['name'] == 'Date'), 'Unknown Date')
            
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
                'date': date,
                'body': body
            })
        return email_data
    except HttpError as error:
        print(f"An error occurred: {error}")
        return []

def summarize_emails(emails: List[Dict]) -> str:
    """Summarizes a list of emails using Gemini."""
    if not emails:
        return "No new emails found."
    
    combined_text = ""
    for idx, email in enumerate(emails):
        combined_text += f"\n--- Email {idx+1} ---\n"
        combined_text += f"From: {email['sender']}\n"
        combined_text += f"Subject: {email['subject']}\n"
        combined_text += f"Content: {email['body'][:5000]}\n" # Limit body per email to save tokens but allow depth

    full_prompt = f"{SUMMARIZATION_PROMPT}\n\nHere are the emails:\n{combined_text}"
    
    response = model.generate_content(full_prompt)
    return response.text

def send_telegram_message(text: str):
    """Sends a message to the specified Telegram chat."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    
    # Handle long messages by splitting
    max_len = 4000
    for i in range(0, len(text), max_len):
        chunk = text[i:i+max_len]
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": chunk
        }
        print(f"Sending chunk {i//max_len + 1} to Telegram...")
        response = requests.post(url, json=payload)
        if response.status_code != 200:
            print(f"Failed to send message: {response.status_code} - {response.text}")
        else:
            print(f"Successfully sent chunk {i//max_len + 1}")

def main():
    tokens = ['token1.json', 'token2.json', 'token3.json']
    all_emails = []
    
    for idx, token_path in enumerate(tokens):
        service = get_gmail_service(token_path)
        if not service:
            print(f"Skipping account {idx+1}: {token_path} not found.")
            continue
        
        print(f"Fetching emails for Account {idx+1}...")
        emails = fetch_recent_emails(service)
        all_emails.extend(emails)

    if not all_emails:
        print("No new emails found across all accounts.")
        send_telegram_message("📧 *Daily Email Digest*\n\nNo new emails found in the last 24 hours.")
        return

    print(f"Summarizing {len(all_emails)} emails...")
    final_digest = summarize_emails(all_emails)
    
    # Prepend dynamic header
    header = f"📧 *Daily Email Digest - {datetime.date.today()}*\n"
    header += f"👤 *Summarised for: mannhingkhor*\n\n"
    final_digest = header + final_digest

    print("Sending digest to Telegram...")
    send_telegram_message(final_digest)
    print("Done!")

if __name__ == "__main__":
    main()
