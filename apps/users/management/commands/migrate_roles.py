"""
Management command: migrate_roles
----------------------------------
Ensures all non-superuser accounts have is_staff=True (Staff role).
Safe to run multiple times — only updates accounts that need it.

Usage:
    python manage.py migrate_roles
"""
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model


class Command(BaseCommand):
    help = 'Set is_staff=True on all non-Admin users (Staff role migration).'

    def handle(self, *args, **options):
        User = get_user_model()

        plain_users = User.objects.filter(is_superuser=False, is_staff=False)
        count = plain_users.count()

        if count == 0:
            self.stdout.write(self.style.SUCCESS('No accounts needed updating — all users already have a role.'))
            return

        updated = plain_users.update(is_staff=True)
        self.stdout.write(
            self.style.SUCCESS(
                f'Migrated {updated} account(s) to Staff role (is_staff=True).'
            )
        )

        # Summary
        admin_count = User.objects.filter(is_superuser=True).count()
        staff_count = User.objects.filter(is_superuser=False, is_staff=True).count()
        self.stdout.write(f'  Admin accounts: {admin_count}')
        self.stdout.write(f'  Staff accounts: {staff_count}')
