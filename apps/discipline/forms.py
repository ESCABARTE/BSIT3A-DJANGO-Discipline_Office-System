import os
import uuid

from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone

from .models import (
    CaseAction,
    CasePerson,
    CaseReport,
    CaseStage,
    CommitteeMeeting,
    DisciplinaryCase,
    DisciplinaryCommittee,
    CommitteeMember,
    OffenseCategory,
    RespondentAnswer,
    Summons,
)
from .permissions import is_official


def _final_authority_positions(user):
    if user.is_superuser:
        return [('System administrator', 'System administrator')]
    memberships = user.committee_memberships.filter(
        active=True,
        committee__active=True,
        role__in=('dean', 'board_member', 'president'),
    ).values_list('role', 'title')
    return list(dict.fromkeys(
        (title or CommitteeMember.Role(role).label, title or CommitteeMember.Role(role).label)
        for role, title in memberships
    ))


class ReportForm(forms.ModelForm):
    submission_token = forms.UUIDField(required=False, widget=forms.HiddenInput, initial=uuid.uuid4)
    respondent_name = forms.CharField(max_length=180, required=False, label='Student name (if known)')
    respondent_student_id = forms.CharField(max_length=80, required=False, label='Student ID (if known)')
    respondent_program = forms.CharField(max_length=160, required=False, label='Course / program')
    respondent_year_section = forms.CharField(max_length=120, required=False, label='Year level and section')
    witness_information = forms.CharField(required=False, widget=forms.Textarea(attrs={'rows': 2}))
    evidence = forms.FileField(required=False)
    reporting_method = forms.ChoiceField(
        required=False,
        choices=(('', 'Select reporting method'), ('in_person', 'In person'), ('written', 'Written'), ('online', 'Online'), ('other', 'Other')),
    )
    classification = forms.ChoiceField(required=False, choices=(('', 'Select preliminary classification'), *DisciplinaryCase.Classification.choices))
    offense_category = forms.ModelChoiceField(
        required=False,
        queryset=OffenseCategory.objects.filter(active=True).order_by('name'),
        label='Related policy',
    )
    classification_basis = forms.CharField(required=False, widget=forms.Textarea(attrs={'rows': 3}))
    classification_remarks = forms.CharField(required=False, widget=forms.Textarea(attrs={'rows': 2}))

    class Meta:
        model = CaseReport
        fields = [
            'complainant_name', 'complainant_student_id', 'complainant_program',
            'complainant_year_section', 'reporting_position', 'respondent_name',
            'complainant_relationship', 'complainant_contact', 'reporting_method',
            'respondent_student_id', 'respondent_program', 'respondent_year_section',
            'incident_at', 'incident_place', 'narration', 'witness_information',
            'acknowledgment',
        ]
        widgets = {
            'incident_at': forms.DateTimeInput(attrs={'type': 'datetime-local'}),
            'narration': forms.Textarea(attrs={'rows': 5}),
            'acknowledgment': forms.CheckboxInput(),
        }

    def __init__(self, *args, report_type, user=None, **kwargs):
        self.report_type = report_type
        super().__init__(*args, **kwargs)
        # Apply Bootstrap form-control to all widgets
        for field in self.fields.values():
            widget = field.widget
            if isinstance(widget, forms.CheckboxInput):
                widget.attrs.setdefault('class', 'form-check-input')
            elif not isinstance(widget, forms.HiddenInput):
                existing = widget.attrs.get('class', '')
                if 'form-control' not in existing and 'form-select' not in existing:
                    widget.attrs['class'] = (existing + ' form-control').strip()
        if report_type == CaseReport.ReportType.COMPLAINT:
            for name in ('complainant_name', 'complainant_student_id', 'complainant_program', 'complainant_year_section', 'complainant_relationship', 'complainant_contact'):
                self.fields[name].required = name in ('complainant_name', 'complainant_relationship')
            self.fields['reporting_method'].required = True
            self.fields['reporting_position'].widget = forms.HiddenInput()
        else:
            self.fields['reporting_position'].required = True
            for name in ('complainant_name', 'complainant_student_id', 'complainant_program', 'complainant_year_section', 'complainant_relationship', 'complainant_contact'):
                self.fields[name].widget = forms.HiddenInput()
        if not is_official(user):
            for name in ('classification', 'offense_category', 'classification_basis', 'classification_remarks'):
                self.fields.pop(name)
        self.fields['evidence'].help_text = 'Optional; PDF, JPG, or PNG, up to 10 MB. Accessible only to authorized case personnel.'
        self.fields['evidence'].widget.attrs.update({'class': 'form-control', 'accept': '.pdf,.jpg,.jpeg,.png'})

    def clean(self):
        cleaned = super().clean()
        classification = cleaned.get('classification')
        policy = cleaned.get('offense_category')
        if classification or policy:
            if not classification:
                self.add_error('classification', 'Choose a preliminary classification when selecting a policy.')
            if not policy:
                self.add_error('offense_category', 'Select the applicable active policy for this classification.')
            if not cleaned.get('classification_basis', '').strip():
                self.add_error('classification_basis', 'Record the factual basis for the preliminary classification.')
        return cleaned

    def clean_evidence(self):
        uploaded = self.cleaned_data.get('evidence')
        if not uploaded:
            return uploaded
        if uploaded.size > 10 * 1024 * 1024:
            raise ValidationError('Evidence files must be 10 MB or smaller.')
        if os.path.splitext(uploaded.name)[1].lower() not in {'.pdf', '.jpg', '.jpeg', '.png'}:
            raise ValidationError('Upload a PDF, JPG, or PNG evidence file.')
        return uploaded

    def clean_submission_token(self):
        return self.cleaned_data.get('submission_token') or uuid.uuid4()

    def clean_acknowledgment(self):
        if not self.cleaned_data.get('acknowledgment'):
            raise ValidationError('Confirm that the information submitted is accurate to the best of your knowledge.')
        return True

    def save(self, commit=True):
        report = super().save(commit=False)
        report.report_type = self.report_type
        report.submission_token = self.cleaned_data['submission_token']
        if commit:
            report.save()
            self.save_m2m()
        return report


class WorkflowActionForm(forms.ModelForm):
    class Meta:
        model = CaseAction
        fields = ('action_type', 'finding', 'recommendation', 'details')
        widgets = {
            'recommendation': forms.Textarea(attrs={'rows': 3}),
            'details': forms.Textarea(attrs={'rows': 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['action_type'].choices = [
            (value, label)
            for value, label in self.fields['action_type'].choices
            if value not in (
                CaseAction.ActionType.STATUS,
                CaseAction.ActionType.SANCTION,
                CaseAction.ActionType.DECISION,
            )
        ]

    def clean(self):
        cleaned = super().clean()
        action_type = cleaned.get('action_type')
        if action_type != CaseAction.ActionType.FINDING:
            cleaned['finding'] = ''
        if action_type != CaseAction.ActionType.RECOMMENDATION:
            cleaned['recommendation'] = ''
        if action_type == CaseAction.ActionType.FINDING and not cleaned.get('finding'):
            self.add_error('finding', 'Choose the finding established by the authorized review.')
        return cleaned


class OffenseCategoryForm(forms.ModelForm):
    decision_options = forms.CharField(
        required=False,
        label='Available decision / penalty options',
        help_text='Enter one school-approved option per line. These options appear in the case decision form; do not add unapproved outcomes.',
        widget=forms.Textarea(attrs={'rows': 4, 'class': 'form-control'}),
    )

    class Meta:
        model = OffenseCategory
        fields = (
            'name', 'description', 'policy_reference', 'classification',
            'permitted_sanctions', 'escalation_rules', 'review_authority',
            'restitution_required', 'policy_version', 'active', 'notes',
        )
        widgets = {
            'name': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'e.g. Student Handbook Policy',
            }),
            'policy_reference': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'e.g. KCC-DO-2024-001',
            }),
            'description': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 3,
                'placeholder': 'A short summary of this policy entry.',
            }),
            'classification': forms.Select(attrs={'class': 'form-select'}),
            'escalation_rules': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 3,
                'placeholder': 'Describe escalation rules or leave blank.',
            }),
            'review_authority': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'e.g. Discipline Officer / Dean',
            }),
            'policy_version': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'e.g. 2024-A or Pending school approval',
            }),
            'restitution_required': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'notes': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 2,
                'placeholder': 'Internal notes for authorized personnel.',
            }),
        }
        fields = tuple(field for field in fields if field != 'permitted_sanctions')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['name'].required = True
        self.fields['classification'].required = True
        self.fields['description'].required = True
        self.fields['decision_options'].initial = '\n'.join(self.instance.permitted_sanctions or [])

    def clean_decision_options(self):
        return list(dict.fromkeys(
            option.strip()
            for option in self.cleaned_data['decision_options'].splitlines()
            if option.strip()
        ))

    def save(self, commit=True):
        policy = super().save(commit=False)
        policy.permitted_sanctions = self.cleaned_data['decision_options']
        if commit:
            policy.save()
            self.save_m2m()
        return policy


class CaseClassificationForm(forms.ModelForm):
    change_reason = forms.CharField(
        label='Reason for classification or update',
        widget=forms.Textarea(attrs={'rows': 2}),
    )

    class Meta:
        model = DisciplinaryCase
        fields = ('classification', 'offense_category', 'classification_basis', 'classification_remarks')
        widgets = {
            'classification_basis': forms.Textarea(attrs={'rows': 3}),
            'classification_remarks': forms.Textarea(attrs={'rows': 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['offense_category'].queryset = OffenseCategory.objects.filter(active=True).order_by('name')
        self.fields['classification'].required = True
        self.fields['offense_category'].required = True
        self.fields['classification_basis'].required = True
        for field in self.fields.values():
            field.widget.attrs.setdefault('class', 'form-select' if isinstance(field.widget, forms.Select) else 'form-control')

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get('classification_basis', '').strip():
            self.add_error('classification_basis', 'Record the factual basis for the preliminary classification.')
        if not cleaned.get('change_reason', '').strip():
            self.add_error('change_reason', 'Explain why the classification is being recorded or changed.')
        return cleaned


class CaseStatusForm(forms.Form):
    status = forms.ChoiceField(label='New status')
    remarks = forms.CharField(widget=forms.Textarea(attrs={'rows': 3}))

    def __init__(self, *args, case, **kwargs):
        super().__init__(*args, **kwargs)
        transitions = {
            DisciplinaryCase.Status.SUBMITTED: (
                DisciplinaryCase.Status.INITIAL_REVIEW,
                DisciplinaryCase.Status.RETURNED,
            ),
            DisciplinaryCase.Status.INITIAL_REVIEW: (
                DisciplinaryCase.Status.RETURNED,
                DisciplinaryCase.Status.INVESTIGATION,
                DisciplinaryCase.Status.HEARING,
            ),
            DisciplinaryCase.Status.RETURNED: (DisciplinaryCase.Status.INITIAL_REVIEW,),
            DisciplinaryCase.Status.INVESTIGATION: (
                DisciplinaryCase.Status.HEARING,
                DisciplinaryCase.Status.COMMITTEE,
                DisciplinaryCase.Status.DECISION,
                DisciplinaryCase.Status.REFERRED,
            ),
            DisciplinaryCase.Status.HEARING: (
                DisciplinaryCase.Status.INVESTIGATION,
                DisciplinaryCase.Status.COMMITTEE,
                DisciplinaryCase.Status.DECISION,
                DisciplinaryCase.Status.AWAITING_GUARDIAN,
            ),
            DisciplinaryCase.Status.COMMITTEE: (
                DisciplinaryCase.Status.INVESTIGATION,
                DisciplinaryCase.Status.DECISION,
                DisciplinaryCase.Status.AWAITING_GUARDIAN,
            ),
            DisciplinaryCase.Status.DECISION: (
                DisciplinaryCase.Status.AWAITING_GUARDIAN,
            ),
            DisciplinaryCase.Status.AWAITING_GUARDIAN: (
                DisciplinaryCase.Status.HEARING,
                DisciplinaryCase.Status.DECISION,
            ),
            DisciplinaryCase.Status.DECISION_RECORDED: (
                DisciplinaryCase.Status.APPEAL,
                DisciplinaryCase.Status.RESOLVED,
            ),
            DisciplinaryCase.Status.APPEAL: (
                DisciplinaryCase.Status.DECISION,
                DisciplinaryCase.Status.RESOLVED,
            ),
            DisciplinaryCase.Status.RESOLVED: (DisciplinaryCase.Status.CLOSED,),
            DisciplinaryCase.Status.REFERRED: (DisciplinaryCase.Status.INITIAL_REVIEW,),
        }
        allowed = transitions.get(case.status, ())
        self.fields['status'].choices = [(status, DisciplinaryCase.Status(status).label) for status in allowed]
        for field in self.fields.values():
            field.widget.attrs.setdefault('class', 'form-select' if isinstance(field.widget, forms.Select) else 'form-control')

    def clean_status(self):
        status = self.cleaned_data['status']
        allowed_statuses = {value for value, _ in self.fields['status'].choices}
        if status not in allowed_statuses:
            raise ValidationError('That status transition is not allowed from the case’s current state.')
        return status

    def clean_remarks(self):
        remarks = self.cleaned_data['remarks'].strip()
        if not remarks:
            raise ValidationError('Record why the case status is changing.')
        return remarks


class CaseDecisionForm(forms.Form):
    finding = forms.ChoiceField(choices=DisciplinaryCase.Finding.choices)
    decision_outcome = forms.ChoiceField(label='Sanction / penalty or outcome')
    authority_position = forms.ChoiceField(label='Decision-maker position')
    details = forms.CharField(label='Decision and justification', widget=forms.Textarea(attrs={'rows': 4}))

    def __init__(self, *args, case, user, **kwargs):
        super().__init__(*args, **kwargs)
        positions = _final_authority_positions(user)
        self.fields['authority_position'].choices = positions
        if not self.is_bound:
            selected_position = self.initial.get('authority_position')
            valid_positions = {value for value, _ in positions}
            if selected_position not in valid_positions:
                self.initial['authority_position'] = positions[0][0] if positions else ''
        configured_options = []
        if case.offense_category_id and case.offense_category.active:
            configured_options = [
                (option, option)
                for option in case.offense_category.permitted_sanctions
            ]
        self.fields['decision_outcome'].choices = [
            ('__no_action__', 'No sanction / no further action'),
            *configured_options,
        ]
        for field in self.fields.values():
            field.widget.attrs.setdefault('class', 'form-select' if isinstance(field.widget, forms.Select) else 'form-control')

    def clean_decision_outcome(self):
        outcome = self.cleaned_data['decision_outcome']
        if outcome != '__no_action__' and not outcome.strip():
            raise ValidationError('Select a policy-configured outcome.')
        return outcome

    def clean_finding(self):
        finding = self.cleaned_data['finding']
        if finding == DisciplinaryCase.Finding.NOT_DETERMINED:
            raise ValidationError('A final decision must record a reviewed finding.')
        return finding

    def clean_details(self):
        details = self.cleaned_data['details'].strip()
        if not details:
            raise ValidationError('Record the decision and its factual basis.')
        return details


class CommitteeForm(forms.ModelForm):
    class Meta:
        model = DisciplinaryCommittee
        fields = ('name', 'academic_year', 'active')


class CommitteeMemberForm(forms.ModelForm):
    class Meta:
        model = CommitteeMember
        fields = ('user', 'role', 'title', 'active')


class CommitteeMeetingForm(forms.ModelForm):
    class Meta:
        model = CommitteeMeeting
        fields = ('title', 'purpose', 'scheduled_at', 'location', 'agenda', 'cases')
        widgets = {
            'scheduled_at': forms.DateTimeInput(attrs={'type': 'datetime-local'}),
            'agenda': forms.Textarea(attrs={'rows': 3}),
            'cases': forms.SelectMultiple(attrs={'class': 'form-select'}),
        }


class SummonsForm(forms.ModelForm):
    class Meta:
        model = Summons
        fields = (
            'respondent',
            'guardian_name',
            'guardian_relationship',
            'guardian_contact',
            'hearing_at',
            'hearing_location',
            'instructions',
            'service_notes',
        )
        widgets = {
            'hearing_at': forms.DateTimeInput(attrs={'type': 'datetime-local'}),
            'instructions': forms.Textarea(attrs={'rows': 3}),
            'service_notes': forms.Textarea(attrs={'rows': 2}),
        }

    def __init__(self, *args, case, issued_by, **kwargs):
        self.case = case
        self.issued_by = issued_by
        super().__init__(*args, **kwargs)
        self.fields['respondent'].queryset = case.people.filter(role='respondent')
        for field in ('guardian_name', 'guardian_relationship', 'hearing_at', 'hearing_location', 'instructions'):
            self.fields[field].required = True
        self.fields['service_notes'].label = 'Preparation notes'

    def clean_hearing_at(self):
        hearing_at = self.cleaned_data['hearing_at']
        if hearing_at < timezone.now():
            raise ValidationError('The hearing date and time must be in the future.')
        return hearing_at

    def save(self, commit=True):
        summons = super().save(commit=False)
        summons.case = self.case
        if not summons.pk:
            summons.issued_by = self.issued_by
        if commit:
            summons.save()
        return summons


class SummonsApprovalForm(forms.Form):
    approved_by_position = forms.ChoiceField(label='Authorized position')

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['approved_by_position'].choices = _final_authority_positions(user)


class ServiceReceiptPrintForm(forms.Form):
    acknowledgment = forms.BooleanField(required=False)


class CaseReceiptForm(forms.Form):
    received_at = forms.DateTimeField(
        label='Date and time received',
        widget=forms.DateTimeInput(attrs={'type': 'datetime-local'}),
    )
    receiving_position = forms.CharField(max_length=160)
    intake_notes = forms.CharField(required=False, widget=forms.Textarea(attrs={'rows': 3}))

    def clean_received_at(self):
        received_at = self.cleaned_data['received_at']
        if received_at > timezone.now():
            raise ValidationError('Receipt time cannot be in the future.')
        return received_at


class ServeSummonsForm(forms.ModelForm):
    class Meta:
        model = Summons
        fields = ('served_at', 'received_at', 'service_notes', 'acknowledgment')
        widgets = {
            'served_at': forms.DateTimeInput(attrs={'type': 'datetime-local'}),
            'received_at': forms.DateInput(attrs={'type': 'date'}),
            'service_notes': forms.Textarea(attrs={'rows': 2}),
        }

    def clean(self):
        cleaned = super().clean()
        received_at = cleaned.get('received_at')
        if received_at and received_at > timezone.localdate():
            self.add_error('received_at', 'Receipt date cannot be in the future.')
        if received_at and not cleaned.get('served_at'):
            self.add_error('served_at', 'Record when the summons was served.')
        served_at = cleaned.get('served_at')
        if not served_at:
            self.add_error('served_at', 'Record the actual date and time the summons was served.')
        if served_at and served_at.date() > timezone.localdate():
            self.add_error('served_at', 'Service date cannot be in the future.')
        if served_at and self.instance.issued_at and served_at < self.instance.issued_at:
            self.add_error('served_at', 'Service cannot occur before the summons was prepared.')
        if served_at and self.instance.approved_at and served_at < self.instance.approved_at:
            self.add_error('served_at', 'Service cannot occur before the summons was approved.')
        if received_at and served_at and received_at < served_at.date():
            self.add_error('received_at', 'Receipt cannot be dated before actual service.')
        if received_at and not cleaned.get('acknowledgment'):
            self.add_error('acknowledgment', 'Confirm the actual recipient acknowledgment before recording receipt.')
        return cleaned


class AnswerForm(forms.ModelForm):
    class Meta:
        model = RespondentAnswer
        fields = ('respondent', 'admissions_or_denials', 'explanation', 'defenses', 'acknowledged_electronically')
        widgets = {
            'admissions_or_denials': forms.Textarea(attrs={'rows': 3}),
            'explanation': forms.Textarea(attrs={'rows': 5}),
            'defenses': forms.Textarea(attrs={'rows': 3}),
        }

    def __init__(self, *args, case, **kwargs):
        self.case = case
        super().__init__(*args, **kwargs)
        self.fields['respondent'].queryset = case.people.filter(role='respondent')

    def clean_acknowledged_electronically(self):
        if not self.cleaned_data.get('acknowledged_electronically'):
            raise ValidationError('Record the respondent’s acknowledgment before submission.')
        return True

    def save(self, commit=True):
        answer = super().save(commit=False)
        answer.case = self.case
        summons = self.case.summonses.filter(respondent=answer.respondent, received_at__isnull=False).order_by('-received_at').first()
        if summons:
            answer.due_date_at_submission = summons.effective_due_date
            answer.received_within_deadline = answer.submitted_at.date() <= summons.effective_due_date if summons.effective_due_date else None
        if commit:
            answer.save()
        return answer


class ExtensionForm(forms.ModelForm):
    class Meta:
        model = Summons
        fields = ('extension_due', 'service_notes')
        widgets = {'extension_due': forms.DateInput(attrs={'type': 'date'}), 'service_notes': forms.Textarea(attrs={'rows': 2})}

    def clean_extension_due(self):
        due = self.cleaned_data['extension_due']
        if due < timezone.localdate():
            raise ValidationError('An extension cannot expire before today.')
        if self.instance.response_due and due < self.instance.response_due:
            raise ValidationError('An extension cannot shorten the originally recorded response deadline.')
        return due


class StageForm(forms.ModelForm):
    class Meta:
        model = CaseStage
        fields = ('stage', 'status', 'responsible', 'due_date', 'notes')
        widgets = {'due_date': forms.DateInput(attrs={'type': 'date'}), 'notes': forms.Textarea(attrs={'rows': 2})}


class ProfileSearchForm(forms.Form):
    student_id = forms.CharField(max_length=80)
