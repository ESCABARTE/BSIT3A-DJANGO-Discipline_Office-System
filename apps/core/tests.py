from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.info.models import Info
from apps.discipline.models import CaseReport, DisciplinaryCase


class HomeViewTests(TestCase):
    def test_home_renders_for_authenticated_user(self):
        user = get_user_model().objects.create_user(username='admin', password='secret1234')

        self.client.force_login(user)
        response = self.client.get(reverse('dashboard'))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'home.html')
        self.assertEqual(response.context['dashboard_role'], 'User')
        self.assertEqual(response.context['records_count'], 0)
        self.assertNotIn('users_count', response.context)

    def test_dashboard_only_includes_the_signed_in_users_records(self):
        user_model = get_user_model()
        user = user_model.objects.create_user(username='member', password='secret1234')
        other_user = user_model.objects.create_user(username='other', password='secret1234')
        Info.objects.create(name='Member record', age=20, address='Member address', user=user)
        Info.objects.create(name='Private record', age=21, address='Private address', user=other_user)

        self.client.force_login(user)
        response = self.client.get(reverse('dashboard'))

        self.assertEqual(response.context['records_count'], 1)
        self.assertEqual(
            [record.name for record in response.context['recent_records']],
            ['Member record'],
        )
        self.assertContains(response, 'Member record')
        self.assertNotContains(response, 'Private record')

    def test_staff_dashboard_shows_office_role_and_real_record_count(self):
        staff = get_user_model().objects.create_user(
            username='officer', password='secret1234', is_staff=True
        )
        Info.objects.create(name='Office record', age=30, address='Office')

        self.client.force_login(staff)
        response = self.client.get(reverse('dashboard'))

        self.assertEqual(response.context['dashboard_role'], 'Staff / Officer')
        self.assertEqual(response.context['records_count'], 1)
        self.assertEqual(response.context['office_records_count'], 1)

    def test_administrator_dashboard_uses_real_account_counts(self):
        user_model = get_user_model()
        admin = user_model.objects.create_superuser(username='admin', password='secret1234')
        user_model.objects.create_user(username='active', password='secret1234')
        user_model.objects.create_user(username='inactive', password='secret1234', is_active=False)
        Info.objects.create(name='Admin record', age=30, address='Office', user=admin)

        self.client.force_login(admin)
        response = self.client.get(reverse('dashboard'))

        self.assertEqual(response.context['dashboard_role'], 'Administrator')
        self.assertEqual(response.context['users_count'], 3)
        self.assertEqual(response.context['active_users_count'], 2)
        self.assertEqual(response.context['records_count'], 1)

    def test_dashboard_shows_scoped_case_metrics_and_recent_records(self):
        admin = get_user_model().objects.create_superuser(username='case-admin', password='secret1234')
        cases = [
            (DisciplinaryCase.Status.SUBMITTED, 'Year 1'),
            (DisciplinaryCase.Status.RETURNED, 'Year 1'),
            (DisciplinaryCase.Status.INVESTIGATION, ''),
            (DisciplinaryCase.Status.CLOSED, ''),
        ]
        for status, year_section in cases:
            case = DisciplinaryCase.objects.create(
                source_type=CaseReport.ReportType.COMPLAINT,
                status=status,
            )
            CaseReport.objects.create(
                case=case,
                report_type=CaseReport.ReportType.COMPLAINT,
                submitted_by=admin,
                respondent_name='Test Student',
                respondent_year_section=year_section,
                narration='Test report',
            )

        self.client.force_login(admin)
        response = self.client.get(reverse('dashboard'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['case_count'], 4)
        self.assertEqual(response.context['pending_cases_count'], 2)
        self.assertEqual(response.context['review_cases_count'], 1)
        self.assertEqual(response.context['resolved_cases_count'], 1)
        self.assertEqual(response.context['records_by_level'][0]['total'], 2)
        self.assertContains(response, 'Recent Discipline Records')
        self.assertContains(response, '?status_group=pending')
        self.assertContains(response, '?status_group=review')
        self.assertContains(response, '?status_group=resolved')
        for group, expected_count in (('pending', 2), ('review', 1), ('resolved', 1)):
            case_list = self.client.get(reverse('discipline:case_list'), {'status_group': group})
            self.assertEqual(case_list.status_code, 200)
            self.assertEqual(case_list.context['cases'].paginator.count, expected_count)

    def test_home_requires_login(self):
        response = self.client.get(reverse('dashboard'))

        self.assertEqual(response.status_code, 302)

    def test_logout_requires_post(self):
        user = get_user_model().objects.create_user(username='member', password='secret1234')
        self.client.force_login(user)

        response = self.client.get(reverse('logout'))

        self.assertEqual(response.status_code, 405)
        self.assertTrue(response.wsgi_request.user.is_authenticated)

    def test_logout_ends_session_with_post(self):
        user = get_user_model().objects.create_user(username='member', password='secret1234')
        self.client.force_login(user)

        response = self.client.post(reverse('logout'))

        self.assertRedirects(response, reverse('login'))
        self.assertNotIn('_auth_user_id', self.client.session)
