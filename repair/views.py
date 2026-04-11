from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Prefetch, Q
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render

from .forms import (
    ApprovalForm,
    AssignTechnicianForm,
    CustomerCreateForm,
    DeviceCreateForm,
    DiagnosticForm,
    PartUpdateForm,
    PartCreateWithStockForm,
    PartLineFormSet,
    PaymentForm,
    ReserveLinePriceFormSet,
    StockAdjustForm,
    WorkItemFormSet,
    WorkOrderCreateForm,
)
from .models import OrderPartLine, Part, StockItem, WorkOrder, WorkOrderStatus
from .permissions import role_required
from .services import (
    WorkflowError,
    approve_order,
    assign_technician,
    complete_order,
    register_payment,
    reserve_parts,
    save_diagnostics,
)


@login_required
def dashboard(request: HttpRequest) -> HttpResponse:
    orders = WorkOrder.objects.select_related("customer", "assigned_to").order_by("-created_at")[:8]
    context = {
        "orders": orders,
        "overdue_count": sum(1 for order in orders if order.is_overdue),
        "awaiting_approval_count": WorkOrder.objects.filter(
            status=WorkOrderStatus.AWAITING_APPROVAL
        ).count(),
    }
    return render(request, "repair/dashboard.html", context)


@login_required
def order_list(request: HttpRequest) -> HttpResponse:
    queryset = WorkOrder.objects.select_related("customer", "device", "assigned_to").all()
    status = request.GET.get("status")
    technician = request.GET.get("technician")
    query = request.GET.get("q")

    if status:
        queryset = queryset.filter(status=status)
    if technician:
        queryset = queryset.filter(assigned_to__username__icontains=technician)
    if query:
        queryset = queryset.filter(
            Q(number__icontains=query)
            | Q(customer__name__icontains=query)
            | Q(device__model__icontains=query)
        )
    return render(
        request,
        "repair/order_list.html",
        {"orders": queryset.order_by("-received_at"), "statuses": WorkOrderStatus.choices},
    )


@login_required
def order_detail(request: HttpRequest, pk: int) -> HttpResponse:
    order = get_object_or_404(
        WorkOrder.objects.select_related("customer", "device", "assigned_to", "created_by")
        .prefetch_related("work_items", "part_lines__part", "part_reservations__part", "payments", "status_history"),
        pk=pk,
    )
    return render(request, "repair/order_detail.html", {"order": order})


@role_required("manager")
def order_create(request: HttpRequest) -> HttpResponse:
    use_new_customer = False
    use_new_device = False
    if request.method == "POST":
        form = WorkOrderCreateForm(request.POST, prefix="order")
        customer_form = CustomerCreateForm(request.POST, prefix="customer")
        device_form = DeviceCreateForm(request.POST, prefix="device")
        use_new_customer = request.POST.get("use_new_customer") == "on"
        use_new_device = request.POST.get("use_new_device") == "on"

        forms_valid = form.is_valid()
        if use_new_customer:
            forms_valid = customer_form.is_valid() and forms_valid
        if use_new_device:
            forms_valid = device_form.is_valid() and forms_valid

        if forms_valid:
            customer = form.cleaned_data.get("customer")
            if use_new_customer:
                customer = customer_form.save()
            elif not customer:
                form.add_error("customer", "Выберите клиента или создайте нового.")
                forms_valid = False

            device = form.cleaned_data.get("device")
            if forms_valid:
                if use_new_device:
                    device = device_form.save(commit=False)
                    device.customer = customer
                    device.save()
                elif not device:
                    form.add_error("device", "Выберите устройство или создайте новое.")
                    forms_valid = False
                elif customer and device.customer_id != customer.id:
                    # UX-friendly fallback: do not block submission if manager selected a mismatched pair.
                    # Bind order to the actual owner of selected device and show warning.
                    customer = device.customer
                    messages.warning(
                        request,
                        "Выбранное устройство принадлежит другому клиенту. "
                        "Клиент в заявке автоматически изменен на владельца устройства.",
                    )

            if forms_valid:
                order = form.save(commit=False)
                order.customer = customer
                order.device = device
                order.created_by = request.user
                order.save()
                messages.success(request, "Заявка создана.")
                return redirect("repair:order-detail", pk=order.pk)
    else:
        form = WorkOrderCreateForm(prefix="order")
        customer_form = CustomerCreateForm(prefix="customer")
        device_form = DeviceCreateForm(prefix="device")
    return render(
        request,
        "repair/order_create.html",
        {
            "form": form,
            "customer_form": customer_form,
            "device_form": device_form,
            "use_new_customer": use_new_customer,
            "use_new_device": use_new_device,
        },
    )


@role_required("manager")
def assign_order(request: HttpRequest, pk: int) -> HttpResponse:
    order = get_object_or_404(WorkOrder, pk=pk)
    if request.method == "POST":
        form = AssignTechnicianForm(request.POST)
        if form.is_valid():
            try:
                assign_technician(order, form.cleaned_data["technician"], request.user)
                messages.success(request, "Исполнитель назначен.")
                return redirect("repair:order-detail", pk=pk)
            except WorkflowError as err:
                messages.error(request, str(err))
    else:
        form = AssignTechnicianForm()
    return render(request, "repair/assign_order.html", {"form": form, "order": order})


@role_required("technician")
def diagnostics(request: HttpRequest, pk: int) -> HttpResponse:
    order = get_object_or_404(WorkOrder, pk=pk)
    diagnostic_instance = getattr(order, "diagnostic", None)
    if request.method == "POST":
        d_form = DiagnosticForm(request.POST, instance=diagnostic_instance)
        work_formset = WorkItemFormSet(request.POST, instance=order, prefix="work")
        part_formset = PartLineFormSet(request.POST, instance=order, prefix="part")
        if d_form.is_valid() and work_formset.is_valid() and part_formset.is_valid():
            work_items = []
            for wf in work_formset:
                cleaned = getattr(wf, "cleaned_data", None)
                if cleaned and not cleaned.get("DELETE", False) and cleaned.get("title"):
                    work_items.append((cleaned["title"], cleaned["labor_cost"]))

            part_lines = []
            for pf in part_formset:
                cleaned = getattr(pf, "cleaned_data", None)
                if cleaned and not cleaned.get("DELETE", False) and cleaned.get("part"):
                    part_lines.append((cleaned["part"], cleaned["quantity"]))
            try:
                save_diagnostics(order, d_form.cleaned_data["findings"], work_items, part_lines, request.user)
                messages.success(request, "Диагностика сохранена, заявка отправлена на согласование.")
                return redirect("repair:order-detail", pk=pk)
            except WorkflowError as err:
                messages.error(request, str(err))
    else:
        d_form = DiagnosticForm(instance=diagnostic_instance)
        work_formset = WorkItemFormSet(instance=order, prefix="work")
        part_formset = PartLineFormSet(instance=order, prefix="part")
    return render(
        request,
        "repair/diagnostics.html",
        {"order": order, "d_form": d_form, "work_formset": work_formset, "part_formset": part_formset},
    )


@role_required("manager")
def approval(request: HttpRequest, pk: int) -> HttpResponse:
    order = get_object_or_404(WorkOrder, pk=pk)
    if request.method == "POST":
        form = ApprovalForm(request.POST)
        if form.is_valid():
            approved = form.cleaned_data["approved"] == "yes"
            try:
                approve_order(order, approved, request.user, form.cleaned_data["note"])
                if approved:
                    messages.success(request, "Стоимость согласована с заказчиком.")
                else:
                    messages.warning(request, "Клиент отказался от ремонта. Доступна оплата диагностики.")
                return redirect("repair:order-detail", pk=pk)
            except WorkflowError as err:
                messages.error(request, str(err))
    else:
        form = ApprovalForm()
    return render(request, "repair/approval.html", {"order": order, "form": form})


@role_required("warehouse")
def stock_list(request: HttpRequest) -> HttpResponse:
    items = StockItem.objects.select_related("part").order_by("part__sku")
    return render(request, "repair/stock_list.html", {"items": items})


@role_required("warehouse")
def stock_adjust(request: HttpRequest) -> HttpResponse:
    adjust_form = StockAdjustForm(prefix="adjust")
    create_part_form = PartCreateWithStockForm(prefix="new_part")
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "create_part":
            create_part_form = PartCreateWithStockForm(request.POST, prefix="new_part")
            if create_part_form.is_valid():
                stock = create_part_form.save()
                messages.success(
                    request, f"Комплектующая добавлена: {stock.part.sku}, остаток {stock.quantity_on_hand}"
                )
                return redirect("repair:stock-list")
        else:
            adjust_form = StockAdjustForm(request.POST, prefix="adjust")
            if adjust_form.is_valid():
                stock = adjust_form.save()
                messages.success(request, f"Остаток обновлен: {stock.part.sku} = {stock.quantity_on_hand}")
                return redirect("repair:stock-list")
    return render(
        request,
        "repair/stock_adjust.html",
        {"form": adjust_form, "create_part_form": create_part_form},
    )


@role_required("warehouse")
def part_edit(request: HttpRequest, pk: int) -> HttpResponse:
    part = get_object_or_404(Part, pk=pk)
    if request.method == "POST":
        form = PartUpdateForm(request.POST, instance=part)
        if form.is_valid():
            form.save()
            messages.success(request, f"Комплектующая {part.sku} обновлена.")
            return redirect("repair:stock-list")
    else:
        form = PartUpdateForm(instance=part)
    return render(request, "repair/part_edit.html", {"form": form, "part": part})


@role_required("warehouse")
def reserve_order_parts(request: HttpRequest, pk: int) -> HttpResponse:
    order = get_object_or_404(WorkOrder, pk=pk)
    lines = list(order.part_lines.select_related("part").all())
    if not lines:
        messages.error(request, "Для заявки не указаны комплектующие.")
        return redirect("repair:order-detail", pk=pk)

    initial = [
        {
            "line_id": line.id,
            "part_name": f"{line.part.name} ({line.part.sku})",
            "quantity": line.quantity,
            "sale_unit_price": line.part.sale_price,
        }
        for line in lines
    ]

    if request.method == "POST":
        formset = ReserveLinePriceFormSet(request.POST, initial=initial, prefix="reserve")
        if formset.is_valid():
            price_map = {}
            for form in formset:
                cleaned = form.cleaned_data
                price_map[cleaned["line_id"]] = cleaned["sale_unit_price"]
            try:
                reserve_parts(order, price_map, request.user)
                messages.success(request, "Детали зарезервированы, заказ передан в работу.")
                return redirect("repair:order-detail", pk=pk)
            except WorkflowError as err:
                messages.error(request, str(err))
    else:
        formset = ReserveLinePriceFormSet(initial=initial, prefix="reserve")
    return render(request, "repair/reserve_parts.html", {"order": order, "formset": formset})


@role_required("technician")
def complete(request: HttpRequest, pk: int) -> HttpResponse:
    order = get_object_or_404(WorkOrder, pk=pk)
    try:
        complete_order(order, request.user)
        messages.success(request, "Ремонт завершен.")
    except WorkflowError as err:
        messages.error(request, str(err))
    return redirect("repair:order-detail", pk=pk)


@role_required("manager")
def payment(request: HttpRequest, pk: int) -> HttpResponse:
    order = get_object_or_404(WorkOrder, pk=pk)
    if request.method == "POST":
        form = PaymentForm(request.POST)
        if form.is_valid():
            try:
                register_payment(order, request.user, form.cleaned_data["note"])
                messages.success(request, "Оплата получена, заказ закрыт.")
                return redirect("repair:order-detail", pk=pk)
            except WorkflowError as err:
                messages.error(request, str(err))
    else:
        form = PaymentForm()
    return render(request, "repair/payment.html", {"order": order, "form": form})
