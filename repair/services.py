from decimal import Decimal

from django.contrib.auth.models import User
from django.db import transaction
from django.utils import timezone

from .models import (
    DiagnosticResult,
    OrderStatusHistory,
    OrderPartLine,
    Part,
    PartReservation,
    PartUsage,
    Payment,
    PaymentKind,
    ReservationStatus,
    StockItem,
    WorkItem,
    WorkOrder,
    WorkOrderStatus,
)


ALLOWED_TRANSITIONS = {
    WorkOrderStatus.NEW: {WorkOrderStatus.ASSIGNED},
    WorkOrderStatus.ASSIGNED: {WorkOrderStatus.DIAGNOSED},
    WorkOrderStatus.DIAGNOSED: {WorkOrderStatus.AWAITING_APPROVAL},
    WorkOrderStatus.AWAITING_APPROVAL: {WorkOrderStatus.APPROVED, WorkOrderStatus.REJECTED},
    WorkOrderStatus.APPROVED: {WorkOrderStatus.IN_PROGRESS},
    WorkOrderStatus.IN_PROGRESS: {WorkOrderStatus.COMPLETED},
    WorkOrderStatus.REJECTED: {WorkOrderStatus.CLOSED},
    WorkOrderStatus.COMPLETED: {WorkOrderStatus.CLOSED},
    WorkOrderStatus.CLOSED: set(),
}


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
def assign_technician(order: WorkOrder, technician: User, user: User | None) -> WorkOrder:
    order.assigned_to = technician
    order.save(update_fields=["assigned_to", "updated_at"])
    return set_status(order, WorkOrderStatus.ASSIGNED, user, "Назначен исполнитель")


@transaction.atomic
def save_diagnostics(
    order: WorkOrder,
    findings: str,
    works: list[tuple[str, Decimal]],
    parts: list[tuple[Part, int]],
    user: User | None,
) -> WorkOrder:
    DiagnosticResult.objects.update_or_create(
        work_order=order,
        defaults={"findings": findings},
    )
    order.work_items.all().delete()
    order.part_lines.all().delete()

    WorkItem.objects.bulk_create(
        [WorkItem(work_order=order, title=title, labor_cost=cost) for title, cost in works if title]
    )
    OrderPartLine.objects.bulk_create(
        [
            OrderPartLine(work_order=order, part=part, quantity=qty, unit_price=part.sale_price)
            for part, qty in parts
        ]
    )
    set_status(order, WorkOrderStatus.DIAGNOSED, user, "Диагностика сохранена")
    return set_status(order, WorkOrderStatus.AWAITING_APPROVAL, user, "Ожидание согласования")


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
def register_payment(order: WorkOrder, user: User | None, note: str = "") -> Payment:
    if order.status not in {WorkOrderStatus.REJECTED, WorkOrderStatus.COMPLETED}:
        raise WorkflowError("Оплата доступна только после отказа или завершения ремонта.")

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
