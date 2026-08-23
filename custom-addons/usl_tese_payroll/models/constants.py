TESE_COMPONENTS = (
    {
        "code": "641100",
        "name": "Gross remuneration",
        "side": "debit",
        "role": "gross",
        "sequence": 10,
    },
    {
        "code": "645100",
        "name": "Employer social-security contributions",
        "side": "debit",
        "role": "employer_contribution",
        "sequence": 20,
    },
    {
        "code": "645200",
        "name": "Employer provident contributions",
        "side": "debit",
        "role": "employer_contribution",
        "sequence": 30,
    },
    {
        "code": "645300",
        "name": "Employer supplementary-pension contributions",
        "side": "debit",
        "role": "employer_contribution",
        "sequence": 40,
    },
    {
        "code": "633300",
        "name": "Professional training contribution",
        "side": "debit",
        "role": "employer_contribution",
        "sequence": 50,
    },
    {
        "code": "633500",
        "name": "Apprenticeship tax",
        "side": "debit",
        "role": "employer_contribution",
        "sequence": 60,
    },
    {
        "code": "421000",
        "name": "Net salary payable",
        "side": "credit",
        "role": "salary",
        "sequence": 70,
    },
    {
        "code": "431000",
        "name": "Social-security liabilities",
        "side": "credit",
        "role": "social",
        "sequence": 80,
    },
    {
        "code": "437020",
        "name": "Provident liabilities",
        "side": "credit",
        "role": "social",
        "sequence": 90,
    },
    {
        "code": "437030",
        "name": "Supplementary-pension liabilities",
        "side": "credit",
        "role": "social",
        "sequence": 100,
    },
    {
        "code": "442100",
        "name": "Withholding income tax",
        "side": "credit",
        "role": "income_tax",
        "sequence": 110,
    },
)

TESE_COMPONENT_BY_CODE = {
    component["code"]: component for component in TESE_COMPONENTS
}
TESE_COMPONENT_CODES = tuple(TESE_COMPONENT_BY_CODE)
TESE_LIABILITY_ROLES = {"salary", "social", "income_tax"}

# RPC clients can choose context keys but cannot reproduce this in-process object.
# It allows model actions to update protected workflow fields without turning a
# user-controlled boolean context flag into an immutability bypass.
TESE_INTERNAL_WRITE_TOKEN = object()
