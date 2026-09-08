#!/usr/bin/env python3
"""Smoke test: fetch the authenticated user's Yahoo Fantasy Football leagues
for the current season, to confirm the token actually has Fantasy Sports
read access. Reports the exact error rather than retrying if it fails.
"""
import json

from yahoo_auth import get_session

URL = "https://fantasysports.yahooapis.com/fantasy/v2/users;use_login=1/games;game_keys=nfl/leagues?format=json"


def main():
    session = get_session()
    resp = session.get(URL)

    print(f"[INFO] GET {URL}")
    print(f"[INFO] HTTP status: {resp.status_code}")

    if resp.status_code != 200:
        print("\n[FAILED] Non-200 response -- stopping, not retrying. Raw body:")
        print(resp.text[:3000])
        return

    try:
        data = resp.json()
    except ValueError:
        print("\n[FAILED] Response wasn't valid JSON -- stopping, not retrying. Raw body:")
        print(resp.text[:3000])
        return

    if "error" in data:
        print("\n[FAILED] API returned an error payload -- stopping, not retrying:")
        print(json.dumps(data, indent=2))
        return

    print("\n[SUCCESS] Fantasy Sports read access confirmed. Raw response:")
    print(json.dumps(data, indent=2))


if __name__ == "__main__":
    main()
