from decimal import Decimal

from django import forms
from django.contrib.auth.models import Group, User
from django.db import transaction
from django.forms import formset_factory, inlineformset_factory

from .models import (
    Customer,
    DeviceCategory,
    DeviceModel,
    DiagnosticResult,
    OrderPartLine,
    Part,
    Payment,
    ProcurementRequest,
    StockItem,
    UserProfile,
    WorkCatalogItem,
    WorkItem,
    WorkOrder,
    ensure_profile,
)


ROLE_CHOICES = (
    ("manager", "Менеджер"),
    ("technician", "Исполнитель"),
    ("warehouse", "Склад"),
    ("admin", "Администратор"),
)


class WorkOrderCreateForm(forms.ModelForm):
    category = forms.ModelChoiceField(
        queryset=DeviceCategory.objects.all(),
        required=False,
        label="Категория устройства",
        empty_label="Выберите категорию",
    )
    device_model = forms.ModelChoiceField(
        queryset=DeviceModel.objects.select_related("category"),
        required=False,
        label="Устройство",
        empty_label="Сначала выберите категорию",
    )
    customer = forms.ModelChoiceField(
        queryset=Customer.objects.all(),
        required=False,
        label="Клиент",
        empty_label="Выберите клиента",
    )
    planned_deadline = forms.SplitDateTimeField(
        required=False,
        label="Плановый срок",
        input_date_formats=["%Y-%m-%d"],
        input_time_formats=["%H:%M"],
        widget=forms.SplitDateTimeWidget(
            date_attrs={"type": "date"},
            time_attrs={"type": "time", "step": "300"},
        ),
    )

    class Meta:
        model = WorkOrder
        fields = [
            "customer",
            "category",
            "device_model",
            "serial_number",
            "issue_description",
            "planned_deadline",
            "diagnosis_fee",
            "notes",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["customer"].queryset = Customer.objects.order_by("name")
        self.fields["category"].queryset = DeviceCategory.objects.order_by("name")
        self.fields["device_model"].queryset = DeviceModel.objects.select_related("category").order_by(
            "category__name", "brand", "model"
        )
        self.fields["serial_number"].required = False
        self.fields["issue_description"].widget.attrs.update(
            {"placeholder": "Опишите неисправность со слов клиента", "rows": 3}
        )
        self.fields["notes"].widget.attrs.update({"placeholder": "Комментарий менеджера по заявке"})
        self.fields["planned_deadline"].widget.widgets[0].attrs.update(
            {"class": "deadline-date", "aria-label": "Дата планового срока"}
        )
        self.fields["planned_deadline"].widget.widgets[1].attrs.update(
            {"class": "deadline-time", "aria-label": "Время планового срока"}
        )


class AssignTechnicianForm(forms.Form):
    technician = forms.ModelChoiceField(queryset=User.objects.none(), label="Исполнитель")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["technician"].queryset = User.objects.filter(groups__name="technician").distinct()


class ReassignRolesForm(forms.Form):
    manager = forms.ModelChoiceField(
        queryset=User.objects.none(),
        required=False,
        label="Менеджер",
        empty_label="— не назначен —",
    )
    technician = forms.ModelChoiceField(
        queryset=User.objects.none(),
        required=False,
        label="Техник",
        empty_label="— не назначен —",
    )
    warehouse_keeper = forms.ModelChoiceField(
        queryset=User.objects.none(),
        required=False,
        label="Кладовщик",
        empty_label="— не назначен —",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["manager"].queryset = User.objects.filter(
            groups__name="manager"
        ).union(User.objects.filter(is_superuser=True)).order_by("username")
        self.fields["technician"].queryset = User.objects.filter(
            groups__name="technician"
        ).order_by("username")
        self.fields["warehouse_keeper"].queryset = User.objects.filter(
            groups__name="warehouse"
        ).order_by("username")


class DiagnosticForm(forms.ModelForm):
    class Meta:
        model = DiagnosticResult
        fields = ["findings"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["findings"].widget.attrs.update(
            {"placeholder": "Что обнаружено и какие работы требуются", "rows": 4}
        )


class WorkItemForm(forms.ModelForm):
    catalog_item = forms.ModelChoiceField(
        queryset=WorkCatalogItem.objects.all(),
        required=False,
        label="Из справочника",
        empty_label="Свой вариант",
    )
    save_to_catalog = forms.BooleanField(required=False, label="Сохранить в справочник")

    class Meta:
        model = WorkItem
        fields = ["catalog_item", "title", "labor_cost", "save_to_catalog"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["title"].widget.attrs.update(
            {"placeholder": "Например, замена матрицы", "list": "works-catalog-list"}
        )


class PartLineForm(forms.ModelForm):
    class Meta:
        model = OrderPartLine
        fields = ["part", "quantity"]

    def __init__(self, *args, **kwargs):
        parts_qs = kwargs.pop("parts_queryset", None)
        super().__init__(*args, **kwargs)
        if parts_qs is not None:
            self.fields["part"].queryset = parts_qs
        self.fields["part"].empty_label = "Выберите деталь"


class ApprovalForm(forms.Form):
    approved = forms.ChoiceField(
        choices=[("yes", "Согласен на ремонт"), ("no", "Отказ от ремонта")],
        label="Решение клиента",
    )
    note = forms.CharField(required=False, max_length=255, label="Комментарий")


class PaymentForm(forms.ModelForm):
    class Meta:
        model = Payment
        fields = ["note"]


class CustomerCreateForm(forms.ModelForm):
    class Meta:
        model = Customer
        fields = ["name", "phone", "email"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["name"].widget.attrs.update({"placeholder": "ФИО клиента"})
        self.fields["phone"].widget.attrs.update({"placeholder": "+7..."})
        self.fields["email"].widget.attrs.update({"placeholder": "client@example.com"})


class DeviceCategoryCreateForm(forms.ModelForm):
    class Meta:
        model = DeviceCategory
        fields = ["name"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["name"].widget.attrs.update({"placeholder": "Например, Принтеры"})


class DeviceModelCreateForm(forms.ModelForm):
    class Meta:
        model = DeviceModel
        fields = ["brand", "model"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["brand"].widget.attrs.update({"placeholder": "Например, HP"})
        self.fields["model"].widget.attrs.update({"placeholder": "Например, LaserJet 1020"})
        self.fields["brand"].required = False


class StockAdjustForm(forms.Form):
    part = forms.ModelChoiceField(queryset=Part.objects.all(), label="Комплектующая")
    quantity = forms.IntegerField(min_value=0, label="Новый остаток")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["part"].queryset = Part.objects.order_by("sku")
        self.fields["part"].empty_label = "Выберите комплектующую"

    def save(self) -> StockItem:
        part = self.cleaned_data["part"]
        quantity = self.cleaned_data["quantity"]
        stock, _ = StockItem.objects.get_or_create(part=part, defaults={"quantity_on_hand": 0})
        stock.quantity_on_hand = quantity
        stock.save(update_fields=["quantity_on_hand"])
        return stock


class PartCreateWithStockForm(forms.ModelForm):
    quantity_on_hand = forms.IntegerField(min_value=0, label="Начальный остаток")

    class Meta:
        model = Part
        fields = ["name", "sku", "category", "compatible_models", "purchase_price", "sale_price"]
        widgets = {
            "compatible_models": forms.SelectMultiple(attrs={"size": 6}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["name"].widget.attrs.update({"placeholder": "Наименование детали"})
        self.fields["sku"].widget.attrs.update({"placeholder": "Уникальный артикул"})
        self.fields["compatible_models"].required = False
        self.fields["compatible_models"].help_text = (
            "Не выбирайте ничего — деталь будет доступна для всех моделей категории."
        )

    @transaction.atomic
    def save(self) -> StockItem:
        part = super().save()
        stock = StockItem.objects.create(part=part, quantity_on_hand=self.cleaned_data["quantity_on_hand"])
        return stock


class PartUpdateForm(forms.ModelForm):
    class Meta:
        model = Part
        fields = ["name", "sku", "category", "compatible_models", "purchase_price", "sale_price"]
        widgets = {
            "compatible_models": forms.SelectMultiple(attrs={"size": 6}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["compatible_models"].required = False


class ReserveLinePriceForm(forms.Form):
    line_id = forms.IntegerField(widget=forms.HiddenInput())
    part_name = forms.CharField(label="Комплектующая", disabled=True, required=False)
    quantity = forms.IntegerField(label="Количество", disabled=True, required=False)
    sale_unit_price = forms.DecimalField(min_value=0, decimal_places=2, max_digits=12, label="Цена продажи")


ReserveLinePriceFormSet = formset_factory(ReserveLinePriceForm, extra=0)


WorkItemFormSet = inlineformset_factory(
    WorkOrder,
    WorkItem,
    form=WorkItemForm,
    extra=2,
    can_delete=True,
)


class ProcurementItemForm(forms.Form):
    name = forms.CharField(max_length=255, required=False, label="Наименование")
    quantity = forms.IntegerField(min_value=1, required=False, label="Количество")
    note = forms.CharField(required=False, max_length=500, widget=forms.TextInput(), label="Примечание")

    def clean(self):
        cleaned = super().clean()
        name = (cleaned.get("name") or "").strip()
        qty = cleaned.get("quantity")
        if name and not qty:
            self.add_error("quantity", "Укажите количество.")
        if qty and not name:
            self.add_error("name", "Укажите наименование.")
        return cleaned


ProcurementItemFormSet = formset_factory(ProcurementItemForm, extra=2, can_delete=True)


class ProcurementDecisionForm(forms.Form):
    request_id = forms.IntegerField(widget=forms.HiddenInput())
    name = forms.CharField(label="Позиция", disabled=True, required=False)
    quantity = forms.IntegerField(label="Кол-во", disabled=True, required=False)
    note = forms.CharField(label="Примечание", disabled=True, required=False)
    sku = forms.SlugField(required=False, label="Артикул (SKU)", max_length=64)
    purchase_price = forms.DecimalField(
        min_value=0, decimal_places=2, max_digits=12, label="Закупочная цена"
    )
    sale_price = forms.DecimalField(
        min_value=0, decimal_places=2, max_digits=12, label="Цена продажи"
    )
    stock_qty = forms.IntegerField(
        min_value=0, required=False, initial=0, label="Закупить штук на склад",
        help_text="Обычно = количеству в заявке. Оставьте 0, если детали выдаются напрямую.",
    )


ProcurementDecisionFormSet = formset_factory(ProcurementDecisionForm, extra=0)


class ProcurementRejectForm(forms.Form):
    reason = forms.CharField(max_length=255, label="Причина отказа")


class UnrepairableForm(forms.Form):
    reason = forms.CharField(max_length=255, label="Причина", required=False)


class UnrepairableCloseForm(forms.Form):
    charge_diagnostic = forms.ChoiceField(
        choices=[("yes", "Взять плату за диагностику"), ("no", "Закрыть без оплаты")],
        label="Решение по оплате",
        widget=forms.RadioSelect,
    )
    note = forms.CharField(required=False, max_length=255, label="Комментарий")


def make_part_line_formset(parts_queryset):
    class _PartLineForm(PartLineForm):
        def __init__(self, *args, **kwargs):
            kwargs["parts_queryset"] = parts_queryset
            super().__init__(*args, **kwargs)

    return inlineformset_factory(
        WorkOrder,
        OrderPartLine,
        form=_PartLineForm,
        extra=2,
        can_delete=True,
    )


class UserCreateForm(forms.Form):
    username = forms.CharField(max_length=150, label="Логин")
    full_name = forms.CharField(max_length=255, label="ФИО")
    email = forms.EmailField(required=False, label="Email")
    phone = forms.CharField(max_length=32, required=False, label="Телефон")
    role = forms.ChoiceField(choices=ROLE_CHOICES, label="Роль")
    initial_password = forms.CharField(
        label="Временный пароль",
        min_length=8,
        help_text="Пользователь сменит его при первом входе.",
    )

    def clean_username(self):
        value = self.cleaned_data["username"].strip()
        if User.objects.filter(username=value).exists():
            raise forms.ValidationError("Пользователь с таким логином уже есть.")
        return value

    @transaction.atomic
    def save(self) -> User:
        data = self.cleaned_data
        user = User(username=data["username"], email=data.get("email", ""))
        full_name_parts = data["full_name"].strip().split(" ", 1)
        user.last_name = full_name_parts[0] if full_name_parts else ""
        user.first_name = full_name_parts[1] if len(full_name_parts) > 1 else ""
        user.is_staff = data["role"] == "admin"
        user.is_superuser = data["role"] == "admin"
        user.set_password(data["initial_password"])
        user.save()

        if data["role"] != "admin":
            group, _ = Group.objects.get_or_create(name=data["role"])
            user.groups.add(group)

        profile = ensure_profile(user)
        profile.full_name = data["full_name"].strip()
        profile.phone = data.get("phone", "")
        profile.require_password_change = True
        profile.save()
        return user


class UserEditForm(forms.Form):
    full_name = forms.CharField(max_length=255, label="ФИО")
    email = forms.EmailField(required=False, label="Email")
    phone = forms.CharField(max_length=32, required=False, label="Телефон")
    role = forms.ChoiceField(choices=ROLE_CHOICES, label="Роль")
    is_active = forms.BooleanField(required=False, label="Активен")

    def __init__(self, *args, user: User | None = None, **kwargs):
        self.user_instance = user
        super().__init__(*args, **kwargs)

    @transaction.atomic
    def save(self) -> User:
        user = self.user_instance
        data = self.cleaned_data
        user.email = data.get("email", "")
        parts = data["full_name"].strip().split(" ", 1)
        user.last_name = parts[0] if parts else ""
        user.first_name = parts[1] if len(parts) > 1 else ""
        user.is_active = data.get("is_active", True)
        user.is_staff = data["role"] == "admin"
        user.is_superuser = data["role"] == "admin"
        user.save()

        user.groups.clear()
        if data["role"] != "admin":
            group, _ = Group.objects.get_or_create(name=data["role"])
            user.groups.add(group)

        profile = ensure_profile(user)
        profile.full_name = data["full_name"].strip()
        profile.phone = data.get("phone", "")
        profile.save()
        return user


class UserResetPasswordForm(forms.Form):
    new_password = forms.CharField(label="Новый временный пароль", min_length=8)

    def __init__(self, *args, user: User | None = None, **kwargs):
        self.user_instance = user
        super().__init__(*args, **kwargs)

    @transaction.atomic
    def save(self) -> User:
        user = self.user_instance
        user.set_password(self.cleaned_data["new_password"])
        user.save()
        profile = ensure_profile(user)
        profile.require_password_change = True
        profile.save(update_fields=["require_password_change"])
        return user


class ProfileForm(forms.Form):
    full_name = forms.CharField(max_length=255, label="ФИО")
    email = forms.EmailField(required=False, label="Email")
    phone = forms.CharField(max_length=32, required=False, label="Телефон")

    def __init__(self, *args, user: User | None = None, **kwargs):
        self.user_instance = user
        super().__init__(*args, **kwargs)

    @transaction.atomic
    def save(self) -> UserProfile:
        user = self.user_instance
        user.email = self.cleaned_data.get("email", "")
        parts = self.cleaned_data["full_name"].strip().split(" ", 1)
        user.last_name = parts[0] if parts else ""
        user.first_name = parts[1] if len(parts) > 1 else ""
        user.save(update_fields=["email", "first_name", "last_name"])
        profile = ensure_profile(user)
        profile.full_name = self.cleaned_data["full_name"].strip()
        profile.phone = self.cleaned_data.get("phone", "")
        profile.save()
        return profile
