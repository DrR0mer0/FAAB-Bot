#!/usr/bin/env python3
"""Step 2 of one-time Yahoo authorization: exchange the redirect URL (or bare
code) for an access_token/refresh_token pair, and save them to yahoo_token.json.
"""
import argparse

from yahoo_auth import exchange_code_for_token, extract_code_from_redirect, load_credentials


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("redirect_url_or_code", help="the full URL pasted from the browser address bar (or just the code)")
    args = ap.parse_args()

    client_id, client_secret = load_credentials()
    code = extract_code_from_redirect(args.redirect_url_or_code)
    print(f"[INFO] extracted authorization code: {code[:8]}...")

    oauth, token_data = exchange_code_for_token(client_id, client_secret, code)
    print("[DONE] access_token and refresh_token saved to yahoo_token.json")
    print(f"  token_type: {token_data['token_type']}")
    print(f"  access_token: {token_data['access_token'][:12]}...")
    print(f"  refresh_token: {token_data['refresh_token'][:12]}...")


if __name__ == "__main__":
    main()
