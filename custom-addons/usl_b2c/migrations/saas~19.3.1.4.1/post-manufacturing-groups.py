"""Give the B2C groups their Manufacturing equivalents on an installed database.

The reconstruction promotes supplier packs and assembled goods through native
Manufacturing, so a B2C operator needs the Manufacturing user group and a B2C
administrator its manager group, exactly as they already hold the Sales,
Inventory and Purchase ones.

`security/b2c_security.xml` is `noupdate`, so declaring the implication there
reaches a new database and never an existing one. Every database that matters
already exists, hence this script.
"""

IMPLICATIONS = (
    ("usl_b2c.group_b2c_operator", "mrp.group_mrp_user"),
    ("usl_b2c.group_b2c_manager", "mrp.group_mrp_manager"),
)


def migrate(cr, version):
    if not version:
        # A fresh install already carries the implication from the data file.
        return
    for holder_xmlid, implied_xmlid in IMPLICATIONS:
        holder_module, holder_name = holder_xmlid.split(".")
        implied_module, implied_name = implied_xmlid.split(".")
        cr.execute(
            """
            INSERT INTO res_groups_implied_rel (gid, hid)
            SELECT holder.res_id, implied.res_id
              FROM ir_model_data AS holder, ir_model_data AS implied
             WHERE holder.model = 'res.groups'
               AND holder.module = %s AND holder.name = %s
               AND implied.model = 'res.groups'
               AND implied.module = %s AND implied.name = %s
            ON CONFLICT DO NOTHING
            """,
            (holder_module, holder_name, implied_module, implied_name),
        )
