import hashlib
import hmac
import json
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Request

from solution.dispatcher import Dispatcher
from solution.store import Store


def create_webhook_app(
    config: dict,
    store: Store,
    dispatcher: Dispatcher,
) -> FastAPI:
    app = FastAPI()
    secret = config["webhook_secret"].encode("utf-8")

    @app.post("/webhooks/telephony")
    async def receive_telephony_webhook(request: Request):
        raw_body = await request.body()
        signature = request.headers.get("X-AU-Signature", "")

        expected_signature = hmac.new(
            secret,
            raw_body,
            hashlib.sha256,
        ).hexdigest()

        if not hmac.compare_digest(signature, expected_signature):
            raise HTTPException(status_code=401, detail="Invalid signature")

        try:
            result = json.loads(raw_body)
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="Invalid JSON")

        if not isinstance(result, dict) or result.get("event") != "call.completed":
            raise HTTPException(status_code=400, detail="Unexpected event")

        try:
            saved = store.record_result(
                result=result,
                raw_body=raw_body,
                received_at=datetime.now(timezone.utc),
                max_attempts=dispatcher.max_attempts,
                retry_minutes=dispatcher.retry_minutes,
            )
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error))
        except (ValueError, TypeError) as error:
            raise HTTPException(status_code=400, detail=str(error))

        return {"ok": True, "duplicate": not saved}

    return app