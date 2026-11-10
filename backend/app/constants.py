"""Shared vocabulary: event sources, event types, payment states, recovery actions."""

# ---- event sources -------------------------------------------------------
SOURCE_API = "paypal_api"          # response from a PayPal REST API call (authoritative if 2xx)
SOURCE_WEBHOOK = "paypal_webhook"  # PayPal webhook (authoritative only if signature-verified)
SOURCE_LOG = "merchant_log"        # merchant application log (observation, never authoritative)
SOURCES = {SOURCE_API, SOURCE_WEBHOOK, SOURCE_LOG}

# ---- normalized event types ----------------------------------------------
ET_ORDER_CREATED = "ORDER_CREATED"
ET_ORDER_APPROVED = "ORDER_APPROVED"
ET_CAPTURE_REQUESTED = "CAPTURE_REQUESTED"
ET_REQUEST_TIMEOUT = "REQUEST_TIMEOUT"
ET_CAPTURE_COMPLETED = "CAPTURE_COMPLETED"
ET_ORDER_COMPLETED = "ORDER_COMPLETED"
ET_CAPTURE_PENDING = "CAPTURE_PENDING"
ET_CAPTURE_DENIED = "CAPTURE_DENIED"
ET_CAPTURE_REFUNDED = "CAPTURE_REFUNDED"
ET_API_ERROR = "API_ERROR"
ET_STATE_LOOKUP = "STATE_LOOKUP"
ET_LOCAL_STATE = "LOCAL_STATE_RECORDED"
ET_OTHER = "OTHER"

# ---- payment state classes -----------------------------------------------
SUCCESS_STATES = {"COMPLETED", "PAID"}
FAILURE_STATES = {"DECLINED", "DENIED", "FAILED", "VOIDED"}
PENDING_STATES = {"PENDING", "CREATED", "APPROVED", "PROCESSING", "SAVED", "PAYER_ACTION_REQUIRED"}
ALL_STATES = SUCCESS_STATES | FAILURE_STATES | PENDING_STATES | {"REFUNDED", "CONFLICT", "PARTIALLY_REFUNDED"}


def state_class(state: str | None) -> str | None:
    s = (state or "").upper()
    if s in SUCCESS_STATES:
        return "success"
    if s in FAILURE_STATES:
        return "failure"
    if s in PENDING_STATES:
        return "pending"
    if s in {"REFUNDED", "PARTIALLY_REFUNDED"}:
        return "refunded"
    return None


# ---- recovery actions ----------------------------------------------------
# Prohibited-or-risky actions (the safety engine decides when each is blocked)
CAPTURE_AGAIN = "CAPTURE_AGAIN"
RETRY_PAYMENT = "RETRY_PAYMENT"          # blind retry - always prohibited
CREATE_NEW_ORDER = "CREATE_NEW_ORDER"
REFUND = "REFUND"
MARK_ORDER_FAILED = "MARK_ORDER_FAILED"
# Safe actions
CHECK_CURRENT_STATE = "CHECK_CURRENT_STATE"
RECONCILE_LOCAL_ORDER = "RECONCILE_LOCAL_ORDER"
WAIT_AND_RECHECK = "WAIT_AND_RECHECK"
ESCALATE_FOR_REVIEW = "ESCALATE_FOR_REVIEW"
REQUEST_MORE_EVIDENCE = "REQUEST_MORE_EVIDENCE"
NO_ACTION_REQUIRED = "NO_ACTION_REQUIRED"
RETRY_WITH_APPROVAL = "RETRY_WITH_APPROVAL"  # idempotent retry, only after verified state + human approval

ACTION_CATALOG = {
    CAPTURE_AGAIN: "Submit another capture for the same order",
    RETRY_PAYMENT: "Blindly retry the payment / charge again",
    CREATE_NEW_ORDER: "Create a new order to replace the existing one",
    REFUND: "Refund the payment",
    MARK_ORDER_FAILED: "Mark the local order as failed",
    CHECK_CURRENT_STATE: "Retrieve the current order/capture state from PayPal",
    RECONCILE_LOCAL_ORDER: "Update the local order record to match PayPal's confirmed state",
    WAIT_AND_RECHECK: "Wait and re-check the authoritative state later",
    ESCALATE_FOR_REVIEW: "Escalate to a human for manual investigation",
    REQUEST_MORE_EVIDENCE: "Collect more diagnostic evidence before acting",
    NO_ACTION_REQUIRED: "No recovery action is required",
    RETRY_WITH_APPROVAL: "Retry using the same PayPal-Request-Id, only with explicit human approval",
}

# Actions that change payment state -> always need explicit human approval.
CONSEQUENTIAL_ACTIONS = {CAPTURE_AGAIN, RETRY_PAYMENT, CREATE_NEW_ORDER, REFUND, MARK_ORDER_FAILED, RETRY_WITH_APPROVAL}

SEVERITY_LEVELS = ["Low", "Medium", "High", "Critical"]
CONFIDENCE_ORDER = ["inconclusive", "low", "medium", "high"]
