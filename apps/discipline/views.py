from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import connection, transaction
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from .forms import (
    AnswerForm,
    CommitteeForm,
    CommitteeMeetingForm,
    CommitteeMemberForm,
    ExtensionForm,
    OffenseCategoryForm,
    ProfileSearchForm,
    ReportForm,
    ServeSummonsForm,
    StageForm,
    SummonsForm,
    WorkflowActionForm,
)
from .models import (
    CaseAction,
    CaseAuditLog,
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
        DisciplinaryCase.Status.CLOSED,
        DisciplinaryCase.Status.REFERRED,
    ),
}


def _case_visible_or_404(request, pk):
    return get_object_or_404(visible_cases(request.user), pk=pk)


def _record_case_access(request, case, action):
    CaseAuditLog.objects.create(case=case, actor=request.user, action=action)


def _report_case(request, report_type):
    if report_type == CaseReport.ReportType.INCIDENT:
        require_official(request.user)
    form = ReportForm(request.POST or None, report_type=report_type)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            case = DisciplinaryCase.objects.create(
                source_type=report_type,
                assigned_officer=request.user if is_discipline_officer(request.user) else None,
            )
            report = form.save(commit=False)
            report.case = case
            report.report_type = report_type
            report.submitted_by = request.user
            report.submitted_at = timezone.now()
            report.save()
            person_name = form.cleaned_data.get('respondent_name')
            if person_name:
                student_id = form.cleaned_data.get('respondent_student_id', '').strip()
                student_account = None
                if student_id and 'tblstudents' in connection.introspection.table_names():
                    legacy_student = LegacyStudent.objects.filter(idno=student_id).first()
                    if legacy_student and legacy_student.user_id:
                        from django.contrib.auth import get_user_model
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
            CaseAction.objects.create(
                case=case,
                action_type=CaseAction.ActionType.STATUS,
                to_status=case.status,
                details=f'{report.get_report_type_display()} submitted for authorized review.',
                actor=request.user,
            )
            _record_case_access(request, case, 'created_report')
        messages.success(request, f'Report submitted for review. Reference: {case.reference}')
        return redirect('discipline:case_detail', pk=case.pk)
    return render(request, 'discipline/report_form.html', {'form': form, 'report_type': report_type, **_navigation_context(request)})


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
    query = request.GET.get('q', '').strip()
    status = request.GET.get('status', '').strip()
    source = request.GET.get('source', '').strip()
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
    page = Paginator(cases.distinct(), 15).get_page(request.GET.get('page'))
    return render(request, 'discipline/case_list.html', {
        **_navigation_context(request),
        'can_view_confidential': can_manage_confidential(request.user),
        'cases': page,
        'query': query,
        'status_filter': status,
        'status_group': status_group if status_group in CASE_STATUS_GROUPS else '',
        'source_filter': source,
        'status_choices': DisciplinaryCase.Status.choices,
        'source_choices': CaseReport.ReportType.choices,
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

    return render(request, 'discipline/case_detail.html', {
        **_navigation_context(request),
        'case': case,
        'visible_summonses': visible_summonses,
        'visible_answers': visible_answers,
        'can_view_confidential': can_view_confidential,
        'can_review': is_discipline_officer(request.user),
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
@require_http_methods(['GET', 'POST'])
def case_action(request, pk):
    case = _case_visible_or_404(request, pk)
    form = WorkflowActionForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        action_type = form.cleaned_data['action_type']
        if action_type == CaseAction.ActionType.FINDING:
            require_decision_authority(request.user)
        elif action_type in (CaseAction.ActionType.DECISION, CaseAction.ActionType.SANCTION) or (
            action_type == CaseAction.ActionType.STATUS
            and form.cleaned_data['to_status'] == DisciplinaryCase.Status.CLOSED
        ):
            require_final_decision_authority(request.user)
        else:
            require_officer(request.user)
        with transaction.atomic():
            previous_status = case.status
            action = form.save(commit=False)
            action.case = case
            action.actor = request.user
            action.from_status = previous_status
            action.save()
            if action_type == CaseAction.ActionType.STATUS and action.to_status:
                case.status = action.to_status
                if action.to_status == DisciplinaryCase.Status.CLOSED:
                    case.closed_at = timezone.now()
            if action_type in (CaseAction.ActionType.FINDING, CaseAction.ActionType.DECISION, CaseAction.ActionType.SANCTION):
                if action.finding:
                    case.finding = action.finding
                if action.recommendation:
                    case.recommendation = action.recommendation
            if action_type == CaseAction.ActionType.DECISION:
                case.decision_authority = action.authority
                case.decision_date = timezone.localdate()
                case.decision_notes = action.details
                if action.final_sanction:
                    case.final_sanction = action.final_sanction
            elif action_type == CaseAction.ActionType.SANCTION:
                case.final_sanction = action.final_sanction
                case.decision_authority = action.authority
                case.decision_date = timezone.localdate()
                case.sanction_details = action.details
            case.save()
            _record_case_access(request, case, f'action_{action_type}')
        messages.success(request, 'The case action was recorded.')
        return redirect('discipline:case_detail', pk=case.pk)
    return render(request, 'discipline/action_form.html', {'form': form, 'case': case, **_navigation_context(request)})


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
    from .models import Summons
    form = SummonsForm(request.POST or None, case=case, issued_by=request.user)
    if request.method == 'POST' and form.is_valid():
        summons = form.save()
        _record_case_access(request, case, 'summons_issued')
        messages.success(request, f'Summons recorded for {summons.respondent.name}. Record service and receipt to calculate the response deadline.')
        return redirect('discipline:case_detail', pk=case.pk)
    return render(request, 'discipline/action_form.html', {'form': form, 'case': case, 'form_title': 'Record summons'})


@login_required(login_url='login')
@require_http_methods(['GET', 'POST'])
def summons_serve(request, pk, summons_id):
    case = _case_visible_or_404(request, pk)
    require_officer(request.user)
    from .models import Summons
    summons = get_object_or_404(Summons, case=case, pk=summons_id)
    form = ServeSummonsForm(request.POST or None, instance=summons)
    if request.method == 'POST' and form.is_valid():
        summons = form.save()
        _record_case_access(request, case, 'summons_service_recorded')
        if summons.response_due:
            messages.success(request, f'Receipt recorded; the three-school-day response deadline is {summons.response_due}. This deadline flag does not determine responsibility.')
        return redirect('discipline:case_detail', pk=case.pk)
    return render(request, 'discipline/action_form.html', {'form': form, 'case': case, 'form_title': 'Record service and receipt'})


@login_required(login_url='login')
@require_http_methods(['GET', 'POST'])
def summons_extension(request, pk, summons_id):
    case = _case_visible_or_404(request, pk)
    require_officer(request.user)
    from .models import Summons
    summons = get_object_or_404(Summons, case=case, pk=summons_id)
    form = ExtensionForm(request.POST or None, instance=summons)
    if request.method == 'POST' and form.is_valid():
        form.save()
        _record_case_access(request, case, 'response_extension_recorded')
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

    # --- Offense category breakdown ---
    category_data = (
        cases.filter(offense_category__isnull=False)
        .values('offense_category__name')
        .annotate(total=Count('id'))
        .order_by('-total')[:10]
    )

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

    # --- Sanction breakdown ---
    sanction_data = (
        cases.exclude(final_sanction='none')
        .values('final_sanction')
        .annotate(total=Count('id'))
        .order_by('-total')
    )
    sanction_labels = [DisciplinaryCase.Sanction(row['final_sanction']).label for row in sanction_data]
    sanction_counts = [row['total'] for row in sanction_data]

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
        'category_data':   list(category_data),
        'monthly_labels':  monthly_labels,
        'monthly_counts':  monthly_counts,
        'finding_map':     finding_map,
        'sanction_labels': sanction_labels,
        'sanction_counts': sanction_counts,
        'status_rows':     status_rows,
        'finding_choices': DisciplinaryCase.Finding.choices,
        **_navigation_context(request),
    }
    return render(request, 'discipline/reports.html', context)


# ─── Unified Intake ───────────────────────────────────────────────────────────

@login_required(login_url='login')
@require_http_methods(['GET', 'POST'])
def intake(request):
    """Single unified page for both Written Complaint and Incident Report."""
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
            report_type=report_type,
        )

    if request.method == 'POST':
        if not report_type:
            messages.error(request, 'Please select a report type before submitting.')
        elif form and form.is_valid():
            with transaction.atomic():
                case = DisciplinaryCase.objects.create(
                    source_type=report_type,
                    assigned_officer=request.user if is_discipline_officer(request.user) else None,
                )
                report = form.save(commit=False)
                report.case = case
                report.report_type = report_type
                report.submitted_by = request.user
                report.submitted_at = timezone.now()
                report.save()
                person_name = form.cleaned_data.get('respondent_name')
                if person_name:
                    student_id = form.cleaned_data.get('respondent_student_id', '').strip()
                    student_account = None
                    if student_id and 'tblstudents' in connection.introspection.table_names():
                        legacy_student = LegacyStudent.objects.filter(idno=student_id).first()
                        if legacy_student and legacy_student.user_id:
                            from django.contrib.auth import get_user_model
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
                CaseAction.objects.create(
                    case=case,
                    action_type=CaseAction.ActionType.STATUS,
                    to_status=case.status,
                    details=f'{report.get_report_type_display()} submitted via unified intake form.',
                    actor=request.user,
                )
                _record_case_access(request, case, 'created_report')
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
    type_filter  = request.GET.get('report_type', '').strip()
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
    if type_filter in CaseReport.ReportType.values:
        qs = qs.filter(report_type=type_filter)
    if status_filter in DisciplinaryCase.Status.values:
        qs = qs.filter(case__status=status_filter)

    from django.core.paginator import Paginator
    page = Paginator(qs, 15).get_page(request.GET.get('page', 1))

    return render(request, 'discipline/incident_log.html', {
        'reports':       page,
        'query':         query,
        'type_filter':   type_filter,
        'status_filter': status_filter,
        'report_type_choices': CaseReport.ReportType.choices,
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
