from django.shortcuts import redirect
from django.urls import reverse

from .models import ensure_profile


WHITELIST_PREFIXES = (
    "/static/",
    "/accounts/logout/",
    "/admin/logout/",
)


class ForcePasswordChangeMiddleware:
    """Если у пользователя выставлен флаг require_password_change — редиректим на смену пароля."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if not user or not user.is_authenticated:
            return self.get_response(request)

        path = request.path
        if any(path.startswith(prefix) for prefix in WHITELIST_PREFIXES):
            return self.get_response(request)

        try:
            target = reverse("repair:profile-password")
        except Exception:
            return self.get_response(request)

        if path == target:
            return self.get_response(request)

        profile = ensure_profile(user)
        if profile.require_password_change:
            return redirect("repair:profile-password")

        return self.get_response(request)
