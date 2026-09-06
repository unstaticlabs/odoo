"""One-off promotion of reviewed B2C evidence into native Odoo records.

`run` owns the qualification gates and the Sales history; `inventory` owns the
purchases, receipts, unbuilds, manufacturing and deliveries; `comparison` holds
the value rules both of them prove a rerun against.
"""

from . import comparison, inventory, run
from .run import run_native_history

__all__ = ["comparison", "inventory", "run", "run_native_history"]
