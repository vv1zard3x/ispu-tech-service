from django.contrib import admin
from django.contrib.auth.admin import GroupAdmin as DjangoGroupAdmin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.contrib.auth.models import Group, User


admin.site.site_header = "Ремонт 4-48 — управление"
admin.site.site_title = "Ремонт 4-48"
admin.site.index_title = "Пользователи и роли"


if admin.site.is_registered(User):
    admin.site.unregister(User)
if admin.site.is_registered(Group):
    admin.site.unregister(Group)


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    list_display = ("username", "get_full_name", "email", "is_active", "is_staff", "is_superuser")
    search_fields = ("username", "first_name", "last_name", "email")


@admin.register(Group)
class GroupAdmin(DjangoGroupAdmin):
    list_display = ("name",)
    search_fields = ("name",)
