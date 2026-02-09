import json

from integ.response_utils import extract_data


async def list_subscription_plugins(client, provider, auth_headers):
    resp_list = await client.get("/api/v1/host/auth/subscriptions", params={"provider": provider}, headers=auth_headers)
    assert resp_list.status_code == 200, resp_list.text
    items = extract_data(resp_list).get("items") or []
    return {i.get("plugin_name") for i in items}


async def execute_plugin(client, plugin, auth_headers):
    return await client.post(
        f"/api/v1/plugins/{plugin}/execute",
        json={
            "params": {
                "op": "mark_read",
                "user_email": "someone@example.com",
                "message_ids": ["abc123", "def456"],
                "preview": True,
            }
        },
        headers=auth_headers,
    )


async def process_streaming_result(response):
    final_event = {}
    try:
        body = response.content
        if hasattr(response, "aread"):
            try:
                body = await response.aread()
            except Exception:
                body = response.content
        text = body.decode() if isinstance(body, (bytes, bytearray)) else str(body)
        for line in text.split("\n"):
            if line[:6] != "data: ":
                continue
            data = {}
            try:
                data = json.loads(line[6:])
            except Exception:
                continue
            if data.get("event") in {"error", "final_message"}:
                final_event = data
    finally:
        try:
            closer = getattr(response, "aclose", None)
            if callable(closer):
                await closer()
            elif hasattr(response, "close"):
                response.close()
        except Exception:
            pass
    return final_event.get("content")
