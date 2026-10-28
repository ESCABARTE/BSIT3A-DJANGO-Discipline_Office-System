from django.contrib import messages
from django.contrib.auth.models import User
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.paginator import Paginator
from django.db import IntegrityError
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_POST

from .forms import ROLE_ADMIN, ROLE_STAFF, user_role


def _is_admin(user):
    return user.is_authenticated and user.is_superuser


def _role_display(user):
    """Human-readable role label for a user."""
    return 'Admin' if user.is_superuser else 'Staff'


# ── Page view ─────────────────────────────────────────────────────────────────

@login_required(login_url='login')
@user_passes_test(_is_admin, login_url='dashboard')
def user_list_page(request):
    query = request.GET.get('q', '').strip()
    role_filter = request.GET.get('role', '').strip()

    qs = User.objects.all().order_by('-date_joined')

    if query:
        qs = qs.filter(
            Q(username__icontains=query) |
            Q(first_name__icontains=query) |
            Q(last_name__icontains=query) |
            Q(email__icontains=query)
        )

    if role_filter == ROLE_ADMIN:
        qs = qs.filter(is_superuser=True)
    elif role_filter == ROLE_STAFF:
        qs = qs.filter(is_superuser=False)

    paginator = Paginator(qs, 15)
    users = paginator.get_page(request.GET.get('page', 1))

    return render(request, 'users/list.html', {
        'users': users,
        'query': query,
        'role_filter': role_filter,
        'total_count': User.objects.count(),
        'admin_count': User.objects.filter(is_superuser=True).count(),
        'staff_count': User.objects.filter(is_superuser=False).count(),
        'active_count': User.objects.filter(is_active=True).count(),
    })


# ── AJAX: list ────────────────────────────────────────────────────────────────

@login_required(login_url='login')
@user_passes_test(_is_admin, login_url='dashboard')
def user_list_ajax(request):
    qs = User.objects.all().order_by('-date_joined')
    paginator = Paginator(qs, 15)
    page = paginator.get_page(request.GET.get('page', 1))
    data = [
        {
            'id':           u.id,
            'username':     u.username,
            'first_name':   u.first_name,
            'last_name':    u.last_name,
            'email':        u.email,
            'role':         _role_display(u),
            'is_active':    u.is_active,
            'date_joined':  u.date_joined.strftime('%b %d, %Y'),
        }
        for u in page.object_list
    ]
    return JsonResponse({
        'data':     data,
        'has_next': page.has_next(),
        'has_prev': page.has_previous(),
        'page':     page.number,
        'pages':    page.paginator.num_pages,
        'total':    page.paginator.count,
    })


# ── AJAX: get single user ─────────────────────────────────────────────────────

@login_required(login_url='login')
@user_passes_test(_is_admin, login_url='dashboard')
def user_get_ajax(request, pk):
    u = get_object_or_404(User, pk=pk)
    return JsonResponse({
        'id':         u.id,
        'username':   u.username,
        'first_name': u.first_name,
        'last_name':  u.last_name,
        'email':      u.email,
        'role':       user_role(u),
        'is_active':  u.is_active,
    })


# ── AJAX: create / update user ────────────────────────────────────────────────

@login_required(login_url='login')
@user_passes_test(_is_admin, login_url='dashboard')
@require_POST
def user_save_ajax(request):
    user_id  = request.POST.get('id')
    is_update = bool(user_id)

    # --- Load or create instance ---
    if is_update:
        target = get_object_or_404(User, pk=user_id)
    else:
        target = User()

    username  = request.POST.get('username', '').strip()
    email     = request.POST.get('email', '').strip()
    role      = request.POST.get('role', ROLE_STAFF).strip()
    is_active = request.POST.get('is_active') == 'true'
    password  = request.POST.get('password', '').strip()

    # --- Basic validation ---
    if not username:
        return JsonResponse({'error': 'Username is required.'}, status=400)
    if role not in (ROLE_ADMIN, ROLE_STAFF):
        return JsonResponse({'error': 'Invalid role.'}, status=400)

    # --- Safeguard: last active Admin ---
    if is_update and target.is_superuser:
        new_is_admin = (role == ROLE_ADMIN)
        active_admin_others = User.objects.filter(
            is_superuser=True, is_active=True
        ).exclude(pk=target.pk).count()
        if (not new_is_admin or not is_active) and active_admin_others == 0:
            noun = 'demote' if not new_is_admin else 'deactivate'
            return JsonResponse(
                {'error': f'Cannot {noun} the last active Admin account.'},
                status=400,
            )

    # --- Prevent Staff from gaining Admin via API ---
    if not request.user.is_superuser:
        return JsonResponse({'error': 'Only an Admin may manage user roles.'}, status=403)

    # --- Duplicate check ---
    dup_qs = User.objects.filter(username=username)
    if is_update:
        dup_qs = dup_qs.exclude(pk=user_id)
    if dup_qs.exists():
        return JsonResponse({'error': 'That username is already taken.'}, status=400)
    if email:
        email_qs = User.objects.filter(email=email)
        if is_update:
            email_qs = email_qs.exclude(pk=user_id)
        if email_qs.exists():
            return JsonResponse({'error': 'That email is already in use.'}, status=400)

    # --- Apply ---
    target.username   = username
    target.first_name = request.POST.get('first_name', '').strip()
    target.last_name  = request.POST.get('last_name', '').strip()
    target.email      = email
    target.is_active  = is_active

    if role == ROLE_ADMIN:
        target.is_superuser = True
        target.is_staff     = True
    else:
        target.is_superuser = False
        target.is_staff     = True   # All staff accounts have is_staff=True

    if password:
        target.set_password(password)
    elif not is_update:
        return JsonResponse({'error': 'Password is required for new users.'}, status=400)

    try:
        target.save()
    except IntegrityError:
        return JsonResponse(
            {'error': 'Could not save due to a conflict (duplicate username or email).'},
            status=400,
        )

    messages.success(
        request,
        f'Account "{target.username}" {"updated" if is_update else "created"} successfully.',
    )

    return JsonResponse({
        'status':     'success',
        'id':         target.id,
        'username':   target.username,
        'first_name': target.first_name,
        'last_name':  target.last_name,
        'email':      target.email,
        'role':       _role_display(target),
        'is_active':  target.is_active,
    })


# ── AJAX: delete user ─────────────────────────────────────────────────────────

@login_required(login_url='login')
@user_passes_test(_is_admin, login_url='dashboard')
@require_POST
def user_delete_ajax(request, pk):
    target = get_object_or_404(User, pk=pk)

    if request.user == target:
        return JsonResponse({'error': 'You cannot delete your own account.'}, status=400)

    if target.is_superuser:
        active_admin_others = User.objects.filter(
            is_superuser=True, is_active=True
        ).exclude(pk=target.pk).count()
        if active_admin_others == 0:
            return JsonResponse(
                {'error': 'Cannot delete the last active Admin account.'},
                status=400,
            )

    username = target.username
    target.delete()
    return JsonResponse({
        'status':  'deleted',
        'message': f'Account "{username}" has been deleted.',
    })
