from decimal import Decimal

from django import forms
from django.contrib.auth.models import User
from django.db import transaction
from django.forms import formset_factory
from django.forms import inlineformset_factory

from .models import (
    Customer,
    Device,
    DiagnosticResult,
    OrderPartLine,
    Part,
    Payment,
    StockItem,
    WorkItem,
    WorkOrder,
)


class WorkOrderCreateForm(forms.ModelForm):
    customer = forms.ModelChoiceField(queryset=Customer.objects.all(), required=False, label="Клиент")
    device = forms.ModelChoiceField(queryset=Device.objects.select_related("customer"), required=False, label="Устройство")
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
        fields = ["customer", "device", "planned_deadline", "diagnosis_fee", "notes"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["customer"].queryset = Customer.objects.order_by("name")
        self.fields["device"].queryset = Device.objects.select_related("customer").order_by("customer__name", "model")
        self.fields["customer"].empty_label = "Выберите клиента"
        self.fields["device"].empty_label = "Выберите устройство"
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


class DiagnosticForm(forms.ModelForm):
    class Meta:
        model = DiagnosticResult
        fields = ["findings"]


class WorkItemForm(forms.ModelForm):
    class Meta:
        model = WorkItem
        fields = ["title", "labor_cost"]


class PartLineForm(forms.ModelForm):
    class Meta:
        model = OrderPartLine
        fields = ["part", "quantity"]


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


class DeviceCreateForm(forms.ModelForm):
    class Meta:
        model = Device
        fields = ["brand", "model", "serial_number", "issue_description"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["brand"].widget.attrs.update({"placeholder": "Например, Apple"})
        self.fields["model"].widget.attrs.update({"placeholder": "Например, iPhone 12"})
        self.fields["serial_number"].widget.attrs.update({"placeholder": "Серийный номер (опционально)"})
        self.fields["issue_description"].widget.attrs.update({"placeholder": "Кратко опишите неисправность"})


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
        fields = ["name", "sku", "purchase_price", "sale_price"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["name"].widget.attrs.update({"placeholder": "Наименование детали"})
        self.fields["sku"].widget.attrs.update({"placeholder": "Уникальный артикул"})

    @transaction.atomic
    def save(self) -> StockItem:
        part = super().save()
        stock = StockItem.objects.create(part=part, quantity_on_hand=self.cleaned_data["quantity_on_hand"])
        return stock


class PartUpdateForm(forms.ModelForm):
    class Meta:
        model = Part
        fields = ["name", "sku", "purchase_price", "sale_price"]


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

PartLineFormSet = inlineformset_factory(
    WorkOrder,
    OrderPartLine,
    form=PartLineForm,
    extra=2,
    can_delete=True,
)
