from abc import ABC, abstractmethod
from typing import Optional
from pydantic import BaseModel

class PaymentResult(BaseModel):
    success: bool
    transaction_id: Optional[str] = None
    payment_url: Optional[str] = None
    error_message: Optional[str] = None

class BasePaymentProvider(ABC):
    @abstractmethod
    async def create_payment(self, amount: float, description: str, metadata: dict) -> PaymentResult:
        pass

    @abstractmethod
    async def check_status(self, transaction_id: str) -> str:
        """Returns 'pending', 'success', or 'failed'."""
        pass

class MockProvider(BasePaymentProvider):
    async def create_payment(self, amount: float, description: str, metadata: dict) -> PaymentResult:
        # Just return a fake URL
        import uuid
        tx_id = f"mock_{uuid.uuid4().hex[:8]}"
        return PaymentResult(
            success=True, 
            transaction_id=tx_id, 
            payment_url=f"https://example.com/pay/{tx_id}"
        )

    async def check_status(self, transaction_id: str) -> str:
        return "success"

class YooMoneyProvider(BasePaymentProvider):
    def __init__(self, receiver: str):
        self.receiver = receiver

    async def create_payment(self, amount: float, description: str, metadata: dict) -> PaymentResult:
        import uuid
        label = f"sub_{uuid.uuid4().hex[:8]}"
        # Standard YooMoney Quickpay URL
        url = (
            f"https://yoomoney.ru/quickpay/confirm.xml?"
            f"receiver={self.receiver}&"
            f"quickpay-form=button&"
            f"targets={description}&"
            f"paymentType=SB&"
            f"sum={amount}&"
            f"label={label}"
        )
        return PaymentResult(
            success=True,
            transaction_id=label,
            payment_url=url
        )

    async def check_status(self, transaction_id: str) -> str:
        # Verification requires HTTP notifications setup (not implemented here for simplicity)
        return "pending"
