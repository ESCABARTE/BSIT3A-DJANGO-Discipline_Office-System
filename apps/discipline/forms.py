import os
import uuid

from django import forms
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.utils import timezone

from .models import (
    CaseAction,
    CasePerson,
    CaseReport,
    CaseStage,
    CommitteeMeeting,
    DisciplinaryCase,
    DisciplinaryCommittee,
    DisciplinarySanction,
    CommitteeMember,
    OffenseCategory,
    PenaltyCatalog,
    RespondentAnswer,
    Summons,
    Violation,
)
from .permissions import is_discipline_officer


class ReportForm(forms.ModelForm):
    submission_token = forms.UUIDField(required=False, widget=forms.HiddenInput, initial=uuid.uuid4)
    respondent_name = forms.CharField(max_length=180, required=False, label='Student name (if known)')
    respondent_student_id = forms.CharField(max_length=80, required=False, label='Student ID (if known)')
    respondent_program = forms.CharField(max_length=160, required=False, label='Course / program')
    respondent_year_section = forms.CharField(max_length=120, required=False, label='Year level and section')
    witness_information = forms.CharField(required=False, widget=forms.Textarea(attrs={'rows': 2}))
    violations = forms.ModelMultipleChoiceField(
        queryset=Violation.objects.none(),
        required=False,
        widget=forms.SelectMultiple(attrs={'class': 'form-select'}),
        help_text='These are initial allegations for review, not findings of responsibility.',
    )
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
        if not user or not is_discipline_officer(user):
            self.fields.pop('violations')
        else:
            self.fields['violations'].queryset = Violation.objects.filter(active=True).select_related('category')
            self.fields['violations'].label_from_instance = lambda item: (
                f'{item.code} — {item.name} · {item.category.name} · '
                f'{item.get_classification_display() or "Unclassified"} · {item.get_severity_display()}'
                + (f' · Rule: {item.applicable_rule}' if item.applicable_rule else '')
                + (f' · Policy: {item.policy_reference}' if item.policy_reference else '')
                + (f' · Recommended: {item.recommended_penalty}' if item.recommended_penalty else '')
            )
            self.fields['violations'].widget.attrs.update({'class': 'form-select', 'size': 5})
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
        fields = ('action_type', 'to_status', 'finding', 'recommendation', 'final_sanction', 'authority', 'details')
        widgets = {
            'recommendation': forms.Textarea(attrs={'rows': 3}),
            'details': forms.Textarea(attrs={'rows': 3}),
        }

    def clean(self):
        cleaned = super().clean()
        action_type = cleaned.get('action_type')
        if action_type != CaseAction.ActionType.FINDING:
            cleaned['finding'] = ''
        if action_type != CaseAction.ActionType.STATUS:
            cleaned['to_status'] = ''
        if action_type not in (CaseAction.ActionType.DECISION, CaseAction.ActionType.SANCTION):
            cleaned['final_sanction'] = ''
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
        if action_type == CaseAction.ActionType.SANCTION:
            if not cleaned.get('final_sanction') or cleaned.get('final_sanction') == 'none':
                self.add_error('final_sanction', 'Select the specifically approved sanction.')
            if not cleaned.get('authority'):
                self.add_error('authority', 'Record the authorizing official.')
            if not cleaned.get('details'):
                self.add_error('details', 'Record the authorization and policy basis.')
        return cleaned


class OffenseCategoryForm(forms.ModelForm):
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
                'placeholder': 'e.g. Violation of Dress Code',
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
            'permitted_sanctions': forms.Textarea(attrs={
                'class': 'form-control font-monospace',
                'rows': 2,
                'placeholder': '["jug", "post_1"]',
            }),
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

    def clean_permitted_sanctions(self):
        return self.cleaned_data.get('permitted_sanctions') or []


class ViolationForm(forms.ModelForm):
    class Meta:
        model = Violation
        fields = (
            'code', 'name', 'description', 'category', 'severity',
            'classification', 'applicable_rule', 'recommended_penalty',
            'policy_reference',
        )
        widgets = {
            'description': forms.Textarea(attrs={'rows': 3}),
            'recommended_penalty': forms.Textarea(attrs={'rows': 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        categories = OffenseCategory.objects.filter(active=True)
        if self.instance.pk and self.instance.category_id:
            categories = OffenseCategory.objects.filter(
                Q(active=True) | Q(pk=self.instance.category_id),
            )
        self.fields['category'].queryset = categories
        self.fields['classification'].required = True
        for field in self.fields.values():
            field.widget.attrs.setdefault(
                'class',
                'form-select' if isinstance(field.widget, forms.Select) else 'form-control',
            )


class CaseViolationForm(forms.Form):
    violations = forms.ModelMultipleChoiceField(
        queryset=Violation.objects.none(),
        required=True,
        widget=forms.SelectMultiple(attrs={'class': 'form-select', 'size': 6}),
    )

    def __init__(self, *args, instance, **kwargs):
        self.instance = instance
        super().__init__(*args, **kwargs)
        violations = Violation.objects.filter(active=True)
        linked_ids = list(instance.violations.values_list('pk', flat=True))
        if instance.violation_id:
            linked_ids.append(instance.violation_id)
        if linked_ids:
            violations = Violation.objects.filter(Q(active=True) | Q(pk__in=linked_ids))
        self.fields['violations'].queryset = violations.select_related('category')
        self.fields['violations'].label_from_instance = lambda item: (
            f'{item.code} — {item.name} · {item.category.name} · '
            f'{item.get_classification_display() or "Unclassified"} · {item.get_severity_display()}'
            + (f' · Rule: {item.applicable_rule}' if item.applicable_rule else '')
            + (f' · Policy: {item.policy_reference}' if item.policy_reference else '')
            + (f' · Recommended: {item.recommended_penalty}' if item.recommended_penalty else '')
        )
        if not self.is_bound:
            self.initial['violations'] = linked_ids

    def save(self):
        selected = self.cleaned_data['violations']
        self.instance.violations.set(selected)
        primary = selected.first()
        self.instance.violation = primary
        self.instance.offense_category = primary.category if primary else None
        self.instance.classification_reviewed = True
        self.instance.save(update_fields=('violation', 'offense_category', 'classification_reviewed', 'updated_at'))
        return self.instance


class PenaltyCatalogForm(forms.ModelForm):
    class Meta:
        model = PenaltyCatalog
        fields = ('name', 'description', 'violation', 'duration_days', 'policy_reference')
        widgets = {'description': forms.Textarea(attrs={'rows': 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        violations = Violation.objects.filter(active=True)
        if self.instance.pk and self.instance.violation_id:
            violations = Violation.objects.filter(
                Q(active=True) | Q(pk=self.instance.violation_id),
            )
        self.fields['violation'].queryset = violations
        for field in self.fields.values():
            field.widget.attrs.setdefault(
                'class',
                'form-select' if isinstance(field.widget, forms.Select) else 'form-control',
            )


class SanctionAssignmentForm(forms.ModelForm):
    class Meta:
        model = DisciplinarySanction
        fields = ('penalty', 'assigned_date', 'due_date', 'responsible_staff')
        widgets = {
            'assigned_date': forms.DateInput(attrs={'type': 'date'}),
            'due_date': forms.DateInput(attrs={'type': 'date'}),
        }

    def __init__(self, *args, case, violation, **kwargs):
        self.case = case
        self.violation = violation
        super().__init__(*args, **kwargs)
        penalties = PenaltyCatalog.objects.filter(
            active=True,
            approved=True,
            violation=violation,
        )
        self.fields['penalty'].queryset = penalties.select_related('violation')
        self.fields['penalty'].label_from_instance = lambda item: (
            f'{item.name}'
            + (f' · {item.duration_days} day(s)' if item.duration_days else '')
            + (f' · {item.policy_reference}' if item.policy_reference else '')
            + (f' · {item.description}' if item.description else '')
        )
        self.fields['penalty'].help_text = (
            f'Approved penalties for {violation.code} — {violation.name}; '
            'the assignment will remain pending until an authorized reviewer approves it.'
        )
        self.fields['responsible_staff'].queryset = get_user_model().objects.filter(
            Q(is_active=True, is_staff=True) |
            Q(is_active=True, is_superuser=True) |
            Q(
                is_active=True,
                committee_memberships__active=True,
                committee_memberships__committee__active=True,
            )
        ).distinct()
        for field in self.fields.values():
            field.widget.attrs.setdefault(
                'class',
                'form-select' if isinstance(field.widget, forms.Select) else 'form-control',
            )

    def clean(self):
        cleaned = super().clean()
        if self.case.finding != DisciplinaryCase.Finding.SUBSTANTIATED:
            raise ValidationError('A penalty can only be assigned after a substantiated finding.')
        if not self.case.violation_id and not self.case.violations.exists():
            raise ValidationError('Assign a violation to the case before assigning a penalty.')
        penalty = cleaned.get('penalty')
        if penalty and penalty.violation_id != self.violation.pk:
            self.add_error('penalty', 'Choose an approved penalty for this case violation.')
        assigned_date = cleaned.get('assigned_date')
        due_date = cleaned.get('due_date')
        if assigned_date and due_date and due_date < assigned_date:
            self.add_error('due_date', 'The due date cannot be before the assigned date.')
        return cleaned


class SanctionStatusForm(forms.Form):
    status = forms.ChoiceField(
        choices=(
            (DisciplinarySanction.Status.PENDING, 'Pending'),
            (DisciplinarySanction.Status.IN_PROGRESS, 'In Progress'),
            (DisciplinarySanction.Status.COMPLETED, 'Completed'),
        ),
        widget=forms.Select(attrs={'class': 'form-select'}),
    )


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
