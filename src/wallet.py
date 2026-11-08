"""Core implementation of the Digital Payment Wallet system.

Supports top-up, wallet transfer, merchant payments, and idempotency guarantees
with thread-safe balance updates and robust transaction auditing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from enum import Enum
import threading
from typing import Any, Dict, List, Optional, Tuple, Union
import uuid


# ==============================================================================
# Domain Enums & Exceptions
# ==============================================================================

class TransactionType(str, Enum):
    """Supported transaction types in the digital payment wallet."""
    TOP_UP = "TOP_UP"
    TRANSFER = "TRANSFER"
    MERCHANT_PAYMENT = "MERCHANT_PAYMENT"


class TransactionStatus(str, Enum):
    """Transaction execution status."""
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


class WalletError(Exception):
    """Base exception for all wallet-related domain errors."""
    pass


class InsufficientFundsError(WalletError):
    """Raised when an account balance is insufficient for a debit operation."""
    pass


class InvalidAmountError(WalletError):
    """Raised when an operation is attempted with an invalid monetary amount."""
    pass


class AccountNotFoundError(WalletError):
    """Raised when a specified account does not exist."""
    pass


class AccountSuspendedError(WalletError):
    """Raised when an operation is attempted on an inactive or suspended account."""
    pass


class SelfTransferError(WalletError):
    """Raised when a transfer is attempted to the same account."""
    pass


class IdempotencyConflictError(WalletError):
    """Raised when an idempotency key is reused with mismatched parameters."""
    pass


class InvalidMerchantError(WalletError):
    """Raised when a merchant payment targets an account not registered as a merchant."""
    pass


# ==============================================================================
# Domain Models
# ==============================================================================

def _normalize_amount(amount: Union[Decimal, float, int, str]) -> Decimal:
    """Validate and normalize monetary amounts to Decimal with 2 decimal places."""
    try:
        if isinstance(amount, float):
            # Convert float via string representation to avoid precision anomalies
            dec = Decimal(str(amount))
        else:
            dec = Decimal(amount)
    except (InvalidOperation, TypeError, ValueError) as err:
        raise InvalidAmountError(f"Invalid monetary amount format: {amount}") from err

    if dec.is_nan() or dec.is_infinite():
        raise InvalidAmountError(f"Amount must be a finite number: {amount}")

    quantized = dec.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if quantized <= Decimal("0.00"):
        raise InvalidAmountError(f"Amount must be strictly positive: {quantized}")

    return quantized


@dataclass(frozen=True)
class TransactionRecord:
    """Immutable audit record representing a transaction executed in the wallet."""
    transaction_id: str
    transaction_type: TransactionType
    sender_id: Optional[str]
    receiver_id: Optional[str]
    amount: Decimal
    status: TransactionStatus
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    idempotency_key: Optional[str] = None
    message: str = "Operation completed successfully"
    metadata: Dict[str, Any] = field(default_factory=dict)


class WalletAccount:
    """Represents an individual user or merchant wallet account."""

    def __init__(
        self,
        account_id: str,
        holder_name: str,
        initial_balance: Union[Decimal, float, int, str] = Decimal("0.00"),
        is_merchant: bool = False,
    ) -> None:
        if not account_id or not account_id.strip():
            raise WalletError("Account ID must be a non-empty string.")
        if not holder_name or not holder_name.strip():
            raise WalletError("Holder name must be a non-empty string.")

        self.account_id: str = account_id.strip()
        self.holder_name: str = holder_name.strip()
        self.is_merchant: bool = is_merchant
        self.is_active: bool = True
        self.created_at: datetime = datetime.now(timezone.utc)
        self.lock: threading.RLock = threading.RLock()

        # Handle initial balance
        if initial_balance == 0 or initial_balance == Decimal("0.00"):
            self._balance: Decimal = Decimal("0.00")
        else:
            self._balance = _normalize_amount(initial_balance)

    @property
    def balance(self) -> Decimal:
        """Thread-safe balance inspection."""
        with self.lock:
            return self._balance

    def credit(self, amount: Decimal) -> Decimal:
        """Credit funds to this wallet."""
        with self.lock:
            if not self.is_active:
                raise AccountSuspendedError(f"Account '{self.account_id}' is suspended/inactive.")
            self._balance = (self._balance + amount).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            return self._balance

    def debit(self, amount: Decimal) -> Decimal:
        """Debit funds from this wallet."""
        with self.lock:
            if not self.is_active:
                raise AccountSuspendedError(f"Account '{self.account_id}' is suspended/inactive.")
            if self._balance < amount:
                raise InsufficientFundsError(
                    f"Insufficient funds in account '{self.account_id}'. "
                    f"Available: {self._balance}, Requested: {amount}."
                )
            self._balance = (self._balance - amount).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            return self._balance

    def suspend(self) -> None:
        """Suspend the account, blocking subsequent debit and credit operations."""
        with self.lock:
            self.is_active = False

    def activate(self) -> None:
        """Activate the account."""
        with self.lock:
            self.is_active = True

    def __repr__(self) -> str:
        return (
            f"WalletAccount(id='{self.account_id}', holder='{self.holder_name}', "
            f"balance={self._balance}, is_merchant={self.is_merchant}, active={self.is_active})"
        )


# ==============================================================================
# Service & Idempotency Management
# ==============================================================================

class WalletService:
    """Core service orchestrating wallet operations, transfers, merchant payments,

    and idempotency management.
    """

    def __init__(self) -> None:
        self._accounts: Dict[str, WalletAccount] = {}
        self._accounts_lock = threading.RLock()

        # Idempotency store: key -> (signature, TransactionRecord)
        # signature: (TransactionType, sender_id, receiver_id, amount)
        self._idempotency_store: Dict[str, Tuple[Tuple[Any, ...], TransactionRecord]] = {}
        self._idempotency_lock = threading.RLock()

        # Audit ledger of all transactions
        self._transactions: List[TransactionRecord] = []
        self._ledger_lock = threading.RLock()

    # --------------------------------------------------------------------------
    # Account Lifecycle
    # --------------------------------------------------------------------------

    def create_account(
        self,
        account_id: str,
        holder_name: str,
        initial_balance: Union[Decimal, float, int, str] = Decimal("0.00"),
        is_merchant: bool = False,
    ) -> WalletAccount:
        """Create and register a new wallet account."""
        with self._accounts_lock:
            if account_id in self._accounts:
                raise WalletError(f"Account with ID '{account_id}' already exists.")
            account = WalletAccount(
                account_id=account_id,
                holder_name=holder_name,
                initial_balance=initial_balance,
                is_merchant=is_merchant,
            )
            self._accounts[account_id] = account
            return account

    def get_account(self, account_id: str) -> WalletAccount:
        """Retrieve an account by ID, raising AccountNotFoundError if missing."""
        with self._accounts_lock:
            account = self._accounts.get(account_id)
            if not account:
                raise AccountNotFoundError(f"Account '{account_id}' not found.")
            return account

    def get_balance(self, account_id: str) -> Decimal:
        """Get the current balance of an account."""
        account = self.get_account(account_id)
        return account.balance

    def suspend_account(self, account_id: str) -> None:
        """Suspend an account."""
        account = self.get_account(account_id)
        account.suspend()

    def activate_account(self, account_id: str) -> None:
        """Activate an account."""
        account = self.get_account(account_id)
        account.activate()

    # --------------------------------------------------------------------------
    # FR-02: Wallet Top-Up
    # --------------------------------------------------------------------------

    def top_up(
        self,
        account_id: str,
        amount: Union[Decimal, float, int, str],
        idempotency_key: Optional[str] = None,
    ) -> TransactionRecord:
        """Add funds to a user's wallet.

        Supports optional idempotency to prevent repeated credits on network retries.
        """
        valid_amount = _normalize_amount(amount)
        signature = (TransactionType.TOP_UP, None, account_id, valid_amount)

        # Synchronize idempotency check and execution
        with self._idempotency_lock:
            if idempotency_key is not None:
                cached = self._check_idempotency(idempotency_key, signature)
                if cached:
                    return cached

            account = self.get_account(account_id)
            with account.lock:
                account.credit(valid_amount)

            record = TransactionRecord(
                transaction_id=uuid.uuid4().hex,
                transaction_type=TransactionType.TOP_UP,
                sender_id=None,
                receiver_id=account_id,
                amount=valid_amount,
                status=TransactionStatus.SUCCESS,
                idempotency_key=idempotency_key,
                message="Wallet top-up successful",
            )

            if idempotency_key is not None:
                self._idempotency_store[idempotency_key] = (signature, record)

            self._record_transaction(record)
            return record

    # --------------------------------------------------------------------------
    # FR-01: Wallet Transfer & FR-04: Idempotency
    # --------------------------------------------------------------------------

    def transfer(
        self,
        sender_id: str,
        receiver_id: str,
        amount: Union[Decimal, float, int, str],
        idempotency_key: str,
    ) -> TransactionRecord:
        """Transfer funds between two wallet accounts.

        Guarantees idempotency via idempotency_key: repeated requests return the
        previously recorded transaction without debiting twice.
        Deadlock-free execution is guaranteed by ordering account locks.
        """
        if not idempotency_key or not idempotency_key.strip():
            raise WalletError("A non-empty idempotency_key is required for transfer requests.")

        if sender_id == receiver_id:
            raise SelfTransferError("Cannot transfer funds to the same account.")

        valid_amount = _normalize_amount(amount)
        clean_key = idempotency_key.strip()
        signature = (TransactionType.TRANSFER, sender_id, receiver_id, valid_amount)

        with self._idempotency_lock:
            # Check if this idempotency key was previously processed
            cached = self._check_idempotency(clean_key, signature)
            if cached:
                return cached

            sender = self.get_account(sender_id)
            receiver = self.get_account(receiver_id)

            # Prevent deadlocks by always acquiring account locks in a deterministic order
            first, second = sorted([sender, receiver], key=lambda a: a.account_id)

            with first.lock:
                with second.lock:
                    # Validate active statuses before mutating balances
                    if not sender.is_active:
                        raise AccountSuspendedError(f"Sender account '{sender_id}' is suspended/inactive.")
                    if not receiver.is_active:
                        raise AccountSuspendedError(f"Receiver account '{receiver_id}' is suspended/inactive.")

                    # Debit sender, credit receiver atomically
                    sender.debit(valid_amount)
                    try:
                        receiver.credit(valid_amount)
                    except Exception as err:
                        # Rollback sender debit in the unexpected event of receiver failure
                        sender.credit(valid_amount)
                        raise err

            record = TransactionRecord(
                transaction_id=uuid.uuid4().hex,
                transaction_type=TransactionType.TRANSFER,
                sender_id=sender_id,
                receiver_id=receiver_id,
                amount=valid_amount,
                status=TransactionStatus.SUCCESS,
                idempotency_key=clean_key,
                message="Wallet transfer successful",
            )

            self._idempotency_store[clean_key] = (signature, record)
            self._record_transaction(record)
            return record

    # --------------------------------------------------------------------------
    # FR-03: Merchant Payment
    # --------------------------------------------------------------------------

    def pay_merchant(
        self,
        sender_id: str,
        merchant_id: str,
        amount: Union[Decimal, float, int, str],
        idempotency_key: Optional[str] = None,
        order_id: Optional[str] = None,
    ) -> TransactionRecord:
        """Make payments to a verified merchant using available wallet balance."""
        if sender_id == merchant_id:
            raise SelfTransferError("Customer and merchant cannot be the same account.")

        valid_amount = _normalize_amount(amount)
        clean_key = idempotency_key.strip() if idempotency_key else None
        signature = (TransactionType.MERCHANT_PAYMENT, sender_id, merchant_id, valid_amount)

        with self._idempotency_lock:
            if clean_key is not None:
                cached = self._check_idempotency(clean_key, signature)
                if cached:
                    return cached

            sender = self.get_account(sender_id)
            merchant = self.get_account(merchant_id)

            if not merchant.is_merchant:
                raise InvalidMerchantError(f"Account '{merchant_id}' is not a registered merchant.")

            first, second = sorted([sender, merchant], key=lambda a: a.account_id)

            with first.lock:
                with second.lock:
                    if not sender.is_active:
                        raise AccountSuspendedError(f"Customer account '{sender_id}' is suspended/inactive.")
                    if not merchant.is_active:
                        raise AccountSuspendedError(f"Merchant account '{merchant_id}' is suspended/inactive.")

                    sender.debit(valid_amount)
                    try:
                        merchant.credit(valid_amount)
                    except Exception as err:
                        sender.credit(valid_amount)
                        raise err

            record = TransactionRecord(
                transaction_id=uuid.uuid4().hex,
                transaction_type=TransactionType.MERCHANT_PAYMENT,
                sender_id=sender_id,
                receiver_id=merchant_id,
                amount=valid_amount,
                status=TransactionStatus.SUCCESS,
                idempotency_key=clean_key,
                message="Merchant payment successful",
                metadata={"order_id": order_id} if order_id else {},
            )

            if clean_key is not None:
                self._idempotency_store[clean_key] = (signature, record)

            self._record_transaction(record)
            return record

    # --------------------------------------------------------------------------
    # Audit & Ledger Inspection
    # --------------------------------------------------------------------------

    def get_transaction_history(self, account_id: Optional[str] = None) -> List[TransactionRecord]:
        """Retrieve transaction history for a specific account or the entire ledger."""
        with self._ledger_lock:
            if account_id is None:
                return list(self._transactions)
            return [
                tx for tx in self._transactions
                if tx.sender_id == account_id or tx.receiver_id == account_id
            ]

    def get_transaction_by_id(self, transaction_id: str) -> Optional[TransactionRecord]:
        """Look up a transaction by its unique ID."""
        with self._ledger_lock:
            for tx in self._transactions:
                if tx.transaction_id == transaction_id:
                    return tx
            return None

    def get_transaction_by_idempotency_key(self, idempotency_key: str) -> Optional[TransactionRecord]:
        """Look up a transaction by its idempotency key."""
        with self._idempotency_lock:
            entry = self._idempotency_store.get(idempotency_key.strip())
            return entry[1] if entry else None

    # --------------------------------------------------------------------------
    # Private Helpers
    # --------------------------------------------------------------------------

    def _check_idempotency(
        self,
        key: str,
        signature: Tuple[Any, ...],
    ) -> Optional[TransactionRecord]:
        """Validate idempotency key against existing entries."""
        if key in self._idempotency_store:
            existing_signature, record = self._idempotency_store[key]
            if existing_signature != signature:
                raise IdempotencyConflictError(
                    f"Idempotency key '{key}' was already used with different request parameters. "
                    f"Existing: {existing_signature}, New: {signature}"
                )
            return record
        return None

    def _record_transaction(self, record: TransactionRecord) -> None:
        """Append record to global audit ledger."""
        with self._ledger_lock:
            self._transactions.append(record)


# ==============================================================================
# Demonstration CLI Entry Point
# ==============================================================================

if __name__ == "__main__":
    wallet_service = WalletService()

    print("=" * 65)
    print("      DIGITAL PAYMENT WALLET - SYSTEM DEMONSTRATION")
    print("=" * 65)

    # 1. Create Accounts
    print("\n[Step 1] Creating Accounts:")
    alice_acc = wallet_service.create_account("ACC-ALICE", "Alice Smith", initial_balance="100.00")
    bob_acc = wallet_service.create_account("ACC-BOB", "Bob Jones", initial_balance="25.00")
    cafe_acc = wallet_service.create_account("MERCHANT-CAFE", "Campus Cafe", is_merchant=True)
    print(f"  • Alice Account : Balance = ${alice_acc.balance}")
    print(f"  • Bob Account   : Balance = ${bob_acc.balance}")
    print(f"  • Cafe Merchant : Balance = ${cafe_acc.balance}")

    # 2. Wallet Top-up (FR-02)
    print("\n[Step 2] Wallet Top-up (FR-02):")
    topup = wallet_service.top_up("ACC-ALICE", "50.00", idempotency_key="topup-key-001")
    print(f"  • Top-up Tx ID  : {topup.transaction_id}")
    print(f"  • Alice Balance : ${wallet_service.get_balance('ACC-ALICE')} (+${topup.amount})")

    # 3. Wallet Transfer (FR-01) & Idempotency Guarantee (FR-04)
    print("\n[Step 3] Wallet Transfer & Idempotency (FR-01, FR-04):")
    print(f"  • Pre-transfer  : Alice = ${wallet_service.get_balance('ACC-ALICE')}, Bob = ${wallet_service.get_balance('ACC-BOB')}")
    xfer = wallet_service.transfer("ACC-ALICE", "ACC-BOB", "40.00", idempotency_key="xfer-key-001")
    print(f"  • Transfer Tx ID: {xfer.transaction_id}")
    print(f"  • Post-transfer : Alice = ${wallet_service.get_balance('ACC-ALICE')}, Bob = ${wallet_service.get_balance('ACC-BOB')}")

    print("\n  [Simulating network retry with IDENTICAL idempotency key 'xfer-key-001']...")
    retry_xfer = wallet_service.transfer("ACC-ALICE", "ACC-BOB", "40.00", idempotency_key="xfer-key-001")
    print(f"  • Retry Tx ID Match  : {retry_xfer.transaction_id == xfer.transaction_id} (Returned cached result)")
    print(f"  • Alice Balance Kept : ${wallet_service.get_balance('ACC-ALICE')} (NO duplicate debit)")
    print(f"  • Bob Balance Kept   : ${wallet_service.get_balance('ACC-BOB')} (NO duplicate credit)")

    # 4. Merchant Payment (FR-03)
    print("\n[Step 4] Merchant Payment (FR-03):")
    payment = wallet_service.pay_merchant(
        sender_id="ACC-BOB",
        merchant_id="MERCHANT-CAFE",
        amount="15.75",
        idempotency_key="pay-key-001",
        order_id="ORDER-CAFE-1001",
    )
    print(f"  • Payment Tx ID : {payment.transaction_id}")
    print(f"  • Order ID      : {payment.metadata.get('order_id')}")
    print(f"  • Bob Balance   : ${wallet_service.get_balance('ACC-BOB')}")
    print(f"  • Cafe Balance  : ${wallet_service.get_balance('MERCHANT-CAFE')}")

    # 5. Audit Ledger (NFR-02)
    print("\n[Step 5] Transaction Audit Ledger (NFR-02):")
    print(f"  {'Type':<18} {'From':<12} {'To':<15} {'Amount':<8} {'Status'}")
    print("  " + "-" * 58)
    for record in wallet_service.get_transaction_history():
        print(f"  {record.transaction_type.value:<18} {str(record.sender_id):<12} {str(record.receiver_id):<15} ${record.amount:<7} {record.status.value}")

    print("\n" + "=" * 65)
    print("      ALL OPERATIONS VERIFIED & EXECUTED SUCCESSFULLY")
    print("=" * 65)

