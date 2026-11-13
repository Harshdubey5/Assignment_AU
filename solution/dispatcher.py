import json
import time

from solution.provider import ProviderClient
from solution.scheduling import is_open, parse_instant, priority_key
from solution.store import Store


class Dispatcher:
    def __init__(
        self,
        config: dict,
        store: Store,
        provider: ProviderClient,
        callback_url: str = "http://127.0.0.1:8200/webhooks/telephony",
    ):
        self.config = config
        self.store = store
        self.provider = provider
        self.callback_url = callback_url

        self.campaigns = {
            campaign["campaign_id"]: campaign
            for campaign in config["campaigns"]
        }
        self.tenants = {
            tenant["tenant_id"]: tenant
            for tenant in config["tenants"]
        }
        self.max_attempts = {
            campaign["campaign_id"]: campaign["max_attempts"]
            for campaign in config["campaigns"]
        }
        self.retry_minutes = {
            "no_answer": config["retry_policy"]["no_answer_minutes"],
            "busy": config["retry_policy"]["busy_minutes"],
            "failed": config["retry_policy"]["failed_minutes"],
        }

    def _record_result(self, result: dict) -> None:
        raw_body = json.dumps(result).encode("utf-8")
        self.store.record_result(
            result=result,
            raw_body=raw_body,
            received_at=self.provider.get_simulated_time(),
            max_attempts=self.max_attempts,
            retry_minutes=self.retry_minutes,
        )

    def _accept_remote_call(self, request_id: str, remote_call: dict) -> None:
        self.store.mark_accepted(
            request_id=request_id,
            call_sid=remote_call["call_sid"],
            started_at=remote_call.get("created_at", ""),
        )

        if remote_call["state"] == "ended":
            self._record_result(remote_call["result"])

    def _send_prepared_call(self, call) -> None:
        """Look up first; if absent, submit using the same saved request ID."""
        remote_call = self.provider.find_call(call["request_id"])

        if remote_call is None:
            campaign = self.campaigns[call["campaign_id"]]
            payload = {
                "request_id": call["request_id"],
                "campaign_id": call["campaign_id"],
                "lead_id": call["lead_id"],
                "phone": call["phone"],
                "callback_url": self.callback_url,
            }
            status_code, response_data = self.provider.place_call(payload)

            if status_code == 503:
                return

            if status_code == 504:
                remote_call = self.provider.find_call(call["request_id"])
                if remote_call is None:
                    return
            else:
                remote_call = response_data

        self._accept_remote_call(call["request_id"], remote_call)

    def reconcile_active_calls(self) -> None:
        """Poll active calls so a missing webhook won’t lose a result."""
        for call in self.store.outstanding_calls():
            if call["state"] != "active":
                continue

            remote_call = self.provider.find_call(call["request_id"])
            if remote_call is not None and remote_call["state"] == "ended":
                self._record_result(remote_call["result"])

    def dispatch_one(self) -> bool:
        """Start at most one eligible call; return whether a request was attempted."""
        now = self.provider.get_simulated_time()
        candidates = self.store.eligible_leads(now)

        candidates = sorted(
            candidates,
            key=lambda lead: priority_key(
                lead["next_kind"],
                parse_instant(lead["next_at"]) if lead["next_at"] else None,
                lead["campaign_id"],
                lead["lead_id"],
            ),
        )

        active_phones = self.store.active_phones()

        for lead in candidates:
            campaign = self.campaigns[lead["campaign_id"]]
            window = campaign["window"]
            tenant = self.tenants[campaign["tenant_id"]]

            if not is_open(
                now,
                window["start"],
                window["end"],
                window["days"],
            ):
                continue

            regulatory = self.config["regulatory_window"]
            if not is_open(
                now,
                regulatory["start"],
                regulatory["end"],
            ):
                continue

            if lead["phone"] in active_phones:
                continue

            if (
                self.store.active_count_for_tenant(tenant["tenant_id"])
                >= tenant["max_concurrent_calls"]
            ):
                continue

            prepared_call = self.store.reserve_call(
                lead["campaign_id"],
                lead["lead_id"],
            )
            self._send_prepared_call(prepared_call)
            return True

        return False

    def all_leads_finished(self) -> bool:
        with self.store._lock:
            row = self.store.connection.execute(
                "SELECT COUNT(*) AS remaining FROM leads WHERE state='ready'"
            ).fetchone()
            return row["remaining"] == 0

    def run(self, poll_seconds: float = 0.1) -> None:
        while True:
            self.reconcile_active_calls()

            if self.all_leads_finished():
                return

            self.dispatch_one()
            time.sleep(poll_seconds)