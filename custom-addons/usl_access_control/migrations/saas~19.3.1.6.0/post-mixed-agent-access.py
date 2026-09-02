from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    agents = env["usl.agent"].with_context(active_test=False).search([])
    for agent in agents:
        read_only_groups = (
            agent.delegated_group_ids
            if agent.access_mode == "read_only"
            else env["res.groups"]
        )
        agent.with_context(usl_agent_internal=True).write(
            {
                "read_only_group_ids": [(6, 0, read_only_groups.ids)],
                "access_mode": agent._access_mode_from_groups(
                    agent.delegated_group_ids,
                    read_only_groups,
                ),
            },
        )
