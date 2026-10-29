from datetime import date, timedelta
import uuid

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .forms import WorkflowActionForm
from .models import (
    CaseAction,
    CaseAuditLog,
    CaseDocument,
    CaseReport,
    CommitteeMember,
    DisciplinaryCase,
    DisciplinaryCommittee,
    DisciplinarySanction,
    KnightsMember,
    KnightsObservation,
    OffenseCategory,
    PenaltyCatalog,
    ViolationAuditLog,
    SchoolCalendarDay,
    Summons,
    Violation,
    add_school_days,
)
from .permissions import can_decide, can_finalize_decision, is_discipline_officer, require_decision_authority


class DisciplineAccessAndWorkflowTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.admin = user_model.objects.create_superuser(username='admin', password='strong-test-pass')
        self.staff = user_model.objects.create_user(username='staff', password='strong-test-pass', is_staff=True)
        self.student = user_model.objects.create_user(username='student', password='strong-test-pass')
        self.other = user_model.objects.create_user(username='other', password='strong-test-pass')
        committee = DisciplinaryCommittee.objects.create(academic_year='2026-2027')
        CommitteeMember.objects.create(
            committee=committee,
            user=self.staff,
            role=CommitteeMember.Role.DISCIPLINE_OFFICER,
        )

    def make_report(self, submitter=None):
        submitter = submitter or self.staff
        case = DisciplinaryCase.objects.create(source_type=CaseReport.ReportType.COMPLAINT)
        report = CaseReport.objects.create(
            case=case,
            report_type=CaseReport.ReportType.COMPLAINT,
            submitted_by=submitter,
            complainant_name='Student Complainant',
            complainant_student_id='S-100',
            respondent_name='Respondent Student',
            respondent_student_id='S-200',
            narration='A test report narrative.',
            acknowledgment=True,
        )
        return case, report

    def test_dashboard_requires_an_authorized_official(self):
        self.client.force_login(self.student)
        response = self.client.get(reverse('discipline:dashboard'))
        self.assertRedirects(response, reverse('discipline:case_list'))

    def test_unified_intake_uses_compact_type_selector_without_choice_cards(self):
        self.client.force_login(self.staff)
        official_response = self.client.get(reverse('discipline:intake'))
        self.assertEqual(official_response.status_code, 200)
        self.assertEqual(official_response.context['report_type'], CaseReport.ReportType.INCIDENT)
        self.assertContains(official_response, 'name="report_type"')
        self.assertNotContains(official_response, 'intake-type-card')

        self.client.force_login(self.student)
        user_response = self.client.get(reverse('discipline:intake'))
        self.assertEqual(user_response.context['report_type'], CaseReport.ReportType.COMPLAINT)
        self.assertNotContains(user_response, 'value="incident"')

    def test_dashboard_summary_cards_link_to_matching_case_status_groups(self):
        cases_by_group = {
            'submitted': [DisciplinaryCase.Status.SUBMITTED] * 2,
            'review': [
                DisciplinaryCase.Status.INITIAL_REVIEW,
                DisciplinaryCase.Status.INVESTIGATION,
                DisciplinaryCase.Status.HEARING,
                DisciplinaryCase.Status.COMMITTEE,
            ],
            'decision': [
                DisciplinaryCase.Status.DECISION,
                DisciplinaryCase.Status.APPEAL,
            ],
        }
        expected_counts = {'submitted': 2, 'review': 4, 'decision': 2}
        for statuses in cases_by_group.values():
            for status in statuses:
                case, _ = self.make_report()
                case.status = status
                case.save(update_fields=['status'])
        closed_case, _ = self.make_report()
        closed_case.status = DisciplinaryCase.Status.CLOSED
        closed_case.save(update_fields=['status'])

        self.client.force_login(self.admin)
        dashboard = self.client.get(reverse('discipline:dashboard'))
        self.assertEqual(dashboard.context['cases_total'], 9)
        self.assertEqual(dashboard.context['submitted_count'], expected_counts['submitted'])
        self.assertEqual(dashboard.context['review_count'], expected_counts['review'])
        self.assertEqual(dashboard.context['decision_count'], expected_counts['decision'])
        for group in cases_by_group:
            self.assertContains(dashboard, f'?status_group={group}')
            response = self.client.get(reverse('discipline:case_list'), {'status_group': group})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.context['cases'].paginator.count, expected_counts[group])

    def test_sidebar_navigation_is_consistent_and_role_aware_on_home(self):
        self.client.force_login(self.staff)
        official_home = self.client.get(reverse('dashboard'))
        self.assertContains(official_home, 'Discipline</span>')
        self.assertContains(official_home, 'Policy Management')
        self.assertContains(official_home, reverse('discipline:incident_log'))

        self.client.force_login(self.student)
        student_home = self.client.get(reverse('dashboard'))
        self.assertContains(student_home, 'Discipline Office Intake')
        self.assertContains(student_home, reverse('discipline:case_list'))
        self.assertNotContains(student_home, 'Policy Catalog')
        self.assertNotContains(student_home, 'Incident Log')

    def test_complaint_submission_creates_reference_and_audit_record(self):
        self.client.force_login(self.student)
        response = self.client.post(reverse('discipline:complaint_create'), {
            'complainant_name': 'Student Complainant',
            'complainant_student_id': 'S-100',
            'complainant_program': 'College',
            'complainant_year_section': '1A',
            'respondent_name': 'Respondent Student',
            'respondent_student_id': 'S-200',
            'respondent_program': 'College',
            'respondent_year_section': '2B',
            'incident_place': 'Campus',
            'narration': 'Observed situation, submitted for review.',
            'acknowledgment': 'on',
        })
        self.assertEqual(response.status_code, 302)
        case = DisciplinaryCase.objects.get()
        report = case.report
        self.assertTrue(case.reference.startswith('DO-'))
        self.assertEqual(report.submitted_by, self.student)
        self.assertEqual(case.finding, DisciplinaryCase.Finding.NOT_DETERMINED)
        self.assertEqual(case.final_sanction, DisciplinaryCase.Sanction.NONE)
        self.assertTrue(CaseAuditLog.objects.filter(case=case, actor=self.student, action='created_report').exists())

    def test_official_incident_submission_is_restricted_to_school_officials(self):
        self.client.force_login(self.student)
        response = self.client.get(reverse('discipline:incident_create'))
        self.assertEqual(response.status_code, 403)

        self.client.force_login(self.staff)
        response = self.client.get(reverse('discipline:incident_create'))
        self.assertEqual(response.status_code, 200)

    def test_case_list_scopes_records_to_reporter_for_regular_accounts(self):
        visible_case, _ = self.make_report(submitter=self.student)
        hidden_case, _ = self.make_report(submitter=self.other)
        self.client.force_login(self.student)
        response = self.client.get(reverse('discipline:case_list'))
        self.assertContains(response, visible_case.reference)
        self.assertNotContains(response, hidden_case.reference)

    def test_nonparticipants_cannot_view_a_confidential_case(self):
        case, _ = self.make_report()
        self.client.force_login(self.other)
        response = self.client.get(reverse('discipline:case_detail', args=[case.pk]))
        self.assertEqual(response.status_code, 404)

    def test_reporter_sees_status_but_not_internal_narrative_or_deliberations(self):
        case, report = self.make_report(submitter=self.student)
        report.narration = 'Confidential witness narrative text'
        report.witness_information = 'Witness identity text'
        report.save(update_fields=['narration', 'witness_information'])
        case.recommendation = 'Confidential internal review note'
        case.save(update_fields=['recommendation'])
        self.client.force_login(self.student)
        response = self.client.get(reverse('discipline:case_detail', args=[case.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, case.reference)
        self.assertNotContains(response, 'Confidential internal review note')
        self.assertNotContains(response, 'Confidential witness narrative text')
        self.assertNotContains(response, 'Witness identity text')

    def test_generic_staff_without_committee_role_cannot_access_case_register(self):
        general_staff = get_user_model().objects.create_user(username='general_staff', password='strong-test-pass', is_staff=True)
        case, _ = self.make_report()
        self.client.force_login(general_staff)
        self.assertEqual(self.client.get(reverse('discipline:case_list')).status_code, 200)
        response = self.client.get(reverse('discipline:case_detail', args=[case.pk]))
        self.assertEqual(response.status_code, 404)
        self.assertNotContains(self.client.get(reverse('discipline:case_list')), case.reference)

    def test_staff_cannot_assign_themselves_a_privileged_committee_role(self):
        committee = DisciplinaryCommittee.objects.create(name='Additional Committee', academic_year='2026-2027')
        self.client.force_login(self.staff)
        response = self.client.post(reverse('discipline:committee_member_add', args=[committee.pk]), {
            'user': self.staff.pk,
            'role': 'president',
            'title': 'President',
            'active': 'on',
        })
        self.assertEqual(response.status_code, 403)
        self.assertFalse(self.staff.committee_memberships.filter(role='president').exists())

    def test_staff_cannot_close_a_case_without_decision_role(self):
        case, _ = self.make_report()
        self.client.force_login(self.staff)
        response = self.client.post(reverse('discipline:case_action', args=[case.pk]), {
            'action_type': CaseAction.ActionType.STATUS,
            'to_status': DisciplinaryCase.Status.CLOSED,
            'details': 'Attempt to close without decision role.',
        })
        self.assertEqual(response.status_code, 403)
        case.refresh_from_db()
        self.assertNotEqual(case.status, DisciplinaryCase.Status.CLOSED)

    def test_official_dashboard_and_catalog_pages_render(self):
        self.client.force_login(self.admin)
        for url_name in ('discipline:dashboard', 'discipline:case_list', 'discipline:policy_list', 'discipline:committee_list', 'discipline:probation_list', 'discipline:suspension_list'):
            with self.subTest(url_name=url_name):
                self.assertEqual(self.client.get(reverse(url_name)).status_code, 200)

    def test_policy_can_be_created_without_optional_permitted_sanctions(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse('discipline:policy_create'), {
            'name': 'Test policy with no penalty guidance',
            'description': 'A policy whose approved penalty guidance is not specified.',
            'classification': 'unclassified',
            'permitted_sanctions': '',
            'policy_version': 'Pending school approval',
        })

        self.assertRedirects(response, reverse('discipline:policy_list'))
        category = OffenseCategory.objects.get(name='Test policy with no penalty guidance')
        self.assertEqual(category.permitted_sanctions, [])

    def test_policy_model_normalizes_null_permitted_sanctions(self):
        category = OffenseCategory.objects.create(
            name='Model-level empty penalty guidance',
            description='A policy without specified penalty guidance.',
            permitted_sanctions=None,
        )

        self.assertEqual(category.permitted_sanctions, [])

    def test_finding_and_sanction_forms_require_explicit_authority_and_record(self):
        finding_form = WorkflowActionForm(data={'action_type': 'finding', 'details': 'Reviewed evidence.'})
        self.assertFalse(finding_form.is_valid())
        self.assertIn('finding', finding_form.errors)

        sanction_form = WorkflowActionForm(data={'action_type': 'sanction', 'authority': 'Dean', 'details': 'Approved.'})
        self.assertFalse(sanction_form.is_valid())
        self.assertIn('final_sanction', sanction_form.errors)

    def test_discipline_officer_role_can_record_findings_but_not_final_sanctions(self):
        self.assertTrue(can_decide(self.staff))
        self.assertFalse(can_finalize_decision(self.staff))
        self.client.force_login(self.staff)
        response = self.client.post(reverse('discipline:case_action', args=[self.make_report()[0].pk]), {
            'action_type': CaseAction.ActionType.SANCTION,
            'final_sanction': DisciplinaryCase.Sanction.SUSPENSION,
            'authority': 'Discipline Officer',
            'details': 'Attempted without a final decision authority.',
        })
        self.assertEqual(response.status_code, 403)

    def test_staff_review_does_not_assume_decision_authority(self):
        self.assertTrue(is_discipline_officer(self.staff))
        self.assertTrue(can_decide(self.staff))
        require_decision_authority(self.staff)
        self.assertTrue(can_decide(self.admin))
        self.assertTrue(can_finalize_decision(self.admin))

    def test_deadline_counts_weekdays_and_configured_school_closures(self):
        SchoolCalendarDay.objects.create(date=date(2026, 11, 3), is_school_day=False, label='School closure')
        deadline = add_school_days(date(2026, 10, 30), 3)
        self.assertEqual(deadline, date(2026, 11, 5))

    def test_deadline_cannot_be_backdated_or_created_without_service(self):
        case, _ = self.make_report()
        from .models import CasePerson
        respondent = CasePerson.objects.create(case=case, role='respondent', name='Respondent')
        self.client.force_login(self.staff)
        response = self.client.post(reverse('discipline:summons_create', args=[case.pk]), {'respondent': respondent.pk, 'service_notes': ''})
        self.assertEqual(response.status_code, 302)
        summons = Summons.objects.get(case=case)
        response = self.client.post(reverse('discipline:summons_serve', args=[case.pk, summons.pk]), {
            'received_at': '2000-01-01', 'service_notes': '', 'acknowledgment': '',
        })
        self.assertEqual(response.status_code, 200)
        summons.refresh_from_db()
        self.assertIsNone(summons.received_at)
        response = self.client.post(reverse('discipline:summons_serve', args=[case.pk, summons.pk]), {
            'received_at': '2099-01-01', 'served_at': '2026-10-28T09:00', 'service_notes': '', 'acknowledgment': 'on',
        })
        self.assertEqual(response.status_code, 200)
        summons.refresh_from_db()
        self.assertIsNone(summons.received_at)

    def test_calendar_can_mark_a_weekday_as_a_school_holiday(self):
        SchoolCalendarDay.objects.create(date=date(2026, 11, 2), is_school_day=False, label='Holiday')
        self.assertEqual(add_school_days(date(2026, 10, 30), 1), date(2026, 11, 3))

    def test_summons_deadline_is_based_on_receipt_date(self):
        case, _ = self.make_report()
        from .models import CasePerson
        respondent = CasePerson.objects.create(case=case, role='respondent', name='Respondent')
        summons = Summons.objects.create(
            case=case,
            respondent=respondent,
            issued_by=self.staff,
            received_at=date(2026, 10, 28),
        )
        self.assertEqual(summons.response_due, date(2026, 11, 2))
        summons.received_at = date(2026, 10, 29)
        summons.save()
        self.assertEqual(summons.response_due, date(2026, 11, 3))

    def test_knights_member_can_submit_observation_but_not_review_it(self):
        membership = KnightsMember.objects.create(
            user=self.student,
            student_id='S-100',
            student_name='Student Member',
            academic_year='2026-2027',
        )
        self.client.force_login(self.student)
        response = self.client.post(reverse('discipline:knights_report'), {'narration': 'Observation for review.'})
        self.assertEqual(response.status_code, 302)
        observation = KnightsObservation.objects.get(submitted_by=membership)
        self.assertEqual(observation.status, KnightsObservation.Status.SUBMITTED)
        response = self.client.post(reverse('discipline:knights_observation_review', args=[observation.pk]), {'action': 'referred'})
        self.assertEqual(response.status_code, 403)


class ViolationAndPenaltyTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.admin = user_model.objects.create_superuser(username='final-authority', password='strong-test-pass')
        self.officer = user_model.objects.create_user(username='discipline-officer', password='strong-test-pass')
        self.student = user_model.objects.create_user(username='student-user', password='strong-test-pass')
        committee = DisciplinaryCommittee.objects.create(academic_year='2026-2027')
        CommitteeMember.objects.create(
            committee=committee,
            user=self.officer,
            role=CommitteeMember.Role.DISCIPLINE_OFFICER,
        )
        self.category = OffenseCategory.objects.create(name='Student Conduct')

    def create_violation(self):
        return Violation.objects.create(
            code='VC-001',
            name='School conduct violation',
            description='Test violation record.',
            severity=Violation.Severity.HIGH,
            classification=Violation.Classification.MAJOR,
            applicable_rule='Student Handbook Rule 1',
            recommended_penalty='Counseling referral when approved.',
            category=self.category,
            created_by=self.officer,
        )

    def create_case(self, violation):
        case = DisciplinaryCase.objects.create(
            source_type=CaseReport.ReportType.INCIDENT,
            assigned_officer=self.officer,
            offense_category=self.category,
            violation=violation,
            finding=DisciplinaryCase.Finding.SUBSTANTIATED,
        )
        CaseReport.objects.create(
            case=case,
            report_type=CaseReport.ReportType.INCIDENT,
            submitted_by=self.officer,
            respondent_name='Test Student',
            respondent_student_id='S-200',
            reporting_position='Discipline Officer',
            narration='Incident submitted for authorized review.',
            acknowledgment=True,
        )
        return case

    def test_intake_links_allegations_without_finding_and_deduplicates_reposts(self):
        violation = self.create_violation()
        token = str(uuid.uuid4())
        payload = {
            'report_type': CaseReport.ReportType.INCIDENT,
            'submission_token': token,
            'reporting_position': 'Discipline Officer',
            'respondent_name': 'Test Student',
            'respondent_student_id': 'S-200',
            'narration': 'Incident submitted for review.',
            'acknowledgment': 'on',
            'violations': [str(violation.pk)],
        }
        self.client.force_login(self.officer)
        first = self.client.post(reverse('discipline:intake'), payload)
        self.assertEqual(first.status_code, 302)
        case = DisciplinaryCase.objects.get()
        self.assertEqual(case.finding, DisciplinaryCase.Finding.NOT_DETERMINED)
        self.assertEqual(list(case.violations.all()), [violation])
        self.assertTrue(CaseAuditLog.objects.filter(case=case, action='violation_reported').exists())

        duplicate = self.client.post(reverse('discipline:intake'), payload)
        self.assertEqual(duplicate.status_code, 302)
        self.assertEqual(DisciplinaryCase.objects.count(), 1)

    def test_multiple_case_violations_filter_authorized_penalty_assignment(self):
        first_violation = self.create_violation()
        second_violation = Violation.objects.create(
            code='VC-002',
            name='Second conduct violation',
            description='Another test violation.',
            severity=Violation.Severity.MODERATE,
            classification=Violation.Classification.MINOR,
            applicable_rule='Student Handbook Rule 2',
            category=self.category,
            created_by=self.officer,
        )
        case = self.create_case(first_violation)
        self.client.force_login(self.officer)
        linked = self.client.post(reverse('discipline:case_violation_assign', args=[case.pk]), {
            'violations': [str(first_violation.pk), str(second_violation.pk)],
        })
        self.assertEqual(linked.status_code, 302)
        case.refresh_from_db()
        self.assertEqual(set(case.violations.all()), {first_violation, second_violation})

        case.finding = DisciplinaryCase.Finding.SUBSTANTIATED
        case.save(update_fields=['finding'])
        penalty = PenaltyCatalog.objects.create(
            name='Counseling for second violation',
            description='Attend counseling.',
            violation=second_violation,
            policy_reference='KCC-DO-02',
            active=True,
            approved=True,
            created_by=self.officer,
            approved_by=self.admin,
            approved_at=timezone.now(),
        )
        response = self.client.post(
            reverse('discipline:sanction_assign_for_violation', args=[case.pk, second_violation.pk]),
            {
                'penalty': penalty.pk,
                'assigned_date': date.today().isoformat(),
                'due_date': '',
                'responsible_staff': self.officer.pk,
            },
        )
        self.assertEqual(response.status_code, 302)
        sanction = DisciplinarySanction.objects.get(case=case)
        self.assertEqual(sanction.violation, second_violation)
        self.assertEqual(sanction.review_status, DisciplinarySanction.ReviewStatus.PENDING)
        self.assertEqual(sanction.status, DisciplinarySanction.Status.PENDING)

    def test_uploaded_evidence_is_private_and_requires_case_authority(self):
        token = str(uuid.uuid4())
        self.client.force_login(self.student)
        response = self.client.post(reverse('discipline:intake'), {
            'report_type': CaseReport.ReportType.COMPLAINT,
            'submission_token': token,
            'complainant_name': 'Student Reporter',
            'complainant_student_id': 'S-100',
            'respondent_name': 'Test Student',
            'respondent_student_id': 'S-200',
            'narration': 'Incident submitted for review.',
            'acknowledgment': 'on',
            'evidence': SimpleUploadedFile('evidence.pdf', b'%PDF-1.4 private evidence'),
        })
        self.assertEqual(response.status_code, 302)
        case = DisciplinaryCase.objects.get()
        document = CaseDocument.objects.get(case=case)
        self.addCleanup(document.file.delete, save=False)
        self.assertIn('private-evidence', document.file.path)
        download_url = reverse('discipline:case_document_download', args=[case.pk, document.pk])

        self.assertEqual(self.client.get(download_url).status_code, 403)
        self.client.force_login(self.officer)
        download = self.client.get(download_url)
        self.assertEqual(download.status_code, 200)
        self.assertEqual(b''.join(download.streaming_content), b'%PDF-1.4 private evidence')
        self.assertEqual(self.client.get(f'/media/{document.file.name}').status_code, 404)

    def test_confidential_student_history_shows_linked_violations_and_penalties(self):
        violation = self.create_violation()
        case = self.create_case(violation)
        DisciplinarySanction.objects.create(
            case=case,
            violation=violation,
            name='Counseling referral',
            description='Attend counseling.',
            responsible_staff=self.officer,
            assigned_by=self.officer,
        )
        self.client.force_login(self.admin)
        response = self.client.get(reverse('discipline:confidential_profile'), {'student_id': 'S-200'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'VC-001 · School conduct violation')
        self.assertContains(response, 'Counseling referral · Pending authorized review')

    def test_violation_catalog_is_searchable_and_officer_managed(self):
        self.client.force_login(self.student)
        self.assertEqual(self.client.get(reverse('discipline:violation_list')).status_code, 403)

        self.client.force_login(self.officer)
        response = self.client.post(reverse('discipline:violation_create'), {
            'code': 'VC-001',
            'name': 'School conduct violation',
            'description': 'Test violation record.',
            'category': self.category.pk,
            'severity': Violation.Severity.HIGH,
            'classification': Violation.Classification.MAJOR,
            'applicable_rule': 'Student Handbook Rule 1',
            'recommended_penalty': 'Counseling referral when approved.',
            'policy_reference': 'KCC-DO-01',
        })
        self.assertEqual(response.status_code, 302)
        violation = Violation.objects.get(code='VC-001')
        self.assertTrue(ViolationAuditLog.objects.filter(violation=violation, action='created').exists())
        response = self.client.get(reverse('discipline:violation_list'), {'q': 'VC-001'})
        self.assertContains(response, 'School conduct violation')

        response = self.client.post(reverse('discipline:violation_archive', args=[violation.pk]))
        self.assertRedirects(response, reverse('discipline:violation_detail', args=[violation.pk]))
        violation.refresh_from_db()
        self.assertFalse(violation.active)
        self.assertTrue(ViolationAuditLog.objects.filter(violation=violation, action='archived').exists())

    def test_penalty_requires_approval_and_substantiated_case_before_assignment(self):
        violation = self.create_violation()
        case = self.create_case(violation)
        assigned_date = date.today()
        due_date = assigned_date + timedelta(days=7)
        self.client.force_login(self.officer)
        response = self.client.post(reverse('discipline:penalty_create'), {
            'name': 'Counseling referral',
            'description': 'Attend an approved counseling session.',
            'violation': violation.pk,
            'duration_days': '',
            'policy_reference': 'KCC-DO-01',
        })
        self.assertEqual(response.status_code, 302)
        penalty = PenaltyCatalog.objects.get(name='Counseling referral')
        self.assertFalse(penalty.approved)
        self.assertEqual(
            self.client.post(reverse('discipline:sanction_assign', args=[case.pk]), {
                'penalty': penalty.pk,
                'assigned_date': assigned_date.isoformat(),
                'due_date': due_date.isoformat(),
                'responsible_staff': self.officer.pk,
            }).status_code,
            200,
        )
        self.assertFalse(DisciplinarySanction.objects.exists())

        self.client.force_login(self.admin)
        response = self.client.post(reverse('discipline:penalty_approve', args=[penalty.pk]), {
            'policy_basis': 'Approved under KCC-DO-01.',
        })
        self.assertRedirects(response, reverse('discipline:sanction_list'))
        penalty.refresh_from_db()
        self.assertTrue(penalty.approved)
        self.assertEqual(penalty.approved_by, self.admin)

        self.client.force_login(self.officer)
        response = self.client.post(reverse('discipline:sanction_assign', args=[case.pk]), {
            'penalty': penalty.pk,
            'assigned_date': assigned_date.isoformat(),
            'due_date': due_date.isoformat(),
            'responsible_staff': self.officer.pk,
        })
        self.assertRedirects(response, reverse('discipline:case_detail', args=[case.pk]))
        sanction = DisciplinarySanction.objects.get(case=case)
        self.assertEqual(sanction.review_status, DisciplinarySanction.ReviewStatus.PENDING)

        self.client.force_login(self.admin)
        response = self.client.post(reverse('discipline:sanction_review', args=[sanction.pk]), {
            'decision': 'approve',
            'review_notes': 'Approved under KCC-DO-01.',
        })
        self.assertRedirects(response, reverse('discipline:case_detail', args=[case.pk]))
        sanction.refresh_from_db()
        self.assertEqual(sanction.review_status, DisciplinarySanction.ReviewStatus.APPROVED)

        self.client.force_login(self.officer)
        response = self.client.post(reverse('discipline:sanction_status_update', args=[sanction.pk]), {
            'status': DisciplinarySanction.Status.IN_PROGRESS,
        })
        self.assertRedirects(response, reverse('discipline:sanction_list'))
        sanction.refresh_from_db()
        self.assertEqual(sanction.status, DisciplinarySanction.Status.IN_PROGRESS)
        self.assertEqual(self.client.get(reverse('discipline:sanction_list')).status_code, 200)
        sanction.due_date = assigned_date - timedelta(days=1)
        sanction.save(update_fields=('due_date', 'updated_at'))
        self.assertEqual(sanction.effective_status, DisciplinarySanction.Status.OVERDUE)
        self.assertContains(
            self.client.get(reverse('discipline:sanction_list'), {'status': 'overdue'}),
            'Counseling referral',
        )
        self.assertContains(self.client.get(reverse('discipline:case_detail', args=[case.pk])), 'Counseling referral')
