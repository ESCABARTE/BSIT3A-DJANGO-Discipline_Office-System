from datetime import date, timedelta
import uuid

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import NoReverseMatch, reverse
from django.utils import timezone

from .forms import CaseDecisionForm, CaseStatusForm, ReviewCaseForm, WorkflowActionForm
from .models import (
    CaseAction,
    CaseAuditLog,
    CaseDocument,
    CasePerson,
    CaseReport,
    CommitteeMember,
    DisciplinaryCase,
    DisciplinaryCommittee,
    DisciplinarySanction,
    KnightsMember,
    KnightsObservation,
    OffenseCategory,
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
        self.assertContains(official_response, 'id="id_complainant_relationship"')
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
        self.assertContains(official_home, 'Campus Rules &amp; Policy')
        self.assertNotContains(official_home, 'Policy Management')
        self.assertContains(official_home, 'data-sidebar-submenu')
        self.assertContains(official_home, 'aria-pressed="false"')
        self.assertContains(official_home, 'Policy Records')
        self.assertContains(official_home, 'Policy Log')
        self.assertContains(official_home, reverse('discipline:incident_log'))
        self.assertNotContains(official_home, 'Disciplinary Management Dashboard')
        self.assertNotContains(official_home, 'Violations')
        self.assertNotContains(official_home, 'Sanctions/Penalties')

        self.client.force_login(self.admin)
        admin_home = self.client.get(reverse('dashboard'))
        self.assertContains(admin_home, 'Add Policy')

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
            'complainant_relationship': 'Student',
            'reporting_method': 'written',
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
        self.assertEqual(report.complainant_relationship, 'Student')
        self.assertEqual(report.reporting_method, 'written')
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
        case, _ = self.make_report(submitter=self.student)
        case.status = DisciplinaryCase.Status.RESOLVED
        case.save(update_fields=['status'])
        self.client.force_login(self.staff)
        response = self.client.post(reverse('discipline:case_status_update', args=[case.pk]), {
            'status': DisciplinaryCase.Status.CLOSED,
            'remarks': 'Attempt to close without decision role.',
        })
        self.assertEqual(response.status_code, 403)
        case.refresh_from_db()
        self.assertNotEqual(case.status, DisciplinaryCase.Status.CLOSED)

    def test_official_dashboard_and_catalog_pages_render(self):
        self.client.force_login(self.admin)
        for url_name in (
            'discipline:dashboard',
            'discipline:case_list',
            'discipline:policy_list',
            'discipline:committee_list',
            'discipline:probation_list',
            'discipline:suspension_list',
            'discipline:reports',
            'discipline:incident_log',
        ):
            with self.subTest(url_name=url_name):
                self.assertEqual(self.client.get(reverse(url_name)).status_code, 200)

    def test_policy_can_be_created_without_optional_permitted_sanctions(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse('discipline:policy_create'), {
            'name': 'Test policy with no penalty guidance',
            'description': 'A policy whose approved penalty guidance is not specified.',
            'classification': 'unclassified',
            'decision_options': '',
            'policy_version': 'Pending school approval',
        })

        self.assertRedirects(response, reverse('discipline:policy_list'))
        category = OffenseCategory.objects.get(name='Test policy with no penalty guidance')
        self.assertEqual(category.permitted_sanctions, [])

    def test_policy_decision_options_are_configured_in_policy_management(self):
        self.client.force_login(self.admin)
        form_response = self.client.get(reverse('discipline:policy_create'))
        self.assertContains(form_response, 'Available decision / penalty options')
        response = self.client.post(reverse('discipline:policy_create'), {
            'name': 'Configured outcomes policy',
            'description': 'Options are entered only when approved by the school.',
            'classification': 'unclassified',
            'decision_options': 'Approved option one\nApproved option two',
            'policy_version': '2026-A',
            'active': 'on',
        })
        self.assertRedirects(response, reverse('discipline:policy_list'))
        category = OffenseCategory.objects.get(name='Configured outcomes policy')
        self.assertEqual(category.permitted_sanctions, ['Approved option one', 'Approved option two'])

    def test_policy_model_normalizes_null_permitted_sanctions(self):
        category = OffenseCategory.objects.create(
            name='Model-level empty penalty guidance',
            description='A policy without specified penalty guidance.',
            permitted_sanctions=None,
        )

        self.assertEqual(category.permitted_sanctions, [])

    def test_finding_form_requires_explicit_record_and_sanction_action_is_unavailable(self):
        finding_form = WorkflowActionForm(data={'action_type': 'finding', 'details': 'Reviewed evidence.'})
        self.assertFalse(finding_form.is_valid())
        self.assertIn('finding', finding_form.errors)

        action_form = WorkflowActionForm()
        self.assertNotIn('sanction', [value for value, _ in action_form.fields['action_type'].choices])
        self.assertNotIn('status', [value for value, _ in action_form.fields['action_type'].choices])
        self.assertNotIn('to_status', action_form.fields)
        self.assertNotIn('final_sanction', action_form.fields)

    def test_case_status_transitions_are_validated_and_authorized(self):
        case, _ = self.make_report(submitter=self.student)
        self.client.force_login(self.staff)
        invalid_response = self.client.post(reverse('discipline:case_status_update', args=[case.pk]), {
            'status': DisciplinaryCase.Status.CLOSED,
            'remarks': 'Attempted to close without review.',
        })
        self.assertEqual(invalid_response.status_code, 200)
        case.refresh_from_db()
        self.assertEqual(case.status, DisciplinaryCase.Status.SUBMITTED)

        valid_response = self.client.post(reverse('discipline:case_status_update', args=[case.pk]), {
            'status': DisciplinaryCase.Status.INITIAL_REVIEW,
            'remarks': 'Initial review started.',
        })
        self.assertEqual(valid_response.status_code, 302)
        case.refresh_from_db()
        self.assertEqual(case.status, DisciplinaryCase.Status.INITIAL_REVIEW)
        self.assertTrue(CaseAction.objects.filter(
            case=case,
            action_type=CaseAction.ActionType.STATUS,
            to_status=DisciplinaryCase.Status.INITIAL_REVIEW,
            authority_position='Staff',
        ).exists())

        self.client.force_login(self.student)
        denied_response = self.client.post(reverse('discipline:case_status_update', args=[case.pk]), {
            'status': DisciplinaryCase.Status.INVESTIGATION,
            'remarks': 'Unauthorized status change.',
        })
        self.assertEqual(denied_response.status_code, 403)

    def test_status_form_lists_all_twelve_statuses_and_limits_designation(self):
        case, _ = self.make_report(submitter=self.student)
        staff_form = CaseStatusForm(case=case, user=self.staff)
        admin_form = CaseStatusForm(case=case, user=self.admin)

        self.assertEqual(
            [value for value, _ in staff_form.fields['status'].choices],
            [
                'initial_review', 'returned', 'investigation', 'hearing', 'committee',
                'decision', 'decision_recorded', 'guardian_hearing', 'appeal',
                'resolved', 'closed', 'referred',
            ],
        )
        self.assertEqual(staff_form.fields['actor_position'].choices, [('', '— Select position —'), ('Staff', 'Staff')])
        self.assertEqual(admin_form.fields['actor_position'].choices, [('', '— Select position —'), ('Admin', 'Admin')])

    def test_case_action_persists_only_authorized_designation(self):
        case, _ = self.make_report(submitter=self.student)
        self.client.force_login(self.staff)
        denied_designation = self.client.post(reverse('discipline:case_action', args=[case.pk]), {
            'action_type': CaseAction.ActionType.NOTE,
            'details': 'Review note.',
            'actor_position': 'Admin',
        })
        self.assertEqual(denied_designation.status_code, 200)
        self.assertFalse(case.actions.exists())

        response = self.client.post(reverse('discipline:case_action', args=[case.pk]), {
            'action_type': CaseAction.ActionType.NOTE,
            'details': 'Review note.',
            'actor_position': 'Staff',
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(case.actions.get().authority_position, 'Staff')

    def test_review_record_saves_findings_recommendation_and_reviewer_designation(self):
        case, _ = self.make_report(submitter=self.student)
        self.client.force_login(self.staff)
        review_page = self.client.get(reverse('discipline:case_review', args=[case.pk]))
        self.assertEqual(review_page.status_code, 200)
        self.assertContains(review_page, 'Verify Supporting Documents')
        self.assertContains(review_page, 'Proceed for Investigation')
        response = self.client.post(reverse('discipline:case_review', args=[case.pk]), {
            'information_findings': 'The incident report is complete and consistent.',
            'document_verification': 'The available attachment is relevant to the report.',
            'recommended_classification': DisciplinaryCase.Classification.MINOR,
            'recommendation': 'proceed_investigation',
            'review_remarks': 'Proceed with interviews and preserve the submitted evidence.',
            'actor_position': 'Staff',
        })
        self.assertEqual(response.status_code, 302)
        review = case.actions.get(action_type=CaseAction.ActionType.REVIEW)
        self.assertEqual(review.actor, self.staff)
        self.assertEqual(review.authority_position, 'Staff')
        self.assertEqual(review.recommendation, 'Proceed for Investigation')
        self.assertIn('Classification at review: Not classified', review.details)
        self.assertIn('The incident report is complete and consistent.', review.details)

    def test_case_level_can_be_verified_by_authorized_staff_and_logged(self):
        case, _ = self.make_report(submitter=self.student)
        self.client.force_login(self.staff)
        response = self.client.post(reverse('discipline:case_level_update', args=[case.pk]), {
            'case_level': DisciplinaryCase.CaseLevel.SHS,
        })
        self.assertEqual(response.status_code, 302)
        case.refresh_from_db()
        self.assertEqual(case.case_level, DisciplinaryCase.CaseLevel.SHS)
        level_audit = CaseAuditLog.objects.filter(
            case=case,
            actor=self.staff,
            action='case_level_updated',
        ).first()
        self.assertIsNotNone(level_audit)
        self.assertIn('SHS', level_audit.details)

    def test_incident_log_filters_by_case_level_instead_of_report_type(self):
        jhs_case, _ = self.make_report(submitter=self.student)
        jhs_case.case_level = DisciplinaryCase.CaseLevel.JHS
        jhs_case.save(update_fields=['case_level'])
        shs_case, _ = self.make_report(submitter=self.student)
        shs_case.case_level = DisciplinaryCase.CaseLevel.SHS
        shs_case.save(update_fields=['case_level'])

        self.client.force_login(self.admin)
        response = self.client.get(reverse('discipline:incident_log'), {'case_level': 'jhs'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['reports'].paginator.count, 1)
        self.assertContains(response, 'Case Level')
        self.assertNotContains(response, 'Report type')
        self.assertContains(response, jhs_case.reference)
        self.assertNotContains(response, shs_case.reference)

    def test_final_decision_requires_authority_and_uses_policy_outcomes(self):
        case, _ = self.make_report()
        policy = OffenseCategory.objects.create(
            name='Decision policy',
            description='Approved options for test decisions.',
            permitted_sanctions=['Approved policy outcome'],
        )
        case.offense_category = policy
        case.classification = DisciplinaryCase.Classification.MAJOR
        case.status = DisciplinaryCase.Status.DECISION
        case.save()

        decision_form = CaseDecisionForm(case=case, user=self.admin)
        self.assertIn(('Approved policy outcome', 'Approved policy outcome'), decision_form.fields['decision_outcome'].choices)
        self.assertNotIn(('unlisted', 'unlisted'), decision_form.fields['decision_outcome'].choices)

        self.client.force_login(self.staff)
        response = self.client.post(reverse('discipline:case_decision', args=[case.pk]), {
            'finding': DisciplinaryCase.Finding.SUBSTANTIATED,
            'decision_outcome': 'Approved policy outcome',
            'authority_position': 'Discipline Officer',
            'details': 'An unauthorized decision attempt.',
        })
        self.assertEqual(response.status_code, 403)
        self.assertFalse(case.actions.filter(action_type=CaseAction.ActionType.DECISION).exists())

        self.client.force_login(self.admin)
        response = self.client.post(reverse('discipline:case_decision', args=[case.pk]), {
            'finding': DisciplinaryCase.Finding.SUBSTANTIATED,
            'decision_outcome': 'Approved policy outcome',
            'authority_position': 'System administrator',
            'details': 'Decision based on reviewed evidence.',
        })
        self.assertEqual(response.status_code, 302)
        case.refresh_from_db()
        self.assertEqual(case.status, DisciplinaryCase.Status.DECISION_RECORDED)
        self.assertTrue(case.classification_reviewed)
        self.assertTrue(case.actions.filter(
            action_type=CaseAction.ActionType.DECISION,
            decision_outcome='Approved policy outcome',
        ).exists())
        self.assertTrue(case.actions.filter(
            action_type=CaseAction.ActionType.STATUS,
            to_status=DisciplinaryCase.Status.DECISION_RECORDED,
            authority_position='System administrator',
        ).exists())

    def test_preliminary_classification_is_audited_without_finding_responsibility(self):
        case, _ = self.make_report()
        policy = OffenseCategory.objects.create(
            name='Classification policy',
            description='A policy for preliminary classification tests.',
        )
        self.client.force_login(self.staff)
        response = self.client.post(reverse('discipline:case_classify', args=[case.pk]), {
            'classification': DisciplinaryCase.Classification.SERIOUS,
            'offense_category': policy.pk,
            'classification_factual_basis': 'Reported facts require review. Preliminary only.',
            'change_reason': 'Initial review.',
        })
        self.assertEqual(response.status_code, 302)
        case.refresh_from_db()
        self.assertEqual(case.finding, DisciplinaryCase.Finding.NOT_DETERMINED)
        self.assertFalse(case.classification_reviewed)
        self.assertEqual(case.policy_version, policy.policy_version)
        self.assertTrue(CaseAuditLog.objects.filter(case=case, action='classification_recorded').exists())

    def test_discipline_officer_role_cannot_submit_removed_sanction_action(self):
        self.assertTrue(can_decide(self.staff))
        self.assertFalse(can_finalize_decision(self.staff))
        self.client.force_login(self.staff)
        case, _ = self.make_report()
        response = self.client.post(reverse('discipline:case_action', args=[case.pk]), {
            'action_type': CaseAction.ActionType.SANCTION,
            'final_sanction': DisciplinaryCase.Sanction.SUSPENSION,
            'authority': 'Discipline Officer',
            'details': 'Attempted without a final decision authority.',
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(CaseAction.objects.filter(case=case).count(), 0)

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
        hearing_at = (timezone.now() + timedelta(days=1)).strftime('%Y-%m-%dT%H:%M')
        response = self.client.post(reverse('discipline:summons_create', args=[case.pk]), {
            'respondent': respondent.pk,
            'guardian_name': 'Parent Name',
            'guardian_relationship': 'Parent',
            'guardian_contact': '555-0100',
            'hearing_at': hearing_at,
            'hearing_location': 'Discipline Office',
            'instructions': 'Attend the scheduled hearing.',
            'service_notes': '',
        })
        self.assertEqual(response.status_code, 302)
        summons = Summons.objects.get(case=case)
        self.assertTrue(summons.reference)
        response = self.client.post(reverse('discipline:summons_print', args=[case.pk, summons.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(CaseAuditLog.objects.filter(case=case, action='summons_printed').exists())
        response = self.client.post(reverse('discipline:summons_serve', args=[case.pk, summons.pk]), {
            'received_at': '2000-01-01', 'service_notes': '', 'acknowledgment': '',
        })
        self.assertEqual(response.status_code, 302)
        summons.refresh_from_db()
        self.assertIsNone(summons.served_at)

        self.client.force_login(self.admin)
        response = self.client.post(reverse('discipline:summons_approve', args=[case.pk, summons.pk]), {
            'approved_by_position': 'System administrator',
        })
        self.assertEqual(response.status_code, 302)
        response = self.client.post(reverse('discipline:summons_print', args=[case.pk, summons.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Approved by:')
        self.assertTrue(CaseAuditLog.objects.filter(case=case, action='summons_printed').exists())
        self.client.force_login(self.staff)
        response = self.client.post(reverse('discipline:summons_serve', args=[case.pk, summons.pk]), {
            'received_at': '2000-01-01', 'service_notes': '', 'acknowledgment': '',
        })
        self.assertEqual(response.status_code, 200)
        summons.refresh_from_db()
        self.assertIsNone(summons.received_at)
        response = self.client.post(reverse('discipline:summons_serve', args=[case.pk, summons.pk]), {
            'received_at': '2099-01-01',
            'served_at': (timezone.now() + timedelta(days=2)).strftime('%Y-%m-%dT%H:%M'),
            'service_notes': '',
            'acknowledgment': 'on',
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


class DisciplineRemovalRegressionTests(TestCase):
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

    def test_intake_saves_neutral_case_and_deduplicates_reposts(self):
        token = str(uuid.uuid4())
        payload = {
            'report_type': CaseReport.ReportType.INCIDENT,
            'submission_token': token,
            'reporting_position': 'Discipline Officer',
            'case_level': DisciplinaryCase.CaseLevel.COLLEGE,
            'respondent_name': 'Test Student',
            'respondent_student_id': 'S-200',
            'narration': 'Incident submitted for review.',
            'acknowledgment': 'on',
        }
        self.client.force_login(self.officer)
        first = self.client.post(reverse('discipline:intake'), payload)
        self.assertEqual(first.status_code, 302)
        case = DisciplinaryCase.objects.get()
        self.assertEqual(case.finding, DisciplinaryCase.Finding.NOT_DETERMINED)
        self.assertEqual(case.case_level, DisciplinaryCase.CaseLevel.COLLEGE)

        duplicate = self.client.post(reverse('discipline:intake'), payload)
        self.assertEqual(duplicate.status_code, 302)
        self.assertEqual(DisciplinaryCase.objects.count(), 1)

    def test_intake_does_not_expose_removed_catalog_controls(self):
        self.client.force_login(self.officer)
        response = self.client.get(reverse('discipline:intake'))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Violation identification')
        self.assertNotContains(response, 'Sanction / penalty assignment')

        self.client.force_login(self.student)
        response = self.client.get(reverse('discipline:intake'))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Violation identification')
        self.assertNotContains(response, 'Sanction / penalty assignment')

    def test_removed_management_catalog_and_assignment_routes_are_unavailable(self):
        removed_names = (
            'discipline:management_dashboard',
            'discipline:violation_list',
            'discipline:violation_create',
            'discipline:violation_detail',
            'discipline:violation_archive',
            'discipline:case_violation_assign',
            'discipline:sanction_list',
            'discipline:penalty_create',
            'discipline:penalty_approve',
            'discipline:sanction_assign',
            'discipline:sanction_assign_for_violation',
            'discipline:sanction_review',
            'discipline:sanction_status_update',
        )
        for url_name in removed_names:
            with self.subTest(url_name=url_name):
                with self.assertRaises(NoReverseMatch):
                    reverse(url_name)

    def test_uploaded_evidence_is_private_and_requires_case_authority(self):
        token = str(uuid.uuid4())
        self.client.force_login(self.student)
        response = self.client.post(reverse('discipline:intake'), {
            'report_type': CaseReport.ReportType.COMPLAINT,
            'submission_token': token,
            'complainant_name': 'Student Reporter',
            'complainant_student_id': 'S-100',
            'complainant_relationship': 'Student',
            'reporting_method': 'written',
            'respondent_name': 'Test Student',
            'respondent_student_id': 'S-200',
            'case_level': DisciplinaryCase.CaseLevel.COLLEGE,
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

    def test_confidential_student_history_hides_removed_catalog_details_and_preserves_data(self):
        violation = self.create_violation()
        case = self.create_case(violation)
        sanction = DisciplinarySanction.objects.create(
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
        self.assertContains(response, case.reference)
        self.assertNotContains(response, 'School conduct violation')
        self.assertNotContains(response, 'Counseling referral')
        self.assertEqual(case.violation_id, violation.pk)
        self.assertTrue(DisciplinarySanction.objects.filter(pk=sanction.pk).exists())
        case_response = self.client.get(reverse('discipline:case_detail', args=[case.pk]))
        self.assertEqual(case_response.status_code, 200)
        self.assertNotContains(case_response, 'School conduct violation')
        self.assertNotContains(case_response, 'Counseling referral')
