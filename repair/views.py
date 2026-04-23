from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth.models import Group, User
from django.db.models import Q
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render

from .forms import (
    ApprovalForm,
    AssignTechnicianForm,
    CustomerCreateForm,
    DeviceCategoryCreateForm,
    DeviceModelCreateForm,
    DiagnosticForm,
    PartCreateWithStockForm,
    PartUpdateForm,
    PaymentForm,
    ProcurementDecisionFormSet,
    ProcurementItemFormSet,
    ProcurementRejectForm,
    ProfileForm,
    ReassignRolesForm,
    ReserveLinePriceFormSet,
    StockAdjustForm,
    UnrepairableCloseForm,
    UnrepairableForm,
    UserCreateForm,
    UserEditForm,
    UserResetPasswordForm,
    WorkItemFormSet,
    WorkOrderCreateForm,
    make_part_line_formset,
)
from .models import (
    DeviceCategory,
    DeviceModel,
    OrderPartLine,
    Part,
    ProcurementRequest,
    ProcurementStatus,
    StockItem,
    UserProfile,
    WorkCatalogItem,
    WorkOrder,
    WorkOrderStatus,
    ensure_profile,
)
from .permissions import role_required
from .services import (
    WorkflowError,
    approve_order,
    approve_procurement,
    assign_technician,
    complete_order,
    mark_unrepairable,
    reassign_role,
    register_payment,
    reject_procurement,
    reserve_parts,
    save_diagnostics,
)


def _is_admin(user) -> bool:
    return user.is_authenticated and (user.is_superuser or user.is_staff)


def build_my_orders_queryset(user):
    queryset = WorkOrder.objects.select_related(
        "customer", "device_model__category", "manager", "technician", "warehouse_keeper"
    )
    if user.is_superuser:
        return queryset

    role_filters = Q(pk__in=[])
    if user.groups.filter(name="manager").exists():
        role_filters |= Q(
            status__in=[
                WorkOrderStatus.NEW,
                WorkOrderStatus.ASSIGNED,
                WorkOrderStatus.AWAITING_APPROVAL,
                WorkOrderStatus.PROCUREMENT_REJECTED,
                WorkOrderStatus.UNREPAIRABLE,
                WorkOrderStatus.COMPLETED,
                WorkOrderStatus.REJECTED,
            ]
        )
    if user.groups.filter(name="technician").exists():
        role_filters |= Q(
            technician=user,
            status__in=[WorkOrderStatus.ASSIGNED, WorkOrderStatus.IN_PROGRESS],
        )
    if user.groups.filter(name="warehouse").exists():
        role_filters |= Q(
            status__in=[WorkOrderStatus.AWAITING_PROCUREMENT, WorkOrderStatus.APPROVED]
        )

    return queryset.filter(role_filters)


@login_required
def dashboard(request: HttpRequest) -> HttpResponse:
    my_orders = build_my_orders_queryset(request.user).order_by("-updated_at")[:8]
    context = {
        "orders": my_orders,
        "overdue_count": sum(1 for order in my_orders if order.is_overdue),
        "awaiting_approval_count": build_my_orders_queryset(request.user).filter(
            status=WorkOrderStatus.AWAITING_APPROVAL
        ).count(),
    }
    return render(request, "repair/dashboard.html", context)


@login_required
def order_list(request: HttpRequest) -> HttpResponse:
    queryset = WorkOrder.objects.select_related(
        "customer", "device_model__category", "manager", "technician", "warehouse_keeper"
    ).all()
    status = request.GET.get("status")
    technician = request.GET.get("technician")
    query = request.GET.get("q")

    if status:
        queryset = queryset.filter(status=status)
    if technician:
        queryset = queryset.filter(technician__username__icontains=technician)
    if query:
        queryset = queryset.filter(
            Q(number__icontains=query)
            | Q(customer__name__icontains=query)
            | Q(device_model__model__icontains=query)
            | Q(device_model__brand__icontains=query)
        )
    return render(
        request,
        "repair/order_list.html",
        {
            "orders": queryset.order_by("-received_at"),
            "statuses": WorkOrderStatus.choices,
            "my_mode": False,
            "page_title": "Заявки на ремонт",
            "page_subtitle": "Фильтруйте и быстро находите нужные заказы.",
        },
    )


@login_required
def my_order_list(request: HttpRequest) -> HttpResponse:
    queryset = build_my_orders_queryset(request.user)
    status = request.GET.get("status")
    technician = request.GET.get("technician")
    query = request.GET.get("q")

    if status:
        queryset = queryset.filter(status=status)
    if technician:
        queryset = queryset.filter(technician__username__icontains=technician)
    if query:
        queryset = queryset.filter(
            Q(number__icontains=query)
            | Q(customer__name__icontains=query)
            | Q(device_model__model__icontains=query)
            | Q(device_model__brand__icontains=query)
        )

    return render(
        request,
        "repair/order_list.html",
        {
            "orders": queryset.order_by("-received_at"),
            "statuses": WorkOrderStatus.choices,
            "my_mode": True,
            "page_title": "Мои заявки",
            "page_subtitle": "Показаны заявки, за которые вы отвечаете по вашей роли.",
        },
    )


@login_required
def order_detail(request: HttpRequest, pk: int) -> HttpResponse:
    order = get_object_or_404(
        WorkOrder.objects.select_related(
            "customer", "device_model__category", "manager", "technician", "warehouse_keeper", "created_by"
        ).prefetch_related(
            "work_items",
            "part_lines__part",
            "part_reservations__part",
            "payments",
            "status_history",
            "procurement_requests",
        ),
        pk=pk,
    )
    return render(request, "repair/order_detail.html", {"order": order})


def _categories_payload() -> list[dict]:
    data = []
    for cat in DeviceCategory.objects.prefetch_related("models").order_by("name"):
        data.append(
            {
                "id": cat.id,
                "name": cat.name,
                "models": [
                    {
                        "id": m.id,
                        "label": f"{m.brand} {m.model}".strip() or m.model,
                    }
                    for m in cat.models.all().order_by("brand", "model")
                ],
            }
        )
    return data


@role_required("manager")
def order_create(request: HttpRequest) -> HttpResponse:
    use_new_customer = False
    use_new_category = False
    use_new_model = False
    if request.method == "POST":
        form = WorkOrderCreateForm(request.POST, prefix="order")
        customer_form = CustomerCreateForm(request.POST, prefix="customer")
        category_form = DeviceCategoryCreateForm(request.POST, prefix="category_new")
        device_model_form = DeviceModelCreateForm(request.POST, prefix="model_new")
        use_new_customer = request.POST.get("use_new_customer") == "on"
        use_new_category = request.POST.get("use_new_category") == "on"
        use_new_model = request.POST.get("use_new_model") == "on"

        forms_valid = form.is_valid()
        if use_new_customer:
            forms_valid = customer_form.is_valid() and forms_valid
        if use_new_category:
            forms_valid = category_form.is_valid() and forms_valid
        if use_new_model:
            forms_valid = device_model_form.is_valid() and forms_valid

        if forms_valid:
            customer = form.cleaned_data.get("customer")
            if use_new_customer:
                customer = customer_form.save()
            elif not customer:
                form.add_error("customer", "Выберите клиента или создайте нового.")
                forms_valid = False

            category = form.cleaned_data.get("category")
            if use_new_category:
                category = category_form.save()
            elif not category and not use_new_model:
                device_model = form.cleaned_data.get("device_model")
                if device_model:
                    category = device_model.category

            if not category and (use_new_model or not form.cleaned_data.get("device_model")):
                form.add_error("category", "Выберите категорию или создайте новую.")
                forms_valid = False

            if forms_valid:
                device_model = form.cleaned_data.get("device_model")
                if use_new_model:
                    device_model = device_model_form.save(commit=False)
                    device_model.category = category
                    device_model.save()
                elif not device_model:
                    form.add_error("device_model", "Выберите устройство или добавьте новое.")
                    forms_valid = False
                elif category and device_model.category_id != category.id:
                    form.add_error("device_model", "Устройство не соответствует выбранной категории.")
                    forms_valid = False

            if forms_valid:
                order = form.save(commit=False)
                order.customer = customer
                order.device_model = device_model
                order.created_by = request.user
                order.save()
                messages.success(request, "Заявка создана.")
                return redirect("repair:order-detail", pk=order.pk)
    else:
        form = WorkOrderCreateForm(prefix="order")
        customer_form = CustomerCreateForm(prefix="customer")
        category_form = DeviceCategoryCreateForm(prefix="category_new")
        device_model_form = DeviceModelCreateForm(prefix="model_new")
    return render(
        request,
        "repair/order_create.html",
        {
            "form": form,
            "customer_form": customer_form,
            "category_form": category_form,
            "device_model_form": device_model_form,
            "use_new_customer": use_new_customer,
            "use_new_category": use_new_category,
            "use_new_model": use_new_model,
            "categories_json": _categories_payload(),
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


def _parts_queryset_for_order(order: WorkOrder):
    qs = Part.objects.all()
    if order.device_model_id:
        qs = qs.filter(category=order.device_model.category_id).filter(
            Q(compatible_models__isnull=True) | Q(compatible_models=order.device_model_id)
        )
    return qs.distinct().order_by("sku")


@role_required("technician")
def diagnostics(request: HttpRequest, pk: int) -> HttpResponse:
    order = get_object_or_404(
        WorkOrder.objects.select_related("device_model__category"), pk=pk
    )
    diagnostic_instance = getattr(order, "diagnostic", None)
    parts_qs = _parts_queryset_for_order(order)
    PartFormSet = make_part_line_formset(parts_qs)

    if request.method == "POST":
        d_form = DiagnosticForm(request.POST, instance=diagnostic_instance)
        work_formset = WorkItemFormSet(request.POST, instance=order, prefix="work")
        part_formset = PartFormSet(request.POST, instance=order, prefix="part")
        proc_formset = ProcurementItemFormSet(request.POST, prefix="proc")
        if (
            d_form.is_valid()
            and work_formset.is_valid()
            and part_formset.is_valid()
            and proc_formset.is_valid()
        ):
            work_items = []
            for wf in work_formset:
                cleaned = getattr(wf, "cleaned_data", None)
                if cleaned and not cleaned.get("DELETE", False) and cleaned.get("title"):
                    work_items.append(
                        (
                            cleaned["title"],
                            cleaned["labor_cost"],
                            bool(cleaned.get("save_to_catalog")),
                        )
                    )

            part_lines = []
            for pf in part_formset:
                cleaned = getattr(pf, "cleaned_data", None)
                if cleaned and not cleaned.get("DELETE", False) and cleaned.get("part"):
                    part_lines.append((cleaned["part"], cleaned["quantity"]))

            procurement_items = []
            for pf in proc_formset:
                cleaned = getattr(pf, "cleaned_data", None)
                if not cleaned or cleaned.get("DELETE", False):
                    continue
                name = (cleaned.get("name") or "").strip()
                qty = cleaned.get("quantity")
                if name and qty:
                    procurement_items.append(
                        {"name": name, "quantity": qty, "note": cleaned.get("note") or ""}
                    )
            try:
                save_diagnostics(
                    order,
                    d_form.cleaned_data["findings"],
                    work_items,
                    part_lines,
                    request.user,
                    procurement_items=procurement_items,
                )
                if procurement_items:
                    messages.success(
                        request,
                        "Диагностика сохранена. Запросы на закупку отправлены кладовщику.",
                    )
                else:
                    messages.success(request, "Диагностика сохранена, заявка отправлена на согласование клиентом.")
                return redirect("repair:order-detail", pk=pk)
            except WorkflowError as err:
                messages.error(request, str(err))
    else:
        d_form = DiagnosticForm(instance=diagnostic_instance)
        work_formset = WorkItemFormSet(instance=order, prefix="work")
        part_formset = PartFormSet(instance=order, prefix="part")
        proc_formset = ProcurementItemFormSet(prefix="proc")

    works_catalog = [
        {"title": wc.title, "default_labor_cost": str(wc.default_labor_cost)}
        for wc in WorkCatalogItem.objects.all().order_by("title")
    ]

    return render(
        request,
        "repair/diagnostics.html",
        {
            "order": order,
            "d_form": d_form,
            "work_formset": work_formset,
            "part_formset": part_formset,
            "proc_formset": proc_formset,
            "works_catalog": works_catalog,
            "parts_available_count": parts_qs.count(),
        },
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
    items = StockItem.objects.select_related("part", "part__category").order_by("part__sku")
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
    if order.status == WorkOrderStatus.UNREPAIRABLE:
        return redirect("repair:order-unrepairable-close", pk=pk)
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


# --- Переназначение ролей и «забрать заявку» ---


@role_required("manager")
def order_reassign(request: HttpRequest, pk: int) -> HttpResponse:
    order = get_object_or_404(WorkOrder, pk=pk)
    initial = {
        "manager": order.manager_id,
        "technician": order.technician_id,
        "warehouse_keeper": order.warehouse_keeper_id,
    }
    if request.method == "POST":
        form = ReassignRolesForm(request.POST, initial=initial)
        if form.is_valid():
            for slot in ("manager", "technician", "warehouse_keeper"):
                new_value = form.cleaned_data.get(slot)
                current_id = getattr(order, f"{slot}_id")
                new_id = new_value.id if new_value else None
                if new_id != current_id:
                    reassign_role(order, slot, new_value, request.user)
            messages.success(request, "Роли обновлены.")
            return redirect("repair:order-detail", pk=pk)
    else:
        form = ReassignRolesForm(initial=initial)
    return render(request, "repair/order_reassign.html", {"form": form, "order": order})


@role_required("manager")
def order_take_over(request: HttpRequest, pk: int) -> HttpResponse:
    order = get_object_or_404(WorkOrder, pk=pk)
    if request.method != "POST":
        return redirect("repair:order-detail", pk=pk)
    reassign_role(order, "manager", request.user, request.user)
    messages.success(request, "Заявка переведена на вас.")
    return redirect("repair:order-detail", pk=pk)


# --- Закупка ---


@role_required("warehouse")
def procurement_queue(request: HttpRequest) -> HttpResponse:
    orders = (
        WorkOrder.objects.filter(status=WorkOrderStatus.AWAITING_PROCUREMENT)
        .select_related("customer", "device_model__category")
        .prefetch_related("procurement_requests")
        .order_by("-received_at")
    )
    return render(request, "repair/procurement_queue.html", {"orders": orders})


@role_required("warehouse")
def procurement_approve(request: HttpRequest, pk: int) -> HttpResponse:
    order = get_object_or_404(WorkOrder, pk=pk)
    if order.status != WorkOrderStatus.AWAITING_PROCUREMENT:
        messages.error(request, "Заявка не ожидает согласования закупки.")
        return redirect("repair:order-detail", pk=pk)

    pending = list(order.procurement_requests.filter(status=ProcurementStatus.PENDING))
    initial = [
        {
            "request_id": req.id,
            "name": req.name,
            "quantity": req.quantity,
            "note": req.note,
            "stock_qty": req.quantity,
        }
        for req in pending
    ]

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "reject":
            reject_form = ProcurementRejectForm(request.POST, prefix="reject")
            if reject_form.is_valid():
                try:
                    reject_procurement(order, request.user, reject_form.cleaned_data["reason"])
                    messages.warning(request, "Закупка отклонена. Заявка ожидает решения менеджера.")
                    return redirect("repair:order-detail", pk=pk)
                except WorkflowError as err:
                    messages.error(request, str(err))
            formset = ProcurementDecisionFormSet(initial=initial, prefix="dec")
        else:
            formset = ProcurementDecisionFormSet(request.POST, initial=initial, prefix="dec")
            reject_form = ProcurementRejectForm(prefix="reject")
            if formset.is_valid():
                decisions = {}
                for form in formset:
                    cleaned = form.cleaned_data
                    decisions[cleaned["request_id"]] = {
                        "sku": cleaned.get("sku") or "",
                        "purchase_price": cleaned["purchase_price"],
                        "sale_price": cleaned["sale_price"],
                        "stock_qty": cleaned.get("stock_qty") or 0,
                    }
                try:
                    approve_procurement(order, decisions, request.user)
                    messages.success(
                        request,
                        "Закупка согласована. Заявка ушла на согласование клиентом.",
                    )
                    return redirect("repair:order-detail", pk=pk)
                except WorkflowError as err:
                    messages.error(request, str(err))
    else:
        formset = ProcurementDecisionFormSet(initial=initial, prefix="dec")
        reject_form = ProcurementRejectForm(prefix="reject")

    return render(
        request,
        "repair/procurement_approve.html",
        {
            "order": order,
            "formset": formset,
            "reject_form": reject_form,
            "pending": pending,
        },
    )


# --- Невозможно починить ---


@role_required("manager")
def order_unrepairable(request: HttpRequest, pk: int) -> HttpResponse:
    order = get_object_or_404(WorkOrder, pk=pk)
    if request.method == "POST":
        form = UnrepairableForm(request.POST)
        if form.is_valid():
            try:
                mark_unrepairable(order, request.user, form.cleaned_data.get("reason") or "")
                messages.warning(
                    request,
                    "Заявка помечена как неремонтопригодная. Теперь нужно закрыть её, выбрав вариант оплаты.",
                )
                return redirect("repair:order-unrepairable-close", pk=pk)
            except WorkflowError as err:
                messages.error(request, str(err))
    else:
        form = UnrepairableForm()
    return render(request, "repair/order_unrepairable.html", {"order": order, "form": form})


@role_required("manager")
def order_unrepairable_close(request: HttpRequest, pk: int) -> HttpResponse:
    order = get_object_or_404(WorkOrder, pk=pk)
    if order.status != WorkOrderStatus.UNREPAIRABLE:
        return redirect("repair:order-detail", pk=pk)
    if request.method == "POST":
        form = UnrepairableCloseForm(request.POST)
        if form.is_valid():
            charge = form.cleaned_data["charge_diagnostic"] == "yes"
            try:
                register_payment(
                    order,
                    request.user,
                    form.cleaned_data.get("note") or "",
                    charge_diagnostic=charge,
                )
                if charge:
                    messages.success(request, "Закрыто с оплатой диагностики.")
                else:
                    messages.success(request, "Заявка закрыта без оплаты.")
                return redirect("repair:order-detail", pk=pk)
            except WorkflowError as err:
                messages.error(request, str(err))
    else:
        form = UnrepairableCloseForm()
    return render(
        request,
        "repair/order_unrepairable_close.html",
        {"order": order, "form": form},
    )


# --- Пользователи ---


def _user_role(user: User) -> str:
    if user.is_superuser or user.is_staff and not user.groups.exists():
        return "admin"
    group = user.groups.first()
    return group.name if group else "admin"


@user_passes_test(_is_admin, login_url="login")
def user_list(request: HttpRequest) -> HttpResponse:
    users = User.objects.select_related("profile").prefetch_related("groups").order_by("username")
    rows = []
    for u in users:
        ensure_profile(u)
        rows.append(
            {
                "obj": u,
                "role": _user_role(u),
                "full_name": getattr(u, "profile", None) and u.profile.full_name,
                "require_password_change": getattr(u, "profile", None) and u.profile.require_password_change,
            }
        )
    return render(request, "repair/user_list.html", {"rows": rows})


@user_passes_test(_is_admin, login_url="login")
def user_create(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        form = UserCreateForm(request.POST)
        if form.is_valid():
            user = form.save()
            messages.success(
                request,
                f"Пользователь {user.username} создан. Передайте ему временный пароль — при первом входе система попросит его сменить.",
            )
            return redirect("repair:user-list")
    else:
        form = UserCreateForm()
    return render(request, "repair/user_form.html", {"form": form, "mode": "create"})


@user_passes_test(_is_admin, login_url="login")
def user_edit(request: HttpRequest, pk: int) -> HttpResponse:
    target = get_object_or_404(User, pk=pk)
    profile = ensure_profile(target)
    initial = {
        "full_name": profile.full_name or target.get_full_name(),
        "email": target.email,
        "phone": profile.phone,
        "role": _user_role(target),
        "is_active": target.is_active,
    }
    if request.method == "POST":
        form = UserEditForm(request.POST, user=target, initial=initial)
        if form.is_valid():
            form.save()
            messages.success(request, "Данные пользователя сохранены.")
            return redirect("repair:user-list")
    else:
        form = UserEditForm(initial=initial, user=target)
    reset_form = UserResetPasswordForm(user=target)
    return render(
        request,
        "repair/user_form.html",
        {"form": form, "mode": "edit", "target": target, "reset_form": reset_form},
    )


@user_passes_test(_is_admin, login_url="login")
def user_reset_password(request: HttpRequest, pk: int) -> HttpResponse:
    target = get_object_or_404(User, pk=pk)
    if request.method == "POST":
        form = UserResetPasswordForm(request.POST, user=target)
        if form.is_valid():
            form.save()
            messages.success(
                request,
                f"Пароль для {target.username} сброшен. При следующем входе пользователь обязан задать новый.",
            )
            return redirect("repair:user-edit", pk=pk)
    else:
        form = UserResetPasswordForm(user=target)
    return render(
        request,
        "repair/user_form.html",
        {"form": form, "mode": "reset", "target": target},
    )


# --- Профиль ---


@login_required
def profile(request: HttpRequest) -> HttpResponse:
    user = request.user
    profile_obj = ensure_profile(user)
    initial = {
        "full_name": profile_obj.full_name or user.get_full_name(),
        "email": user.email,
        "phone": profile_obj.phone,
    }
    if request.method == "POST":
        form = ProfileForm(request.POST, user=user, initial=initial)
        if form.is_valid():
            form.save()
            messages.success(request, "Данные профиля обновлены.")
            return redirect("repair:profile")
    else:
        form = ProfileForm(initial=initial, user=user)
    return render(
        request,
        "repair/profile.html",
        {"form": form, "profile": profile_obj},
    )


@login_required
def profile_password(request: HttpRequest) -> HttpResponse:
    user = request.user
    profile_obj = ensure_profile(user)
    if request.method == "POST":
        form = PasswordChangeForm(user=user, data=request.POST)
        if form.is_valid():
            form.save()
            update_session_auth_hash(request, user)
            if profile_obj.require_password_change:
                profile_obj.require_password_change = False
                profile_obj.save(update_fields=["require_password_change"])
            messages.success(request, "Пароль успешно изменён.")
            return redirect("repair:profile")
    else:
        form = PasswordChangeForm(user=user)
    return render(
        request,
        "repair/profile_password.html",
        {"form": form, "force": profile_obj.require_password_change},
    )
