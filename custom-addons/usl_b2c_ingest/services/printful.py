"""Reading fulfilment from Printful.

Printful is the supplier behind most of what the channels sell, and it is the
only place the cost of a sale is stated per order.  The token is configuration,
never code: it is read from a system parameter so the repository never carries
it and a rotated token needs no release.
"""

import logging

import requests

_logger = logging.getLogger(__name__)

BASE_URL = "https://api.printful.com"
TOKEN_PARAMETER = "usl_b2c_ingest.printful_token"
PAGE_SIZE = 100
#: A page beyond this many is a paging bug, not a large shop.
MAX_PAGES = 50
TIMEOUT = 30


class PrintfulError(RuntimeError):
    """Printful refused or could not answer."""


class PrintfulClient:
    """A read-only client for the orders Printful has fulfilled."""

    def __init__(self, token, *, base_url=BASE_URL, session=None):
        if not token:
            message = "No Printful token is configured."
            raise PrintfulError(message)
        self._token = token
        self._base_url = base_url.rstrip("/")
        self._session = session or requests.Session()

    def fetch(self, path, params):
        """Return one decoded page. Overridden in tests to avoid the network."""
        response = self._session.get(
            f"{self._base_url}{path}",
            params=params,
            headers={"Authorization": f"Bearer {self._token}"},
            timeout=TIMEOUT,
        )
        if response.status_code != 200:
            message = f"Printful answered {response.status_code} for {path}."
            raise PrintfulError(message)
        return response.json()

    def orders(self):
        """Yield every fulfilled order, newest first, with its items expanded."""
        for page in range(MAX_PAGES):
            payload = self.fetch(
                "/v2/orders",
                {"limit": PAGE_SIZE, "offset": page * PAGE_SIZE, "expand": "order_items"},
            )
            found = payload.get("data") or []
            yield from found
            if len(found) < PAGE_SIZE:
                return
        _logger.warning("Stopped reading Printful orders after %s pages.", MAX_PAGES)
