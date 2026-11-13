from pathlib import Path

from solution.store import Store


ROOT = Path(__file__).parents[1]
FIXTURES = ROOT / "assignment-b-workflow" / "fixtures"

CAMPAIGNS = [
    {
        "campaign_id": "c_acme_feedback",
        "tenant_id": "acme_insurance",
        "leads_file": "leads_c_acme_feedback.csv",
    }
]


def test_seed_is_idempotent_and_attempt_counts_only_after_acceptance():
    store = Store(":memory:")
    try:
        assert store.seed_leads(FIXTURES, CAMPAIGNS) == 60
        assert store.seed_leads(FIXTURES, CAMPAIGNS) == 0

        lead_id = store.connection.execute(
            "SELECT lead_id FROM leads ORDER BY lead_id LIMIT 1"
        ).fetchone()[0]
        first = store.reserve_call("c_acme_feedback", lead_id)
        same_reservation = store.reserve_call("c_acme_feedback", lead_id)
        assert first["request_id"] == same_reservation["request_id"]
        assert first["attempt_no"] == 1
        assert store.connection.execute(
            "SELECT attempts FROM leads WHERE campaign_id=? AND lead_id=?",
            ("c_acme_feedback", lead_id),
        ).fetchone()[0] == 0

        store.mark_accepted(first["request_id"], "CA-test")
        assert store.connection.execute(
            "SELECT attempts FROM leads WHERE campaign_id=? AND lead_id=?",
            ("c_acme_feedback", lead_id),
        ).fetchone()[0] == 1
        second = store.reserve_call("c_acme_feedback", lead_id)
        assert second["attempt_no"] == 2
    finally:
        store.close()
