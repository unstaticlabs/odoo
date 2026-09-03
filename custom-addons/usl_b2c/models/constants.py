SOURCE_PROVIDERS = [
    ("etsy", "Etsy"),
    ("medusa_legacy", "Legacy Goodboys / Medusa"),
    ("medusa", "Medusa"),
    ("stripe", "Stripe"),
    ("revolut", "Revolut Merchant"),
    ("printful", "Printful"),
    ("manual", "Manual"),
    ("other", "Other"),
]

HISTORICAL_B2C_COMMUNICATION_PARAMETER = (
    "usl_b2c.allow_historical_customer_communication"
)
HISTORICAL_B2C_MATERIALIZATION_CONTEXT = "usl_b2c_history_materialization"

ORIGINS = [
    ("imported", "Imported"),
    ("manual", "Manual"),
    ("synchronized", "Synchronized"),
]

REVIEW_STATES = [
    ("pending", "Pending"),
    ("reviewed", "Reviewed"),
    ("blocked", "Blocked"),
]

COMPLETENESS_STATES = [
    ("complete", "Complete"),
    ("partial", "Partial"),
    ("header_only", "Header only"),
    ("unknown", "Unknown"),
]

MAPPING_STATES = [
    ("pending", "Pending"),
    ("verified", "Verified"),
    ("rejected", "Rejected"),
    ("partial", "Partial"),
    ("not_applicable", "Not applicable"),
]

FULFILMENT_MODES = [
    ("own_stock", "Own stock"),
    ("printful", "Printful / POD"),
    ("mixed", "Mixed"),
    ("not_applicable", "Not applicable"),
    ("unknown", "Unknown"),
]

CONVERSION_STATES = [
    ("not_needed", "Same currency"),
    ("processor_evidenced", "Processor evidenced"),
    ("restored_historical_rate", "Restored historical rate"),
    ("pending", "Pending evidence"),
    ("not_applicable", "Not applicable"),
]
