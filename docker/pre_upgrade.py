"""Preserve two native Mail memberships when their seed XML IDs were lost.

Odoo calls migrate before loading Base/Mail during a normal module upgrade.
Only ir_model_data may change; existing Mail records remain authoritative.
"""

# Transaction-scoped and database-local. Cooperating upgrade processes acquire
# this before row locks, in the same order, and keep it until their caller ends.
_LOCK_NAMESPACE = 0x55534C  # USL
_LOCK_RESOURCE = 0x4D41494C  # MAIL
_MEMBER_MODEL = "discuss.channel.member"
_SEEDS = (
    ("channel_member_general_channel_for_admin", "channel_all_employees"),
    ("channel_member_channel_admin_partner_admin", "channel_admin"),
)


def _refuse(reason):
    # Reasons are fixed source strings: no record IDs, names or values in logs.
    raise RuntimeError("Mail seed metadata repair refused: " + reason)


def _anchor(cr, module, name, model, table):
    cr.execute(
        "SELECT model, res_id FROM ir_model_data "
        "WHERE module=%s AND name=%s FOR SHARE",
        (module, name),
    )
    rows = cr.fetchall()
    if len(rows) != 1 or rows[0][0] != model or not rows[0][1]:
        _refuse("a native anchor is missing or malformed")
    target = rows[0][1]
    # table is a source constant, never a database or caller-provided value.
    cr.execute("SELECT id FROM " + table + " WHERE id=%s FOR KEY SHARE", (target,))
    if len(cr.fetchall()) != 1:
        _refuse("a native anchor target is absent")
    return target


def _binding(cr, name):
    cr.execute(
        "SELECT id, model, res_id, noupdate FROM ir_model_data "
        "WHERE module='mail' AND name=%s FOR UPDATE",
        (name,),
    )
    rows = cr.fetchall()
    if len(rows) > 1:
        _refuse("a seed identifier is ambiguous")
    return rows[0] if rows else None


def _plan(cr, name, partner_id, channel_id):
    binding = _binding(cr, name)
    if binding and binding[1] != _MEMBER_MODEL:
        _refuse("a seed identifier belongs to another model")
    cr.execute(
        "SELECT id FROM discuss_channel_member "
        "WHERE partner_id=%s AND channel_id=%s ORDER BY id LIMIT 2 FOR KEY SHARE",
        (partner_id, channel_id),
    )
    members = cr.fetchall()
    if len(members) > 1:
        _refuse("an expected membership is ambiguous")
    expected = members[0][0] if members else None
    if binding:
        cr.execute(
            "SELECT partner_id, channel_id FROM discuss_channel_member "
            "WHERE id=%s FOR KEY SHARE",
            (binding[2],),
        )
        existing = cr.fetchall()
        if existing:
            if existing != [(partner_id, channel_id)] or binding[2] != expected:
                _refuse("a seed identifier points to another live membership")
            return None  # Preserve healthy metadata, including its current flags.
        if expected is None:
            _refuse("a dangling seed identifier has no unique replacement")
        return (name, expected, binding)
    if expected is None:
        return None  # No existing member: leave native installation unchanged.
    return (name, expected, None)


def _apply(cr, plan):
    name, expected, binding = plan
    if binding:
        cr.execute(
            "UPDATE ir_model_data SET res_id=%s, noupdate=true "
            "WHERE id=%s AND model=%s AND res_id IS NOT DISTINCT FROM %s RETURNING id",
            (expected, binding[0], _MEMBER_MODEL, binding[2]),
        )
        if not cr.fetchone():
            _refuse("a dangling seed identifier changed concurrently")
    else:
        cr.execute(
            "INSERT INTO ir_model_data(module,name,model,res_id,noupdate) "
            "VALUES ('mail',%s,%s,%s,true) "
            "ON CONFLICT (module,name) DO NOTHING RETURNING id",
            (name, _MEMBER_MODEL, expected),
        )
        cr.fetchone()
    current = _binding(cr, name)
    if current is None or current[1:] != (_MEMBER_MODEL, expected, True):
        _refuse("a seed identifier conflicts with a concurrent change")


def migrate(cr, version):
    """Bind only uniquely identified existing native seeds, in Odoo's transaction."""
    cr.execute("SELECT to_regclass('ir_model_data'), to_regclass('discuss_channel_member')")
    metadata, members = cr.fetchone()
    if members is None:
        return  # Before Mail is installed, native creation owns both records.
    if metadata is None:
        _refuse("native identifier metadata is absent")
    cr.execute("SELECT pg_advisory_xact_lock(%s,%s)", (_LOCK_NAMESPACE, _LOCK_RESOURCE))
    # A late concurrent conflict must also undo any earlier adoption if a caller
    # catches the refusal. This is a savepoint, never an independent commit.
    with cr.savepoint(flush=False):
        partner_id = _anchor(cr, "base", "partner_admin", "res.partner", "res_partner")
        channels = {
            name: _anchor(cr, "mail", name, "discuss.channel", "discuss_channel")
            for name in sorted({channel for _, channel in _SEEDS})
        }
        plans = [
            plan
            for name, channel in _SEEDS
            if (plan := _plan(cr, name, partner_id, channels[channel])) is not None
        ]
        # Both identifiers, anchors and existing targets were validated before
        # the first write; never replay native role or seen-message defaults.
        for plan in plans:
            _apply(cr, plan)
