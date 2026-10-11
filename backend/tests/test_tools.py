from app import tools
from tests.conftest import NOW


def test_order_status_requires_both_identifiers(db):
    r = tools.get_order_status(db, "OP-1003", None, now=NOW)
    assert not r["ok"] and r["error"] == "missing_details"


def test_order_status_verified(db):
    r = tools.get_order_status(db, "op1003", "Sofia.Rossi@example.com", now=NOW)
    assert r["ok"] and r["status"] == "shipped" and r["tracking_number"]


def test_wrong_email_and_unknown_order_look_identical(db):
    wrong = tools.get_order_status(db, "OP-1003", "amina.khan@example.com", now=NOW)
    missing = tools.get_order_status(db, "OP-9999", "amina.khan@example.com", now=NOW)
    assert wrong == missing
    assert wrong["error"] == "verification_failed"
    assert "Alpine" not in str(wrong)


def test_delivered_order_shows_return_window(db):
    r = tools.get_order_status(db, "OP-1001", "amina.khan@example.com", now=NOW)
    assert r["return_window_open"] is True


def test_return_approved_within_window(db):
    r = tools.create_return_request(db, "OP-1001", "amina.khan@example.com", "Too small", now=NOW)
    assert r["ok"] and r["rma_code"].startswith("RMA-1001")
    assert r["return_fee_krw"] == 5000


def test_defective_return_is_free(db):
    r = tools.create_return_request(db, "OP-1007", "hana.kim@example.com", "Zip is broken", now=NOW)
    assert r["ok"] and r["return_fee_krw"] == 0


def test_return_window_edges(db):
    assert tools.create_return_request(db, "OP-1009", "elena.novak@example.com", "Too big", now=NOW)["ok"]
    late = tools.create_return_request(db, "OP-1010", "kwame.mensah@example.com", "Too big", now=NOW)
    assert not late["ok"] and late["error"] == "window_closed"


def test_return_rejections(db):
    assert tools.create_return_request(db, "OP-1003", "sofia.rossi@example.com", "x", now=NOW)["error"] == "not_delivered"
    assert tools.create_return_request(db, "OP-1004", "lucas.martin@example.com", "x", now=NOW)["error"] == "already_returned"
    assert tools.create_return_request(db, "OP-1001", "daniel.park@example.com", "x", now=NOW)["error"] == "verification_failed"


def test_second_return_is_blocked(db):
    assert tools.create_return_request(db, "OP-1001", "amina.khan@example.com", "Too small", now=NOW)["ok"]
    again = tools.create_return_request(db, "OP-1001", "amina.khan@example.com", "Too small", now=NOW)
    assert again["error"] == "already_returned"


def test_inventory_states(db):
    out = tools.check_inventory(db, "Summit Down Jacket")["products"][0]
    assert out["availability"] == "out_of_stock" and out["restock_date"]
    low = tools.check_inventory(db, "ridge daypack")["products"][0]
    assert low["availability"] == "low_stock" and low["stock"] == 3
    by_sku = tools.check_inventory(db, "is NB-TNT-01 available")["products"][0]
    assert by_sku["name"] == "Alpine 2-Person Tent"
    assert tools.check_inventory(db, "kayak")["error"] == "not_found"


def test_escalation_creates_ticket(db):
    r = tools.escalate_to_human(db, "sess", "human_requested", "wants a person", "urgent")
    assert r["ok"] and r["ticket_id"].startswith("T-") and r["priority"] == "normal"
