"""A single native task transaction for a two-dimensional Kanban move."""
from odoo import Command, _, fields, models
from odoo.exceptions import AccessError, RedirectWarning, UserError, ValidationError
from odoo.fields import Domain


class ProjectTask(models.Model):
    _inherit = 'project.task'

    def _web_read_group_groupby_properties_formatter(self, groupby_spec, values):
        formatter = super()._web_read_group_groupby_properties_formatter(groupby_spec, values)
        if not self.env.context.get('usl_task_swimlanes'):
            return formatter
        fullname = groupby_spec.partition(':')[0]
        definition = self.get_property_definition(fullname)
        property_type = definition.get('type')
        if property_type not in {'many2one', 'many2many'}:
            return formatter
        ids = {id_ for value in values if value for id_ in (value if isinstance(value, (tuple, list)) else [value])}
        try:
            labels = {record['id']: record['display_name'] for record in self.env[definition['comodel']].with_context(active_test=False).search_read([('id', 'in', list(ids))], ['display_name'])}
        except AccessError:
            labels = {}

        def caller_formatter(value):
            if not value:
                return formatter(value)  # retain native missing/deleted-value predicates
            # These are the exact native property predicates. No date bucket or
            # property dictionary is reconstructed. Labels use only caller-readable
            # related records already returned by the native grouping.
            operator = '=' if property_type == 'many2one' else 'in'
            return (value, labels.get(value) or self.env._('Unavailable')), Domain(fullname, operator, value)

        return caller_formatter

    def action_move_kanban_cell(self, dimensions, observed_write_date=False):
        """Transfer exactly the displayed memberships, preserving all others.

        Native task write remains the access, company and assignment boundary.
        Ambiguous grouped dates/properties are readable but edited in the form.
        """
        self.ensure_one()
        self.check_access('read')
        self.check_access('write')
        if not isinstance(dimensions, list) or not 1 <= len(dimensions) <= 2:
            raise ValidationError(_('Choose at most two grouping dimensions.'))
        specs = [item.get('field') if isinstance(item, dict) else None for item in dimensions]
        if any(not isinstance(spec, str) or not spec for spec in specs) or len(set(specs)) != len(specs):
            raise ValidationError(_('Choose distinct grouping dimensions.'))
        names = [spec.split(':')[0].split('.')[0] for spec in specs]
        metadata = self.fields_get(names)
        if any(name not in metadata for name in names):
            raise ValidationError(_('This grouping is unavailable. Remove it using Group By.'))
        self.lock_for_update(allow_referencing=True)
        self.invalidate_recordset(names + ['write_date'])
        if observed_write_date and fields.Datetime.to_string(self.write_date) != observed_write_date:
            raise UserError(_('This task changed. Reload the board before moving it.'))
        changes = {}
        for item, spec, name in zip(dimensions, specs, names):
            if set(item) != {'field', 'source', 'target'}:
                raise ValidationError(_('The grouping destination is invalid.'))
            source, target = item['source'], item['target']
            # Native formatting verifies membership, including personal-stage aliases,
            # date granularity and property groups. Never trust a supplied domain.
            groups, _length = self._formatted_read_group_with_length(
                [('id', '=', self.id)], [spec], ['__count'],
            )
            def value(group):
                result = group[spec]
                return result[0] if isinstance(result, tuple) else result
            if not any(group['__count'] > 0 and value(group) == source and type(value(group)) is type(source) for group in groups):
                raise UserError(_('This task moved. Reload the board before moving it.'))
            if type(source) is type(target) and source == target:
                continue
            field = self._fields[name]
            if ':' in spec or '.' in spec or field.readonly or field.type not in {
                    'char', 'boolean', 'integer', 'selection', 'many2one', 'many2many'}:
                raise UserError(_('Edit this grouping value in the task form.'))
            if field.type in {'many2one', 'many2many'}:
                if target is not False and (type(target) is not int or target <= 0):
                    raise ValidationError(_('Choose a valid related record.'))
                if target:
                    related = self.env[field.comodel_name].search([('id', '=', target)])
                    if not related:
                        raise UserError(_('That destination is unavailable. Choose another destination.'))
                    related.read(['display_name'])
                if field.type == 'many2many':
                    current = self[name].ids
                    if target is False and set(current) != {source}:
                        raise RedirectWarning(
                            _('Other memberships remain. Edit them in the task form to leave this task unassigned.'),
                            {'type': 'ir.actions.act_window', 'res_model': 'project.task', 'res_id': self.id, 'views': [(False, 'form')], 'target': 'current'},
                            _('Open task form'),
                        )
                    changes[name] = ([Command.unlink(source)] if source else []) + ([Command.link(target)] if target else [])
                else:
                    changes[name] = target
            elif field.type == 'boolean':
                if type(target) is not bool:
                    raise ValidationError(_('Choose a valid boolean value.'))
                changes[name] = target
            elif field.type == 'integer':
                if type(target) is not int:
                    raise ValidationError(_('Choose a valid number.'))
                changes[name] = target
            elif field.type == 'selection':
                choices = {key for key, _label in field._description_selection(self.env)}
                if target is not False and target not in choices:
                    raise ValidationError(_('Choose a valid option.'))
                changes[name] = target
            else:
                if target is not False and not isinstance(target, str):
                    raise ValidationError(_('Choose a valid text value.'))
                changes[name] = target
        if changes:
            self.write(changes)
        return True
