#!/usr/bin/env python3
"""Yahoo OAuth2 helper built on top of the yahoo_oauth library.

Client ID/Secret live in .env (never hardcoded). Access/refresh tokens live
in yahoo_token.json (written after the one-time authorization, read and
auto-refreshed on every subsequent run). Both files are gitignored.

yahoo_oauth's own OAuth2 class does the full authorize-and-exchange flow
inside its constructor via a blocking input() call and an auto-opened
browser -- that doesn't work across a conversation that needs a human to
actually visit the URL in their own browser and paste back the result.
NonInteractiveOAuth2 below reuses the library's real machinery (its OAuth2Service
object, header signing, response parsing, refresh logic) but swaps the
blocking prompt for a pre-supplied authorization code, so the flow can be
split into two steps.
"""
import json
import os
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from dotenv import load_dotenv
from yahoo_oauth import OAuth2
from yahoo_oauth.utils import services

HERE = Path(__file__).parent
ENV_PATH = HERE / ".env"
TOKEN_PATH = HERE / "yahoo_token.json"
REDIRECT_URI = "https://localhost:8080"


def load_credentials():
    load_dotenv(ENV_PATH)
    client_id = os.environ.get("YAHOO_CLIENT_ID", "").strip()
    client_secret = os.environ.get("YAHOO_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        raise SystemExit(
            f"[ERROR] YAHOO_CLIENT_ID / YAHOO_CLIENT_SECRET not set in {ENV_PATH}. "
            "Fill them in with the values from the Yahoo Developer app, then rerun."
        )
    return client_id, client_secret


# Fantasy Sports read access is granted via this scope in the authorization
# request itself -- NOT via a developer-console permission checkbox (that
# was a wrong assumption; the API confirmed it with
# oauth_problem="additional_authorization_required" on a scope-less token).
FANTASY_READ_SCOPE = "fspt-r"


def build_authorize_url(client_id, client_secret, scope=FANTASY_READ_SCOPE):
    """Builds the same authorize URL yahoo_oauth.OAuth2 would, using the
    library's own service definitions, without triggering its blocking
    constructor flow. rauth's get_authorize_url(**params) just urlencodes
    whatever kwargs it's given, so scope is passed straight through --
    no manual URL construction needed."""
    service_params = {
        "client_id": client_id,
        "client_secret": client_secret,
        "name": "yahoo",
        "access_token_url": services["oauth2"]["ACCESS_TOKEN_URL"],
        "authorize_url": services["oauth2"]["AUTHORIZE_TOKEN_URL"],
        "base_url": None,
    }
    oauth_service = services["oauth2"]["SERVICE"](**service_params)
    kwargs = {"redirect_uri": REDIRECT_URI, "response_type": "code"}
    if scope:
        kwargs["scope"] = scope
    return oauth_service.get_authorize_url(**kwargs)


def extract_code_from_redirect(pasted_url_or_code):
    """Accepts either the full (failed-to-load) redirect URL the user pastes
    back, or a bare code, and returns just the authorization code."""
    s = pasted_url_or_code.strip()
    if s.startswith("http"):
        qs = parse_qs(urlparse(s).query)
        if "code" not in qs:
            raise ValueError(f"No 'code' parameter found in pasted URL: {s}")
        return qs["code"][0]
    return s


class NonInteractiveOAuth2(OAuth2):
    """Same as yahoo_oauth.OAuth2, except the authorization-code exchange
    uses a code supplied ahead of time instead of blocking on input() /
    auto-opening a browser."""

    def __init__(self, consumer_key, consumer_secret, code, **kwargs):
        self._injected_code = code
        super().__init__(consumer_key, consumer_secret, **kwargs)

    def handler(self):
        self.verifier = self._injected_code
        self.token_time = time.time()
        credentials = {"token_time": self.token_time}
        headers = self.generate_oauth2_headers()
        raw_access = self.oauth.get_raw_access_token(
            data={"code": self.verifier, "redirect_uri": self.callback_uri, "grant_type": "authorization_code"},
            headers=headers,
        )
        credentials.update(self.oauth2_access_parser(raw_access))
        return credentials


def exchange_code_for_token(client_id, client_secret, code):
    """Step 2 of the one-time flow: trade the authorization code for an
    access_token/refresh_token pair, and persist them."""
    oauth = NonInteractiveOAuth2(
        client_id, client_secret, code,
        callback_uri=REDIRECT_URI,
        store_file=False,
    )
    token_data = {
        "access_token": oauth.access_token,
        "token_type": oauth.token_type,
        "refresh_token": oauth.refresh_token,
        "token_time": oauth.token_time,
    }
    save_token(token_data)
    return oauth, token_data


def load_token():
    if not TOKEN_PATH.exists():
        return None
    with open(TOKEN_PATH, encoding="utf-8") as f:
        return json.load(f)


def save_token(token_data):
    with open(TOKEN_PATH, "w", encoding="utf-8") as f:
        json.dump(token_data, f, indent=2)


def get_session():
    """For all normal (post-setup) use: loads credentials + saved token,
    auto-refreshing via yahoo_oauth's own refresh logic if the token has
    expired, and returns an authenticated requests session."""
    client_id, client_secret = load_credentials()
    token = load_token()
    if token is None:
        raise SystemExit(
            "[ERROR] No yahoo_token.json found. Run the one-time authorization "
            "(yahoo_auth_step1.py then yahoo_auth_step2.py) first."
        )

    oauth = OAuth2(
        client_id, client_secret,
        access_token=token["access_token"],
        token_type=token["token_type"],
        refresh_token=token["refresh_token"],
        token_time=token["token_time"],
        callback_uri=REDIRECT_URI,
        store_file=False,
    )

    # OAuth2.__init__ auto-refreshes if expired; persist if it did.
    refreshed = {
        "access_token": oauth.access_token,
        "token_type": oauth.token_type,
        "refresh_token": oauth.refresh_token,
        "token_time": oauth.token_time,
    }
    if refreshed != token:
        save_token(refreshed)

    return oauth.session
