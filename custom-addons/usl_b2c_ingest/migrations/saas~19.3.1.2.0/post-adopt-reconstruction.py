"""Adopt the months the reconstruction settled, on the upgrade path.

The work itself is in the module's hooks, so installing and upgrading adopt the
same months in the same way.
"""

from odoo.addons.usl_b2c_ingest.hooks import migrate  # noqa: F401
