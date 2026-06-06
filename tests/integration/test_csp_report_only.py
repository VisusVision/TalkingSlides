import json

from django.test import Client, override_settings


CSP_HEADER = "Content-Security-Policy-Report-Only"
CSP_REPORT_PATH = "/api/v1/security/csp-report/"


def test_csp_report_only_header_absent_by_default():
    response = Client().get("/api/v1/ready/")

    assert CSP_HEADER not in response


@override_settings(CSP_REPORT_ONLY_ENABLED=True)
def test_csp_report_only_header_present_when_enabled():
    response = Client().get("/api/v1/ready/")

    assert response[CSP_HEADER]
    assert "default-src 'self'" in response[CSP_HEADER]
    assert "frame-ancestors 'none'" in response[CSP_HEADER]
    assert "media-src 'self' blob: https:" in response[CSP_HEADER]


@override_settings(CSP_REPORT_ONLY_ENABLED=True)
def test_existing_security_headers_are_preserved_with_csp_report_only():
    response = Client().get("/api/v1/ready/")

    assert response["X-Frame-Options"] == "DENY"
    assert response["X-Content-Type-Options"] == "nosniff"
    assert response["Referrer-Policy"] == "same-origin"
    assert response["Permissions-Policy"]
    assert response["Cross-Origin-Resource-Policy"] == "same-site"
    assert CSP_HEADER in response
    assert "Content-Security-Policy" not in response


@override_settings(CSP_REPORT_ONLY_ENABLED=True)
def test_csp_report_endpoint_accepts_valid_json_without_auth():
    response = Client().post(
        CSP_REPORT_PATH,
        data=json.dumps(
            {
                "csp-report": {
                    "document-uri": "https://app.example.com/",
                    "violated-directive": "script-src-elem",
                }
            }
        ),
        content_type="application/csp-report",
    )

    assert response.status_code == 204
    assert response[CSP_HEADER]


def test_csp_report_endpoint_invalid_json_is_fail_safe():
    response = Client().post(
        CSP_REPORT_PATH,
        data=b"{not-json",
        content_type="application/csp-report",
    )

    assert response.status_code == 204


def test_csp_report_endpoint_rejects_get():
    response = Client().get(CSP_REPORT_PATH)

    assert response.status_code == 405


@override_settings(CSP_REPORT_BODY_MAX_BYTES=16)
def test_csp_report_endpoint_rejects_oversized_payload():
    response = Client().post(
        CSP_REPORT_PATH,
        data=b"x" * 17,
        content_type="application/csp-report",
    )

    assert response.status_code == 413
