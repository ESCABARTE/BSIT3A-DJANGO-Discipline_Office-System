from .permissions import is_official


def navigation(request):
    return {'is_authorized_official': is_official(request.user)}
