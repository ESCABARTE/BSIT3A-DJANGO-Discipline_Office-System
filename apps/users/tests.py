from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse


class UserListTemplateTests(TestCase):
    def test_user_list_does_not_render_template_comment_dividers(self):
        admin = get_user_model().objects.create_superuser(
            username='admin',
            password='strong-test-pass',
        )
        self.client.force_login(admin)

        response = self.client.get(reverse('users:user_list'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Add User')
        self.assertContains(response, 'Delete User')
        self.assertNotContains(response, '{#')
        self.assertNotContains(response, '════════')
