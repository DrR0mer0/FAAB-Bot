#!/usr/bin/env python3
"""Step 1 of one-time Yahoo authorization: print the login URL to open in a browser."""
from yahoo_auth import REDIRECT_URI, build_authorize_url, load_credentials


def main():
    client_id, client_secret = load_credentials()
    url = build_authorize_url(client_id, client_secret)
    print("Open this URL in a browser and authorize the app:\n")
    print(url)
    print(f"\nAfter authorizing, Yahoo will redirect to {REDIRECT_URI}/?code=...")
    print("That page will fail to load -- that's expected (nothing is listening on localhost:8080).")
    print("Copy the full URL from your address bar and pass it to yahoo_auth_step2.py")


if __name__ == "__main__":
    main()
