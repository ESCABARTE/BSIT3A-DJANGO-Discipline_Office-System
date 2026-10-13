from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.info.models import Info


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

    def test_home_requires_login(self):
        response = self.client.get(reverse('dashboard'))

        self.assertEqual(response.status_code, 302)
