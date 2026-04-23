from decimal import Decimal

from django.contrib.auth.models import User
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify

from .models import (
    DiagnosticResult,
    OrderPartLine,
    OrderStatusHistory,
    Part,
    PartReservation,
    PartUsage,
    Payment,
    PaymentKind,
    ProcurementRequest,
    ProcurementStatus,
    ReservationStatus,
    StockItem,
    WorkCatalogItem,
    WorkItem,
    WorkOrder,
    WorkOrderStatus,
)


ALLOWED_TRANSITIONS = {
    WorkOrderStatus.NEW: {WorkOrderStatus.ASSIGNED},
    WorkOrderStatus.ASSIGNED: {WorkOrderStatus.DIAGNOSED},
    WorkOrderStatus.DIAGNOSED: {
        WorkOrderStatus.AWAITING_APPROVAL,
        WorkOrderStatus.AWAITING_PROCUREMENT,
    },
    WorkOrderStatus.AWAITING_PROCUREMENT: {
        WorkOrderStatus.AWAITING_APPROVAL,
        WorkOrderStatus.PROCUREMENT_REJECTED,
    },
    WorkOrderStatus.PROCUREMENT_REJECTED: {
        WorkOrderStatus.UNREPAIRABLE,
        WorkOrderStatus.AWAITING_APPROVAL,
    },
    WorkOrderStatus.AWAITING_APPROVAL: {
        WorkOrderStatus.APPROVED,
        WorkOrderStatus.REJECTED,
    },
    WorkOrderStatus.APPROVED: {WorkOrderStatus.IN_PROGRESS},
    WorkOrderStatus.IN_PROGRESS: {WorkOrderStatus.COMPLETED},
    WorkOrderStatus.REJECTED: {WorkOrderStatus.CLOSED},
    WorkOrderStatus.COMPLETED: {WorkOrderStatus.CLOSED},
    WorkOrderStatus.UNREPAIRABLE: {WorkOrderStatus.CLOSED},
    WorkOrderStatus.CLOSED: set(),
}


VALID_ROLE_SLOTS = ("manager", "technician", "warehouse_keeper")


class WorkflowError(ValueError):
    pass


def set_status(order: WorkOrder, to_status: str, user: User | None, note: str = "") -> WorkOrder:
    if to_status not in ALLOWED_TRANSITIONS.get(order.status, set()):
        raise WorkflowError(f"Нельзя перевести заявку из {order.status} в {to_status}.")

    from_status = order.status
    order.status = to_status
    if to_status == WorkOrderStatus.COMPLETED:
        order.completed_at = timezone.now()
    order.save(update_fields=["status", "completed_at", "updated_at"])
    OrderStatusHistory.objects.create(
        work_order=order,
        from_status=from_status,
        to_status=to_status,
        user=user,
        note=note,
    )
    return order


@transaction.atomic
def reassign_role(order: WorkOrder, slot: str, user: User | None, actor: User | None) -> WorkOrder:
    if slot not in VALID_ROLE_SLOTS:
        raise WorkflowError(f"Неизвестный ролевой слот: {slot}")
    previous = getattr(order, slot)
    setattr(order, slot, user)
    order.save(update_fields=[slot, "updated_at"])
    OrderStatusHistory.objects.create(
        work_order=order,
        from_status=order.status,
        to_status=order.status,
        user=actor,
        note=f"Переназначение '{slot}': {previous} → {user}",
    )
    return order


@transaction.atomic
def assign_technician(order: WorkOrder, technician: User, user: User | None) -> WorkOrder:
    """Менеджер назначает техника и автоматически берёт заявку себе (если менеджер ещё не выставлен)."""
    order.technician = technician
    fields = ["technician", "updated_at"]
    if order.manager_id is None and user is not None:
        order.manager = user
        fields.insert(0, "manager")
    order.save(update_fields=fields)
    return set_status(order, WorkOrderStatus.ASSIGNED, user, "Назначен исполнитель")


@transaction.atomic
def save_diagnostics(
    order: WorkOrder,
    findings: str,
    works: list[tuple[str, Decimal, bool]] | list[tuple[str, Decimal]],
    parts: list[tuple[Part, int]],
    user: User | None,
    procurement_items: list[dict] | None = None,
) -> WorkOrder:
    """Сохраняет диагностику. Если есть procurement_items — уходим в AWAITING_PROCUREMENT."""
    DiagnosticResult.objects.update_or_create(
        work_order=order,
        defaults={"findings": findings},
    )
    order.work_items.all().delete()
    order.part_lines.all().delete()

    normalized_works: list[tuple[str, Decimal, bool]] = []
    for entry in works:
        if len(entry) == 3:
            title, cost, save_flag = entry
        else:
            title, cost = entry
            save_flag = False
        if title:
            normalized_works.append((title, cost, save_flag))

    WorkItem.objects.bulk_create(
        [WorkItem(work_order=order, title=title, labor_cost=cost) for title, cost, _ in normalized_works]
    )
    category = order.device_model.category if order.device_model_id else None
    for title, cost, save_flag in normalized_works:
        if save_flag:
            WorkCatalogItem.objects.get_or_create(
                title=title,
                defaults={"default_labor_cost": cost, "category": category},
            )

    OrderPartLine.objects.bulk_create(
        [
            OrderPartLine(work_order=order, part=part, quantity=qty, unit_price=part.sale_price)
            for part, qty in parts
        ]
    )

    # Пересоздаём ожидающие запросы (ранее созданные PENDING убираем, чтобы не дублировать).
    order.procurement_requests.filter(status=ProcurementStatus.PENDING).delete()
    items = procurement_items or []
    has_procurement = False
    for item in items:
        name = (item.get("name") or "").strip()
        quantity = int(item.get("quantity") or 0)
        if not name or quantity <= 0:
            continue
        ProcurementRequest.objects.create(
            work_order=order,
            name=name,
            quantity=quantity,
            note=(item.get("note") or "").strip(),
            category=category,
            created_by=user,
        )
        has_procurement = True

    set_status(order, WorkOrderStatus.DIAGNOSED, user, "Диагностика сохранена")
    if has_procurement:
        return set_status(
            order,
            WorkOrderStatus.AWAITING_PROCUREMENT,
            user,
            "Требуется закупка деталей — ожидание склада",
        )
    return set_status(order, WorkOrderStatus.AWAITING_APPROVAL, user, "Ожидание согласования клиентом")


def _unique_sku(base: str) -> str:
    base = slugify(base, allow_unicode=False) or "part"
    base = base[:48]
    candidate = base
    counter = 1
    while Part.objects.filter(sku=candidate).exists():
        counter += 1
        candidate = f"{base}-{counter}"[:64]
    return candidate


@transaction.atomic
def approve_procurement(
    order: WorkOrder,
    decisions: dict[int, dict],
    user: User | None,
) -> WorkOrder:
    """
    decisions: {procurement_request_id: {"purchase_price": Decimal, "sale_price": Decimal,
                                         "sku": str (optional), "compatible_models": [ids],
                                         "stock_qty": int (optional)}}
    Одобряем все запросы оптом — статус позиций переходит в APPROVED, создаются Part/Stock/OrderPartLine.
    """
    if order.status != WorkOrderStatus.AWAITING_PROCUREMENT:
        raise WorkflowError("Согласование закупки доступно только в статусе AWAITING_PROCUREMENT.")

    pending = list(order.procurement_requests.filter(status=ProcurementStatus.PENDING))
    if not pending:
        raise WorkflowError("Нет ожидающих запросов на закупку.")

    for req in pending:
        data = decisions.get(req.id)
        if data is None:
            raise WorkflowError(f"Нет решения по позиции '{req.name}'.")
        purchase_price = data.get("purchase_price")
        sale_price = data.get("sale_price")
        if purchase_price is None or sale_price is None:
            raise WorkflowError(f"Нужны обе цены для позиции '{req.name}'.")

        sku = (data.get("sku") or "").strip() or _unique_sku(req.name)
        if Part.objects.filter(sku=sku).exists():
            raise WorkflowError(f"SKU '{sku}' уже существует. Укажите другой для позиции '{req.name}'.")

        category = req.category or (order.device_model.category if order.device_model_id else None)
        if category is None:
            raise WorkflowError(f"Нужна категория для позиции '{req.name}'.")

        part = Part.objects.create(
            name=req.name,
            sku=sku,
            category=category,
            purchase_price=purchase_price,
            sale_price=sale_price,
        )
        compatible_ids = data.get("compatible_models") or []
        if compatible_ids:
            part.compatible_models.set(compatible_ids)
        elif order.device_model_id:
            part.compatible_models.set([order.device_model_id])

        stock_qty = int(data.get("stock_qty") or 0)
        StockItem.objects.create(part=part, quantity_on_hand=stock_qty)

        OrderPartLine.objects.create(
            work_order=order,
            part=part,
            quantity=req.quantity,
            unit_price=sale_price,
        )

        req.status = ProcurementStatus.APPROVED
        req.purchase_price = purchase_price
        req.sale_price = sale_price
        req.resulting_part = part
        req.resolved_by = user
        req.resolved_at = timezone.now()
        req.save()

    if user and order.warehouse_keeper_id is None:
        order.warehouse_keeper = user
        order.save(update_fields=["warehouse_keeper", "updated_at"])

    return set_status(
        order,
        WorkOrderStatus.AWAITING_APPROVAL,
        user,
        "Закупка согласована складом, отправлено клиенту",
    )


@transaction.atomic
def reject_procurement(order: WorkOrder, user: User | None, reason: str = "") -> WorkOrder:
    if order.status != WorkOrderStatus.AWAITING_PROCUREMENT:
        raise WorkflowError("Отклонение закупки доступно только в статусе AWAITING_PROCUREMENT.")
    order.procurement_requests.filter(status=ProcurementStatus.PENDING).update(
        status=ProcurementStatus.REJECTED,
        resolved_by=user,
        resolved_at=timezone.now(),
        resolution_note=reason,
    )
    if user and order.warehouse_keeper_id is None:
        order.warehouse_keeper = user
        order.save(update_fields=["warehouse_keeper", "updated_at"])
    return set_status(
        order,
        WorkOrderStatus.PROCUREMENT_REJECTED,
        user,
        f"Склад отказал в закупке: {reason}" if reason else "Склад отказал в закупке",
    )


@transaction.atomic
def mark_unrepairable(order: WorkOrder, user: User | None, reason: str = "") -> WorkOrder:
    if order.status != WorkOrderStatus.PROCUREMENT_REJECTED:
        raise WorkflowError(
            "Объявить заявку неремонтопригодной можно только после отказа склада в закупке."
        )
    return set_status(
        order,
        WorkOrderStatus.UNREPAIRABLE,
        user,
        f"Невозможно починить: {reason}" if reason else "Невозможно починить",
    )


@transaction.atomic
def approve_order(order: WorkOrder, approved: bool, user: User | None, note: str = "") -> WorkOrder:
    if order.status != WorkOrderStatus.AWAITING_APPROVAL:
        raise WorkflowError("Согласование доступно только на этапе ожидания согласования.")

    order.customer_approved = approved
    order.save(update_fields=["customer_approved", "updated_at"])
    if approved:
        return set_status(order, WorkOrderStatus.APPROVED, user, note or "Клиент согласовал ремонт")
    return set_status(order, WorkOrderStatus.REJECTED, user, note or "Клиент отказался от ремонта")


@transaction.atomic
def reserve_parts(order: WorkOrder, sales_prices: dict[int, Decimal], user: User | None) -> None:
    if order.status != WorkOrderStatus.APPROVED:
        raise WorkflowError("Резервирование доступно только для согласованных заявок.")

    if order.part_reservations.exists():
        raise WorkflowError("Для заявки уже создан резерв деталей.")

    for line in order.part_lines.select_related("part"):
        stock = StockItem.objects.select_for_update().get(part=line.part)
        if stock.quantity_on_hand < line.quantity:
            raise WorkflowError(f"Недостаточно деталей на складе: {line.part.sku}")
        sale_price = sales_prices.get(line.id)
        if sale_price is None:
            raise WorkflowError(f"Не указана цена продажи для {line.part.name}.")
        PartReservation.objects.create(
            work_order=order,
            part=line.part,
            quantity=line.quantity,
            sale_unit_price=sale_price,
            status=ReservationStatus.RESERVED,
        )
    if user and order.warehouse_keeper_id is None:
        order.warehouse_keeper = user
        order.save(update_fields=["warehouse_keeper", "updated_at"])
    set_status(order, WorkOrderStatus.IN_PROGRESS, user, "Детали зарезервированы, ремонт начат")


@transaction.atomic
def issue_reserved_parts(order: WorkOrder, user: User | None) -> None:
    reservations = order.part_reservations.select_related("part").filter(status=ReservationStatus.RESERVED)
    for reservation in reservations:
        stock = StockItem.objects.select_for_update().get(part=reservation.part)
        if stock.quantity_on_hand < reservation.quantity:
            raise WorkflowError(f"Остаток изменился, не хватает {reservation.part.sku}")
        stock.quantity_on_hand -= reservation.quantity
        stock.save(update_fields=["quantity_on_hand"])
        reservation.status = ReservationStatus.ISSUED
        reservation.issued_at = timezone.now()
        reservation.issued_by = user
        reservation.save(update_fields=["status", "issued_at", "issued_by"])
        PartUsage.objects.create(reservation=reservation, used_quantity=reservation.quantity)


@transaction.atomic
def complete_order(order: WorkOrder, user: User | None) -> WorkOrder:
    if order.status != WorkOrderStatus.IN_PROGRESS:
        raise WorkflowError("Завершение ремонта доступно только для заказов в работе.")
    issue_reserved_parts(order, user)
    return set_status(order, WorkOrderStatus.COMPLETED, user, "Ремонт завершен")


@transaction.atomic
def register_payment(
    order: WorkOrder,
    user: User | None,
    note: str = "",
    charge_diagnostic: bool | None = None,
) -> Payment | None:
    """
    Правила:
    - COMPLETED → FULL_REPAIR на full_repair_total
    - REJECTED (отказ клиента) → DIAGNOSTIC_ONLY на diagnosis_fee
    - UNREPAIRABLE → выбор менеджера: True → DIAGNOSTIC_ONLY, False → WAIVED (без платежа)
    """
    if order.status not in {
        WorkOrderStatus.REJECTED,
        WorkOrderStatus.COMPLETED,
        WorkOrderStatus.UNREPAIRABLE,
    }:
        raise WorkflowError("Оплата доступна только после отказа, завершения ремонта или признания нере монтопригодности.")

    if order.status == WorkOrderStatus.UNREPAIRABLE:
        if charge_diagnostic is None:
            raise WorkflowError("Нужно явно указать, брать ли плату за диагностику.")
        if charge_diagnostic:
            payment = Payment.objects.create(
                work_order=order,
                amount=order.diagnosis_fee,
                kind=PaymentKind.DIAGNOSTIC_ONLY,
                recorded_by=user,
                note=note,
            )
        else:
            payment = None
        set_status(order, WorkOrderStatus.CLOSED, user, "Закрыто: невозможно починить")
        return payment

    if order.status == WorkOrderStatus.REJECTED:
        amount = order.diagnosis_fee
        kind = PaymentKind.DIAGNOSTIC_ONLY
    else:
        amount = order.full_repair_total
        kind = PaymentKind.FULL_REPAIR

    payment = Payment.objects.create(
        work_order=order,
        amount=amount,
        kind=kind,
        recorded_by=user,
        note=note,
    )
    set_status(order, WorkOrderStatus.CLOSED, user, "Оплата получена")
    return payment
