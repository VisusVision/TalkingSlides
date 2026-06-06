import re

from core.trace import extract_request_context, outbound_trace_headers, parse_traceparent


def test_parse_traceparent_valid():
    parsed = parse_traceparent("00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01")
    assert parsed["version"] == "00"
    assert parsed["trace_id"] == "4bf92f3577b34da6a3ce929d0e0e4736"
    assert parsed["parent_id"] == "00f067aa0ba902b7"
    assert parsed["trace_flags"] == "01"


def test_parse_traceparent_invalid():
    parsed = parse_traceparent("not-a-traceparent")
    assert parsed == {"version": "", "trace_id": "", "parent_id": "", "trace_flags": ""}


def test_parse_traceparent_zero_trace_id_rejected():
    parsed = parse_traceparent("00-00000000000000000000000000000000-00f067aa0ba902b7-01")
    assert parsed == {"version": "", "trace_id": "", "parent_id": "", "trace_flags": ""}


def test_extract_request_context_fallback_request_id():
    request = type("Request", (), {"headers": {}})()
    context = extract_request_context(request)
    assert context["request_id"].startswith("req_")
    assert len(context["request_id"]) <= 120
    assert re.fullmatch(r"[0-9a-f]{32}", context["trace_id"])
    assert context["traceparent"].startswith(f"00-{context['trace_id']}-")
    assert context["traceparent"].endswith("-01")


def test_outbound_trace_headers_normalize_and_preserve_request_id():
    headers = outbound_trace_headers(
        trace_id="ABCDEF0123456789ABCDEF0123456789",
        request_id="  req-custom  ",
    )
    assert headers["X-Request-ID"] == "req-custom"
    assert headers["traceparent"].startswith("00-abcdef0123456789abcdef0123456789-")
    assert headers["traceparent"].endswith("-01")
