import unittest
from decimal import Decimal
from unittest.mock import MagicMock, patch

from app.events import Event, PermanentError, contract
from app.wallet_events import handle_wallet_event


def validated_order_event(
    *,
    order_id: int = 42,
    user_id: int = 1,
    total_price: float | str = 10.0,
) -> Event:
    return Event.new(
        contract.ASSET_VALIDATED,
        correlation_id=order_id,
        payload={
            "order_id": order_id,
            "user_id": user_id,
            "asset_id": 1,
            "quantity": 1,
            "total_price": total_price,
        },
    )


class HandleWalletEventTests(unittest.TestCase):
    def setUp(self):
        self.connection = MagicMock()
        self.connection.__enter__.return_value = self.connection
        self.cursor = MagicMock()
        self.connection.cursor.return_value.__enter__.return_value = self.cursor

    @patch("app.wallet_events.save_to_outbox")
    @patch("app.wallet_events.mark_processed", return_value=True)
    @patch("app.wallet_events.get_connection")
    def test_debit_is_recorded_once_under_order_id(
        self, get_connection, mark_processed, save_to_outbox
    ):
        get_connection.return_value = self.connection
        self.cursor.fetchone.side_effect = [(Decimal("100.00"),), None]
        event = validated_order_event()

        handle_wallet_event(event)

        mark_processed.assert_called_once_with(self.cursor, "wallet", event.event_id)
        statements = [call.args[0] for call in self.cursor.execute.call_args_list]
        self.assertTrue(any("UPDATE wallets" in statement for statement in statements))
        insert_call = next(
            call
            for call in self.cursor.execute.call_args_list
            if "INSERT INTO wallet_transactions" in call.args[0]
        )
        self.assertEqual(
            ("order:42", 1, Decimal("-10.00"), Decimal("100.00"), Decimal("90.00")),
            insert_call.args[1],
        )
        result = save_to_outbox.call_args.args[1]
        self.assertEqual(contract.FUNDS_DEBITED, result.event_type)

    @patch("app.wallet_events.save_to_outbox")
    @patch("app.wallet_events.mark_processed", return_value=True)
    @patch("app.wallet_events.get_connection")
    def test_existing_order_transaction_is_not_debited_again(
        self, get_connection, mark_processed, save_to_outbox
    ):
        get_connection.return_value = self.connection
        self.cursor.fetchone.side_effect = [
            (Decimal("90.00"),),
            (1, Decimal("-10.00"), Decimal("100.00"), Decimal("90.00")),
        ]

        handle_wallet_event(validated_order_event())

        statements = [call.args[0] for call in self.cursor.execute.call_args_list]
        self.assertFalse(any("UPDATE wallets" in statement for statement in statements))
        self.assertFalse(
            any("INSERT INTO wallet_transactions" in statement for statement in statements)
        )
        save_to_outbox.assert_not_called()

    @patch("app.wallet_events.save_to_outbox")
    @patch("app.wallet_events.mark_processed", return_value=True)
    @patch("app.wallet_events.get_connection")
    def test_insufficient_funds_publishes_rejection_without_debit(
        self, get_connection, mark_processed, save_to_outbox
    ):
        get_connection.return_value = self.connection
        self.cursor.fetchone.side_effect = [(Decimal("5.00"),), None]

        handle_wallet_event(validated_order_event())

        statements = [call.args[0] for call in self.cursor.execute.call_args_list]
        self.assertFalse(any("UPDATE wallets" in statement for statement in statements))
        self.assertFalse(
            any("INSERT INTO wallet_transactions" in statement for statement in statements)
        )
        result = save_to_outbox.call_args.args[1]
        self.assertEqual(contract.FUNDS_REJECTED, result.event_type)

    @patch("app.wallet_events.get_connection")
    def test_invalid_order_amount_goes_to_permanent_error(self, get_connection):
        event = validated_order_event(total_price=float("nan"))

        with self.assertRaises(PermanentError):
            handle_wallet_event(event)

        get_connection.assert_not_called()

    @patch("app.wallet_events.get_connection")
    def test_unrepresentable_amount_goes_to_permanent_error(self, get_connection):
        event = validated_order_event(total_price="1e1000")

        with self.assertRaises(PermanentError):
            handle_wallet_event(event)

        get_connection.assert_not_called()


if __name__ == "__main__":
    unittest.main()
