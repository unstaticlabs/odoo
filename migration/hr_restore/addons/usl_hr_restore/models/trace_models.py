from odoo import models


class ResourceCalendar(models.Model):
    _name = "resource.calendar"
    _inherit = ["resource.calendar", "usl.accounting.restore.source.mixin"]


class ResourceCalendarAttendance(models.Model):
    _name = "resource.calendar.attendance"
    _inherit = ["resource.calendar.attendance", "usl.accounting.restore.source.mixin"]


class ResourceResource(models.Model):
    _name = "resource.resource"
    _inherit = ["resource.resource", "usl.accounting.restore.source.mixin"]


class HrContractType(models.Model):
    _name = "hr.contract.type"
    _inherit = ["hr.contract.type", "usl.accounting.restore.source.mixin"]


class HrDepartment(models.Model):
    _name = "hr.department"
    _inherit = ["hr.department", "usl.accounting.restore.source.mixin"]


class HrDepartureReason(models.Model):
    _name = "hr.departure.reason"
    _inherit = ["hr.departure.reason", "usl.accounting.restore.source.mixin"]


class HrJob(models.Model):
    _name = "hr.job"
    _inherit = ["hr.job", "usl.accounting.restore.source.mixin"]


class HrPayrollStructureType(models.Model):
    _name = "hr.payroll.structure.type"
    _inherit = ["hr.payroll.structure.type", "usl.accounting.restore.source.mixin"]


class HrResumeLineType(models.Model):
    _name = "hr.resume.line.type"
    _inherit = ["hr.resume.line.type", "usl.accounting.restore.source.mixin"]


class HrSkill(models.Model):
    _name = "hr.skill"
    _inherit = ["hr.skill", "usl.accounting.restore.source.mixin"]


class HrSkillLevel(models.Model):
    _name = "hr.skill.level"
    _inherit = ["hr.skill.level", "usl.accounting.restore.source.mixin"]


class HrSkillType(models.Model):
    _name = "hr.skill.type"
    _inherit = ["hr.skill.type", "usl.accounting.restore.source.mixin"]


class HrVersion(models.Model):
    _name = "hr.version"
    _inherit = ["hr.version", "usl.accounting.restore.source.mixin"]


class HrWorkLocation(models.Model):
    _name = "hr.work.location"
    _inherit = ["hr.work.location", "usl.accounting.restore.source.mixin"]


class ResPartnerBank(models.Model):
    _name = "res.partner.bank"
    _inherit = ["res.partner.bank", "usl.accounting.restore.source.mixin"]


class ResUsers(models.Model):
    _name = "res.users"
    _inherit = ["res.users", "usl.accounting.restore.source.mixin"]
