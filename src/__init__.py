"""Digital Payment Wallet Package."""

__all__ = [
    "WalletService",
    "WalletAccount",
    "TransactionRecord",
    "TransactionType",
    "TransactionStatus",
    "WalletError",
    "InsufficientFundsError",
    "InvalidAmountError",
    "AccountNotFoundError",
    "AccountSuspendedError",
    "SelfTransferError",
    "IdempotencyConflictError",
    "InvalidMerchantError",
]


def __getattr__(name: str):
    if name in __all__:
        import importlib
        wallet_module = importlib.import_module(".wallet", package=__name__)
        return getattr(wallet_module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return __all__
