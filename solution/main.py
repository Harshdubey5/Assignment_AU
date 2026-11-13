import threading
import time
import os
from pathlib import Path

import uvicorn

from solution.config import load_config
from solution.dispatcher import Dispatcher
from solution.provider import ProviderClient
from solution.store import Store
from solution.webhook import create_webhook_app
from solution.reporting import write_final_state


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = PROJECT_ROOT / "assignment-b-workflow" / "fixtures"
CONFIG_FILE = FIXTURE_DIR / "campaigns.json"
DATABASE_FILE = Path(
    os.environ.get(
        "AU_DISPATCHER_DB",
        str(PROJECT_ROOT / "data" / "dispatcher.sqlite3"),
    )
)


def main() -> None:
    config = load_config(CONFIG_FILE)
    store = Store(DATABASE_FILE)
    provider = ProviderClient()

    server = None
    webhook_thread = None

    try:
        inserted = store.seed_leads(FIXTURE_DIR, config["campaigns"])
        print(f"Campaigns loaded: {len(config['campaigns'])}")
        print(f"New leads inserted: {inserted}")

        simulated_time = provider.get_simulated_time()
        print(f"Provider simulated time: {simulated_time.isoformat()}")

        dispatcher = Dispatcher(config, store, provider)
        webhook_app = create_webhook_app(config, store, dispatcher)

        server = uvicorn.Server(
            uvicorn.Config(
                webhook_app,
                host="127.0.0.1",
                port=8200,
                log_level="info",
            )
        )
        webhook_thread = threading.Thread(target=server.run, daemon=True)
        webhook_thread.start()

        startup_deadline = time.monotonic() + 10
        while (
            not server.started
            and webhook_thread.is_alive()
            and time.monotonic() < startup_deadline
        ):
            time.sleep(0.05)

        if not server.started:
            raise RuntimeError("Webhook server failed to start on port 8200")

        print("Webhook server listening on http://127.0.0.1:8200")
        dispatcher.run()

        output_file = PROJECT_ROOT / "final_state.csv"
        write_final_state(store, output_file)
        print(f"Final results written to {output_file}")

    finally:
        if server is not None:
            server.should_exit = True
        if webhook_thread is not None:
            webhook_thread.join(timeout=5)
        provider.close()
        store.close()


if __name__ == "__main__":
    main()
