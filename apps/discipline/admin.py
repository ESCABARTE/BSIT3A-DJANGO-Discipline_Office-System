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
    exclude = (
        'classification_reviewed',
        'offense_category',
        'violation',
        'violations',
        'offense_description',
        'final_sanction',
        'sanction_details',
    )


@admin.register(CaseAction)
class CaseActionAdmin(admin.ModelAdmin):
    exclude = ('final_sanction',)

    def formfield_for_choice_field(self, db_field, request, **kwargs):
        if db_field.name == 'action_type':
            kwargs['choices'] = [
                (value, label)
                for value, label in db_field.choices
                if value != CaseAction.ActionType.SANCTION
            ]
        return super().formfield_for_choice_field(db_field, request, **kwargs)


@admin.register(OffenseCategory)
class OffenseCategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'classification', 'policy_version', 'active')
    list_filter = ('classification', 'active', 'policy_version')
    search_fields = ('name', 'description', 'policy_reference')
    exclude = ('permitted_sanctions',)


@admin.register(CaseReport)
class CaseReportAdmin(admin.ModelAdmin):
    list_display = ('case', 'report_type', 'submitted_by', 'submitted_at', 'reviewed_by')
    list_filter = ('report_type', 'submitted_at')
    search_fields = ('case__reference', 'complainant_name', 'respondent_name', 'respondent_student_id')
    readonly_fields = ('submitted_at',)


for model in (
    CaseAuditLog, CasePerson, CaseStage, CommitteeMeeting,
    CommitteeMember, DisciplinaryCommittee, JugPostAssignment, KnightsActivity,
    KnightsMember, KnightsObservation, MeetingAttendance, ProbationRecord,
    ProbationReview, RespondentAnswer, SchoolCalendarDay, StudentSuspension,
    Summons, CaseAppeal,
):
    admin.site.register(model)
