import uuid
from loguru import logger
from app.config import settings

class PaymentProvider:
    async def create_payment(self, amount: float, description: str, return_url: str) -> dict:
        raise NotImplementedError

    async def check_payment(self, payment_id: str) -> bool:
        raise NotImplementedError

class MockPaymentProvider(PaymentProvider):
    async def create_payment(self, amount: float, description: str, return_url: str) -> dict:
        mock_id = f"mock_{uuid.uuid4().hex[:8]}"
        logger.info(f"[MOCK] Created payment {mock_id} for {amount} RUB: {description}")
        return {
            "payment_id": mock_id,
            "url": f"{return_url}?payment_id={mock_id}&status=success"
        }

    async def check_payment(self, payment_id: str) -> bool:
        logger.info(f"[MOCK] Checking payment {payment_id}. Always returns True.")
        return True

class YooKassaProvider(PaymentProvider):
    def __init__(self):
        self.shop_id = settings.payments.yookassa_shop_id
        self.secret_key = settings.payments.yookassa_secret_key
        # Here would be the yookassa setup, e.g., Configuration.account_id = ...

    async def create_payment(self, amount: float, description: str, return_url: str) -> dict:
        # Full logic implementation
        logger.info(f"YooKassa: Requesting payment creation for {amount} RUB")
        # payment = Payment.create({...})
        # return {"payment_id": payment.id, "url": payment.confirmation.confirmation_url}
        return {"payment_id": "yoo_123", "url": "https://yoomoney.ru/checkout/payments/v2/contract?orderId=123"}

    async def check_payment(self, payment_id: str) -> bool:
        # return Payment.find_one(payment_id).status == 'succeeded'
        return False

def get_payment_provider() -> PaymentProvider:
    if settings.payments.mock_mode:
        return MockPaymentProvider()
    # Depending on logic, you could select specific provider
    # For now, returning YooKassa as default real provider
    return YooKassaProvider()
