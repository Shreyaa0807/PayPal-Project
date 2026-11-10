"""Small curated reference of PayPal statuses/errors given to the LLM as 'relevant documentation'.

Paraphrased from PayPal's public docs; the model must not claim anything beyond these notes and the evidence.
Verify against https://developer.paypal.com/api/rest/reference/orders/v2/errors/ before relying on wording.
"""

ERROR_DOCS = {
    "INSTRUMENT_DECLINED": "The funding instrument was declined. The buyer usually needs to choose a different funding source; a blind retry will not help.",
    "ORDER_NOT_APPROVED": "The buyer has not approved the order yet, so it cannot be captured.",
    "ORDER_ALREADY_CAPTURED": "The order was already captured. Capturing again is not allowed - retrieve the order to see the existing capture.",
    "PAYER_ACTION_REQUIRED": "The buyer must complete an action (for example approval) before the order can proceed.",
    "TRANSACTION_REFUSED": "PayPal refused the transaction. Details of the underlying reason may not be exposed.",
    "DUPLICATE_INVOICE_ID": "An invoice ID was reused; this is a merchant-side duplicate-detection error, not proof of a duplicate charge.",
}

STATUS_DOCS = {
    "COMPLETED": "The funds for this capture were credited to the payee's account.",
    "PENDING": "The funds for this capture have not yet been credited. A final status (COMPLETED or DECLINED) is expected later; pending is neither success nor failure.",
    "DECLINED": "The capture was declined.",
    "REFUNDED": "The captured amount was fully refunded.",
    "APPROVED": "The buyer approved the order; it can now be captured.",
    "CREATED": "The order was created but the buyer has not approved it yet.",
}

GENERAL_NOTES = [
    "A request timeout means the merchant did not receive a response. It does NOT mean the payment failed.",
    "PayPal webhooks use at-least-once delivery, so the same event ID can legitimately arrive more than once. Duplicate notifications do not prove duplicate charges.",
    "Use the PayPal-Request-Id header (idempotency key) when retrying retryable API requests.",
]


def docs_for(error_codes: list[str], states: list[str]) -> dict:
    return {
        "errors": {c: ERROR_DOCS[c] for c in error_codes if c in ERROR_DOCS},
        "statuses": {s: STATUS_DOCS[s] for s in states if s in STATUS_DOCS},
        "general": GENERAL_NOTES,
    }
