#!/usr/bin/env python3
"""Tests for the token-endpoint check in league/yahoo_auth.py. From the repo root:

    python -m unittest discover -s league -v

No network, ever: the token request is a patched stand-in (or, for the timeout
test, requests.Session.request itself is patched), credentials are made up,
the token file is written to a temp dir and the retry wait is skipped.
"""
import json
import logging
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import requests
import rauth

import league_data
import league_availability   # noqa: E402 -- tracker/, put on the path by league_data
import yahoo_auth

CLIENT_ID, CLIENT_SECRET = "fake-client-id-123", "fake-client-secret-456"
OLD = {"access_token": "OLD-ACCESS", "token_type": "bearer", "refresh_token": "OLD-REFRESH", "token_time": 0}
GOOD = {"access_token": "NEW-ACCESS-TOKEN", "token_type": "bearer", "refresh_token": "NEW-REFRESH-TOKEN", "expires_in": 3600}
OUTAGE = "<html>\n  <head><title>503 Service   Unavailable</title></head>\n  <body>Yahoo is down</body>\n</html>"


class FakeResponse:
    def __init__(self, status, body):
        self.status_code = status
        self.content = body.encode("utf-8") if isinstance(body, str) else body
        self.text = self.content.decode("utf-8", "replace")


def reply(status, body):
    return FakeResponse(status, json.dumps(body) if isinstance(body, (dict, list)) else body)


class TokenEndpoint:
    """Stands in for rauth's OAuth2Service.get_raw_access_token: answers with the queued replies (a response or an
    exception to raise), one per call, and records each call's keyword arguments."""

    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []

    def __call__(self, method="POST", **kwargs):   # a class attribute that isn't a function: no self of the service
        self.calls.append(kwargs)
        r = self.replies.pop(0)
        if isinstance(r, BaseException):
            raise r
        return r


class Base(unittest.TestCase):
    def setUp(self):
        logging.getLogger("yahoo_oauth").setLevel(logging.WARNING)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.token_path = Path(tmp.name) / "yahoo_token.json"
        self.token_path.write_text(json.dumps(OLD), encoding="utf-8")
        self.sleeps = []
        for target, value in (("TOKEN_PATH", self.token_path), ("_sleep", self.sleeps.append),
                              ("load_credentials", lambda: (CLIENT_ID, CLIENT_SECRET))):
            p = mock.patch.object(yahoo_auth, target, value)
            p.start()
            self.addCleanup(p.stop)

    def endpoint(self, *replies):
        fake = TokenEndpoint(*replies)
        p = mock.patch.object(rauth.OAuth2Service, "get_raw_access_token", fake)
        p.start()
        self.addCleanup(p.stop)
        return fake

    def refresh_error(self):
        with self.assertRaises(yahoo_auth.YahooTokenError) as cm:
            yahoo_auth.get_session()
        return cm.exception

    def assert_no_secrets(self, message):
        for secret in (CLIENT_ID, CLIENT_SECRET, "OLD-ACCESS", "OLD-REFRESH", "NEW-ACCESS-TOKEN", "NEW-REFRESH-TOKEN"):
            self.assertNotIn(secret, message)


class TestRefresh(Base):
    def test_valid_200_parses_as_before(self):
        fake = self.endpoint(reply(200, GOOD))
        session = yahoo_auth.get_session()
        self.assertIsInstance(session, requests.Session)
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(fake.calls[0]["data"]["grant_type"], "refresh_token")
        self.assertEqual(fake.calls[0]["data"]["refresh_token"], "OLD-REFRESH")
        self.assertEqual(fake.calls[0]["timeout"], yahoo_auth.TOKEN_TIMEOUT_S)
        saved = json.loads(self.token_path.read_text(encoding="utf-8"))
        self.assertEqual({k: saved[k] for k in ("access_token", "token_type", "refresh_token")},
                         {k: GOOD[k] for k in ("access_token", "token_type", "refresh_token")})
        self.assertGreater(saved["token_time"], 0)
        self.assertEqual(self.sleeps, [])

    def test_parser_matches_the_library_on_a_good_reply(self):
        """Same credentials dict and attributes as yahoo_oauth's own parser."""
        checked = yahoo_auth.CheckedOAuth2.__new__(yahoo_auth.CheckedOAuth2)
        plain = yahoo_auth.OAuth2.__new__(yahoo_auth.OAuth2)
        r = reply(200, GOOD)
        self.assertEqual(checked.oauth2_access_parser(r), yahoo_auth.OAuth2.oauth2_access_parser(plain, r))
        self.assertEqual((checked.access_token, checked.token_type, checked.refresh_token),
                         (plain.access_token, plain.token_type, plain.refresh_token))

    def test_503_html_is_retried_once_then_a_clear_error(self):
        fake = self.endpoint(reply(503, OUTAGE), reply(503, OUTAGE))
        e = self.refresh_error()
        self.assertEqual(len(fake.calls), 2)
        self.assertEqual(self.sleeps, [yahoo_auth.TOKEN_RETRY_WAIT_S])
        self.assertEqual(e.status, 503)
        msg = str(e)
        self.assertTrue(msg.startswith("Yahoo token refresh failed: HTTP 503, body '<html> <head><title>503 Service Unavailable"), msg)
        self.assertIn("(also on a retry after 20 s)", msg)
        self.assertNotIn("re-authorize", msg)
        self.assertNotIn("\n", msg)
        self.assert_no_secrets(msg)
        self.assertEqual(json.loads(self.token_path.read_text(encoding="utf-8")), OLD)   # nothing saved

    def test_503_then_200_succeeds_on_the_retry_and_saves_the_token(self):
        fake = self.endpoint(reply(503, OUTAGE), reply(200, GOOD))
        yahoo_auth.get_session()
        self.assertEqual(len(fake.calls), 2)
        self.assertEqual(self.sleeps, [yahoo_auth.TOKEN_RETRY_WAIT_S])
        saved = json.loads(self.token_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["access_token"], "NEW-ACCESS-TOKEN")
        self.assertEqual(saved["refresh_token"], "NEW-REFRESH-TOKEN")

    def test_400_invalid_grant_is_not_retried_and_says_to_reauthorize(self):
        fake = self.endpoint(reply(400, {"error": "invalid_grant", "error_description": "token revoked"}), reply(200, GOOD))
        e = self.refresh_error()
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(self.sleeps, [])
        self.assertEqual(str(e), "Yahoo token refresh rejected: HTTP 400 invalid_grant -- re-authorize with "
                                 "league/yahoo_auth_step1.py then step2.py, signed in to the Yahoo account that is in the league")
        self.assertFalse(e.retryable)

    def test_401_invalid_client_is_not_retried(self):
        fake = self.endpoint(reply(401, {"error": "invalid_client"}), reply(200, GOOD))
        self.assertIn("rejected: HTTP 401 invalid_client -- re-authorize", str(self.refresh_error()))
        self.assertEqual(len(fake.calls), 1)

    def test_4xx_without_json_is_not_retried(self):
        fake = self.endpoint(reply(403, "<html>Forbidden</html>"), reply(200, GOOD))
        msg = str(self.refresh_error())
        self.assertIn("rejected: HTTP 403, body '<html>Forbidden</html>' -- not retried", msg)
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(self.sleeps, [])

    def test_invalid_grant_is_not_retried_even_on_a_5xx(self):
        fake = self.endpoint(reply(500, {"error": "invalid_grant"}), reply(200, GOOD))
        self.assertIn("rejected: HTTP 500 invalid_grant", str(self.refresh_error()))
        self.assertEqual(len(fake.calls), 1)

    def test_temporarily_unavailable_json_on_a_5xx_is_retried(self):
        fake = self.endpoint(reply(503, {"error": "temporarily_unavailable"}), reply(200, GOOD))
        yahoo_auth.get_session()
        self.assertEqual(len(fake.calls), 2)

    def test_200_missing_access_token_does_not_show_the_body(self):
        body = {"token_type": "bearer", "refresh_token": "NEW-REFRESH-TOKEN", "xoauth_yahoo_guid": "GUID-SECRET"}
        fake = self.endpoint(reply(200, body), reply(200, GOOD))
        e = self.refresh_error()
        msg = str(e)
        self.assertEqual(msg, "Yahoo token refresh failed: HTTP 200, reply has no access_token (body not shown)")
        self.assertNotIn("GUID-SECRET", msg)
        self.assert_no_secrets(msg)
        self.assertEqual(len(fake.calls), 1)   # not a temporary failure

    def test_empty_200_is_non_json_and_never_shown(self):
        fake = self.endpoint(reply(200, ""), reply(200, b""))
        msg = str(self.refresh_error())
        self.assertEqual(msg, "Yahoo token refresh failed: HTTP 200, reply is not JSON (body not shown) (also on a retry after 20 s)")
        self.assertEqual(len(fake.calls), 2)

    def test_empty_503_body(self):
        self.endpoint(reply(503, ""), reply(503, ""))
        self.assertIn("failed: HTTP 503, body ''", str(self.refresh_error()))

    def test_non_json_200_body_is_never_shown(self):
        self.endpoint(reply(200, "access_token=NEW-ACCESS-TOKEN&x=1"), reply(200, "access_token=NEW-ACCESS-TOKEN&x=1"))
        msg = str(self.refresh_error())
        self.assertIn("reply is not JSON (body not shown)", msg)
        self.assert_no_secrets(msg)

    def test_connection_error_and_timeout_are_retried(self):
        fake = self.endpoint(requests.exceptions.ConnectionError("Max retries exceeded"), requests.exceptions.ReadTimeout("read timed out"))
        msg = str(self.refresh_error())
        self.assertEqual(msg, "Yahoo token refresh failed: ReadTimeout (read timed out) (also on a retry after 20 s)")
        self.assertEqual(len(fake.calls), 2)
        fake = self.endpoint(requests.exceptions.ConnectTimeout("connect timed out"), reply(200, GOOD))
        yahoo_auth.get_session()
        self.assertEqual(len(fake.calls), 2)

    def test_body_snippet_is_capped_collapsed_and_redacted(self):
        page = "<html>" + ("  x\n" * 300) + f" {CLIENT_SECRET} OLD-REFRESH </html>"
        self.endpoint(reply(502, f"{CLIENT_ID} {page}"), reply(502, page))
        msg = str(self.refresh_error())
        snippet = msg.split("body '", 1)[1].split("'", 1)[0]
        self.assertLessEqual(len(snippet), yahoo_auth.BODY_SNIPPET_CHARS + 3)
        self.assertTrue(snippet.endswith("..."))
        self.assertNotIn("  ", snippet)
        self.assert_no_secrets(msg)
        tail = yahoo_auth.body_snippet(f"a {CLIENT_SECRET} b OLD-REFRESH", [CLIENT_SECRET, "OLD-REFRESH", None])
        self.assertEqual(tail, "a [redacted] b [redacted]")

    def test_valid_token_makes_no_request(self):
        with open(self.token_path, "w", encoding="utf-8") as f:
            json.dump(dict(OLD, token_time=time.time()), f)
        fake = self.endpoint()
        yahoo_auth.get_session()
        self.assertEqual(fake.calls, [])

    def test_the_timeout_reaches_requests(self):
        """Through rauth's real get_raw_access_token, down to requests.Session.request (patched: no network)."""
        seen = []

        def fake_request(session, method, url, **kwargs):
            seen.append((method, url, kwargs.get("timeout")))
            return reply(200, GOOD)

        with mock.patch.object(requests.Session, "request", fake_request):
            yahoo_auth.get_session()
        self.assertEqual(seen, [("POST", "https://api.login.yahoo.com/oauth2/get_token", yahoo_auth.TOKEN_TIMEOUT_S)])


class TestCodeExchange(Base):
    def test_code_exchange_gets_the_same_check_without_a_retry(self):
        fake = self.endpoint(reply(503, OUTAGE), reply(200, GOOD))
        with self.assertRaises(yahoo_auth.YahooTokenError) as cm:
            yahoo_auth.exchange_code_for_token(CLIENT_ID, CLIENT_SECRET, "THE-CODE")
        self.assertTrue(str(cm.exception).startswith("Yahoo code exchange failed: HTTP 503, body '<html>"))
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(json.loads(self.token_path.read_text(encoding="utf-8")), OLD)

    def test_code_exchange_rejected(self):
        self.endpoint(reply(400, {"error": "invalid_grant"}))
        with self.assertRaises(yahoo_auth.YahooTokenError) as cm:
            yahoo_auth.exchange_code_for_token(CLIENT_ID, CLIENT_SECRET, "THE-CODE")
        self.assertIn("Yahoo code exchange rejected: HTTP 400 invalid_grant -- re-authorize", str(cm.exception))

    def test_code_exchange_ok(self):
        fake = self.endpoint(reply(200, GOOD))
        _oauth, token = yahoo_auth.exchange_code_for_token(CLIENT_ID, CLIENT_SECRET, "THE-CODE")
        self.assertEqual(fake.calls[0]["data"]["grant_type"], "authorization_code")
        self.assertEqual(fake.calls[0]["data"]["code"], "THE-CODE")
        self.assertEqual(token["access_token"], "NEW-ACCESS-TOKEN")
        self.assertEqual(json.loads(self.token_path.read_text(encoding="utf-8"))["refresh_token"], "NEW-REFRESH-TOKEN")


class TestTheMessageReachesTheCallers(Base):
    """league_data.py and tracker/league_availability.py record `type(e).__name__: e` unchanged."""

    def test_league_data_records_it_on_every_resource(self):
        self.endpoint(reply(503, OUTAGE), reply(503, OUTAGE))
        out = league_data.fetch("470.l.999", open_session=yahoo_auth.get_session, budget_s=5)
        errors = {r["error"] for r in out.values()}
        self.assertEqual(len(errors), 1)
        self.assertTrue(errors.pop().startswith("YahooTokenError: Yahoo token refresh failed: HTTP 503, body '<html>"))

    def test_league_availability_records_it(self):
        self.endpoint(reply(400, {"error": "invalid_grant"}))
        out = league_availability.fetch("470.l.999", open_session=yahoo_auth.get_session, budget_s=5)
        self.assertFalse(out["ok"])
        self.assertTrue(out["error"].startswith("YahooTokenError: Yahoo token refresh rejected: HTTP 400 invalid_grant -- re-authorize"))


if __name__ == "__main__":
    unittest.main()
