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

Token-endpoint replies are checked before they are parsed (CheckedOAuth2,
from 2026-11-09): the library runs json.loads() on whatever comes back, so a
Yahoo outage page used to surface as a bare JSONDecodeError on every
resource. Now a failure is a YahooTokenError that says which kind it is:
  "Yahoo token refresh failed: HTTP 503, body '...'"  -- temporary (a 5xx, a
      reply that isn't JSON, a connection error or timeout). Retried once
      after TOKEN_RETRY_WAIT_S; if it still fails, re-run the job later.
  "Yahoo token refresh rejected: HTTP 400 invalid_grant -- re-authorize ..."
      -- a 4xx or an OAuth error such as invalid_grant / invalid_client.
      Never retried: only a new authorization fixes it.
A 200 reply's body is never put in a message (it holds the tokens), and the
client id/secret and the tokens are redacted from any body snippet shown.
"""
import json
import os
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests
from dotenv import load_dotenv
from yahoo_oauth import OAuth2
from yahoo_oauth.utils import services

HERE = Path(__file__).parent
ENV_PATH = HERE / ".env"
TOKEN_PATH = HERE / "yahoo_token.json"
REDIRECT_URI = "https://localhost:8080"

# The whole league fetch, token refresh included, has 180 s (FETCH_BUDGET_S in league_data.py and
# tracker/league_availability.py). Worst case here: two attempts of at most 10 s connect + 15 s read, plus the wait.
TOKEN_TIMEOUT_S = (10, 15)
TOKEN_RETRY_WAIT_S = 20
BODY_SNIPPET_CHARS = 200
REAUTH_HINT = ("re-authorize with league/yahoo_auth_step1.py then step2.py, "
               "signed in to the Yahoo account that is in the league")
# OAuth error codes (RFC 6749) that say the server, not the login, is the problem; any other error code needs re-authorization
TRANSIENT_OAUTH_ERRORS = {"server_error", "temporarily_unavailable"}
REQUIRED_TOKEN_FIELDS = ("access_token", "token_type", "refresh_token")
_sleep = time.sleep   # the tests replace it


class YahooTokenError(Exception):
    """The token endpoint's reply was not a usable token. `status` is the HTTP
    status (None for a connection error), `retryable` whether trying again
    could help."""

    def __init__(self, message, status=None, retryable=False):
        super().__init__(message)
        self.status = status
        self.retryable = retryable


def body_snippet(text, secrets=()):
    """The first BODY_SNIPPET_CHARS of a NON-200 reply, whitespace collapsed, with any of `secrets` redacted."""
    s = " ".join((text or "").split())
    for secret in secrets:
        if secret:
            s = s.replace(str(secret), "[redacted]")
    return s[:BODY_SNIPPET_CHARS] + ("..." if len(s) > BODY_SNIPPET_CHARS else "")


class CheckedOAuth2(OAuth2):
    """yahoo_oauth.OAuth2 with the token endpoint's reply checked before it is
    parsed, a timeout on that request, and one retry when the failure looks
    temporary. Overrides oauth2_access_parser (the library's parsing hook, used
    by both the refresh and the code exchange) and refresh_access_token."""

    _token_action = "token refresh"

    def _secrets(self):
        return [getattr(self, a, None) for a in ("consumer_key", "consumer_secret", "access_token", "refresh_token", "verifier")]

    def oauth2_access_parser(self, raw_access):
        what = f"Yahoo {self._token_action}"
        status = getattr(raw_access, "status_code", None)
        try:
            text = raw_access.content.decode("utf-8")
        except (AttributeError, UnicodeDecodeError):
            text = ""
        try:
            parsed = json.loads(text)
        except ValueError:
            parsed = None
        error = parsed.get("error") if isinstance(parsed, dict) else None
        error = error if isinstance(error, str) and error else None

        if error and error not in TRANSIENT_OAUTH_ERRORS:   # invalid_grant, invalid_client, ...: whatever the status
            raise YahooTokenError(f"{what} rejected: HTTP {status} {body_snippet(error, self._secrets())} -- {REAUTH_HINT}", status)
        if status != 200:
            shown = f"HTTP {status}, body '{body_snippet(text, self._secrets())}'"
            if status is not None and 400 <= status < 500:
                raise YahooTokenError(f"{what} rejected: {shown} -- not retried; if it repeats, {REAUTH_HINT}", status)
            raise YahooTokenError(f"{what} failed: {shown}", status, retryable=True)
        # A 200 reply holds the tokens: nothing of its body goes into a message.
        if parsed is None:
            raise YahooTokenError(f"{what} failed: HTTP 200, reply is not JSON (body not shown)", status, retryable=True)
        if error:
            raise YahooTokenError(f"{what} failed: HTTP 200, OAuth error {error}", status, retryable=True)
        missing = [k for k in REQUIRED_TOKEN_FIELDS if not (isinstance(parsed, dict) and parsed.get(k))]
        if missing:
            raise YahooTokenError(f"{what} failed: HTTP 200, reply has no {', '.join(missing)} (body not shown)", status)

        self.access_token = parsed["access_token"]
        self.token_type = parsed["token_type"]
        self.refresh_token = parsed["refresh_token"]
        return {"access_token": self.access_token, "token_type": self.token_type, "refresh_token": self.refresh_token}

    def _token_request(self, data, action, retry):
        """POST to the token endpoint and parse the reply; one retry after TOKEN_RETRY_WAIT_S if `retry` and the failure
        is temporary."""
        self._token_action = action
        attempts = 2 if retry else 1
        for attempt in range(1, attempts + 1):
            try:
                try:
                    raw_access = self.oauth.get_raw_access_token(data=dict(data), headers=self.generate_oauth2_headers(),
                                                                 timeout=TOKEN_TIMEOUT_S)
                except requests.exceptions.RequestException as e:   # connection error, timeout
                    raise YahooTokenError(f"Yahoo {action} failed: {type(e).__name__} ({body_snippet(str(e), self._secrets())})",
                                          retryable=True) from e
                return self.oauth2_access_parser(raw_access)
            except YahooTokenError as e:
                if e.retryable and attempt < attempts:
                    _sleep(TOKEN_RETRY_WAIT_S)
                    continue
                if attempt > 1:
                    e.args = (f"{e.args[0]} (also on a retry after {TOKEN_RETRY_WAIT_S} s)",)
                raise

    def refresh_access_token(self):
        self.token_time = time.time()
        credentials = {"token_time": self.token_time}
        credentials.update(self._token_request(
            {"refresh_token": self.refresh_token, "redirect_uri": self.callback_uri, "grant_type": "refresh_token"},
            "token refresh", retry=True))
        return credentials


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


class NonInteractiveOAuth2(CheckedOAuth2):
    """Same as CheckedOAuth2, except the authorization-code exchange uses a
    code supplied ahead of time instead of blocking on input() / auto-opening
    a browser. The reply gets the same check; it is not retried, since an
    authorization code is single-use and someone is at the keyboard to rerun
    step 1 and step 2."""

    def __init__(self, consumer_key, consumer_secret, code, **kwargs):
        self._injected_code = code
        super().__init__(consumer_key, consumer_secret, **kwargs)

    def handler(self):
        self.verifier = self._injected_code
        self.token_time = time.time()
        credentials = {"token_time": self.token_time}
        credentials.update(self._token_request(
            {"code": self.verifier, "redirect_uri": self.callback_uri, "grant_type": "authorization_code"},
            "code exchange", retry=False))
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
    refreshing it if it has expired (CheckedOAuth2: checked reply, one retry
    on a temporary failure, YahooTokenError otherwise), and returns an
    authenticated requests session."""
    client_id, client_secret = load_credentials()
    token = load_token()
    if token is None:
        raise SystemExit(
            "[ERROR] No yahoo_token.json found. Run the one-time authorization "
            "(yahoo_auth_step1.py then yahoo_auth_step2.py) first."
        )

    oauth = CheckedOAuth2(
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
