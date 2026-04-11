from django import template

register = template.Library()


@register.filter
def has_role(user, role_name: str) -> bool:
    if not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_superuser", False):
        return True
    return user.groups.filter(name=role_name).exists()


@register.filter
def has_any_role(user, role_names: str) -> bool:
    if not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_superuser", False):
        return True
    names = [name.strip() for name in role_names.split(",") if name.strip()]
    return user.groups.filter(name__in=names).exists()
