from datetime import timedelta
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone

from .storage import PrivateEvidenceStorage


class LegacyStudent(models.Model):
    """Read-only mapping to the existing tblstudents table; migrations never manage it."""
    id = models.AutoField(primary_key=True)
    idno = models.CharField(max_length=50)
    fullname = models.CharField(max_length=255)
    courseid = models.CharField(max_length=50)
    enrollment_type = models.CharField(max_length=20)
    user_id = models.IntegerField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = 'tblstudents'

    def __str__(self):
        return f'{self.idno} — {self.fullname}'


class SchoolCalendarDay(models.Model):
    date = models.DateField(unique=True)
    is_school_day = models.BooleanField(default=False)
    label = models.CharField(max_length=160, blank=True)

    class Meta:
        ordering = ['date']

    def __str__(self):
        return f'{self.date}: {self.label or ("school day" if self.is_school_day else "closed")}'


def add_school_days(start_date, number_of_days):
    """Count school days after a receipt date; skips weekends and configured closures."""
    if number_of_days < 0:
        raise ValueError('School-day duration cannot be negative.')
    date = start_date
    counted = 0
    while counted < number_of_days:
        date += timedelta(days=1)
        calendar_day = SchoolCalendarDay.objects.filter(date=date).first()
        is_school_day = calendar_day.is_school_day if calendar_day else date.weekday() < 5
        if is_school_day:
            counted += 1
    return date


class OffenseCategory(models.Model):
    class Classification(models.TextChoices):
        JUG_POST = 'jug_post', 'Jug and/or Post review'
        SUSPENSION = 'suspension', 'Suspension review'
        SERIOUS = 'serious', 'Serious / special review'
        UNCLASSIFIED = 'unclassified', 'Unclassified pending review'

    name = models.CharField(max_length=180, unique=True)
    description = models.TextField(blank=True)
    policy_reference = models.CharField(max_length=160, blank=True)
    classification = models.CharField(max_length=20, choices=Classification.choices, default=Classification.UNCLASSIFIED)
    permitted_sanctions = models.JSONField(default=list, blank=True)
    escalation_rules = models.TextField(blank=True)
    review_authority = models.CharField(max_length=180, blank=True)
    restitution_required = models.BooleanField(default=False)
    policy_version = models.CharField(max_length=80, default='Pending school approval')
    active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']

    def save(self, *args, **kwargs):
        if self.permitted_sanctions is None:
            self.permitted_sanctions = []
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class Violation(models.Model):
    class Classification(models.TextChoices):
        MINOR = 'minor', 'Minor'
        MAJOR = 'major', 'Major'
        GRAVE = 'grave', 'Grave'

    class Severity(models.TextChoices):
        LOW = 'low', 'Low'
        MODERATE = 'moderate', 'Moderate'
        HIGH = 'high', 'High'
        CRITICAL = 'critical', 'Critical'

    code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=180)
    description = models.TextField()
    severity = models.CharField(max_length=12, choices=Severity.choices)
    classification = models.CharField(max_length=8, choices=Classification.choices, blank=True, default='')
    applicable_rule = models.CharField(max_length=180, blank=True)
    recommended_penalty = models.TextField(blank=True)
    policy_reference = models.CharField(max_length=160, blank=True)
    active = models.BooleanField(default=True)
    category = models.ForeignKey(OffenseCategory, on_delete=models.PROTECT, related_name='violations')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='violations_created')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['code']

    def __str__(self):
        return f'{self.code} — {self.name}'


class ViolationAuditLog(models.Model):
    violation = models.ForeignKey(Violation, on_delete=models.PROTECT, related_name='audit_logs')
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='violation_audit_logs')
    action = models.CharField(max_length=40)
    details = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at', '-id']


class DisciplinaryCase(models.Model):
    class Status(models.TextChoices):
        SUBMITTED = 'submitted', 'Submitted'
        INITIAL_REVIEW = 'initial_review', 'Under initial review'
        RETURNED = 'returned', 'Returned for completion'
        INVESTIGATION = 'investigation', 'Accepted for investigation'
        HEARING = 'hearing', 'Hearing / evidence review'
        COMMITTEE = 'committee', 'Awaiting committee action'
        DECISION = 'decision', 'Awaiting final decision'
        DECISION_RECORDED = 'decision_recorded', 'Decision recorded'
        AWAITING_GUARDIAN = 'guardian_hearing', 'Awaiting parent / guardian hearing'
        APPEAL = 'appeal', 'Appeal / reconsideration'
        RESOLVED = 'resolved', 'Resolved'
        CLOSED = 'closed', 'Closed'
        REFERRED = 'referred', 'Referred'

    class Classification(models.TextChoices):
        MINOR = 'minor', 'Minor'
        MAJOR = 'major', 'Major'
        SERIOUS = 'serious', 'Serious'
        EXPULSION_REVIEW = 'expulsion_review', 'Expulsion-level review'

    class Finding(models.TextChoices):
        NOT_DETERMINED = 'not_determined', 'Not determined'
        NOT_SUBSTANTIATED = 'not_substantiated', 'Not substantiated'
        SUBSTANTIATED = 'substantiated', 'Substantiated'

    class Sanction(models.TextChoices):
        NONE = 'none', 'No sanction decided'
        JUG = 'jug', 'Jug'
        POST_1 = 'post_1', 'One-hour Post'
        POST_2 = 'post_2', 'Two-hour Post'
        SUSPENSION = 'suspension', 'Suspension'
        PROBATION = 'probation', 'Disciplinary probation'
        DISMISSAL = 'dismissal', 'Dismissal / expulsion review'
        OTHER = 'other', 'Other (record details)'

    reference = models.CharField(max_length=32, unique=True, editable=False)
    source_type = models.CharField(max_length=20, choices=[('complaint', 'Student complaint'), ('incident', 'Official incident report')])
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.SUBMITTED, db_index=True)
    academic_year = models.CharField(max_length=40, blank=True)
    finding = models.CharField(max_length=20, choices=Finding.choices, default=Finding.NOT_DETERMINED)
    policy_version = models.CharField(max_length=80, default='Pending school approval')
    opened_at = models.DateTimeField(default=timezone.now, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    assigned_officer = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='discipline_cases_assigned')
    classification = models.CharField(max_length=24, choices=Classification.choices, blank=True)
    classification_remarks = models.TextField(blank=True)
    classification_basis = models.TextField(blank=True)
    classified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='discipline_cases_classified',
    )
    classified_at = models.DateTimeField(null=True, blank=True)
    classification_reviewed = models.BooleanField(default=False)
    offense_category = models.ForeignKey(OffenseCategory, null=True, blank=True, on_delete=models.PROTECT, related_name='cases')
    violation = models.ForeignKey(Violation, null=True, blank=True, on_delete=models.PROTECT, related_name='cases')
    violations = models.ManyToManyField(Violation, blank=True, related_name='linked_cases')
    offense_description = models.TextField(blank=True)
    recommendation = models.TextField(blank=True)
    final_sanction = models.CharField(max_length=20, choices=Sanction.choices, default=Sanction.NONE)
    sanction_details = models.TextField(blank=True)
    decision_authority = models.CharField(max_length=160, blank=True)
    decision_date = models.DateField(null=True, blank=True)
    decision_notes = models.TextField(blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    def save(self, *args, **kwargs):
        if not self.reference:
            self.reference = f'DO-{timezone.now():%Y%m%d}-{uuid.uuid4().hex[:8].upper()}'
        super().save(*args, **kwargs)

    def __str__(self):
        return self.reference


class PenaltyCatalog(models.Model):
    name = models.CharField(max_length=160)
    description = models.TextField(blank=True)
    duration_days = models.PositiveSmallIntegerField(null=True, blank=True)
    policy_reference = models.CharField(max_length=160, blank=True)
    violation = models.ForeignKey(Violation, on_delete=models.PROTECT, related_name='penalties')
    active = models.BooleanField(default=True)
    approved = models.BooleanField(default=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='penalties_created')
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
        related_name='penalties_approved',
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
        constraints = [
            models.UniqueConstraint(fields=('name', 'violation'), name='unique_penalty_for_violation'),
        ]

    def clean(self):
        if self.approved and (not self.approved_by_id or not self.approved_at):
            raise ValidationError('An approved penalty must include its approving authority and timestamp.')

    def __str__(self):
        return f'{self.name} — {self.violation.code}'


class PenaltyAuditLog(models.Model):
    penalty = models.ForeignKey(PenaltyCatalog, on_delete=models.PROTECT, related_name='audit_logs')
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='penalty_audit_logs')
    action = models.CharField(max_length=40)
    details = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at', '-id']


class DisciplinarySanction(models.Model):
    class ReviewStatus(models.TextChoices):
        PENDING = 'pending', 'Pending authorized review'
        APPROVED = 'approved', 'Approved'
        REJECTED = 'rejected', 'Rejected'

    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        IN_PROGRESS = 'in_progress', 'In Progress'
        COMPLETED = 'completed', 'Completed'
        OVERDUE = 'overdue', 'Overdue'
        CANCELLED = 'cancelled', 'Cancelled'

    case = models.ForeignKey('DisciplinaryCase', on_delete=models.PROTECT, related_name='sanctions')
    violation = models.ForeignKey(Violation, on_delete=models.PROTECT, related_name='sanctions')
    penalty = models.ForeignKey(
        PenaltyCatalog, null=True, blank=True, on_delete=models.PROTECT, related_name='assignments',
    )
    name = models.CharField(max_length=160)
    description = models.TextField(blank=True)
    duration_days = models.PositiveSmallIntegerField(null=True, blank=True)
    assigned_date = models.DateField(default=timezone.localdate)
    due_date = models.DateField(null=True, blank=True)
    responsible_staff = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='disciplinary_sanctions_assigned',
    )
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='disciplinary_sanctions_created',
    )
    review_status = models.CharField(max_length=12, choices=ReviewStatus.choices, default=ReviewStatus.PENDING)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
        related_name='disciplinary_sanctions_reviewed',
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_notes = models.TextField(blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-assigned_date', '-id']

    def clean(self):
        if self.due_date and self.due_date < self.assigned_date:
            raise ValidationError({'due_date': 'The due date cannot be before the assigned date.'})
        if self.case_id and self.violation_id and self.case.violation_id and self.case.violation_id != self.violation_id:
            raise ValidationError({'violation': 'The sanction violation must match the case violation.'})
        if self.penalty_id and self.violation_id and self.penalty.violation_id != self.violation_id:
            raise ValidationError({'penalty': 'The approved penalty must match the selected violation.'})
        if self.review_status == self.ReviewStatus.APPROVED and not self.reviewed_by_id:
            raise ValidationError({'reviewed_by': 'An authorized reviewer is required to approve a sanction.'})
        if self.status == self.Status.COMPLETED and not self.completed_at:
            raise ValidationError({'completed_at': 'Record when the sanction was completed.'})

    @property
    def effective_status(self):
        if (
            self.review_status == self.ReviewStatus.APPROVED
            and self.status in (self.Status.PENDING, self.Status.IN_PROGRESS)
            and self.due_date and self.due_date < timezone.localdate()
        ):
            return self.Status.OVERDUE
        return self.status

    @property
    def effective_status_display(self):
        return self.Status(self.effective_status).label


class CaseReport(models.Model):
    class ReportType(models.TextChoices):
        COMPLAINT = 'complaint', 'Student complaint'
        INCIDENT = 'incident', 'Official incident report'

    case = models.OneToOneField(DisciplinaryCase, on_delete=models.CASCADE, related_name='report')
    submission_token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    report_type = models.CharField(max_length=20, choices=ReportType.choices)
    submitted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='discipline_reports_submitted')
    complainant_name = models.CharField(max_length=180, blank=True)
    complainant_student_id = models.CharField(max_length=80, blank=True)
    complainant_program = models.CharField(max_length=160, blank=True)
    complainant_year_section = models.CharField(max_length=120, blank=True)
    complainant_relationship = models.CharField(max_length=120, blank=True)
    complainant_contact = models.CharField(max_length=180, blank=True)
    reporting_method = models.CharField(max_length=80, blank=True)
    respondent_name = models.CharField(max_length=180, blank=True)
    respondent_student_id = models.CharField(max_length=80, blank=True)
    respondent_program = models.CharField(max_length=160, blank=True)
    respondent_year_section = models.CharField(max_length=120, blank=True)
    incident_at = models.DateTimeField(null=True, blank=True)
    incident_place = models.CharField(max_length=180, blank=True)
    narration = models.TextField()
    witness_information = models.TextField(blank=True)
    reporting_position = models.CharField(max_length=160, blank=True)
    acknowledgment = models.BooleanField(default=False)
    submitted_at = models.DateTimeField(default=timezone.now)
    received_at = models.DateTimeField(null=True, blank=True)
    received_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='discipline_reports_received',
    )
    receiving_position = models.CharField(max_length=160, blank=True)
    intake_notes = models.TextField(blank=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='discipline_reports_reviewed')
    review_notes = models.TextField(blank=True)

    def clean(self):
        if self.report_type == self.ReportType.COMPLAINT and not self.complainant_name:
            raise ValidationError({'complainant_name': 'A complainant name is required.'})
        if self.report_type == self.ReportType.INCIDENT and not self.reporting_position:
            raise ValidationError({'reporting_position': 'A reporting official position is required.'})

    def __str__(self):
        return f'{self.case.reference} report'


class CasePerson(models.Model):
    class Role(models.TextChoices):
        RESPONDENT = 'respondent', 'Respondent'
        WITNESS = 'witness', 'Witness'
        PARENT = 'parent', 'Parent / guardian'

    case = models.ForeignKey(DisciplinaryCase, on_delete=models.CASCADE, related_name='people')
    role = models.CharField(max_length=30, choices=Role.choices)
    name = models.CharField(max_length=180)
    student_id = models.CharField(max_length=80, blank=True)
    account = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='discipline_case_persons')
    program = models.CharField(max_length=160, blank=True)
    year_section = models.CharField(max_length=120, blank=True)
    contact_details = models.CharField(max_length=180, blank=True)

    def __str__(self):
        return f'{self.name} ({self.get_role_display()})'


class CaseDocument(models.Model):
    case = models.ForeignKey(DisciplinaryCase, on_delete=models.CASCADE, related_name='documents')
    document_type = models.CharField(max_length=40, choices=[('evidence', 'Evidence'), ('summons', 'Summons'), ('answer', 'Respondent answer'), ('decision', 'Decision'), ('minutes', 'Committee minutes'), ('other', 'Other')])
    file = models.FileField(upload_to='discipline/restricted/', storage=PrivateEvidenceStorage())
    original_name = models.CharField(max_length=255)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.original_name


class Summons(models.Model):
    class ApprovalStatus(models.TextChoices):
        PENDING = 'pending', 'Pending approval'
        APPROVED = 'approved', 'Approved'

    reference = models.CharField(max_length=32, unique=True)
    case = models.ForeignKey(DisciplinaryCase, on_delete=models.CASCADE, related_name='summonses')
    respondent = models.ForeignKey(CasePerson, on_delete=models.PROTECT, related_name='summonses')
    issued_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='summonses_issued')
    issued_at = models.DateTimeField(default=timezone.now)
    guardian_name = models.CharField(max_length=180, blank=True)
    guardian_relationship = models.CharField(max_length=120, blank=True)
    guardian_contact = models.CharField(max_length=180, blank=True)
    hearing_at = models.DateTimeField(null=True, blank=True)
    hearing_location = models.CharField(max_length=180, blank=True)
    instructions = models.TextField(blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
        related_name='summonses_approved',
    )
    approved_by_position = models.CharField(max_length=180, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    served_at = models.DateTimeField(null=True, blank=True)
    received_at = models.DateField(null=True, blank=True)
    service_recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
        related_name='summons_service_records',
    )
    response_due = models.DateField(null=True, blank=True)
    extension_due = models.DateField(null=True, blank=True)
    service_notes = models.TextField(blank=True)
    acknowledgment = models.BooleanField(default=False)

    def save(self, *args, **kwargs):
        if not self.reference:
            self.reference = f'SUM-{timezone.now():%Y%m%d}-{uuid.uuid4().hex[:8].upper()}'
        if self.received_at:
            self.response_due = add_school_days(self.received_at, 3)
        else:
            self.response_due = None
        super().save(*args, **kwargs)

    @property
    def effective_due_date(self):
        return self.extension_due or self.response_due

    @property
    def deadline_state(self):
        due = self.effective_due_date
        if not due:
            return 'not_served'
        if timezone.localdate() > due:
            return 'past_due_review_required'
        if (due - timezone.localdate()).days <= 1:
            return 'due_soon'
        return 'upcoming'


class RespondentAnswer(models.Model):
    case = models.ForeignKey(DisciplinaryCase, on_delete=models.CASCADE, related_name='answers')
    respondent = models.ForeignKey(CasePerson, on_delete=models.PROTECT, related_name='answers')
    admissions_or_denials = models.TextField()
    explanation = models.TextField()
    defenses = models.TextField(blank=True)
    submitted_at = models.DateTimeField(default=timezone.now)
    received_at = models.DateTimeField(default=timezone.now)
    acknowledged_electronically = models.BooleanField(default=False)
    received_within_deadline = models.BooleanField(null=True, blank=True)
    due_date_at_submission = models.DateField(null=True, blank=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='discipline_answers_reviewed')
    forwarded_to_dean_at = models.DateTimeField(null=True, blank=True)


class CaseStage(models.Model):
    case = models.ForeignKey(DisciplinaryCase, on_delete=models.CASCADE, related_name='stages')
    stage = models.CharField(max_length=50)
    status = models.CharField(max_length=30, default='pending')
    responsible = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    due_date = models.DateField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='discipline_stages_created')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at', 'id']


class CaseAction(models.Model):
    class ActionType(models.TextChoices):
        STATUS = 'status', 'Workflow status'
        FINDING = 'finding', 'Finding'
        RECOMMENDATION = 'recommendation', 'Recommendation'
        DECISION = 'decision', 'Final decision'
        SANCTION = 'sanction', 'Approved sanction'
        APPEAL = 'appeal', 'Appeal / reconsideration'
        NOTE = 'note', 'Case note'
        REFERRAL = 'referral', 'Referral'

    case = models.ForeignKey(DisciplinaryCase, on_delete=models.CASCADE, related_name='actions')
    action_type = models.CharField(max_length=20, choices=ActionType.choices)
    from_status = models.CharField(max_length=20, choices=DisciplinaryCase.Status.choices, blank=True)
    to_status = models.CharField(max_length=20, choices=DisciplinaryCase.Status.choices, blank=True)
    finding = models.CharField(max_length=20, choices=DisciplinaryCase.Finding.choices, blank=True)
    recommendation = models.TextField(blank=True)
    final_sanction = models.CharField(max_length=20, choices=DisciplinaryCase.Sanction.choices, blank=True)
    decision_outcome = models.CharField(max_length=180, blank=True)
    authority = models.CharField(max_length=180, blank=True)
    authority_position = models.CharField(max_length=180, blank=True)
    details = models.TextField(blank=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='discipline_actions')
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at', '-id']


class CaseAuditLog(models.Model):
    case = models.ForeignKey(DisciplinaryCase, on_delete=models.PROTECT, related_name='audit_logs')
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='discipline_audit_logs')
    action = models.CharField(max_length=40)
    details = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at', '-id']


class DisciplinaryCommittee(models.Model):
    name = models.CharField(max_length=180, default='Disciplinary Committee')
    academic_year = models.CharField(max_length=40)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [('name', 'academic_year')]
        ordering = ['-academic_year', 'name']

    def __str__(self):
        return f'{self.name} — {self.academic_year}'


class CommitteeMember(models.Model):
    class Role(models.TextChoices):
        DISCIPLINE_OFFICER = 'discipline_officer', 'Discipline Officer / Chairperson'
        DEAN = 'dean', 'Dean'
        PROGRAM_CHAIR = 'program_chair', 'Program Chair'
        SSAO = 'ssao_coordinator', 'SSAO Coordinator'
        CLASS_ADVISER = 'class_adviser', 'Class Adviser'
        COMMITTEE = 'committee_member', 'Committee member'
        BOARD = 'board_member', 'Disciplinary Board member'
        PRESIDENT = 'president', 'President'
        AUDITOR = 'read_only_auditor', 'Read-only auditor'

    committee = models.ForeignKey(DisciplinaryCommittee, on_delete=models.CASCADE, related_name='members')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='committee_memberships')
    role = models.CharField(max_length=30, choices=Role.choices)
    title = models.CharField(max_length=180, blank=True)
    active = models.BooleanField(default=True)
    assigned_at = models.DateField(default=timezone.localdate)

    class Meta:
        unique_together = [('committee', 'user', 'role')]

    def __str__(self):
        return f'{self.user} — {self.get_role_display()}'


class CommitteeMeeting(models.Model):
    committee = models.ForeignKey(DisciplinaryCommittee, on_delete=models.CASCADE, related_name='meetings')
    title = models.CharField(max_length=180)
    purpose = models.CharField(max_length=40, choices=[('policy_review', 'Policy review'), ('case_review', 'Case review'), ('other', 'Other')], default='other')
    scheduled_at = models.DateTimeField()
    location = models.CharField(max_length=180, blank=True)
    agenda = models.TextField(blank=True)
    minutes = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    cases = models.ManyToManyField(DisciplinaryCase, blank=True, related_name='committee_meetings')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-scheduled_at']


class MeetingAttendance(models.Model):
    meeting = models.ForeignKey(CommitteeMeeting, on_delete=models.CASCADE, related_name='attendance')
    member = models.ForeignKey(CommitteeMember, on_delete=models.PROTECT)
    attended = models.BooleanField(default=False)
    notes = models.CharField(max_length=255, blank=True)

    class Meta:
        unique_together = [('meeting', 'member')]


class ProbationRecord(models.Model):
    case = models.ForeignKey(DisciplinaryCase, on_delete=models.PROTECT, related_name='probations')
    student_id = models.CharField(max_length=80)
    student_name = models.CharField(max_length=180)
    start_date = models.DateField()
    duration_school_days = models.PositiveSmallIntegerField(validators=[MinValueValidator(1)])
    end_date = models.DateField()
    conditions = models.TextField()
    monitoring_officer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='probations_monitored')
    approved_by = models.CharField(max_length=180)
    approved_at = models.DateTimeField(default=timezone.now)
    active = models.BooleanField(default=True)
    review_notes = models.TextField(blank=True)

    def clean(self):
        if not 60 <= self.duration_school_days <= 100:
            raise ValidationError({'duration_school_days': 'Probation duration must be 60–100 school days, subject to approved school policy.'})


class ProbationReview(models.Model):
    probation = models.ForeignKey(ProbationRecord, on_delete=models.PROTECT, related_name='reviews')
    reviewer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    reviewed_at = models.DateTimeField(default=timezone.now)
    compliance_status = models.CharField(max_length=30, choices=[('compliant', 'Compliant'), ('concern', 'Concern reported'), ('alleged_breach', 'Alleged breach — not a finding')])
    notes = models.TextField()


class JugPostAssignment(models.Model):
    class Type(models.TextChoices):
        JUG = 'jug', 'Jug'
        POST_1 = 'post_1', 'One-hour Post'
        POST_2 = 'post_2', 'Two-hour Post'

    case = models.ForeignKey(DisciplinaryCase, on_delete=models.PROTECT, related_name='assignments')
    student_id = models.CharField(max_length=80)
    student_name = models.CharField(max_length=180)
    assignment_type = models.CharField(max_length=12, choices=Type.choices)
    assigned_for = models.DateTimeField()
    task = models.TextField()
    supervisor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='discipline_assignments_supervised')
    authorized_by = models.CharField(max_length=180)
    authorized_at = models.DateTimeField(default=timezone.now)
    attended = models.BooleanField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    deferment_reason = models.TextField(blank=True)
    deferment_approved_by = models.CharField(max_length=180, blank=True)
    follow_up_review = models.TextField(blank=True)


class StudentSuspension(models.Model):
    case = models.ForeignKey(DisciplinaryCase, on_delete=models.PROTECT, related_name='suspensions')
    student_id = models.CharField(max_length=80)
    student_name = models.CharField(max_length=180)
    decision_authority = models.CharField(max_length=180)
    approved_at = models.DateTimeField(default=timezone.now)
    starts_on = models.DateField()
    school_days = models.PositiveSmallIntegerField(validators=[MinValueValidator(1)])
    ends_on = models.DateField()
    parent_notified_at = models.DateTimeField(null=True, blank=True)
    return_conditions = models.TextField(blank=True)
    completed = models.BooleanField(default=False)


class CaseAppeal(models.Model):
    case = models.ForeignKey(DisciplinaryCase, on_delete=models.PROTECT, related_name='appeals')
    authority = models.CharField(max_length=180)
    appeal_type = models.CharField(max_length=30, choices=[('reconsideration', 'Reconsideration'), ('appeal', 'Appeal')])
    submitted_at = models.DateTimeField(default=timezone.now)
    due_date = models.DateField(null=True, blank=True)
    grounds = models.TextField()
    status = models.CharField(max_length=30, default='submitted')
    decision = models.TextField(blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    suspension_during_appeal_confirmed = models.BooleanField(default=False)


class KnightsMember(models.Model):
    class Role(models.TextChoices):
        DISCIPLINE_OFFICER = 'discipline_officer', 'Discipline Officer'
        STUDENT_MEMBER = 'student_member', 'Student member'
        STUDENT_AGENT = 'student_agent', 'Student agent'

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='knights_memberships')
    student_id = models.CharField(max_length=80)
    student_name = models.CharField(max_length=180)
    role = models.CharField(max_length=24, choices=Role.choices, default=Role.STUDENT_MEMBER)
    academic_year = models.CharField(max_length=40)
    active = models.BooleanField(default=True)
    joined_at = models.DateField(default=timezone.localdate)
    ended_at = models.DateField(null=True, blank=True)
    responsibilities = models.TextField(blank=True)

    class Meta:
        unique_together = [('user', 'academic_year')]


class KnightsActivity(models.Model):
    title = models.CharField(max_length=180)
    activity_type = models.CharField(max_length=40, choices=[('campaign', 'Awareness campaign'), ('patrol', 'Campus monitoring'), ('meeting', 'Meeting'), ('other', 'Other')])
    academic_year = models.CharField(max_length=40)
    scheduled_at = models.DateTimeField()
    description = models.TextField(blank=True)
    accomplishment_report = models.TextField(blank=True)
    members = models.ManyToManyField(KnightsMember, blank=True, related_name='activities')
    submitted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='knights_activities_reviewed')
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class KnightsObservation(models.Model):
    class Status(models.TextChoices):
        SUBMITTED = 'submitted', 'Awaiting Discipline Officer review'
        REVIEWED = 'reviewed', 'Reviewed'
        REFERRED = 'referred', 'Referred for official review'

    submitted_by = models.ForeignKey(KnightsMember, on_delete=models.PROTECT, related_name='observations')
    submitted_at = models.DateTimeField(default=timezone.now)
    observed_at = models.DateTimeField(null=True, blank=True)
    location = models.CharField(max_length=180, blank=True)
    narration = models.TextField()
    student_identifiers = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.SUBMITTED)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    review_notes = models.TextField(blank=True)
    formal_case = models.ForeignKey(DisciplinaryCase, null=True, blank=True, on_delete=models.SET_NULL, related_name='knights_observations')


class CaseNotification(models.Model):
    case = models.ForeignKey(DisciplinaryCase, on_delete=models.CASCADE, related_name='notifications')
    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    title = models.CharField(max_length=180)
    message = models.TextField()
    due_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    delivery_status = models.CharField(max_length=20, default='pending')

    class Meta:
        ordering = ['-sent_at', '-id']
