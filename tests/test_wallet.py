"""Comprehensive automated test suite for Digital Payment Wallet.

Covers functional requirements (FR-01 to FR-04) and non-functional requirements (NFR-01, NFR-02).
Fully compatible with pytest and python -m unittest.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from decimal import Decimal
import unittest

from src.wallet import (
    AccountNotFoundError,
    AccountSuspendedError,
    IdempotencyConflictError,
    InsufficientFundsError,
    InvalidAmountError,
    InvalidMerchantError,
    SelfTransferError,
    TransactionRecord,
    TransactionStatus,
    TransactionType,
    WalletAccount,
    WalletError,
    WalletService,
)


class TestWalletAccount(unittest.TestCase):
    """Unit tests for WalletAccount model."""

    def test_account_creation_valid(self):
        account = WalletAccount("ACC-001", "Alice", initial_balance=Decimal("150.50"))
        self.assertEqual(account.account_id, "ACC-001")
        self.assertEqual(account.holder_name, "Alice")
        self.assertEqual(account.balance, Decimal("150.50"))
        self.assertTrue(account.is_active)
        self.assertFalse(account.is_merchant)

    def test_account_creation_default_zero_balance(self):
        account = WalletAccount("ACC-002", "Bob")
        self.assertEqual(account.balance, Decimal("0.00"))

    def test_account_creation_invalid_names(self):
        with self.assertRaises(WalletError):
            WalletAccount("", "Bob")
        with self.assertRaises(WalletError):
            WalletAccount("ACC-003", "")

    def test_account_creation_invalid_negative_balance(self):
        with self.assertRaises(InvalidAmountError):
            WalletAccount("ACC-004", "Charlie", initial_balance=Decimal("-10.00"))

    def test_credit_and_debit_operations(self):
        account = WalletAccount("ACC-005", "David", initial_balance=Decimal("100.00"))
        new_balance = account.credit(Decimal("50.25"))
        self.assertEqual(new_balance, Decimal("150.25"))
        self.assertEqual(account.balance, Decimal("150.25"))

        new_balance = account.debit(Decimal("30.00"))
        self.assertEqual(new_balance, Decimal("120.25"))
        self.assertEqual(account.balance, Decimal("120.25"))

    def test_debit_insufficient_funds(self):
        account = WalletAccount("ACC-006", "Eve", initial_balance=Decimal("50.00"))
        with self.assertRaises(InsufficientFundsError):
            account.debit(Decimal("50.01"))
        self.assertEqual(account.balance, Decimal("50.00"))

    def test_suspended_account_blocks_debit_and_credit(self):
        account = WalletAccount("ACC-007", "Frank", initial_balance=Decimal("100.00"))
        account.suspend()
        self.assertFalse(account.is_active)

        with self.assertRaises(AccountSuspendedError):
            account.credit(Decimal("20.00"))

        with self.assertRaises(AccountSuspendedError):
            account.debit(Decimal("20.00"))

        account.activate()
        self.assertTrue(account.is_active)
        account.credit(Decimal("20.00"))
        self.assertEqual(account.balance, Decimal("120.00"))


class TestWalletTopUp(unittest.TestCase):
    """Tests for FR-02: Wallet Top-Up."""

    def setUp(self):
        self.service = WalletService()
        self.account = self.service.create_account("USER-1", "Alice", initial_balance=Decimal("50.00"))

    def test_top_up_successful_with_various_types(self):
        # Decimal
        tx1 = self.service.top_up("USER-1", Decimal("25.50"))
        self.assertEqual(tx1.status, TransactionStatus.SUCCESS)
        self.assertEqual(tx1.amount, Decimal("25.50"))
        self.assertEqual(self.service.get_balance("USER-1"), Decimal("75.50"))

        # Integer
        tx2 = self.service.top_up("USER-1", 10)
        self.assertEqual(tx2.amount, Decimal("10.00"))
        self.assertEqual(self.service.get_balance("USER-1"), Decimal("85.50"))

        # String
        tx3 = self.service.top_up("USER-1", "14.50")
        self.assertEqual(tx3.amount, Decimal("14.50"))
        self.assertEqual(self.service.get_balance("USER-1"), Decimal("100.00"))

        # Float
        tx4 = self.service.top_up("USER-1", 20.0)
        self.assertEqual(tx4.amount, Decimal("20.00"))
        self.assertEqual(self.service.get_balance("USER-1"), Decimal("120.00"))

    def test_top_up_invalid_negative_or_zero_amount(self):
        with self.assertRaises(InvalidAmountError):
            self.service.top_up("USER-1", Decimal("0.00"))
        with self.assertRaises(InvalidAmountError):
            self.service.top_up("USER-1", Decimal("-15.00"))
        with self.assertRaises(InvalidAmountError):
            self.service.top_up("USER-1", "invalid_amount")

    def test_top_up_nonexistent_account(self):
        with self.assertRaises(AccountNotFoundError):
            self.service.top_up("NON_EXISTENT", Decimal("50.00"))

    def test_top_up_suspended_account(self):
        self.service.suspend_account("USER-1")
        with self.assertRaises(AccountSuspendedError):
            self.service.top_up("USER-1", Decimal("20.00"))

    def test_top_up_idempotency_retry(self):
        tx1 = self.service.top_up("USER-1", Decimal("30.00"), idempotency_key="topup-key-1")
        self.assertEqual(self.service.get_balance("USER-1"), Decimal("80.00"))

        # Retry with identical key and amount
        tx2 = self.service.top_up("USER-1", Decimal("30.00"), idempotency_key="topup-key-1")
        self.assertEqual(tx1.transaction_id, tx2.transaction_id)
        # Balance must NOT be credited a second time
        self.assertEqual(self.service.get_balance("USER-1"), Decimal("80.00"))


class TestWalletTransfer(unittest.TestCase):
    """Tests for FR-01: Wallet Transfer."""

    def setUp(self):
        self.service = WalletService()
        self.alice = self.service.create_account("ALICE", "Alice", initial_balance=Decimal("200.00"))
        self.bob = self.service.create_account("BOB", "Bob", initial_balance=Decimal("50.00"))

    def test_transfer_successful(self):
        tx = self.service.transfer("ALICE", "BOB", Decimal("60.00"), idempotency_key="xfer-001")
        self.assertEqual(tx.status, TransactionStatus.SUCCESS)
        self.assertEqual(tx.amount, Decimal("60.00"))
        self.assertEqual(tx.sender_id, "ALICE")
        self.assertEqual(tx.receiver_id, "BOB")

        self.assertEqual(self.service.get_balance("ALICE"), Decimal("140.00"))
        self.assertEqual(self.service.get_balance("BOB"), Decimal("110.00"))

    def test_transfer_insufficient_funds(self):
        with self.assertRaises(InsufficientFundsError):
            self.service.transfer("ALICE", "BOB", Decimal("300.00"), idempotency_key="xfer-002")

        # Verify balances remain untouched
        self.assertEqual(self.service.get_balance("ALICE"), Decimal("200.00"))
        self.assertEqual(self.service.get_balance("BOB"), Decimal("50.00"))

    def test_transfer_self_transfer_rejected(self):
        with self.assertRaises(SelfTransferError):
            self.service.transfer("ALICE", "ALICE", Decimal("10.00"), idempotency_key="xfer-003")

    def test_transfer_invalid_amounts(self):
        with self.assertRaises(InvalidAmountError):
            self.service.transfer("ALICE", "BOB", Decimal("0.00"), idempotency_key="xfer-004")
        with self.assertRaises(InvalidAmountError):
            self.service.transfer("ALICE", "BOB", Decimal("-50.00"), idempotency_key="xfer-005")

    def test_transfer_nonexistent_parties(self):
        with self.assertRaises(AccountNotFoundError):
            self.service.transfer("GHOST", "BOB", Decimal("20.00"), idempotency_key="xfer-006")
        with self.assertRaises(AccountNotFoundError):
            self.service.transfer("ALICE", "GHOST", Decimal("20.00"), idempotency_key="xfer-007")

    def test_transfer_requires_idempotency_key(self):
        with self.assertRaises(WalletError):
            self.service.transfer("ALICE", "BOB", Decimal("10.00"), idempotency_key="")
        with self.assertRaises(WalletError):
            self.service.transfer("ALICE", "BOB", Decimal("10.00"), idempotency_key="   ")

    def test_transfer_with_suspended_parties(self):
        self.service.suspend_account("ALICE")
        with self.assertRaises(AccountSuspendedError):
            self.service.transfer("ALICE", "BOB", Decimal("20.00"), idempotency_key="xfer-008")

        self.service.activate_account("ALICE")
        self.service.suspend_account("BOB")
        with self.assertRaises(AccountSuspendedError):
            self.service.transfer("ALICE", "BOB", Decimal("20.00"), idempotency_key="xfer-009")


class TestMerchantPayment(unittest.TestCase):
    """Tests for FR-03: Merchant Payment."""

    def setUp(self):
        self.service = WalletService()
        self.alice = self.service.create_account("ALICE", "Alice", initial_balance=Decimal("300.00"))
        self.merchant = self.service.create_account(
            "MERCHANT-STORE", "Store Inc", initial_balance=Decimal("0.00"), is_merchant=True
        )
        self.regular_user = self.service.create_account(
            "USER-REGULAR", "Regular User", initial_balance=Decimal("100.00"), is_merchant=False
        )

    def test_merchant_payment_successful(self):
        tx = self.service.pay_merchant(
            sender_id="ALICE",
            merchant_id="MERCHANT-STORE",
            amount=Decimal("79.99"),
            idempotency_key="order-101",
            order_id="ORD-12345",
        )
        self.assertEqual(tx.status, TransactionStatus.SUCCESS)
        self.assertEqual(tx.amount, Decimal("79.99"))
        self.assertEqual(tx.metadata.get("order_id"), "ORD-12345")
        self.assertEqual(self.service.get_balance("ALICE"), Decimal("220.01"))
        self.assertEqual(self.service.get_balance("MERCHANT-STORE"), Decimal("79.99"))

    def test_merchant_payment_to_non_merchant_rejected(self):
        with self.assertRaises(InvalidMerchantError):
            self.service.pay_merchant(
                sender_id="ALICE",
                merchant_id="USER-REGULAR",
                amount=Decimal("20.00"),
                idempotency_key="order-102",
            )

    def test_merchant_payment_insufficient_funds(self):
        with self.assertRaises(InsufficientFundsError):
            self.service.pay_merchant(
                sender_id="ALICE",
                merchant_id="MERCHANT-STORE",
                amount=Decimal("500.00"),
                idempotency_key="order-103",
            )
        self.assertEqual(self.service.get_balance("ALICE"), Decimal("300.00"))
        self.assertEqual(self.service.get_balance("MERCHANT-STORE"), Decimal("0.00"))

    def test_merchant_payment_self_payment_rejected(self):
        with self.assertRaises(SelfTransferError):
            self.service.pay_merchant(
                sender_id="MERCHANT-STORE",
                merchant_id="MERCHANT-STORE",
                amount=Decimal("10.00"),
            )


class TestIdempotency(unittest.TestCase):
    """Tests for FR-04: Idempotency Guarantees."""

    def setUp(self):
        self.service = WalletService()
        self.service.create_account("ALICE", "Alice", initial_balance=Decimal("500.00"))
        self.service.create_account("BOB", "Bob", initial_balance=Decimal("100.00"))

    def test_repeated_transfer_returns_same_transaction_and_no_double_debit(self):
        idempotency_key = "idemp-xfer-100"
        tx1 = self.service.transfer("ALICE", "BOB", Decimal("75.00"), idempotency_key=idempotency_key)

        self.assertEqual(self.service.get_balance("ALICE"), Decimal("425.00"))
        self.assertEqual(self.service.get_balance("BOB"), Decimal("175.00"))

        # Re-send exact same request 3 times (simulating client retry / network timeout)
        for _ in range(3):
            tx_retry = self.service.transfer("ALICE", "BOB", Decimal("75.00"), idempotency_key=idempotency_key)
            self.assertEqual(tx1.transaction_id, tx_retry.transaction_id)
            self.assertEqual(tx1.timestamp, tx_retry.timestamp)

        # Confirm balances debited and credited ONLY once
        self.assertEqual(self.service.get_balance("ALICE"), Decimal("425.00"))
        self.assertEqual(self.service.get_balance("BOB"), Decimal("175.00"))

        # Confirm single transaction in ledger
        alice_history = self.service.get_transaction_history("ALICE")
        self.assertEqual(len(alice_history), 1)

    def test_idempotency_key_reused_with_different_amount_raises_conflict(self):
        idempotency_key = "conflict-key-1"
        self.service.transfer("ALICE", "BOB", Decimal("50.00"), idempotency_key=idempotency_key)

        with self.assertRaises(IdempotencyConflictError):
            # Attempt to use same idempotency key with different amount
            self.service.transfer("ALICE", "BOB", Decimal("75.00"), idempotency_key=idempotency_key)

    def test_idempotency_key_reused_with_different_recipient_raises_conflict(self):
        idempotency_key = "conflict-key-2"
        self.service.create_account("CHARLIE", "Charlie", initial_balance=Decimal("0.00"))
        self.service.transfer("ALICE", "BOB", Decimal("40.00"), idempotency_key=idempotency_key)

        with self.assertRaises(IdempotencyConflictError):
            # Same key with different receiver
            self.service.transfer("ALICE", "CHARLIE", Decimal("40.00"), idempotency_key=idempotency_key)

    def test_concurrent_idempotent_transfers_execute_exactly_once(self):
        """Simulate concurrent threads submitting the exact same idempotent transfer."""
        idempotency_key = "concurrent-idemp-test"
        transfer_amount = Decimal("100.00")
        num_threads = 10

        results = []
        with ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = [
                executor.submit(
                    self.service.transfer,
                    "ALICE",
                    "BOB",
                    transfer_amount,
                    idempotency_key,
                )
                for _ in range(num_threads)
            ]
            for future in as_completed(futures):
                results.append(future.result())

        # All threads must receive the same transaction record
        tx_ids = {r.transaction_id for r in results}
        self.assertEqual(len(tx_ids), 1)

        # Balance must reflect only ONE debit and credit
        self.assertEqual(self.service.get_balance("ALICE"), Decimal("400.00"))
        self.assertEqual(self.service.get_balance("BOB"), Decimal("200.00"))

        # Ledger must contain exactly one transaction
        ledger = self.service.get_transaction_history()
        self.assertEqual(len(ledger), 1)


class TestReliabilityAndConcurrency(unittest.TestCase):
    """Tests for NFR-01: Reliability, Thread-safety, and Precision."""

    def test_deadlock_free_bidirectional_transfers(self):
        """Verify that concurrent cross-transfers (A->B and B->A) do not deadlock."""
        service = WalletService()
        service.create_account("USER_A", "User A", initial_balance=Decimal("1000.00"))
        service.create_account("USER_B", "User B", initial_balance=Decimal("1000.00"))

        iterations = 50
        errors = []

        def transfer_a_to_b(idx):
            try:
                service.transfer("USER_A", "USER_B", Decimal("5.00"), idempotency_key=f"a2b-{idx}")
            except Exception as e:
                errors.append(e)

        def transfer_b_to_a(idx):
            try:
                service.transfer("USER_B", "USER_A", Decimal("5.00"), idempotency_key=f"b2a-{idx}")
            except Exception as e:
                errors.append(e)

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = []
            for i in range(iterations):
                futures.append(executor.submit(transfer_a_to_b, i))
                futures.append(executor.submit(transfer_b_to_a, i))
            for f in as_completed(futures):
                f.result()

        self.assertEqual(len(errors), 0)
        # Net sum of balances across accounts must remain exactly 2000.00
        balance_a = service.get_balance("USER_A")
        balance_b = service.get_balance("USER_B")
        self.assertEqual(balance_a + balance_b, Decimal("2000.00"))

    def test_concurrent_transfers_and_topups_conserve_funds(self):
        """High-concurrency test: multiple threads transferring and topping up concurrently."""
        service = WalletService()
        num_accounts = 5
        initial_per_account = Decimal("500.00")
        for i in range(num_accounts):
            service.create_account(f"ACC_{i}", f"Holder {i}", initial_balance=initial_per_account)

        total_system_initial = initial_per_account * num_accounts

        def do_transfer(worker_id):
            sender = f"ACC_{worker_id % num_accounts}"
            receiver = f"ACC_{(worker_id + 1) % num_accounts}"
            service.transfer(sender, receiver, Decimal("10.00"), idempotency_key=f"worker-{worker_id}")

        with ThreadPoolExecutor(max_workers=15) as executor:
            futures = [executor.submit(do_transfer, i) for i in range(60)]
            for f in as_completed(futures):
                f.result()

        # Money conservation check: total funds in system must remain constant
        total_after = sum(service.get_balance(f"ACC_{i}") for i in range(num_accounts))
        self.assertEqual(total_after, total_system_initial)

    def test_decimal_precision_avoids_floating_point_drift(self):
        """Ensure repeated fractional cent operations do not produce IEEE-754 errors."""
        service = WalletService()
        service.create_account("PRECISION_ACC", "Precision Tester", initial_balance=Decimal("0.00"))

        # Top up 0.10 ten times
        for i in range(10):
            service.top_up("PRECISION_ACC", "0.10", idempotency_key=f"prec-{i}")

        self.assertEqual(service.get_balance("PRECISION_ACC"), Decimal("1.00"))


class TestSecurityAndAuditing(unittest.TestCase):
    """Tests for NFR-02: Security, Status Integrity, and Audit Trails."""

    def setUp(self):
        self.service = WalletService()
        self.service.create_account("ACC-AUDIT-1", "User 1", initial_balance=Decimal("500.00"))
        self.service.create_account("ACC-AUDIT-2", "User 2", initial_balance=Decimal("100.00"))
        self.service.create_account("MERCHANT-1", "Store", is_merchant=True)

    def test_audit_ledger_records_all_operations(self):
        self.service.top_up("ACC-AUDIT-1", Decimal("50.00"))
        self.service.transfer("ACC-AUDIT-1", "ACC-AUDIT-2", Decimal("100.00"), idempotency_key="audit-xfer")
        self.service.pay_merchant("ACC-AUDIT-2", "MERCHANT-1", Decimal("25.00"), order_id="ORD-999")

        # Global ledger
        all_txs = self.service.get_transaction_history()
        self.assertEqual(len(all_txs), 3)

        # Account-specific ledger
        acc1_txs = self.service.get_transaction_history("ACC-AUDIT-1")
        self.assertEqual(len(acc1_txs), 2)

        merchant_txs = self.service.get_transaction_history("MERCHANT-1")
        self.assertEqual(len(merchant_txs), 1)
        self.assertEqual(merchant_txs[0].metadata.get("order_id"), "ORD-999")

    def test_lookup_transaction_by_id_and_idempotency_key(self):
        tx = self.service.transfer(
            "ACC-AUDIT-1", "ACC-AUDIT-2", Decimal("30.00"), idempotency_key="lookup-key"
        )
        found_by_id = self.service.get_transaction_by_id(tx.transaction_id)
        self.assertIsNotNone(found_by_id)
        self.assertEqual(found_by_id.transaction_id, tx.transaction_id)

        found_by_key = self.service.get_transaction_by_idempotency_key("lookup-key")
        self.assertIsNotNone(found_by_key)
        self.assertEqual(found_by_key.idempotency_key, "lookup-key")


if __name__ == "__main__":
    unittest.main()
