from django.contrib import messages
from django.contrib.auth import authenticate, get_user_model, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db.models import Count
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from apps.scoping import scoped_infos
from apps.discipline.models import DisciplinaryCase
from apps.discipline.permissions import visible_cases

User = get_user_model()

INVALID_LOGIN_MESSAGE = 'Invalid username or password.'


def _validate_registration_data(data):
    errors = {}

    username = data.get('username', '').strip()
    first_name = data.get('first_name', '').strip()
    last_name = data.get('last_name', '').strip()
    email = data.get('email', '').strip()
    password = data.get('password', '')
    confirm_password = data.get('confirm_password', '')

    if not username:
        errors['username'] = 'Username is required.'
    elif User.objects.filter(username=username).exists():
        errors['username'] = 'Username already taken.'

    if not first_name:
        errors['first_name'] = 'First name is required.'

    if not last_name:
        errors['last_name'] = 'Last name is required.'

    if not email:
        errors['email'] = 'Email is required.'
    else:
        try:
            validate_email(email)
        except ValidationError:
            errors['email'] = 'Enter a valid email address.'
        else:
            if User.objects.filter(email=email).exists():
                errors['email'] = 'Email already registered.'

    if not password:
        errors['password'] = 'Password is required.'
    elif len(password) < 8:
        errors['password'] = 'Password must be at least 8 characters.'
    else:
        try:
            validate_password(password)
        except ValidationError as exc:
            errors['password'] = ' '.join(exc.messages)

    if password != confirm_password:
        errors['confirm_password'] = 'Passwords do not match.'

    return errors


@require_http_methods(['GET', 'POST'])
def login_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')

    request.session.pop('failed_attempts', None)
    request.session.pop('lock_time', None)

    if request.method == 'GET':
        return render(request, 'auth/login.html')

    username = request.POST.get('username', '').strip()
    password = request.POST.get('password', '')
    user = authenticate(request, username=username, password=password)

    if user is not None:
        login(request, user)
        return redirect('dashboard')

    messages.error(request, INVALID_LOGIN_MESSAGE)
    return render(request, 'auth/login.html')


@require_POST
def logout_view(request):
    logout(request)
    return redirect('login')


@login_required(login_url='login')
def home(request):
    records = scoped_infos(request.user).order_by('-updated_at')
    cases = visible_cases(request.user).distinct()
    pending_statuses = (
        DisciplinaryCase.Status.SUBMITTED,
        DisciplinaryCase.Status.INITIAL_REVIEW,
        DisciplinaryCase.Status.RETURNED,
    )
    review_statuses = (
        DisciplinaryCase.Status.INVESTIGATION,
        DisciplinaryCase.Status.HEARING,
        DisciplinaryCase.Status.COMMITTEE,
    )
    resolved_statuses = (
        DisciplinaryCase.Status.CLOSED,
        DisciplinaryCase.Status.REFERRED,
    )
    context = {
        'dashboard_role': (
            'Administrator' if request.user.is_superuser
            else 'Staff / Officer' if request.user.is_staff
            else 'User'
        ),
        'records_count': records.count(),
        'recent_records': records[:5],
        'case_count': cases.count(),
        'pending_cases_count': cases.filter(status__in=pending_statuses).count(),
        'review_cases_count': cases.filter(status__in=review_statuses).count(),
        'resolved_cases_count': cases.filter(status__in=resolved_statuses).count(),
        'recent_cases': cases.select_related('report', 'offense_category').order_by('-opened_at')[:5],
        'records_by_level': (
            cases.exclude(report__respondent_year_section='')
            .values('report__respondent_year_section')
            .annotate(total=Count('pk', distinct=True))
            .order_by('-total', 'report__respondent_year_section')[:5]
        ),
    }

    if request.user.is_superuser:
        context.update({
            'users_count': User.objects.count(),
            'active_users_count': User.objects.filter(is_active=True).count(),
            'staff_users_count': User.objects.filter(is_staff=True, is_superuser=False).count(),
            'administrator_users_count': User.objects.filter(is_superuser=True).count(),
        })
    elif request.user.is_staff:
        context['office_records_count'] = records.count()

    return render(request, 'home.html', context)


@require_http_methods(['GET', 'POST'])
def register_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')

    if request.method == 'GET':
        return render(request, 'auth/register.html')

    form_data = {
        'username': request.POST.get('username', ''),
        'first_name': request.POST.get('first_name', ''),
        'last_name': request.POST.get('last_name', ''),
        'email': request.POST.get('email', ''),
        'password': request.POST.get('password', ''),
        'confirm_password': request.POST.get('confirm_password', ''),
    }

    errors = _validate_registration_data(form_data)
    if errors:
        return render(request, 'auth/register.html', {'errors': errors, 'form_data': form_data})

    user = User.objects.create_user(
        username=form_data['username'].strip(),
        first_name=form_data['first_name'].strip(),
        last_name=form_data['last_name'].strip(),
        email=form_data['email'].strip(),
        password=form_data['password'],
        is_staff=True,       # All self-registered accounts are Staff by default
        is_superuser=False,  # Public registration never grants Admin
    )
    login(request, user)
    return redirect('dashboard')
