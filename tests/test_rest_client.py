"""Offline unit tests for the ČEZ Distribuce REST client.

These tests never touch the network: HTTP is mocked with ``requests_mock`` and
pure helpers are checked directly. They guard against regressions of the
CAS/MEPAS host migration (cas.cez.cz → mepas.cez.cz) that previously broke the
integration in production with a ``TypeError`` on the missing ``execution``
form field.
"""
from __future__ import annotations

import types

import pytest
import requests_mock as rm_module

from custom_components.cez_distribuce.rest_client.rest_client import (
    CAS_BASE_URL,
    CEZ_DISTRIBUCE_BASE_URL,
    CEZ_DISTRIBUCECLIENT_ID,
    CezDistribuceRestClient,
    is_array,
    log_history,
)

# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def test_is_array():
    # Non-strings sequences are arrays
    assert is_array([1, 2, 3]) is True
    assert is_array((1, 2)) is True
    # Strings are sequences but must return False
    assert is_array("abc") is False
    # Non-sequences are False
    assert is_array(123) is False
    assert is_array({"a": 1}) is False


def test_is_array_edge_cases():
    # Empty containers are still arrays by shape
    assert is_array([]) is True
    assert is_array(()) is True
    # None and scalars are not arrays
    assert is_array(None) is False
    assert is_array(3.14) is False


def _hop(status_code=302, is_redirect=True, headers=None, url="http://a"):
    """Build a history-item-like object exposing the attrs log_history reads."""
    return types.SimpleNamespace(
        status_code=status_code,
        is_redirect=is_redirect,
        headers=headers or {"Location": "http://b"},
        url=url,
    )


def test_log_history_formats_each_hop_on_new_line():
    response_like = types.SimpleNamespace(
        history=[_hop(302, True, {"Location": "http://b"}, "http://a")],
        status_code=200,
        is_redirect=False,
        headers={"Content-Type": "text/html"},
        url="http://b",
    )

    out = log_history(response_like)

    lines = [ln for ln in out.split("\n") if ln]
    assert len(lines) == 2
    assert "302" in lines[0] and "http://a" in lines[0]
    assert "200" in lines[1] and "http://b" in lines[1]


def test_log_history_empty_history_returns_single_line():
    """With no redirect hops only the final response line is emitted."""
    response_like = types.SimpleNamespace(
        history=[],
        status_code=200,
        is_redirect=False,
        headers={"Content-Type": "application/json"},
        url="http://final",
    )
    out = log_history(response_like)
    lines = [ln for ln in out.split("\n") if ln]
    assert lines == ["200 False {'Content-Type': 'application/json'} http://final"]


def test_log_history_multiple_hops_preserve_order():
    response_like = types.SimpleNamespace(
        history=[
            _hop(302, True, {"Location": "http://b"}, "http://a"),
            _hop(301, True, {"Location": "http://c"}, "http://b"),
        ],
        status_code=200,
        is_redirect=False,
        headers={"Content-Type": "application/json"},
        url="http://c",
    )
    out = log_history(response_like)
    lines = [ln for ln in out.split("\n") if ln]
    assert [l.split()[0] for l in lines] == ["302", "301", "200"]
    assert "http://a" in lines[0] and "http://b" in lines[1] and "http://c" in lines[2]


# ---------------------------------------------------------------------------
# URL / config regression tests
#
# ČEZ moved the central authentication service from cas.cez.cz to mepas.cez.cz
# and changed the OAuth client_id. If either reverts or changes again, these
# fail loudly with a clear message rather than a cryptic TypeError in prod.
# ---------------------------------------------------------------------------

def test_cas_base_url_is_mepas_host():
    assert CAS_BASE_URL == "https://mepas.cez.cz/cas", (
        "ČEZ přesunul CAS na jiný host – integraci je třeba aktualizovat. "
        "Ověř tlačítko přihlášení na https://dip.cezdistribuce.cz/irj/portal"
    )


def test_client_id_is_current():
    assert CEZ_DISTRIBUCECLIENT_ID == "emiCuDBbivwYxraX.dip.dip.ext.zak.prod.v1", (
        "client_id se změnil – zkontroluj přihlašovací odkaz na portálu."
    )


def test_client_urls_target_mepas_and_new_authorize_path():
    client = CezDistribuceRestClient()
    # All auth URLs must point at the new MEPAS host
    assert "mepas.cez.cz" in client._login_url
    assert "mepas.cez.cz" in client._authorize_url
    assert "mepas.cez.cz" in client._service
    # The legacy cas.cez.cz host must NOT appear anywhere in auth URLs
    assert "cas.cez.cz" not in client._login_url
    assert "cas.cez.cz" not in client._authorize_url
    # The authorize endpoint uses the new /oidc/oidcAuthorize path
    assert "/oidc/oidcAuthorize" in client._authorize_url
    # client_id is embedded in both service and authorize URLs
    assert CEZ_DISTRIBUCECLIENT_ID in client._service
    assert CEZ_DISTRIBUCECLIENT_ID in client._authorize_url


def test_old_cas_host_returns_403_and_no_execution_field(requests_mock):
    """Regression test for the original production failure.

    The legacy host (cas.cez.cz) with the legacy client_id now returns 403
    with an HTML page that has no <input name="execution">, which used to
    surface as `TypeError: 'NoneType' object is not subscriptable`.
    """
    legacy_client = CezDistribuceRestClient(
        base_url=CEZ_DISTRIBUCE_BASE_URL,
        client_id="fjR3ZL9zrtsNcDQF.onpremise.dip.sap.dipcezdistribucecz.prod",
    )
    # Force the legacy CAS host by overriding the base for this test
    legacy_client._login_url = legacy_client._login_url.replace(
        "mepas.cez.cz", "cas.cez.cz"
    )
    requests_mock.get(legacy_client._login_url, status_code=403, text="<html>no form</html>")

    with pytest.raises(TypeError, match="NoneType"):
        legacy_client.login("user", "pass")


# ---------------------------------------------------------------------------
# Mocked full login flow + data methods
# ---------------------------------------------------------------------------

LOGIN_HTML = """<!DOCTYPE html><html><body>
<form action="login" method="post">
  <input name="username" />
  <input name="password" />
  <input name="execution" value="exec-token-12345" />
  <input name="_eventId" value="submit" />
  <input name="geolocation" />
</form></body></html>"""


def _envelope(data):
    """Build a standard ČEZ JSON envelope: {statusCode, data, flashMessages}."""
    return {"statusCode": 200, "data": data, "flashMessages": []}


@pytest.fixture()
def mocked_client(requests_mock):
    """A CezDistribuceRestClient with the full login flow + token endpoints mocked.

    Every endpoint the integration touches is registered so individual tests
    can override only the one they care about.
    """
    client = CezDistribuceRestClient()
    b = client._base_url
    m = requests_mock
    # CAS login chain: GET login HTML, POST credentials (302), GET authorize
    m.get(client._login_url, text=LOGIN_HTML)
    m.post(client._login_url, status_code=302, headers={"Location": client._authorize_url})
    m.get(client._authorize_url, status_code=200, json={"ok": True})
    # Token endpoints return a bare JSON string token (no envelope)
    m.get(f"{b}/rest-auth-api?path=/token/get", json="auth-token-abc")
    m.get(f"{b}/anonymous/rest-auth-api?path=/token/get", json="anon-token-xyz")
    return client


def test_login_populates_both_session_tokens(mocked_client, requests_mock):
    mocked_client.login("user", "pass")

    # Authenticated session received the auth token header
    assert mocked_client._session.headers["X-Request-Token"] == "auth-token-abc"
    # Anonymous session received the anon token header
    assert mocked_client._anonymous_session.headers["X-Request-Token"] == "anon-token-xyz"
    # The execution token from the HTML form was posted back
    posted_exec = [
        req for req in requests_mock.request_history
        if req.method == "POST" and "cas.cez.cz" not in req.url and "mepas.cez.cz" in req.url
    ]
    assert any(req.text and "exec-token-12345" in req.text for req in posted_exec)


def test_common_header_unwraps_data_envelope(mocked_client, requests_mock):
    b = mocked_client._base_url
    requests_mock.get(
        f"{b}/common-api?path=/common/header",
        json=_envelope({"user": "john", "lang": "cs"}),
    )
    result = mocked_client.common_header()
    assert result == {"user": "john", "lang": "cs"}


def test_get_supply_points_unwraps_data(mocked_client, requests_mock):
    b = mocked_client._base_url
    payload = {"vstelleBlocks": {"blocks": [{"vstelles": [{"uid": "U1", "ean": "E1"}]}]}}
    requests_mock.post(
        f"{b}/vyhledani-om?path=/vyhledaniom/zakladniInfo/50/PREHLED_OM_CELEK",
        json=_envelope(payload),
    )
    result = mocked_client.get_supply_points()
    assert result == payload
    assert result["vstelleBlocks"]["blocks"][0]["vstelles"][0]["uid"] == "U1"


def test_get_supply_point_detail_unwraps_data(mocked_client, requests_mock):
    b = mocked_client._base_url
    requests_mock.get(
        f"{b}/prehled-om?path=supply-point-detail/U1",
        json=_envelope({"ean": "E1", "hdo": True}),
    )
    result = mocked_client.get_supply_point_detail("U1")
    assert result == {"ean": "E1", "hdo": True}


def test_get_readings_unwraps_data_list(mocked_client, requests_mock):
    b = mocked_client._base_url
    readings = [{"datumOdectu": "2025-01-01", "stavVt": 100.0, "stavNt": 50.0}]
    requests_mock.post(
        f"{b}/prehled-om?path=supply-point-detail/meter-reading-history/U1/false",
        json=_envelope(readings),
    )
    result = mocked_client.get_readings("U1")
    assert result == readings
    assert isinstance(result, list)


def test_get_signals_unwraps_data(mocked_client, requests_mock):
    b = mocked_client._base_url
    signals = {"signals": [{"datum": "01.01.2025", "casy": "00:00 - 08:00"}]}
    requests_mock.get(
        f"{b}/prehled-om?path=supply-point-detail/signals/E1",
        json=_envelope(signals),
    )
    result = mocked_client.get_signals("E1")
    assert result == signals
    assert "signals" in result


def test_get_outages_wraps_single_ean_and_unwraps_data(mocked_client, requests_mock):
    b = mocked_client._base_url
    outages = [{"dateFormatted": "29.10.2025", "timeFormatted": "08:00 - 12:00", "number": "X"}]
    requests_mock.post(
        f"{b}/anonymous/vyhledani-odstavek?path=shutdown-search",
        json=_envelope(outages),
    )
    result = mocked_client.get_outages(ean="E1")
    assert result == outages
    # Single string EAN must be wrapped into a one-element list in the payload
    outage_req = next(
        req for req in requests_mock.request_history
        if "shutdown-search" in req.url
    )
    assert outage_req.json() == {"eans": ["E1"]}


def test_get_outages_with_ean_list_passes_through(mocked_client, requests_mock):
    b = mocked_client._base_url
    requests_mock.post(
        f"{b}/anonymous/vyhledani-odstavek?path=shutdown-search",
        json=_envelope([]),
    )
    mocked_client.get_outages(ean=["E1", "E2"])
    outage_req = next(
        req for req in requests_mock.request_history if "shutdown-search" in req.url
    )
    assert outage_req.json() == {"eans": ["E1", "E2"]}


def test_get_outages_without_any_identifier_raises_valueerror(mocked_client):
    with pytest.raises(ValueError, match="Either ean, meterNumber or psc and mesto"):
        mocked_client.get_outages()


def test_get_outages_by_meter_number(mocked_client, requests_mock):
    b = mocked_client._base_url
    requests_mock.post(
        f"{b}/anonymous/vyhledani-odstavek?path=shutdown-search",
        json=_envelope([]),
    )
    mocked_client.get_outages(meterNumber="M123")
    outage_req = next(
        req for req in requests_mock.request_history if "shutdown-search" in req.url
    )
    assert outage_req.json() == {"meterNumbers": ["M123"]}


def test_status_code_401_in_envelope_triggers_token_refresh_then_retry(
    mocked_client, requests_mock
):
    """A statusCode=401 envelope must trigger refresh_api_token and retry."""
    b = mocked_client._base_url
    # First call returns 401 envelope, second (retry) returns success envelope.
    # The token endpoint must be callable; requests_mock returns the latest by
    # default so we register two responses for the same endpoint.
    requests_mock.get(
        f"{b}/common-api?path=/common/header",
        [
            {"json": {"statusCode": 401, "data": None, "flashMessages": []}},
            {"json": _envelope({"ok": True})},
        ],
    )
    # refresh_api_token hits the auth token endpoint again on 401
    requests_mock.get(
        f"{b}/rest-auth-api?path=/token/get",
        json="auth-token-refreshed",
    )
    result = mocked_client.common_header()
    assert result == {"ok": True}
    # Token was refreshed during the retry
    assert mocked_client._session.headers["X-Request-Token"] == "auth-token-refreshed"


def test_unable_to_get_rest_token_after_retries(mocked_client, requests_mock):
    """Persistent statusCode != 200/401 exhausts retries and raises."""
    b = mocked_client._base_url
    requests_mock.get(
        f"{b}/common-api?path=/common/header",
        json={"statusCode": 500, "data": None, "flashMessages": []},
    )
    with pytest.raises(Exception, match="Unable to get rest token"):
        mocked_client.common_header()


def test_handle_login_401_triggers_relogin(requests_mock):
    """An HTTP 401 (not envelope 401) on a data call re-runs login() and retries."""
    client = CezDistribuceRestClient()
    b = client._base_url
    # CAS chain used by login()
    requests_mock.get(client._login_url, text=LOGIN_HTML)
    requests_mock.post(client._login_url, status_code=302, headers={"Location": client._authorize_url})
    requests_mock.get(client._authorize_url, status_code=200, json={"ok": True})
    requests_mock.get(f"{b}/rest-auth-api?path=/token/get", json="auth-token")
    requests_mock.get(f"{b}/anonymous/rest-auth-api?path=/token/get", json="anon-token")
    # First common_header call -> 401 HTTP, second -> 200 envelope
    requests_mock.get(
        f"{b}/common-api?path=/common/header",
        [
            {"status_code": 401, "text": "unauthorized"},
            {"json": _envelope({"ok": True})},
        ],
    )

    # login() once up front so _handle_login's no-arg self.login() has creds
    client.login("user", "pass")
    result = client.common_header()
    assert result == {"ok": True}


# ---------------------------------------------------------------------------
# Expired portal session (issue #8)
#
# After roughly a day the portal session expires and data calls are answered
# with the CAS login page (HTTP 200, HTML) instead of JSON. The client used to
# re-login only on HTTP 401, so every refresh failed with "Expecting value:
# line 1 column 1 (char 0)" until Home Assistant was restarted.
# ---------------------------------------------------------------------------

def test_html_instead_of_json_triggers_relogin_then_retry(mocked_client, requests_mock):
    b = mocked_client._base_url
    mocked_client.login("user", "pass")
    requests_mock.get(
        f"{b}/prehled-om?path=supply-point-detail/signals/E1",
        [
            {"status_code": 200, "text": LOGIN_HTML, "headers": {"Content-Type": "text/html"}},
            {"json": _envelope({"signals": [{"datum": "25.10.2026", "casy": "00:00-10:00"}]})},
        ],
    )
    logins_before = sum(1 for r in requests_mock.request_history if r.method == "POST" and "mepas.cez.cz" in r.url)

    result = mocked_client.get_signals("E1")

    assert result == {"signals": [{"datum": "25.10.2026", "casy": "00:00-10:00"}]}
    logins_after = sum(1 for r in requests_mock.request_history if r.method == "POST" and "mepas.cez.cz" in r.url)
    assert logins_after == logins_before + 1


def test_persistent_html_raises_unable_to_login(mocked_client, requests_mock):
    b = mocked_client._base_url
    mocked_client.login("user", "pass")
    requests_mock.get(
        f"{b}/prehled-om?path=supply-point-detail/signals/E1",
        status_code=200, text=LOGIN_HTML, headers={"Content-Type": "text/html"},
    )
    with pytest.raises(Exception, match="Unable to login"):
        mocked_client.get_signals("E1")


def test_login_starts_with_empty_cookie_jar(mocked_client, requests_mock):
    """A fresh login must not carry the expired session's cookies (like a restart)."""
    mocked_client._session.cookies.set("JSESSIONID", "expired", domain="dip.cezdistribuce.cz")
    mocked_client._session.cookies.set("TGC", "expired", domain="mepas.cez.cz")

    mocked_client.login("user", "pass")

    first_login_get = next(r for r in requests_mock.request_history if r.method == "GET" and "/cas/login" in r.url)
    assert "expired" not in (first_login_get.headers.get("Cookie") or "")


def test_html_from_token_endpoint_during_relogin_is_bounded(mocked_client, requests_mock):
    """Wrong password or a maintenance page: the token endpoint answers HTML too.

    The re-login must not recurse (login -> token -> re-login -> ...), which would
    hammer CAS with logins until RecursionError and risk an account lockout.
    """
    b = mocked_client._base_url
    mocked_client.login("user", "pass")
    html = {"status_code": 200, "text": LOGIN_HTML, "headers": {"Content-Type": "text/html"}}
    requests_mock.get(f"{b}/prehled-om?path=supply-point-detail/signals/E1", **html)
    requests_mock.get(f"{b}/rest-auth-api?path=/token/get", **html)
    logins_before = sum(1 for r in requests_mock.request_history if r.method == "POST" and "mepas.cez.cz" in r.url)

    with pytest.raises(Exception, match="Unable to login"):
        mocked_client.get_signals("E1")

    logins = sum(1 for r in requests_mock.request_history if r.method == "POST" and "mepas.cez.cz" in r.url)
    assert logins - logins_before == 1


def test_relogin_after_non_json_answer_is_logged_without_body(mocked_client, requests_mock, caplog):
    """What the portal returns for an expired session is not documented; log its shape (no body)."""
    import logging

    b = mocked_client._base_url
    mocked_client.login("user", "pass")
    requests_mock.get(
        f"{b}/prehled-om?path=supply-point-detail/signals/E1",
        [
            {"status_code": 200, "text": LOGIN_HTML, "headers": {"Content-Type": "text/html;charset=UTF-8"}},
            {"json": _envelope({"signals": []})},
        ],
    )
    with caplog.at_level(logging.INFO, logger="custom_components.cez_distribuce"):
        mocked_client.get_signals("E1")

    records = [r for r in caplog.records if r.name.startswith("custom_components.cez_distribuce")]
    assert len(records) == 1
    message = records[0].getMessage()
    assert "200" in message and "text/html" in message
    assert "execution" not in message   # no response body
