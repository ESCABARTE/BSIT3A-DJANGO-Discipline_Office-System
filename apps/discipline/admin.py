from django.contrib import admin

from .models import (
    CaseAction,
    CaseAuditLog,
    CaseDocument,
    CasePerson,
    CaseReport,
    CaseStage,
    CommitteeMeeting,
    CommitteeMember,
    DisciplinaryCase,
    DisciplinaryCommittee,
    JugPostAssignment,
    KnightsActivity,
    KnightsMember,
    KnightsObservation,
    MeetingAttendance,
    OffenseCategory,
    ProbationRecord,
    ProbationReview,
    RespondentAnswer,
    SchoolCalendarDay,
    StudentSuspension,
    Summons,
    CaseAppeal,
)


@admin.register(DisciplinaryCase)
class DisciplinaryCaseAdmin(admin.ModelAdmin):
    list_display = ('reference', 'source_type', 'status', 'finding', 'opened_at', 'assigned_officer')
    list_filter = ('source_type', 'status', 'finding', 'opened_at')
    search_fields = ('reference', 'report__respondent_name', 'report__respondent_student_id', 'report__complainant_name')
    readonly_fields = ('reference', 'opened_at', 'updated_at', 'closed_at')


@admin.register(OffenseCategory)
class OffenseCategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'classification', 'policy_version', 'active')
    list_filter = ('classification', 'active', 'policy_version')
    search_fields = ('name', 'description', 'policy_reference')


@admin.register(CaseReport)
class CaseReportAdmin(admin.ModelAdmin):
    list_display = ('case', 'report_type', 'submitted_by', 'submitted_at', 'reviewed_by')
    list_filter = ('report_type', 'submitted_at')
    search_fields = ('case__reference', 'complainant_name', 'respondent_name', 'respondent_student_id')
    readonly_fields = ('submitted_at',)


for model in (
    CaseAction, CaseAuditLog, CasePerson, CaseStage, CommitteeMeeting,
    CommitteeMember, DisciplinaryCommittee, JugPostAssignment, KnightsActivity,
    KnightsMember, KnightsObservation, MeetingAttendance, ProbationRecord,
    ProbationReview, RespondentAnswer, SchoolCalendarDay, StudentSuspension,
    Summons, CaseAppeal,
):
    admin.site.register(model)
