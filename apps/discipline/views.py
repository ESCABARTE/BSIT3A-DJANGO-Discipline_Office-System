import os

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import IntegrityError, connection, transaction
from django.db.models import Q
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from .forms import (
    AnswerForm,
    CaseClassificationForm,
    CaseDecisionForm,
    CaseLevelForm,
    CaseReceiptForm,
    CaseStatusForm,
    CommitteeForm,
    CommitteeMeetingForm,
    CommitteeMemberForm,
    ExtensionForm,
    OffenseCategoryForm,
    ProfileSearchForm,
    ReportForm,
    ReviewCaseForm,
    ServeSummonsForm,
    SummonsApprovalForm,
    StageForm,
    SummonsForm,
    WorkflowActionForm,
)
from .models import (
    CaseAction,
    CaseAuditLog,
    CaseDocument,
    CasePerson,
    CaseReport,
    CaseStage,
    CommitteeMeeting,
    DisciplinaryCase,
    DisciplinaryCommittee,
    JugPostAssignment,
    KnightsMember,
    KnightsObservation,
    LegacyStudent,
    OffenseCategory,
    ProbationRecord,
    SchoolCalendarDay,
    StudentSuspension,
    Summons,
)
from .permissions import (
    can_decide,
    can_manage_confidential,
    can_manage_policy,
    can_finalize_decision,
    committee_roles,
    is_discipline_officer,
    is_official,
    require_decision_authority,
    require_final_decision_authority,
    require_officer,
    require_official,
    require_policy_admin,
    visible_cases,
)


def _navigation_context(request):
    authorized = is_official(request.user)
    return {'is_authorized_official': authorized, 'can_access_case_register': authorized}


CASE_STATUS_GROUPS = {
    'pending': (
        DisciplinaryCase.Status.SUBMITTED,
        DisciplinaryCase.Status.INITIAL_REVIEW,
        DisciplinaryCase.Status.RETURNED,
    ),
    'submitted': (DisciplinaryCase.Status.SUBMITTED,),
    'review': (
        DisciplinaryCase.Status.INITIAL_REVIEW,
        DisciplinaryCase.Status.INVESTIGATION,
        DisciplinaryCase.Status.HEARING,
        DisciplinaryCase.Status.COMMITTEE,
    ),
    'decision': (
        DisciplinaryCase.Status.DECISION,
        DisciplinaryCase.Status.APPEAL,
    ),
    'resolved': (
        DisciplinaryCase.Status.DECISION_RECORDED,
        DisciplinaryCase.Status.RESOLVED,
        DisciplinaryCase.Status.CLOSED,
        DisciplinaryCase.Status.REFERRED,
    ),
}


def _case_visible_or_404(request, pk):
    return get_object_or_404(visible_cases(request.user), pk=pk)


def _record_case_access(request, case, action):
    return CaseAuditLog.objects.create(case=case, actor=request.user, action=action)


def _user_position(user):
    if user.is_superuser:
        return 'System administrator'
    membership = user.committee_memberships.filter(
        active=True, committee__active=True,
    ).order_by('assigned_at').first()
    if not membership:
        return ''
    return membership.title or membership.get_role_display()


def _report_case(request, report_type):
    if report_type == CaseReport.ReportType.INCIDENT:
        require_official(request.user)
    form = ReportForm(
        request.POST or None,
        request.FILES or None,
        report_type=report_type,
        user=request.user,
    )
    if request.method == 'POST' and form.is_valid():
        case = _create_report_case(request, form, report_type)
        if case is None:
            return render(request, 'discipline/intake.html', {
                'form': form,
                'report_type': report_type,
                'can_file_incident': is_official(request.user),
                **_navigation_context(request),
            })
        messages.success(request, f'Report submitted for review. Reference: {case.reference}')
        return redirect('discipline:case_detail', pk=case.pk)
    return render(request, 'discipline/intake.html', {
        'form': form,
        'report_type': report_type,
        'can_file_incident': is_official(request.user),
        **_navigation_context(request),
    })


def _create_report_case(request, form, report_type):
    token = form.cleaned_data['submission_token']
    existing = CaseReport.objects.filter(submission_token=token).select_related('case').first()
    if existing:
        if existing.submitted_by_id == request.user.pk:
            return existing.case
        form.add_error(None, 'This submission token has already been used. Reload the form and try again.')
        return None

    try:
        with transaction.atomic():
            case = DisciplinaryCase.objects.create(
                source_type=report_type,
                case_level=form.cleaned_data.get('case_level', ''),
                assigned_officer=request.user if is_discipline_officer(request.user) else None,
            )
            report = form.save(commit=False)
            report.case = case
            report.report_type = report_type
            report.submitted_by = request.user
            report.submitted_at = timezone.now()
            if is_official(request.user):
                report.received_at = report.submitted_at
                report.received_by = request.user
                report.receiving_position = _user_position(request.user)
            report.save()
            if form.cleaned_data.get('classification'):
                case.classification = form.cleaned_data['classification']
                case.offense_category = form.cleaned_data.get('offense_category')
                case.policy_version = case.offense_category.policy_version
                case.classification_factual_basis = form.cleaned_data.get('classification_factual_basis', '')
                case.classified_by = request.user
                case.classified_at = timezone.now()
                case.classification_reviewed = False
                case.save(update_fields=(
                    'classification', 'offense_category', 'classification_factual_basis',
                    'policy_version', 'classified_by', 'classified_at',
                    'classification_reviewed', 'updated_at',
                ))
                CaseAuditLog.objects.create(
                    case=case,
                    actor=request.user,
                    action='classification_recorded',
                    details=(
                        f'Preliminary classification: {case.get_classification_display()}; '
                        f'policy: {case.offense_category.name if case.offense_category else "not selected"}; '
                        f'factual basis: {case.classification_factual_basis}'
                    ),
                )
            person_name = form.cleaned_data.get('respondent_name')
            if person_name:
                student_id = form.cleaned_data.get('respondent_student_id', '').strip()
                student_account = None
                if student_id and 'tblstudents' in connection.introspection.table_names():
                    from django.contrib.auth import get_user_model
                    legacy_student = LegacyStudent.objects.filter(idno=student_id).first()
                    if legacy_student and legacy_student.user_id:
                        student_account = get_user_model().objects.filter(pk=legacy_student.user_id).first()
                CasePerson.objects.create(
                    case=case,
                    role=CasePerson.Role.RESPONDENT,
                    name=person_name,
                    student_id=student_id,
                    account=student_account,
                    program=form.cleaned_data.get('respondent_program', ''),
                    year_section=form.cleaned_data.get('respondent_year_section', ''),
                )
            evidence = form.cleaned_data.get('evidence')
            if evidence:
                document = CaseDocument.objects.create(
                    case=case,
                    document_type='evidence',
                    file=evidence,
                    original_name=os.path.basename(evidence.name)[:255],
                    uploaded_by=request.user,
                )
                CaseAuditLog.objects.create(
                    case=case,
                    actor=request.user,
                    action='evidence_uploaded',
                    details=f'Evidence uploaded: {document.original_name}.',
                )
            CaseAction.objects.create(
                case=case,
                action_type=CaseAction.ActionType.STATUS,
                to_status=case.status,
                details=f'{report.get_report_type_display()} submitted for authorized review.',
                actor=request.user,
            )
            _record_case_access(request, case, 'created_report')
        return case
    except IntegrityError:
        existing = CaseReport.objects.filter(submission_token=token).select_related('case').first()
        if existing and existing.submitted_by_id == request.user.pk:
            return existing.case
        raise


@login_required(login_url='login')
@require_http_methods(['GET', 'POST'])
def complaint_create(request):
    return _report_case(request, CaseReport.ReportType.COMPLAINT)


@login_required(login_url='login')
@require_http_methods(['GET', 'POST'])
def incident_create(request):
    require_official(request.user)
    return _report_case(request, CaseReport.ReportType.INCIDENT)


@login_required(login_url='login')
@require_GET
def case_list(request):
    cases = visible_cases(request.user).order_by('-opened_at')
    can_view_confidential = can_manage_confidential(request.user)
    query = request.GET.get('q', '').strip()
    status = request.GET.get('status', '').strip()
    source = request.GET.get('source', '').strip()
    classification = request.GET.get('classification', '').strip()
    status_group = request.GET.get('status_group', '').strip()
    if query:
        cases = cases.filter(
            Q(reference__icontains=query) |
            Q(report__respondent_name__icontains=query) |
            Q(report__respondent_student_id__icontains=query) |
            Q(report__complainant_name__icontains=query) |
            Q(report__complainant_student_id__icontains=query)
        )
    if status in DisciplinaryCase.Status.values:
        cases = cases.filter(status=status)
    if status_group in CASE_STATUS_GROUPS:
        cases = cases.filter(status__in=CASE_STATUS_GROUPS[status_group])
    if source in CaseReport.ReportType.values:
        cases = cases.filter(source_type=source)
    if can_view_confidential and classification in DisciplinaryCase.Classification.values:
        cases = cases.filter(classification=classification)
    page = Paginator(cases.distinct(), 15).get_page(request.GET.get('page'))
    return render(request, 'discipline/case_list.html', {
        **_navigation_context(request),
        'can_view_confidential': can_view_confidential,
        'cases': page,
        'query': query,
        'status_filter': status,
        'status_group': status_group if status_group in CASE_STATUS_GROUPS else '',
        'source_filter': source,
        'classification_filter': classification if can_view_confidential else '',
        'status_choices': DisciplinaryCase.Status.choices,
        'source_choices': CaseReport.ReportType.choices,
        'classification_choices': DisciplinaryCase.Classification.choices,
    })


@login_required(login_url='login')
@require_GET
def case_detail(request, pk):
    case = _case_visible_or_404(request, pk)
    can_view_confidential = can_manage_confidential(request.user, case)
    is_reporter = case.report.submitted_by_id == request.user.pk
    is_respondent = case.people.filter(
        account=request.user,
        role=CasePerson.Role.RESPONDENT,
    ).exists()
    if not (can_view_confidential or is_reporter or is_respondent):
        raise PermissionDenied('You are not authorized to access this confidential record.')
    _record_case_access(request, case, 'viewed')
    if can_view_confidential:
        visible_summonses = case.summonses.select_related('respondent').all()
        visible_answers = case.answers.select_related('respondent').all()
    else:
        visible_summonses = case.summonses.filter(respondent__account=request.user).select_related('respondent')
        visible_answers = case.answers.filter(respondent__account=request.user).select_related('respondent')
    status_form = CaseStatusForm(case=case, user=request.user)
    decision_eligible = case.status in (
        DisciplinaryCase.Status.DECISION,
        DisciplinaryCase.Status.APPEAL,
        DisciplinaryCase.Status.DECISION_RECORDED,
    )
    # Pre-build decision form so modals can render field choices inline
    decision_form = None
    if can_finalize_decision(request.user) and decision_eligible:
        decision_form = CaseDecisionForm(
            case=case,
            user=request.user,
            initial={'authority_position': _user_position(request.user)},
        )

    return render(request, 'discipline/case_detail.html', {
        **_navigation_context(request),
        'case': case,
        'visible_actions': case.actions.exclude(
            action_type__in=(CaseAction.ActionType.SANCTION, CaseAction.ActionType.REVIEW),
        ).select_related('actor'),
        'visible_decisions': case.actions.filter(
            action_type=CaseAction.ActionType.DECISION,
        ).select_related('actor'),
        'summons_history': case.audit_logs.filter(action__startswith='summons_').select_related('actor'),
        'status_history': case.actions.filter(action_type=CaseAction.ActionType.STATUS).select_related('actor').order_by('-created_at'),
        'review_history': case.actions.filter(action_type=CaseAction.ActionType.REVIEW).select_related('actor').order_by('-created_at'),
        'classification_history': case.audit_logs.filter(action='classification_recorded').select_related('actor'),
        'case_documents': case.documents.all() if can_view_confidential else (),
        'visible_summonses': visible_summonses,
        'visible_answers': visible_answers,
        'can_view_confidential': can_view_confidential,
        'can_review': is_discipline_officer(request.user),
        'can_finalize_decision': can_finalize_decision(request.user),
        'decision_eligible': decision_eligible,
        'status_transition_available': bool(
            request.user.is_superuser or status_form.TRANSITIONS.get(case.status)
        ),
        'status_update_form': status_form,
        'decision_form': decision_form,
        'has_respondent': case.people.filter(role=CasePerson.Role.RESPONDENT).exists(),
        'summons_approval_form': SummonsApprovalForm(user=request.user) if can_finalize_decision(request.user) else None,
        'can_decide': can_decide(request.user),
        'can_manage_policy': can_manage_policy(request.user),
        'can_view_audit': can_view_confidential,
        'is_authorized_official': is_official(request.user),
        'can_access_case_register': is_official(request.user),
        'is_reporter': is_reporter,
        'is_case_person': is_respondent,
        'can_submit_answer': is_respondent,
    })


@login_required(login_url='login')
@require_GET
def case_document_download(request, pk, document_pk):
    case = _case_visible_or_404(request, pk)
    if not can_manage_confidential(request.user, case):
        raise PermissionDenied('You are not authorized to access case evidence.')
    document = get_object_or_404(CaseDocument, pk=document_pk, case=case)
    _record_case_access(request, case, 'evidence_accessed')
    try:
        file_handle = document.file.open('rb')
    except (OSError, ValueError) as error:
        raise Http404('The evidence file is unavailable.') from error
    response = FileResponse(
        file_handle,
        as_attachment=True,
        filename=os.path.basename(document.original_name),
    )
    response['X-Content-Type-Options'] = 'nosniff'
    return response


def deny_public_case_media(request, path):
    raise Http404


@login_required(login_url='login')
@require_http_methods(['GET', 'POST'])
def case_action(request, pk):
    case = _case_visible_or_404(request, pk)
    require_officer(request.user)
    form = WorkflowActionForm(request.POST or None, user=request.user)
    if request.method == 'POST' and form.is_valid():
        action_type = form.cleaned_data['action_type']
        if action_type == CaseAction.ActionType.FINDING:
            require_decision_authority(request.user)
        else:
            require_officer(request.user)
        with transaction.atomic():
            previous_status = case.status
            action = form.save(commit=False)
            action.case = case
            action.actor = request.user
            action.authority_position = form.cleaned_data['actor_position']
            action.from_status = previous_status
            action.save()
            update_fields = []
            if action.finding:
                case.finding = action.finding
                update_fields.append('finding')
            if action.recommendation:
                case.recommendation = action.recommendation
                update_fields.append('recommendation')
            if update_fields:
                update_fields.append('updated_at')
                case.save(update_fields=update_fields)
            _record_case_access(request, case, f'action_{action_type}')
        messages.success(request, 'The case action was recorded.')
        return redirect('discipline:case_detail', pk=case.pk)
    return render(request, 'discipline/action_form.html', {'form': form, 'case': case, **_navigation_context(request)})


@login_required(login_url='login')
@require_http_methods(['GET', 'POST'])
def case_review(request, pk):
    case = _case_visible_or_404(request, pk)
    require_officer(request.user)
    form = ReviewCaseForm(request.POST or None, user=request.user)
    if request.method == 'POST' and form.is_valid():
        recommendation = dict(ReviewCaseForm.RECOMMENDATION_CHOICES)[
            form.cleaned_data['recommendation']
        ]
        recommended_classification = dict(DisciplinaryCase.Classification.choices).get(
            form.cleaned_data['recommended_classification'],
            'No recommendation',
        )
        previous_classification = (
            case.get_classification_display() if case.classification else 'Not classified'
        )
        review_details = '\n\n'.join((
            f'Review findings:\n{form.cleaned_data["information_findings"].strip()}',
            'Supporting document verification remarks:\n'
            f'{form.cleaned_data["document_verification"].strip() or "Not recorded"}',
            f'Classification at review: {previous_classification}',
            f'Recommended classification:\n{recommended_classification}',
            f'Review remarks:\n{form.cleaned_data["review_remarks"].strip()}',
        ))
        with transaction.atomic():
            CaseAction.objects.create(
                case=case,
                action_type=CaseAction.ActionType.REVIEW,
                recommendation=recommendation,
                details=review_details,
                actor=request.user,
                authority_position=form.cleaned_data['actor_position'],
            )
            CaseAuditLog.objects.create(
                case=case,
                actor=request.user,
                action='case_reviewed',
                details=f'Recommendation: {recommendation}',
            )
        messages.success(request, 'The review record and recommendation were saved.')
        return redirect('discipline:case_detail', pk=case.pk)
    return render(request, 'discipline/review_case.html', {
        'case': case,
        'form': form,
        'case_documents': case.documents.all(),
        **_navigation_context(request),
    })


@login_required(login_url='login')
@require_http_methods(['GET', 'POST'])
def case_classify(request, pk):
    case = _case_visible_or_404(request, pk)
    require_officer(request.user)
    form = CaseClassificationForm(request.POST or None, instance=case)
    if request.method == 'POST' and form.is_valid():
        old_classification = case.get_classification_display() if case.classification else 'Unclassified'
        old_policy = (
            f'{case.offense_category.name} ({case.policy_version})'
            if case.offense_category_id else 'Not selected'
        )
        with transaction.atomic():
            case = form.save(commit=False)
            case.policy_version = case.offense_category.policy_version
            case.classified_by = request.user
            case.classified_at = timezone.now()
            case.classification_reviewed = False
            case.save()
            CaseAuditLog.objects.create(
                case=case,
                actor=request.user,
                action='classification_recorded',
                details=(
                    f'{old_classification} ({old_policy}) → {case.get_classification_display()} '
                    f'({case.offense_category.name}, {case.policy_version}); reason: {form.cleaned_data["change_reason"]}; '
                    f'factual basis: {case.classification_factual_basis}'
                ),
            )
        messages.success(request, 'Preliminary classification saved for authorized review.')
        return redirect('discipline:case_detail', pk=case.pk)
    return render(request, 'discipline/action_form.html', {
        'form': form,
        'case': case,
        'form_title': 'Record preliminary classification',
        **_navigation_context(request),
    })


@login_required(login_url='login')
@require_http_methods(['GET', 'POST'])
def case_level_update(request, pk):
    case = _case_visible_or_404(request, pk)
    require_officer(request.user)
    form = CaseLevelForm(request.POST or None, instance=case)
    if request.method == 'POST' and form.is_valid():
        previous = case.get_case_level_display() if case.case_level else 'Not recorded'
        with transaction.atomic():
            case = form.save()
            CaseAuditLog.objects.create(
                case=case,
                actor=request.user,
                action='case_level_updated',
                details=f'{previous} → {case.get_case_level_display()}',
            )
        messages.success(request, 'Case Level was updated and added to the case history.')
        return redirect('discipline:case_detail', pk=case.pk)
    return render(request, 'discipline/action_form.html', {
        'form': form,
        'case': case,
        'form_title': 'Verify or Update Case Level',
        **_navigation_context(request),
    })


@login_required(login_url='login')
@require_http_methods(['GET', 'POST'])
def case_receive(request, pk):
    case = _case_visible_or_404(request, pk)
    require_officer(request.user)
    form = CaseReceiptForm(request.POST or None, initial={
        'received_at': case.report.received_at,
        'receiving_position': case.report.receiving_position or _user_position(request.user),
        'intake_notes': case.report.intake_notes,
    })
    if request.method == 'POST' and form.is_valid():
        report = case.report
        previous = (
            f'{report.received_at}; {report.received_by}; '
            f'{report.receiving_position}; {report.intake_notes}'
        )
        with transaction.atomic():
            report.received_at = form.cleaned_data['received_at']
            report.received_by = request.user
            report.receiving_position = form.cleaned_data['receiving_position']
            report.intake_notes = form.cleaned_data['intake_notes']
            report.save(update_fields=('received_at', 'received_by', 'receiving_position', 'intake_notes'))
            CaseAuditLog.objects.create(
                case=case,
                actor=request.user,
                action='complaint_received',
                details=(
                    f'Previous receipt details: {previous}. New receipt details: {report.received_at}; '
                    f'{request.user.get_full_name() or request.user.username}; {report.receiving_position}; '
                    f'{report.intake_notes}'
                ),
            )
        messages.success(request, 'Complaint receipt details saved with an audit record.')
        return redirect('discipline:case_detail', pk=case.pk)
    return render(request, 'discipline/action_form.html', {
        'form': form,
        'case': case,
        'form_title': 'Record complaint receipt',
        **_navigation_context(request),
    })


@login_required(login_url='login')
@require_http_methods(['GET', 'POST'])
def case_status_update(request, pk):
    case = _case_visible_or_404(request, pk)
    require_officer(request.user)
    form = CaseStatusForm(request.POST or None, case=case, user=request.user)
    if request.method == 'POST' and form.is_valid():
        target = form.cleaned_data['status']
        if target in (DisciplinaryCase.Status.RESOLVED, DisciplinaryCase.Status.CLOSED):
            require_final_decision_authority(request.user)
        with transaction.atomic():
            previous = case.status
            case.status = target
            if target == DisciplinaryCase.Status.CLOSED:
                case.closed_at = timezone.now()
            case.save(update_fields=('status', 'closed_at', 'updated_at'))
            CaseAction.objects.create(
                case=case,
                action_type=CaseAction.ActionType.STATUS,
                from_status=previous,
                to_status=target,
                details=form.cleaned_data['remarks'],
                actor=request.user,
                authority_position=form.cleaned_data['actor_position'],
            )
            CaseAuditLog.objects.create(
                case=case,
                actor=request.user,
                action='status_updated',
                details=f'{DisciplinaryCase.Status(previous).label} → {DisciplinaryCase.Status(target).label}: {form.cleaned_data["remarks"]}',
            )
        messages.success(request, 'Case status updated and added to the history.')
        return redirect('discipline:case_detail', pk=case.pk)
    return render(request, 'discipline/action_form.html', {
        'form': form,
        'case': case,
        'form_title': 'Update Case Status',
        **_navigation_context(request),
    })


@login_required(login_url='login')
@require_http_methods(['GET', 'POST'])
def case_decision(request, pk):
    case = _case_visible_or_404(request, pk)
    require_final_decision_authority(request.user)
    eligible_statuses = (DisciplinaryCase.Status.DECISION, DisciplinaryCase.Status.APPEAL, DisciplinaryCase.Status.DECISION_RECORDED)
    if case.status not in eligible_statuses:
        messages.error(request, 'A final decision is not available yet. Complete the case review and move the case to Awaiting final decision first.')
        return redirect('discipline:case_detail', pk=case.pk)
    form = CaseDecisionForm(request.POST or None, case=case, user=request.user, initial={
        'finding': case.finding,
        'authority_position': _user_position(request.user),
    })
    if request.method == 'POST' and form.is_valid():
        outcome = form.cleaned_data['decision_outcome']
        outcome_label = (
            'No sanction / no further action'
            if outcome == '__no_action__'
            else outcome
        )
        latest = case.actions.filter(action_type=CaseAction.ActionType.DECISION).first()
        with transaction.atomic():
            action = CaseAction.objects.create(
                case=case,
                action_type=CaseAction.ActionType.DECISION,
                from_status=case.status,
                finding=form.cleaned_data['finding'],
                decision_outcome=outcome_label,
                authority=request.user.get_full_name() or request.user.username,
                authority_position=form.cleaned_data['authority_position'],
                details=form.cleaned_data['details'],
                actor=request.user,
            )
            previous_status = case.status
            case.finding = action.finding
            case.decision_authority = action.authority
            case.decision_date = timezone.localdate()
            case.decision_notes = action.details
            case.status = DisciplinaryCase.Status.DECISION_RECORDED
            update_fields = [
                'finding', 'decision_authority', 'decision_date', 'decision_notes',
                'status', 'updated_at',
            ]
            if case.classification:
                case.classification_reviewed = True
                update_fields.append('classification_reviewed')
            case.save(update_fields=update_fields)
            CaseAction.objects.create(
                case=case,
                action_type=CaseAction.ActionType.STATUS,
                from_status=previous_status,
                to_status=case.status,
                details='Status updated after an authorized final decision was recorded.',
                actor=request.user,
                authority_position=action.authority_position,
            )
            CaseAuditLog.objects.create(
                case=case,
                actor=request.user,
                action='decision_recorded' if latest is None else 'decision_updated',
                details=(
                    f'Outcome: {outcome_label}; finding: {action.get_finding_display()}; '
                    f'authority: {action.authority} ({action.authority_position}); '
                    f'details: {action.details}'
                ),
            )
        messages.success(request, 'Authorized final decision recorded in the case history.')
        return redirect('discipline:case_detail', pk=case.pk)
    return render(request, 'discipline/action_form.html', {
        'form': form,
        'case': case,
        'form_title': 'Record Decision' if not case.actions.filter(action_type=CaseAction.ActionType.DECISION).exists() else 'Update Decision',
        **_navigation_context(request),
    })


@login_required(login_url='login')
@require_http_methods(['GET', 'POST'])
def stage_create(request, pk):
    case = _case_visible_or_404(request, pk)
    require_officer(request.user)
    form = StageForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        stage = form.save(commit=False)
        stage.case = case
        stage.created_by = request.user
        stage.save()
        _record_case_access(request, case, 'stage_created')
        messages.success(request, 'Workflow stage recorded.')
        return redirect('discipline:case_detail', pk=case.pk)
    return render(request, 'discipline/action_form.html', {'form': form, 'case': case, 'form_title': 'Add workflow stage'})


@login_required(login_url='login')
@require_http_methods(['GET', 'POST'])
def summons_create(request, pk):
    case = _case_visible_or_404(request, pk)
    require_officer(request.user)
    form = SummonsForm(request.POST or None, case=case, issued_by=request.user)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            summons = form.save()
            CaseAuditLog.objects.create(
                case=case,
                actor=request.user,
                action='summons_issued',
                details=f'Summons {summons.reference} prepared for {summons.respondent.name}; approval and service remain separate.',
            )
        messages.success(request, f'Summons {summons.reference} prepared. It is not approved or served until those events are recorded.')
        return redirect('discipline:case_detail', pk=case.pk)
    return render(request, 'discipline/action_form.html', {
        'form': form,
        'case': case,
        'form_title': 'Prepare Parent/Guardian Summons',
        **_navigation_context(request),
    })


@login_required(login_url='login')
@require_http_methods(['GET', 'POST'])
def summons_edit(request, pk, summons_id):
    case = _case_visible_or_404(request, pk)
    require_officer(request.user)
    summons = get_object_or_404(Summons, case=case, pk=summons_id)
    if summons.served_at:
        messages.error(request, 'A served summons cannot be edited. Prepare a new summons to preserve the service record.')
        return redirect('discipline:case_detail', pk=case.pk)
    form = SummonsForm(request.POST or None, instance=summons, case=case, issued_by=request.user)
    if request.method == 'POST' and form.is_valid():
        old = (
            f'{summons.guardian_name}; {summons.guardian_relationship}; {summons.hearing_at}; '
            f'{summons.hearing_location}; {summons.instructions}; approved={summons.approved_at}'
        )
        with transaction.atomic():
            summons = form.save(commit=False)
            changed = any(
                form.has_changed() and field in form.changed_data
                for field in ('guardian_name', 'guardian_relationship', 'guardian_contact', 'hearing_at', 'hearing_location', 'instructions', 'respondent')
            )
            if changed and summons.approved_at:
                summons.approved_by = None
                summons.approved_by_position = ''
                summons.approved_at = None
            summons.save()
            CaseAuditLog.objects.create(
                case=case,
                actor=request.user,
                action='summons_updated',
                details=f'Previous details: {old}. Updated summons {summons.reference}; changed fields: {", ".join(form.changed_data)}. Approval is required again if the document changed.',
            )
        messages.success(request, 'Summons updated. Any prior approval is cleared when approved content changes.')
        return redirect('discipline:case_detail', pk=case.pk)
    return render(request, 'discipline/action_form.html', {
        'form': form,
        'case': case,
        'form_title': 'Update Parent/Guardian Summons',
        **_navigation_context(request),
    })


@login_required(login_url='login')
@require_POST
def summons_approve(request, pk, summons_id):
    case = _case_visible_or_404(request, pk)
    require_final_decision_authority(request.user)
    summons = get_object_or_404(Summons, case=case, pk=summons_id)
    if summons.approved_at:
        messages.error(request, 'This summons is already approved. Edit it to require renewed approval.')
        return redirect('discipline:case_detail', pk=case.pk)
    form = SummonsApprovalForm(request.POST, user=request.user)
    if form.is_valid():
        with transaction.atomic():
            summons.approved_by = request.user
            summons.approved_by_position = form.cleaned_data['approved_by_position']
            summons.approved_at = timezone.now()
            summons.save(update_fields=('approved_by', 'approved_by_position', 'approved_at'))
            CaseAuditLog.objects.create(
                case=case,
                actor=request.user,
                action='summons_approved',
                details=f'Summons {summons.reference} expressly approved by {request.user.get_full_name() or request.user.username} ({summons.approved_by_position}).',
            )
        messages.success(request, 'Summons approval recorded.')
    else:
        messages.error(request, 'Select an authorized position to approve this summons.')
    return redirect('discipline:case_detail', pk=case.pk)


@login_required(login_url='login')
@require_GET
def summons_preview(request, pk, summons_id):
    case = _case_visible_or_404(request, pk)
    if not can_manage_confidential(request.user, case):
        raise PermissionDenied('You are not authorized to preview this summons.')
    summons = get_object_or_404(
        Summons.objects.select_related('respondent', 'issued_by', 'approved_by'),
        case=case,
        pk=summons_id,
    )
    return render(request, 'discipline/summons_print.html', {
        'case': case,
        'summons': summons,
        'preview': True,
    })


@login_required(login_url='login')
@require_POST
def summons_print(request, pk, summons_id):
    case = _case_visible_or_404(request, pk)
    if not can_manage_confidential(request.user, case):
        raise PermissionDenied('You are not authorized to print this summons.')
    summons = get_object_or_404(
        Summons.objects.select_related('respondent', 'issued_by', 'approved_by'),
        case=case,
        pk=summons_id,
    )
    if not summons.approved_at:
        messages.error(request, 'This summons must be approved by an authorized decision authority before it can be printed.')
        return redirect('discipline:case_detail', pk=case.pk)
    CaseAuditLog.objects.create(
        case=case,
        actor=request.user,
        action='summons_printed',
        details=f'Summons {summons.reference} print generated; approved={bool(summons.approved_at)}.',
    )
    return render(request, 'discipline/summons_print.html', {
        'case': case,
        'summons': summons,
        'print_on_load': True,
    })


@login_required(login_url='login')
@require_POST
def summons_receipt_print(request, pk, summons_id):
    case = _case_visible_or_404(request, pk)
    if not can_manage_confidential(request.user, case):
        raise PermissionDenied('You are not authorized to print this service receipt.')
    summons = get_object_or_404(
        Summons.objects.select_related('respondent', 'service_recorded_by'),
        case=case,
        pk=summons_id,
    )
    if not summons.served_at or not (summons.received_at or summons.acknowledgment):
        messages.error(request, 'A receipt is available only after actual service or acknowledgment has been recorded.')
        return redirect('discipline:case_detail', pk=case.pk)
    CaseAuditLog.objects.create(
        case=case,
        actor=request.user,
        action='summons_receipt_printed',
        details=f'Service receipt generated for summons {summons.reference}.',
    )
    return render(request, 'discipline/summons_receipt.html', {
        'case': case,
        'summons': summons,
        'print_on_load': True,
    })


@login_required(login_url='login')
@require_http_methods(['GET', 'POST'])
def summons_serve(request, pk, summons_id):
    case = _case_visible_or_404(request, pk)
    require_officer(request.user)
    summons = get_object_or_404(Summons, case=case, pk=summons_id)
    if not summons.approved_at:
        messages.error(request, 'This summons must be approved before actual service can be recorded.')
        return redirect('discipline:case_detail', pk=case.pk)
    form = ServeSummonsForm(request.POST or None, instance=summons)
    if request.method == 'POST' and form.is_valid():
        previous = f'served_at={summons.served_at}; received_at={summons.received_at}; acknowledgment={summons.acknowledgment}; notes={summons.service_notes}'
        with transaction.atomic():
            summons = form.save(commit=False)
            summons.service_recorded_by = request.user
            summons.save()
            CaseAuditLog.objects.create(
                case=case,
                actor=request.user,
                action='summons_service_recorded',
                details=(
                    f'Summons {summons.reference}. Previous: {previous}. Current: served_at={summons.served_at}; '
                    f'received_at={summons.received_at}; acknowledgment={summons.acknowledgment}; '
                    f'recorded_by={request.user.get_full_name() or request.user.username}; notes={summons.service_notes}'
                ),
            )
        if summons.response_due:
            messages.success(request, f'Receipt recorded; the three-school-day response deadline is {summons.response_due}. This deadline flag does not determine responsibility.')
        else:
            messages.success(request, 'Service details recorded. Printing or preparing the summons does not mark it as served.')
        return redirect('discipline:case_detail', pk=case.pk)
    return render(request, 'discipline/action_form.html', {
        'form': form,
        'case': case,
        'form_title': 'Record Service Details',
        **_navigation_context(request),
    })


@login_required(login_url='login')
@require_http_methods(['GET', 'POST'])
def summons_extension(request, pk, summons_id):
    case = _case_visible_or_404(request, pk)
    require_officer(request.user)
    from .models import Summons
    summons = get_object_or_404(Summons, case=case, pk=summons_id)
    form = ExtensionForm(request.POST or None, instance=summons)
    if request.method == 'POST' and form.is_valid():
        previous_due = summons.extension_due
        with transaction.atomic():
            summons = form.save()
            CaseAuditLog.objects.create(
                case=case,
                actor=request.user,
                action='response_extension_recorded',
                details=(
                    f'Summons {summons.reference}; prior extension: {previous_due}; '
                    f'new extension: {summons.extension_due}; notes: {summons.service_notes}'
                ),
            )
        messages.success(request, 'The approved response extension was recorded.')
        return redirect('discipline:case_detail', pk=case.pk)
    return render(request, 'discipline/action_form.html', {'form': form, 'case': case, 'form_title': 'Record approved extension'})


@login_required(login_url='login')
@require_http_methods(['GET', 'POST'])
def answer_submit(request, pk):
    case = _case_visible_or_404(request, pk)
    form = AnswerForm(request.POST or None, case=case)
    respondent_person_ids = set(case.people.filter(account=request.user, role='respondent').values_list('id', flat=True))
    if not respondent_person_ids and not is_discipline_officer(request.user):
        raise PermissionDenied('Only the assigned respondent or Discipline Office may submit an answer.')
    if request.method == 'POST' and form.is_valid():
        if respondent_person_ids and form.cleaned_data['respondent'].pk not in respondent_person_ids:
            raise PermissionDenied('You may only submit an answer for yourself.')
        answer = form.save()
        answer.respondent.account = request.user
        answer.respondent.save(update_fields=['account'])
        _record_case_access(request, case, 'respondent_answer_submitted')
        messages.success(request, 'The respondent’s answer was recorded for review. It does not itself determine the case outcome.')
        return redirect('discipline:case_detail', pk=case.pk)
    if respondent_person_ids:
        form.fields['respondent'].queryset = case.people.filter(pk__in=respondent_person_ids)
    return render(request, 'discipline/action_form.html', {'form': form, 'case': case, 'form_title': 'Respondent answer'})


@login_required(login_url='login')
@require_GET
def policy_list(request):
    require_official(request.user)
    from django.db.models import Count, Q as Qm

    query       = request.GET.get('q', '').strip()
    cls_filter  = request.GET.get('classification', '').strip()
    status_filter = request.GET.get('status', '').strip()

    qs = OffenseCategory.objects.all()

    if query:
        qs = qs.filter(
            Qm(name__icontains=query) |
            Qm(description__icontains=query) |
            Qm(policy_reference__icontains=query)
        )
    if cls_filter in [c for c, _ in OffenseCategory.Classification.choices]:
        qs = qs.filter(classification=cls_filter)
    if status_filter == 'active':
        qs = qs.filter(active=True)
    elif status_filter == 'inactive':
        qs = qs.filter(active=False)

    # Stats (always from full queryset)
    total_count    = OffenseCategory.objects.count()
    active_count   = OffenseCategory.objects.filter(active=True).count()
    inactive_count = OffenseCategory.objects.filter(active=False).count()

    # Classification breakdown
    cls_counts = {
        code: OffenseCategory.objects.filter(classification=code).count()
        for code, _ in OffenseCategory.Classification.choices
    }

    return render(request, 'discipline/policy_list.html', {
        'categories':        qs.order_by('name'),
        'can_edit':          can_manage_policy(request.user),
        'query':             query,
        'cls_filter':        cls_filter,
        'status_filter':     status_filter,
        'total_count':       total_count,
        'active_count':      active_count,
        'inactive_count':    inactive_count,
        'cls_counts':        cls_counts,
        'classification_choices': OffenseCategory.Classification.choices,
        **_navigation_context(request),
    })


@login_required(login_url='login')
@require_http_methods(['GET', 'POST'])
def policy_edit(request, pk=None):
    require_policy_admin(request.user)
    instance = get_object_or_404(OffenseCategory, pk=pk) if pk else None
    form = OffenseCategoryForm(request.POST or None, instance=instance)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Policy catalog entry saved. Review and approve the content with the school before applying it to live cases.')
        return redirect('discipline:policy_list')
    return render(request, 'discipline/policy_form.html', {
        'form':      form,
        'instance':  instance,
        'is_edit':   bool(pk),
        **_navigation_context(request),
    })


@login_required(login_url='login')
@require_GET
def policy_detail(request, pk):
    require_official(request.user)
    category = get_object_or_404(OffenseCategory, pk=pk)
    case_count = category.cases.count()
    return render(request, 'discipline/policy_detail.html', {
        'category':   category,
        'can_edit':   can_manage_policy(request.user),
        'case_count': case_count,
        **_navigation_context(request),
    })


@login_required(login_url='login')
@require_GET
def committee_list(request):
    require_official(request.user)
    committees = DisciplinaryCommittee.objects.prefetch_related('members__user').order_by('-academic_year')
    meetings = CommitteeMeeting.objects.filter(committee__active=True).order_by('-scheduled_at')[:15]
    return render(request, 'discipline/committee_list.html', {'committees': committees, 'meetings': meetings, 'can_manage': is_discipline_officer(request.user), **_navigation_context(request)})


@login_required(login_url='login')
@require_http_methods(['GET', 'POST'])
def committee_create(request):
    require_officer(request.user)
    form = CommitteeForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        committee = form.save()
        messages.success(request, 'Committee roster created. Assign the designated officials before confidential case review.')
        return redirect('discipline:committee_detail', pk=committee.pk)
    return render(request, 'discipline/action_form.html', {'form': form, 'form_title': 'New committee roster'})


@login_required(login_url='login')
@require_GET
def committee_detail(request, pk):
    require_officer(request.user)
    committee = get_object_or_404(DisciplinaryCommittee.objects.prefetch_related('members__user', 'meetings'), pk=pk)
    return render(request, 'discipline/committee_detail.html', {'committee': committee, 'can_manage': is_discipline_officer(request.user)})


@login_required(login_url='login')
@require_http_methods(['GET', 'POST'])
def committee_member_add(request, pk):
    require_policy_admin(request.user)
    committee = get_object_or_404(DisciplinaryCommittee, pk=pk)
    form = CommitteeMemberForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        member = form.save(commit=False)
        member.committee = committee
        member.save()
        messages.success(request, 'Committee role assigned.')
        return redirect('discipline:committee_detail', pk=committee.pk)
    return render(request, 'discipline/action_form.html', {'form': form, 'form_title': 'Assign committee role'})


@login_required(login_url='login')
@require_http_methods(['GET', 'POST'])
def meeting_create(request, pk):
    require_officer(request.user)
    committee = get_object_or_404(DisciplinaryCommittee, pk=pk)
    form = CommitteeMeetingForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        meeting = form.save(commit=False)
        meeting.committee = committee
        meeting.created_by = request.user
        meeting.save()
        form.save_m2m()
        messages.success(request, 'Committee meeting scheduled.')
        return redirect('discipline:committee_detail', pk=committee.pk)
    return render(request, 'discipline/action_form.html', {'form': form, 'form_title': 'Schedule committee meeting'})


@login_required(login_url='login')
@require_http_methods(['GET', 'POST'])
def knights_report(request):
    member = get_object_or_404(KnightsMember, user=request.user, active=True)
    if request.method == 'POST':
        narration = request.POST.get('narration', '').strip()
        if not narration:
            messages.error(request, 'Describe the observation before submitting.')
        else:
            KnightsObservation.objects.create(
                submitted_by=member,
                observed_at=request.POST.get('observed_at') or None,
                location=request.POST.get('location', '').strip(),
                narration=narration,
                student_identifiers=request.POST.get('student_identifiers', '').strip(),
            )
            messages.success(request, 'Observation submitted for Discipline Officer review. This is not a finding of responsibility.')
            return redirect('discipline:knights_observations')
    return render(request, 'discipline/knights_report.html')


@login_required(login_url='login')
@require_GET
def knights_observations(request):
    if is_discipline_officer(request.user):
        observations = KnightsObservation.objects.select_related('submitted_by__user', 'reviewed_by').order_by('-submitted_at')
    else:
        member = get_object_or_404(KnightsMember, user=request.user, active=True)
        observations = KnightsObservation.objects.filter(submitted_by=member).order_by('-submitted_at')
    return render(request, 'discipline/knights_observations.html', {'observations': observations, 'can_review': is_discipline_officer(request.user)})


@login_required(login_url='login')
@require_POST
def knights_observation_review(request, pk):
    require_officer(request.user)
    observation = get_object_or_404(KnightsObservation, pk=pk)
    action = request.POST.get('action')
    if action not in (KnightsObservation.Status.REVIEWED, KnightsObservation.Status.REFERRED):
        raise PermissionDenied('Choose a valid review action.')
    observation.status = action
    observation.reviewed_by = request.user
    observation.review_notes = request.POST.get('review_notes', '').strip()
    observation.save(update_fields=['status', 'reviewed_by', 'review_notes'])
    messages.success(request, 'Observation review recorded.')
    return redirect('discipline:knights_observations')


@login_required(login_url='login')
@require_GET
def confidential_profile(request):
    require_official(request.user)
    if 'read_only_auditor' in committee_roles(request.user) and not can_decide(request.user) and not request.user.is_superuser:
        raise PermissionDenied('Auditors may inspect authorized case audit trails but may not search student histories.')
    form = ProfileSearchForm(request.GET or None)
    case_history = []
    if form.is_valid():
        student_id = form.cleaned_data['student_id'].strip()
        case_history = visible_cases(request.user).filter(
            Q(report__respondent_student_id=student_id) |
            Q(report__complainant_student_id=student_id) |
            Q(people__student_id=student_id)
        ).distinct().order_by('-opened_at')
        for case in case_history:
            CaseAuditLog.objects.create(case=case, actor=request.user, action='student_history_viewed', details=f'Student ID search: {student_id}')
    return render(request, 'discipline/confidential_profile.html', {'form': form, 'cases': case_history})


@login_required(login_url='login')
@require_GET
def probation_list(request):
    require_official(request.user)
    probations = ProbationRecord.objects.select_related('case', 'monitoring_officer').order_by('-start_date')
    return render(request, 'discipline/probation_list.html', {'probations': probations})


@login_required(login_url='login')
@require_GET
def suspension_list(request):
    require_official(request.user)
    suspensions = StudentSuspension.objects.select_related('case').order_by('-starts_on')
    return render(request, 'discipline/suspension_list.html', {'suspensions': suspensions})


@login_required(login_url='login')
@require_GET
def dashboard(request):
    if not is_official(request.user):
        return redirect('discipline:case_list')
    if not can_manage_confidential(request.user):
        return redirect('discipline:case_list')
    cases = visible_cases(request.user).distinct()
    context = {
        'cases_total': cases.count(),
        'submitted_count': cases.filter(status__in=CASE_STATUS_GROUPS['submitted']).count(),
        'review_count': cases.filter(status__in=CASE_STATUS_GROUPS['review']).count(),
        'decision_count': cases.filter(status__in=CASE_STATUS_GROUPS['decision']).count(),
    }
    return render(request, 'discipline/dashboard.html', {**_navigation_context(request), **context})


@login_required(login_url='login')
@require_GET
def reports(request):
    require_official(request.user)
    from django.db.models import Count
    from django.db.models.functions import TruncMonth

    cases = visible_cases(request.user)

    # --- Status breakdown ---
    status_data = (
        cases.values('status')
        .annotate(total=Count('id'))
        .order_by('status')
    )
    status_map = {s: 0 for s in DisciplinaryCase.Status.values}
    for row in status_data:
        status_map[row['status']] = row['total']

    pending_count   = sum(status_map.get(s, 0) for s in ['submitted', 'initial_review', 'returned'])
    review_count    = sum(status_map.get(s, 0) for s in ['investigation', 'hearing', 'committee'])
    decision_count  = sum(status_map.get(s, 0) for s in ['decision', 'appeal'])
    resolved_count  = sum(status_map.get(s, 0) for s in ['closed', 'referred'])

    # --- Source breakdown ---
    source_data = (
        cases.values('source_type')
        .annotate(total=Count('id'))
    )
    complaint_count = 0
    incident_count  = 0
    for row in source_data:
        if row['source_type'] == 'complaint':
            complaint_count = row['total']
        elif row['source_type'] == 'incident':
            incident_count = row['total']

    # --- Month-by-month trend (last 12 months) ---
    twelve_months_ago = timezone.now() - timezone.timedelta(days=365)
    monthly_raw = (
        cases.filter(opened_at__gte=twelve_months_ago)
        .annotate(month=TruncMonth('opened_at'))
        .values('month')
        .annotate(total=Count('id'))
        .order_by('month')
    )
    monthly_labels = [row['month'].strftime('%b %Y') for row in monthly_raw]
    monthly_counts = [row['total'] for row in monthly_raw]

    # --- Finding breakdown ---
    finding_data = (
        cases.values('finding')
        .annotate(total=Count('id'))
    )
    finding_map = {f: 0 for f in DisciplinaryCase.Finding.values}
    for row in finding_data:
        finding_map[row['finding']] = row['total']

    # Build a list of (code, label, count) for the status table
    status_rows = [
        (code, label, status_map.get(code, 0))
        for code, label in DisciplinaryCase.Status.choices
    ]

    context = {
        'total_cases':     cases.count(),
        'pending_count':   pending_count,
        'review_count':    review_count,
        'decision_count':  decision_count,
        'resolved_count':  resolved_count,
        'complaint_count': complaint_count,
        'incident_count':  incident_count,
        'monthly_labels':  monthly_labels,
        'monthly_counts':  monthly_counts,
        'finding_map':     finding_map,
        'status_rows':     status_rows,
        'finding_choices': DisciplinaryCase.Finding.choices,
        **_navigation_context(request),
    }
    return render(request, 'discipline/reports.html', context)


# ─── Unified Intake ───────────────────────────────────────────────────────────

@login_required(login_url='login')
@require_http_methods(['GET', 'POST'])
def intake(request):
    """Single reporting form for complaints and official incident reports."""
    raw_type = request.POST.get('report_type') if request.method == 'POST' else request.GET.get('report_type')
    if raw_type is None or raw_type == '':
        raw_type = (
            CaseReport.ReportType.INCIDENT
            if is_official(request.user)
            else CaseReport.ReportType.COMPLAINT
        )
    report_type = raw_type if raw_type in CaseReport.ReportType.values else ''

    # Build the form only when we have a valid report_type
    form = None
    if report_type:
        if report_type == CaseReport.ReportType.INCIDENT:
            require_official(request.user)
        form = ReportForm(
            request.POST if request.method == 'POST' else None,
            request.FILES if request.method == 'POST' else None,
            report_type=report_type,
            user=request.user,
        )

    if request.method == 'POST':
        if not report_type:
            messages.error(request, 'Please select a report type before submitting.')
        elif form and form.is_valid():
            case = _create_report_case(request, form, report_type)
            if case:
                return redirect('discipline:intake_success', pk=case.pk)

    return render(request, 'discipline/intake.html', {
        'form': form,
        'report_type': report_type,
        'can_file_incident': is_official(request.user),
        **_navigation_context(request),
    })


@login_required(login_url='login')
@require_GET
def intake_success(request, pk):
    """Success page shown after a unified-intake submission."""
    case = _case_visible_or_404(request, pk)
    # Only the submitter (or an official) may see the success page
    if case.report.submitted_by != request.user and not is_official(request.user):
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied
    return render(request, 'discipline/intake_success.html', {
        'case': case,
        **_navigation_context(request),
    })


# ─── Incident Log ─────────────────────────────────────────────────────────────

@login_required(login_url='login')
@require_GET
def incident_log(request):
    """
    Incident Log — all submitted written complaints and incident reports
    (CaseReport objects), viewable by authorized officials.
    """
    require_official(request.user)
    from django.db.models import Q as Ql

    query        = request.GET.get('q', '').strip()
    case_level_filter = request.GET.get('case_level', '').strip()
    status_filter = request.GET.get('status', '').strip()

    qs = CaseReport.objects.select_related('case', 'submitted_by').order_by('-submitted_at')

    # Scope: non-officers see only their own submissions
    if not (can_manage_confidential(request.user)):
        qs = qs.filter(submitted_by=request.user)

    if query:
        qs = qs.filter(
            Ql(case__reference__icontains=query) |
            Ql(respondent_name__icontains=query) |
            Ql(respondent_student_id__icontains=query) |
            Ql(complainant_name__icontains=query)
        )
    if case_level_filter in DisciplinaryCase.CaseLevel.values:
        qs = qs.filter(case__case_level=case_level_filter)
    if status_filter in DisciplinaryCase.Status.values:
        qs = qs.filter(case__status=status_filter)

    from django.core.paginator import Paginator
    page = Paginator(qs, 15).get_page(request.GET.get('page', 1))

    return render(request, 'discipline/incident_log.html', {
        'reports':       page,
        'query':         query,
        'case_level_filter': case_level_filter,
        'status_filter': status_filter,
        'case_level_choices': DisciplinaryCase.CaseLevel.choices,
        'status_choices': DisciplinaryCase.Status.choices,
        **_navigation_context(request),
    })


# ─── Policy Log ───────────────────────────────────────────────────────────────

@login_required(login_url='login')
@require_GET
def policy_log(request):
    """
    Policy Log — shows all OffenseCategory entries ordered by last update,
    giving a clear history of policy additions and changes.
    """
    require_official(request.user)
    from django.db.models import Q as Ql

    query = request.GET.get('q', '').strip()
    show  = request.GET.get('show', 'all').strip()   # all | active | inactive

    qs = OffenseCategory.objects.all().order_by('-updated_at')
    if query:
        qs = qs.filter(
            Ql(name__icontains=query) |
            Ql(policy_reference__icontains=query) |
            Ql(policy_version__icontains=query)
        )
    if show == 'active':
        qs = qs.filter(active=True)
    elif show == 'inactive':
        qs = qs.filter(active=False)

    from django.core.paginator import Paginator
    page = Paginator(qs, 20).get_page(request.GET.get('page', 1))

    return render(request, 'discipline/policy_log.html', {
        'policies': page,
        'query':    query,
        'show':     show,
        'can_edit': can_manage_policy(request.user),
        **_navigation_context(request),
    })
