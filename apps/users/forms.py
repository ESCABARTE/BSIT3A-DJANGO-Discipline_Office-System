from django import forms
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError


ROLE_ADMIN = 'admin'
ROLE_STAFF = 'staff'
ROLE_CHOICES = [
    (ROLE_ADMIN, 'Admin'),
    (ROLE_STAFF, 'Staff'),
]


def user_role(user):
    """Return a clean two-value role string for a user."""
    if user.is_superuser:
        return ROLE_ADMIN
    return ROLE_STAFF


class UserForm(forms.ModelForm):
    password = forms.CharField(
        widget=forms.PasswordInput,
        required=False,
        help_text='Leave blank to keep the current password.',
    )
    role = forms.ChoiceField(
        choices=ROLE_CHOICES,
        initial=ROLE_STAFF,
        help_text='Admin has full system access. Staff has restricted access.',
    )

    class Meta:
        model = User
        fields = [
            'username',
            'first_name',
            'last_name',
            'email',
            'password',
            'is_active',
        ]

    def __init__(self, *args, current_admin=None, **kwargs):
        """
        current_admin — the User performing this action (used for safeguard checks).
        """
        self.current_admin = current_admin
        super().__init__(*args, **kwargs)
        # Apply form-control CSS
        for name, field in self.fields.items():
            widget = field.widget
            if hasattr(widget, 'attrs'):
                if isinstance(widget, forms.CheckboxInput):
                    widget.attrs.setdefault('class', 'form-check-input')
                elif isinstance(widget, forms.Select):
                    widget.attrs.setdefault('class', 'form-select')
                else:
                    existing = widget.attrs.get('class', '')
                    if 'form-control' not in existing:
                        widget.attrs['class'] = (existing + ' form-control').strip()

        # Pre-populate role from existing instance
        if self.instance.pk:
            self.fields['role'].initial = user_role(self.instance)

        # Password required only for new users
        if not self.instance.pk:
            self.fields['password'].required = True
            self.fields['password'].help_text = ''

        # Make standard fields required
        for fname in ('username', 'first_name', 'last_name'):
            self.fields[fname].required = True

    def clean_role(self):
        role = self.cleaned_data.get('role')
        if role not in (ROLE_ADMIN, ROLE_STAFF):
            raise ValidationError('Invalid role.')
        return role

    def clean(self):
        cleaned = super().clean()
        # Safeguard: prevent demoting the last active admin
        if self.instance.pk and self.instance.is_superuser:
            new_role = cleaned.get('role')
            new_active = cleaned.get('is_active', True)
            if new_role == ROLE_STAFF or not new_active:
                active_admin_count = User.objects.filter(
                    is_superuser=True, is_active=True
                ).exclude(pk=self.instance.pk).count()
                if active_admin_count == 0:
                    if new_role == ROLE_STAFF:
                        self.add_error('role', 'Cannot demote the last active Admin account.')
                    if not new_active:
                        self.add_error('is_active', 'Cannot deactivate the last active Admin account.')
        return cleaned

    def save(self, commit=True):
        user = super().save(commit=False)
        password = self.cleaned_data.get('password')
        if password:
            user.set_password(password)
        role = self.cleaned_data.get('role', ROLE_STAFF)
        if role == ROLE_ADMIN:
            user.is_superuser = True
            user.is_staff = True   # superusers need is_staff=True for Django admin
        else:
            user.is_superuser = False
            user.is_staff = True   # All non-admin users are staff by default
        if commit:
            user.save()
        return user
