from django.shortcuts import get_object_or_404
from apps.info.models import Info

def scoped_infos(user):
    """Return records visible to the user's existing Django role."""
    if user.is_superuser or user.is_staff:
        return Info.objects.all()
    return Info.objects.filter(user=user)

def scoped_info(user, pk):
    return get_object_or_404(scoped_infos(user), pk=pk)
