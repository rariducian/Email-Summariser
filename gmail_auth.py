import os.path
import sys
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

# If modifying these scopes, delete the file token.json.
SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']

def main():
    if len(sys.argv) < 2:
        print("Usage: python gmail_auth.py <account_number>")
        print("Example: python gmail_auth.py 1")
        return

    account_num = sys.argv[1]
    token_file = f'token{account_num}.json'
    creds = None

    # The file credentials.json stores the user's client ID and client secret, and is
    # created when the OAuth consent screen is authorized in the Google Cloud Console.
    if not os.path.exists('credentials.json'):
        print("Error: credentials.json not found. Please download it from Google Cloud Console.")
        return

    flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
    creds = flow.run_local_server(port=0)

    # Save the credentials for the next run
    with open(token_file, 'w') as token:
        token.write(creds.to_json())
    
    print(f"Successfully saved credentials to {token_file}")

if __name__ == '__main__':
    main()
