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


class ReportForm(forms.ModelForm):
    submission_token = forms.UUIDField(required=False, widget=forms.HiddenInput, initial=uuid.uuid4)
    respondent_name = forms.CharField(max_length=180, required=False, label='Student name (if known)')
    respondent_student_id = forms.CharField(max_length=80, required=False, label='Student ID (if known)')
    respondent_program = forms.CharField(max_length=160, required=False, label='Course / program')
    respondent_year_section = forms.CharField(max_length=120, required=False, label='Year level and section')
    witness_information = forms.CharField(required=False, widget=forms.Textarea(attrs={'rows': 2}))
    evidence = forms.FileField(required=False)

    class Meta:
        model = CaseReport
        fields = [
            'complainant_name', 'complainant_student_id', 'complainant_program',
            'complainant_year_section', 'reporting_position', 'respondent_name',
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
            for name in ('complainant_name', 'complainant_student_id', 'complainant_program', 'complainant_year_section'):
                self.fields[name].required = name in ('complainant_name', 'complainant_student_id')
            self.fields['reporting_position'].widget = forms.HiddenInput()
        else:
            self.fields['reporting_position'].required = True
            for name in ('complainant_name', 'complainant_student_id', 'complainant_program', 'complainant_year_section'):
                self.fields[name].widget = forms.HiddenInput()
        self.fields['evidence'].help_text = 'Optional; PDF, JPG, or PNG, up to 10 MB. Accessible only to authorized case personnel.'
        self.fields['evidence'].widget.attrs.update({'class': 'form-control', 'accept': '.pdf,.jpg,.jpeg,.png'})

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
        fields = ('action_type', 'to_status', 'finding', 'recommendation', 'authority', 'details')
        widgets = {
            'recommendation': forms.Textarea(attrs={'rows': 3}),
            'details': forms.Textarea(attrs={'rows': 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['action_type'].choices = [
            (value, label)
            for value, label in self.fields['action_type'].choices
            if value != CaseAction.ActionType.SANCTION
        ]

    def clean(self):
        cleaned = super().clean()
        action_type = cleaned.get('action_type')
        if action_type != CaseAction.ActionType.FINDING:
            cleaned['finding'] = ''
        if action_type != CaseAction.ActionType.STATUS:
            cleaned['to_status'] = ''
        if action_type != CaseAction.ActionType.RECOMMENDATION:
            cleaned['recommendation'] = ''
        if action_type == CaseAction.ActionType.FINDING and not cleaned.get('finding'):
            self.add_error('finding', 'Choose the finding established by the authorized review.')
        if action_type == CaseAction.ActionType.DECISION:
            if not cleaned.get('finding'):
                self.add_error('finding', 'Record the finding supporting this decision.')
            if not cleaned.get('authority'):
                self.add_error('authority', 'Record the decision-making authority.')
            if not cleaned.get('details'):
                self.add_error('details', 'Record the decision and its basis.')
        return cleaned


class OffenseCategoryForm(forms.ModelForm):
    class Meta:
        model = OffenseCategory
        fields = (
            'name', 'description', 'policy_reference', 'classification',
            'escalation_rules', 'review_authority',
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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['name'].required = True
        self.fields['classification'].required = True
        self.fields['description'].required = True

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
        fields = ('respondent', 'service_notes')
        widgets = {'service_notes': forms.Textarea(attrs={'rows': 2})}

    def __init__(self, *args, case, issued_by, **kwargs):
        self.case = case
        self.issued_by = issued_by
        super().__init__(*args, **kwargs)
        self.fields['respondent'].queryset = case.people.filter(role='respondent')

    def save(self, commit=True):
        summons = super().save(commit=False)
        summons.case = self.case
        summons.issued_by = self.issued_by
        if commit:
            summons.save()
        return summons


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
        if served_at and served_at.date() > timezone.localdate():
            self.add_error('served_at', 'Service date cannot be in the future.')
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
