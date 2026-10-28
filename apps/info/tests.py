from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import Info


class InfoRoleAccessTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.admin = user_model.objects.create_superuser(username='admin', password='secret1234')
        self.staff = user_model.objects.create_user(
            username='officer', password='secret1234', is_staff=True
        )
        self.owner = user_model.objects.create_user(username='owner', password='secret1234')
        self.other_user = user_model.objects.create_user(username='other', password='secret1234')
        self.record = Info.objects.create(
            name='Private record', age=20, address='School', user=self.other_user
        )

    def test_staff_can_view_and_update_office_records(self):
        self.client.force_login(self.staff)

        response = self.client.get(reverse('info:info_list'))
        self.assertContains(response, 'Private record')

        update_response = self.client.post(reverse('info:info_save_ajax'), {
            'id': self.record.pk,
            'name': 'Updated record',
            'age': '21',
            'address': 'Office',
            'email': '',
        })
        self.assertEqual(update_response.status_code, 200)
        self.record.refresh_from_db()
        self.assertEqual(self.record.name, 'Updated record')
        self.assertEqual(self.record.user, self.other_user)

    def test_regular_users_cannot_view_or_update_another_users_record(self):
        self.client.force_login(self.owner)

        response = self.client.get(reverse('info:info_list'))
        self.assertNotContains(response, 'Private record')

        detail_response = self.client.get(
            reverse('info:info_get_ajax', args=[self.record.pk])
        )
        self.assertEqual(detail_response.status_code, 404)

        update_response = self.client.post(reverse('info:info_save_ajax'), {
            'id': self.record.pk,
            'name': 'Unauthorized change',
            'age': '21',
            'address': 'Office',
            'email': '',
        })
        self.assertEqual(update_response.status_code, 404)
        self.record.refresh_from_db()
        self.assertEqual(self.record.name, 'Private record')

    def test_only_administrators_can_delete_records(self):
        for user in (self.owner, self.staff):
            self.client.force_login(user)
            response = self.client.post(
                reverse('info:info_delete_ajax', args=[self.record.pk])
            )
            self.assertEqual(response.status_code, 403)
            self.assertTrue(Info.objects.filter(pk=self.record.pk).exists())

        self.client.force_login(self.admin)
        response = self.client.post(
            reverse('info:info_delete_ajax', args=[self.record.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Info.objects.filter(pk=self.record.pk).exists())
