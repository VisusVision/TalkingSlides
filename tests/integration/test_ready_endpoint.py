def test_ready_endpoint_returns_ok(client):
    response = client.get("/api/v1/ready/")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
