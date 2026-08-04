from odoo import models


class ResourceCalendar(models.Model):
    _name = "resource.calendar"
    _inherit = ["resource.calendar", "rebuild.source.trace.mixin"]


class ResourceCalendarAttendance(models.Model):
    _name = "resource.calendar.attendance"
    _inherit = ["resource.calendar.attendance", "rebuild.source.trace.mixin"]


class ResourceResource(models.Model):
    _name = "resource.resource"
    _inherit = ["resource.resource", "rebuild.source.trace.mixin"]


class HrContractType(models.Model):
    _name = "hr.contract.type"
    _inherit = ["hr.contract.type", "rebuild.source.trace.mixin"]


class HrDepartment(models.Model):
    _name = "hr.department"
    _inherit = ["hr.department", "rebuild.source.trace.mixin"]


class HrDepartureReason(models.Model):
    _name = "hr.departure.reason"
    _inherit = ["hr.departure.reason", "rebuild.source.trace.mixin"]


class HrJob(models.Model):
    _name = "hr.job"
    _inherit = ["hr.job", "rebuild.source.trace.mixin"]


class HrPayrollStructureType(models.Model):
    _name = "hr.payroll.structure.type"
    _inherit = ["hr.payroll.structure.type", "rebuild.source.trace.mixin"]


class HrResumeLineType(models.Model):
    _name = "hr.resume.line.type"
    _inherit = ["hr.resume.line.type", "rebuild.source.trace.mixin"]


class HrSkill(models.Model):
    _name = "hr.skill"
    _inherit = ["hr.skill", "rebuild.source.trace.mixin"]


class HrSkillLevel(models.Model):
    _name = "hr.skill.level"
    _inherit = ["hr.skill.level", "rebuild.source.trace.mixin"]


class HrSkillType(models.Model):
    _name = "hr.skill.type"
    _inherit = ["hr.skill.type", "rebuild.source.trace.mixin"]


class HrVersion(models.Model):
    _name = "hr.version"
    _inherit = ["hr.version", "rebuild.source.trace.mixin"]


class HrWorkLocation(models.Model):
    _name = "hr.work.location"
    _inherit = ["hr.work.location", "rebuild.source.trace.mixin"]


class ResPartnerBank(models.Model):
    _name = "res.partner.bank"
    _inherit = ["res.partner.bank", "rebuild.source.trace.mixin"]


class ResUsers(models.Model):
    _name = "res.users"
    _inherit = ["res.users", "rebuild.source.trace.mixin"]
