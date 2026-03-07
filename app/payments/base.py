from __future__ import annotations

import base64
import json
import uuid
from abc import ABC, abstractmethod
from typing import Any, Optional
from urllib.parse import urlencode

import httpx
from loguru import logger
from pydantic import BaseModel


class PaymentResult(BaseModel):
    success: bool
    transaction_id: Optional[str] = None
    payment_url: Optional[str] = None
    error_message: Optional[str] = None
    recurring_token: Optional[str] = None
    raw: dict[str, Any] = {}


class RefundResult(BaseModel):
    success: bool
    refund_id: Optional[str] = None
    error_message: Optional[str] = None
    raw: dict[str, Any] = {}


class BasePaymentProvider(ABC):
    @abstractmethod
    async def create_payment(
        self,
        amount: float,
        description: str,
        metadata: dict[str, Any],
        *,
        return_url: str | None = None,
        save_payment_method: bool = False,
    ) -> PaymentResult:
        """Create a payment and return provider transaction reference."""

    @abstractmethod
    async def check_status(self, transaction_id: str) -> str:
        """Return one of: pending, success, failed."""

    @abstractmethod
    async def refund_payment(self, transaction_id: str, amount: Optional[float] = None) -> RefundResult:
        """Create a refund for a successful payment."""

    async def get_payment_details(self, transaction_id: str) -> dict[str, Any]:
        return {"transaction_id": transaction_id}

    async def charge_recurring(
        self,
        amount: float,
        description: str,
        recurring_token: str,
        metadata: dict[str, Any],
        *,
        return_url: str | None = None,
    ) -> PaymentResult:
        return PaymentResult(success=False, error_message="Recurring payments are not supported by this provider")


class MockProvider(BasePaymentProvider):
    async def create_payment(
        self,
        amount: float,
        description: str,
        metadata: dict[str, Any],
        *,
        return_url: str | None = None,
        save_payment_method: bool = False,
    ) -> PaymentResult:
        tx_id = f"mock_{uuid.uuid4().hex[:12]}"
        logger.info(f"Mock payment created: {tx_id}, amount={amount}, description='{description}'")
        return PaymentResult(
            success=True,
            transaction_id=tx_id,
            payment_url=return_url or f"mock://paid/{tx_id}",
            recurring_token=f"mock_recurring_{uuid.uuid4().hex[:12]}" if save_payment_method else None,
            raw={"mode": "mock", "metadata": metadata},
        )

    async def check_status(self, transaction_id: str) -> str:
        return "success"

    async def refund_payment(self, transaction_id: str, amount: Optional[float] = None) -> RefundResult:
        return RefundResult(
            success=True,
            refund_id=f"mock_refund_{uuid.uuid4().hex[:12]}",
            raw={"transaction_id": transaction_id, "amount": amount},
        )

    async def get_payment_details(self, transaction_id: str) -> dict[str, Any]:
        return {"transaction_id": transaction_id, "status": "success"}

    async def charge_recurring(
        self,
        amount: float,
        description: str,
        recurring_token: str,
        metadata: dict[str, Any],
        *,
        return_url: str | None = None,
    ) -> PaymentResult:
        return await self.create_payment(
            amount,
            description,
            metadata,
            return_url=return_url,
            save_payment_method=bool(recurring_token),
        )


class YooMoneyProvider(BasePaymentProvider):
    def __init__(self, receiver: str):
        self.receiver = receiver

    async def create_payment(
        self,
        amount: float,
        description: str,
        metadata: dict[str, Any],
        *,
        return_url: str | None = None,
        save_payment_method: bool = False,
    ) -> PaymentResult:
        label = f"ym_{uuid.uuid4().hex[:16]}"
        query = urlencode(
            {
                "receiver": self.receiver,
                "quickpay-form": "button",
                "targets": description,
                "paymentType": "SB",
                "sum": f"{amount:.2f}",
                "label": label,
                "successURL": return_url or "https://t.me",
            }
        )
        return PaymentResult(
            success=True,
            transaction_id=label,
            payment_url=f"https://yoomoney.ru/quickpay/confirm.xml?{query}",
            raw={"provider": "yoomoney", "manual_confirmation": True, "metadata": metadata},
        )

    async def check_status(self, transaction_id: str) -> str:
        return "pending"

    async def refund_payment(self, transaction_id: str, amount: Optional[float] = None) -> RefundResult:
        return RefundResult(
            success=False,
            error_message="YooMoney manual donations require manual refund handling.",
            raw={"transaction_id": transaction_id, "amount": amount},
        )

    async def get_payment_details(self, transaction_id: str) -> dict[str, Any]:
        return {
            "transaction_id": transaction_id,
            "status": "pending",
            "manual_confirmation": True,
        }


class YooKassaProvider(BasePaymentProvider):
    def __init__(self, shop_id: str, secret_key: str):
        token = base64.b64encode(f"{shop_id}:{secret_key}".encode("utf-8")).decode("utf-8")
        self.headers = {
            "Authorization": f"Basic {token}",
            "Content-Type": "application/json",
            "Idempotence-Key": "",
        }

    async def create_payment(
        self,
        amount: float,
        description: str,
        metadata: dict[str, Any],
        *,
        return_url: str | None = None,
        save_payment_method: bool = False,
    ) -> PaymentResult:
        payload = {
            "amount": {"value": f"{amount:.2f}", "currency": "RUB"},
            "capture": True,
            "confirmation": {"type": "redirect", "return_url": return_url or "https://t.me"},
            "description": description,
            "metadata": metadata,
            "save_payment_method": save_payment_method,
        }
        idempotence_key = str(uuid.uuid4())
        headers = {**self.headers, "Idempotence-Key": idempotence_key}
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.post("https://api.yookassa.ru/v3/payments", headers=headers, json=payload)
            response.raise_for_status()
            body = response.json()
            payment_method = body.get("payment_method") or {}
            return PaymentResult(
                success=True,
                transaction_id=body.get("id"),
                payment_url=(body.get("confirmation") or {}).get("confirmation_url"),
                recurring_token=payment_method.get("id") if payment_method.get("saved") else None,
                raw=body,
            )
        except Exception as exc:
            logger.error(f"YooKassa create_payment failed: {exc}")
            return PaymentResult(success=False, error_message=str(exc))

    async def check_status(self, transaction_id: str) -> str:
        try:
            details = await self.get_payment_details(transaction_id)
            status = details.get("status", "")
            if status == "succeeded":
                return "success"
            if status in {"pending", "waiting_for_capture"}:
                return "pending"
            return "failed"
        except Exception as exc:
            logger.error(f"YooKassa check_status failed: {exc}")
            return "failed"

    async def refund_payment(self, transaction_id: str, amount: Optional[float] = None) -> RefundResult:
        payload: dict[str, Any] = {"payment_id": transaction_id}
        if amount is not None:
            payload["amount"] = {"value": f"{amount:.2f}", "currency": "RUB"}
        headers = {**self.headers, "Idempotence-Key": str(uuid.uuid4())}
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.post("https://api.yookassa.ru/v3/refunds", headers=headers, json=payload)
            response.raise_for_status()
            body = response.json()
            return RefundResult(success=True, refund_id=body.get("id"), raw=body)
        except Exception as exc:
            logger.error(f"YooKassa refund failed: {exc}")
            return RefundResult(success=False, error_message=str(exc))

    async def get_payment_details(self, transaction_id: str) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(
                f"https://api.yookassa.ru/v3/payments/{transaction_id}",
                headers=self.headers,
            )
        response.raise_for_status()
        body = response.json()
        payment_method = body.get("payment_method") or {}
        if payment_method.get("saved") and payment_method.get("id"):
            body["recurring_token"] = payment_method.get("id")
        return body

    async def charge_recurring(
        self,
        amount: float,
        description: str,
        recurring_token: str,
        metadata: dict[str, Any],
        *,
        return_url: str | None = None,
    ) -> PaymentResult:
        payload = {
            "amount": {"value": f"{amount:.2f}", "currency": "RUB"},
            "capture": True,
            "payment_method_id": recurring_token,
            "description": description,
            "metadata": metadata,
        }
        headers = {**self.headers, "Idempotence-Key": str(uuid.uuid4())}
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.post("https://api.yookassa.ru/v3/payments", headers=headers, json=payload)
            response.raise_for_status()
            body = response.json()
            return PaymentResult(
                success=True,
                transaction_id=body.get("id"),
                recurring_token=recurring_token,
                raw=body,
            )
        except Exception as exc:
            logger.error(f"YooKassa recurring charge failed: {exc}")
            return PaymentResult(success=False, error_message=str(exc))


class SberbankProvider(BasePaymentProvider):
    def __init__(self, username: str, password: str):
        self.username = username
        self.password = password
        self.base_url = "https://securepayments.sberbank.ru/payment/rest"

    async def create_payment(
        self,
        amount: float,
        description: str,
        metadata: dict[str, Any],
        *,
        return_url: str | None = None,
        save_payment_method: bool = False,
    ) -> PaymentResult:
        order_number = f"boterator-{uuid.uuid4().hex[:20]}"
        payload = {
            "userName": self.username,
            "password": self.password,
            "orderNumber": order_number,
            "amount": str(int(round(amount * 100))),
            "description": description,
            "returnUrl": return_url or "https://t.me",
            "jsonParams": json.dumps(metadata, ensure_ascii=False),
        }
        if save_payment_method:
            payload["features"] = "AUTO_PAYMENT"
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.post(f"{self.base_url}/register.do", data=payload)
            response.raise_for_status()
            body = response.json()
            if body.get("errorCode") and body.get("errorCode") != "0":
                return PaymentResult(success=False, error_message=body.get("errorMessage", "Sberbank error"), raw=body)
            return PaymentResult(
                success=True,
                transaction_id=body.get("orderId"),
                payment_url=body.get("formUrl"),
                recurring_token=body.get("bindingId"),
                raw=body,
            )
        except Exception as exc:
            logger.error(f"Sberbank create_payment failed: {exc}")
            return PaymentResult(success=False, error_message=str(exc))

    async def check_status(self, transaction_id: str) -> str:
        try:
            details = await self.get_payment_details(transaction_id)
            status_code = int(details.get("orderStatus", -1))
            if status_code in {0, 1}:
                return "pending"
            if status_code == 2:
                return "success"
            return "failed"
        except Exception as exc:
            logger.error(f"Sberbank check_status failed: {exc}")
            return "failed"

    async def refund_payment(self, transaction_id: str, amount: Optional[float] = None) -> RefundResult:
        payload = {
            "userName": self.username,
            "password": self.password,
            "orderId": transaction_id,
            "amount": str(int(round(amount * 100))) if amount is not None else "",
        }
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.post(f"{self.base_url}/refund.do", data=payload)
            response.raise_for_status()
            body = response.json()
            if body.get("errorCode") and body.get("errorCode") != "0":
                return RefundResult(success=False, error_message=body.get("errorMessage", "Sberbank refund error"), raw=body)
            return RefundResult(success=True, refund_id=body.get("orderId"), raw=body)
        except Exception as exc:
            logger.error(f"Sberbank refund failed: {exc}")
            return RefundResult(success=False, error_message=str(exc))

    async def get_payment_details(self, transaction_id: str) -> dict[str, Any]:
        payload = {
            "userName": self.username,
            "password": self.password,
            "orderId": transaction_id,
        }
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(f"{self.base_url}/getOrderStatusExtended.do", data=payload)
        response.raise_for_status()
        body = response.json()
        binding_info = body.get("bindingInfo") or {}
        body["recurring_token"] = binding_info.get("bindingId") or body.get("bindingId")
        return body

    async def charge_recurring(
        self,
        amount: float,
        description: str,
        recurring_token: str,
        metadata: dict[str, Any],
        *,
        return_url: str | None = None,
    ) -> PaymentResult:
        order_number = f"boterator-recurring-{uuid.uuid4().hex[:20]}"
        payload = {
            "userName": self.username,
            "password": self.password,
            "bindingId": recurring_token,
            "orderNumber": order_number,
            "amount": str(int(round(amount * 100))),
            "description": description,
            "returnUrl": return_url or "https://t.me",
            "jsonParams": json.dumps(metadata, ensure_ascii=False),
        }
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.post(f"{self.base_url}/paymentOrderBinding.do", data=payload)
            response.raise_for_status()
            body = response.json()
            if body.get("errorCode") and body.get("errorCode") != "0":
                return PaymentResult(success=False, error_message=body.get("errorMessage", "Sberbank recurring error"), raw=body)
            return PaymentResult(
                success=True,
                transaction_id=body.get("orderId"),
                payment_url=body.get("formUrl"),
                recurring_token=recurring_token,
                raw=body,
            )
        except Exception as exc:
            logger.error(f"Sberbank recurring charge failed: {exc}")
            return PaymentResult(success=False, error_message=str(exc))


def build_payment_provider(
    mode: str,
    *,
    yoomoney_receiver: str,
    yookassa_shop_id: str,
    yookassa_secret_key: str,
    sberbank_username: str,
    sberbank_password: str,
) -> BasePaymentProvider:
    normalized = (mode or "").strip().lower()
    if normalized == "yoomoney":
        if yoomoney_receiver:
            return YooMoneyProvider(receiver=yoomoney_receiver)
        logger.warning("YooMoney mode selected, but receiver is missing. Falling back to mock mode.")
        return MockProvider()
    if normalized == "yookassa":
        if yookassa_shop_id and yookassa_secret_key:
            return YooKassaProvider(shop_id=yookassa_shop_id, secret_key=yookassa_secret_key)
        logger.warning("YooKassa mode selected, but credentials are missing. Falling back to mock mode.")
        return MockProvider()
    if normalized == "sberbank":
        if sberbank_username and sberbank_password:
            return SberbankProvider(username=sberbank_username, password=sberbank_password)
        logger.warning("Sberbank mode selected, but credentials are missing. Falling back to mock mode.")
        return MockProvider()
    return MockProvider()
