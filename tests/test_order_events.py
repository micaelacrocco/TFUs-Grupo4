import unittest
from unittest.mock import patch

from app.events import Event, PermanentError, contract
from app.order_events import handle_order_event


class HandleOrderEventTests(unittest.TestCase):
    @patch("app.order_events.apply_order_result")
    def test_funds_debited_completes_order(self, apply_order_result):
        event = Event.new(
            contract.FUNDS_DEBITED,
            correlation_id=42,
            payload={
                "order_id": 42,
                "user_id": 1,
                "amount": 10,
                "new_balance": 90,
            },
        )

        handle_order_event(event)

        apply_order_result.assert_called_once_with(42, "COMPLETED", None, event.event_id)

    @patch("app.order_events.apply_order_result")
    def test_rejections_fail_order_with_reason(self, apply_order_result):
        for event_type in (contract.ORDER_REJECTED, contract.FUNDS_REJECTED):
            with self.subTest(event_type=event_type):
                event = Event.new(
                    event_type,
                    correlation_id=42,
                    payload={"order_id": 42, "user_id": 1, "reason": "Rejected"},
                )

                handle_order_event(event)

                apply_order_result.assert_called_with(
                    42, "FAILED", "Rejected", event.event_id
                )
                apply_order_result.reset_mock()

    @patch("app.order_events.apply_order_result")
    def test_invalid_result_is_permanent_error(self, apply_order_result):
        event = Event(
            event_type=contract.FUNDS_DEBITED,
            correlation_id="42",
            payload={
                "order_id": True,
                "user_id": 1,
                "amount": 10,
                "new_balance": 90,
            },
        )

        with self.assertRaises(PermanentError):
            handle_order_event(event)

        apply_order_result.assert_not_called()


if __name__ == "__main__":
    unittest.main()
