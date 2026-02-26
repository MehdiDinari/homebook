from __future__ import annotations

import base64
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

import httpx


class PaymentProviderError(RuntimeError):
    pass


async def create_stripe_checkout_session(
    *,
    secret_key: str,
    amount_cents: int,
    currency: str,
    title: str,
    success_url: str,
    cancel_url: str,
    metadata: dict[str, str] | None = None,
) -> dict[str, Any]:
    if not secret_key:
        raise PaymentProviderError("STRIPE_SECRET_KEY missing")

    form: dict[str, str] = {
        "mode": "payment",
        "success_url": success_url,
        "cancel_url": cancel_url,
        "line_items[0][price_data][currency]": currency.lower(),
        "line_items[0][price_data][unit_amount]": str(amount_cents),
        "line_items[0][price_data][product_data][name]": title,
        "line_items[0][quantity]": "1",
    }
    for key, value in (metadata or {}).items():
        form[f"metadata[{key}]"] = value

    headers = {"Authorization": f"Bearer {secret_key}"}
    async with httpx.AsyncClient(timeout=30) as client:
        res = await client.post(
            "https://api.stripe.com/v1/checkout/sessions",
            data=form,
            headers=headers,
        )
    if not res.is_success:
        raise PaymentProviderError(f"Stripe checkout error ({res.status_code}): {res.text}")
    payload = res.json()
    return {
        "session_id": str(payload.get("id") or ""),
        "checkout_url": str(payload.get("url") or ""),
        "status": str(payload.get("status") or "open"),
    }


async def get_stripe_checkout_session(*, secret_key: str, session_id: str) -> dict[str, Any]:
    if not secret_key:
        raise PaymentProviderError("STRIPE_SECRET_KEY missing")
    if not session_id:
        raise PaymentProviderError("Stripe session id missing")
    headers = {"Authorization": f"Bearer {secret_key}"}
    async with httpx.AsyncClient(timeout=20) as client:
        res = await client.get(f"https://api.stripe.com/v1/checkout/sessions/{session_id}", headers=headers)
    if not res.is_success:
        raise PaymentProviderError(f"Stripe fetch error ({res.status_code}): {res.text}")
    payload = res.json()
    return {
        "payment_status": str(payload.get("payment_status") or ""),
        "status": str(payload.get("status") or ""),
    }


def _paypal_base_url(env: str) -> str:
    return "https://api-m.paypal.com" if env == "live" else "https://api-m.sandbox.paypal.com"


def _paypal_basic_auth(client_id: str, client_secret: str) -> str:
    raw = f"{client_id}:{client_secret}".encode("utf-8")
    return base64.b64encode(raw).decode("ascii")


def _amount_value_from_cents(amount_cents: int) -> str:
    amount = (Decimal(amount_cents) / Decimal(100)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return format(amount, "f")


async def _paypal_access_token(*, client_id: str, client_secret: str, env: str) -> str:
    if not client_id or not client_secret:
        raise PaymentProviderError("PayPal credentials are missing")
    headers = {
        "Authorization": f"Basic {_paypal_basic_auth(client_id, client_secret)}",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    base_url = _paypal_base_url(env)
    async with httpx.AsyncClient(timeout=30) as client:
        res = await client.post(
            f"{base_url}/v1/oauth2/token",
            data={"grant_type": "client_credentials"},
            headers=headers,
        )
    if not res.is_success:
        raise PaymentProviderError(f"PayPal oauth error ({res.status_code}): {res.text}")
    payload = res.json()
    token = str(payload.get("access_token") or "")
    if not token:
        raise PaymentProviderError("PayPal oauth did not return an access_token")
    return token


async def create_paypal_order(
    *,
    client_id: str,
    client_secret: str,
    env: str,
    amount_cents: int,
    currency: str,
    title: str,
    return_url: str,
    cancel_url: str,
    custom_id: str = "",
) -> dict[str, Any]:
    token = await _paypal_access_token(client_id=client_id, client_secret=client_secret, env=env)
    base_url = _paypal_base_url(env)
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    purchase_unit: dict[str, Any] = {
        "amount": {
            "currency_code": currency.upper(),
            "value": _amount_value_from_cents(amount_cents),
        },
        "description": title,
    }
    if custom_id:
        purchase_unit["custom_id"] = custom_id[:127]

    body: dict[str, Any] = {
        "intent": "CAPTURE",
        "purchase_units": [purchase_unit],
        "application_context": {
            "return_url": return_url,
            "cancel_url": cancel_url,
            "user_action": "PAY_NOW",
        },
    }
    async with httpx.AsyncClient(timeout=30) as client:
        res = await client.post(f"{base_url}/v2/checkout/orders", json=body, headers=headers)
    if not res.is_success:
        raise PaymentProviderError(f"PayPal create order error ({res.status_code}): {res.text}")
    payload = res.json()
    order_id = str(payload.get("id") or "")
    links = payload.get("links") or []
    approve_url = ""
    if isinstance(links, list):
        for link in links:
            if isinstance(link, dict) and str(link.get("rel") or "").lower() == "approve":
                approve_url = str(link.get("href") or "")
                break
    if not order_id:
        raise PaymentProviderError("PayPal create order did not return an order id")
    if not approve_url:
        raise PaymentProviderError("PayPal create order did not return an approval URL")
    return {
        "order_id": order_id,
        "checkout_url": approve_url,
        "status": str(payload.get("status") or "CREATED"),
    }


async def capture_paypal_order(
    *,
    client_id: str,
    client_secret: str,
    env: str,
    order_id: str,
) -> dict[str, Any]:
    if not order_id:
        raise PaymentProviderError("PayPal order id missing")
    token = await _paypal_access_token(client_id=client_id, client_secret=client_secret, env=env)
    base_url = _paypal_base_url(env)
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=30) as client:
        res = await client.post(f"{base_url}/v2/checkout/orders/{order_id}/capture", headers=headers, json={})
    if not res.is_success:
        # PayPal returns 422 when an order is already captured; recover with GET order.
        if res.status_code != 422:
            raise PaymentProviderError(f"PayPal capture error ({res.status_code}): {res.text}")
        async with httpx.AsyncClient(timeout=20) as client:
            get_res = await client.get(f"{base_url}/v2/checkout/orders/{order_id}", headers=headers)
        if not get_res.is_success:
            raise PaymentProviderError(f"PayPal order fetch error ({get_res.status_code}): {get_res.text}")
        payload = get_res.json()
    else:
        payload = res.json()

    status = str(payload.get("status") or "")
    capture_id = ""
    purchase_units = payload.get("purchase_units") or []
    if isinstance(purchase_units, list):
        for unit in purchase_units:
            if not isinstance(unit, dict):
                continue
            payments = unit.get("payments") or {}
            captures = payments.get("captures") if isinstance(payments, dict) else None
            if not isinstance(captures, list):
                continue
            for capture in captures:
                if not isinstance(capture, dict):
                    continue
                capture_id = str(capture.get("id") or "")
                capture_status = str(capture.get("status") or "")
                if capture_status == "COMPLETED":
                    return {
                        "payment_status": "paid",
                        "order_status": status,
                        "capture_id": capture_id,
                    }

    if status == "COMPLETED":
        return {
            "payment_status": "paid",
            "order_status": status,
            "capture_id": capture_id,
        }
    return {
        "payment_status": "pending",
        "order_status": status,
        "capture_id": capture_id,
    }


async def create_paypal_payout(
    *,
    client_id: str,
    client_secret: str,
    env: str,
    receiver_email: str,
    amount_cents: int,
    currency: str = "EUR",
    note: str = "",
    sender_item_id: str = "",
) -> dict[str, Any]:
    if not receiver_email:
        raise PaymentProviderError("PayPal receiver email is required")
    if amount_cents <= 0:
        raise PaymentProviderError("PayPal payout amount must be positive")

    token = await _paypal_access_token(client_id=client_id, client_secret=client_secret, env=env)
    base_url = _paypal_base_url(env)
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    body: dict[str, Any] = {
        "sender_batch_header": {
            "sender_batch_id": sender_item_id[:120] or f"hbwd-{amount_cents}",
            "email_subject": "HomeBook payout",
            "email_message": "Votre retrait HomeBook est en cours de traitement.",
        },
        "items": [
            {
                "recipient_type": "EMAIL",
                "amount": {
                    "value": _amount_value_from_cents(amount_cents),
                    "currency": currency.upper(),
                },
                "receiver": receiver_email,
                "note": note[:4000] or "HomeBook teacher withdrawal",
                "sender_item_id": sender_item_id[:120] or "homebook-withdrawal",
            }
        ],
    }

    async with httpx.AsyncClient(timeout=30) as client:
        res = await client.post(
            f"{base_url}/v1/payments/payouts?sync_mode=true",
            json=body,
            headers=headers,
        )
    if not res.is_success:
        raise PaymentProviderError(f"PayPal payout error ({res.status_code}): {res.text}")
    payload = res.json()
    batch_header = payload.get("batch_header") if isinstance(payload, dict) else {}
    items = payload.get("items") if isinstance(payload, dict) else []
    payout_batch_id = ""
    payout_item_id = ""
    payout_status = ""
    if isinstance(batch_header, dict):
        payout_batch_id = str(batch_header.get("payout_batch_id") or "")
        payout_status = str(batch_header.get("batch_status") or "")
    if isinstance(items, list) and items:
        first = items[0] if isinstance(items[0], dict) else {}
        payout_item_id = str(first.get("payout_item_id") or "")

    return {
        "payout_batch_id": payout_batch_id,
        "payout_item_id": payout_item_id,
        "payout_status": payout_status,
    }
