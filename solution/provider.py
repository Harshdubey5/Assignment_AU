import httpx

from solution.scheduling import parse_instant


class ProviderClient:
    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8099",
        timeout: float = 10.0,
    ):
        self.http = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
        )

    def get_simulated_time(self):
        response = self.http.get("/v1/clock")
        response.raise_for_status()
        return parse_instant(response.json()["sim_now"])

    def place_call(self, payload: dict) -> tuple[int, dict]:
        response = self.http.post("/v1/calls", json=payload)

        if response.status_code in (200, 201, 503, 504):
            return response.status_code, response.json()

        response.raise_for_status()
        raise RuntimeError(f"Unexpected provider response: {response.status_code}")

    def find_call(self, request_id: str) -> dict | None:
        response = self.http.get(
            "/v1/calls",
            params={"request_id": request_id},
        )

        if response.status_code == 404:
            return None

        response.raise_for_status()
        return response.json()

    def get_call(self, call_sid: str) -> dict:
        response = self.http.get(f"/v1/calls/{call_sid}")
        response.raise_for_status()
        return response.json()

    def close(self) -> None:
        self.http.close()