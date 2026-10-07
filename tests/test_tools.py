import pytest

from agent import tools


def test_lookup_order_found(test_db):
    order = tools.lookup_order(1)
    assert order["product"] == "Wireless Headphones"
    assert order["amount"] == 40.00
    assert order["delivery_status"] == "delivered"


def test_lookup_order_not_found(test_db):
    assert "error" in tools.lookup_order(999)


def test_run_tool_accepts_number_as_string(test_db):
    # Models sometimes send "1" instead of 1; pydantic converts it
    assert tools.run_tool("lookup_order", '{"order_id": "1"}')["order_id"] == 1


@pytest.mark.parametrize("raw", ['{"order_id": "abc"}', "{}", '{"order_id": -1}', "not json"])
def test_run_tool_rejects_bad_arguments(test_db, raw):
    with pytest.raises(tools.InvalidToolCall):
        tools.run_tool("lookup_order", raw)


def test_run_tool_rejects_unknown_tool(test_db):
    with pytest.raises(tools.InvalidToolCall):
        tools.run_tool("delete_everything", "{}")
