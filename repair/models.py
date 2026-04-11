from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone


class WorkOrderStatus(models.TextChoices):
    NEW = "new", "Новая"
    ASSIGNED = "assigned", "Назначена исполнителю"
    DIAGNOSED = "diagnosed", "Диагностика выполнена"
    AWAITING_APPROVAL = "awaiting_approval", "Ожидает согласования"
    APPROVED = "approved", "Согласовано"
    REJECTED = "rejected", "Отказ от ремонта"
    IN_PROGRESS = "in_progress", "В работе"
    COMPLETED = "completed", "Ремонт выполнен"
    CLOSED = "closed", "Закрыта"


class PaymentKind(models.TextChoices):
    DIAGNOSTIC_ONLY = "diagnostic_only", "Только диагностика (отказ)"
    FULL_REPAIR = "full_repair", "Полный ремонт"


class ReservationStatus(models.TextChoices):
    RESERVED = "reserved", "Зарезервировано"
    ISSUED = "issued", "Выдано"
    CANCELLED = "cancelled", "Отменено"


class Customer(models.Model):
    name = models.CharField("ФИО", max_length=255)
    phone = models.CharField("Телефон", max_length=32)
    email = models.EmailField("Email", blank=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "Клиент"
        verbose_name_plural = "Клиенты"

    def __str__(self) -> str:
        return f"{self.name} ({self.phone})"


class Device(models.Model):
    customer = models.ForeignKey(
        Customer,
        on_delete=models.CASCADE,
        related_name="devices",
        verbose_name="Клиент",
    )
    brand = models.CharField("Бренд", max_length=128, blank=True)
    model = models.CharField("Модель", max_length=128)
    serial_number = models.CharField("Серийный номер", max_length=128, blank=True)
    issue_description = models.TextField("Описание неисправности")

    class Meta:
        verbose_name = "Устройство"
        verbose_name_plural = "Устройства"

    def __str__(self) -> str:
        return f"{self.brand} {self.model}".strip()


class Part(models.Model):
    name = models.CharField("Наименование", max_length=255)
    sku = models.SlugField("Артикул", max_length=64, unique=True)
    purchase_price = models.DecimalField(
        "Закупочная цена",
        max_digits=12,
        decimal_places=2,
        default=Decimal("0"),
        validators=[MinValueValidator(Decimal("0"))],
    )
    sale_price = models.DecimalField(
        "Цена продажи",
        max_digits=12,
        decimal_places=2,
        default=Decimal("0"),
        validators=[MinValueValidator(Decimal("0"))],
    )

    class Meta:
        ordering = ["sku"]
        verbose_name = "Деталь"
        verbose_name_plural = "Детали (номенклатура)"

    def __str__(self) -> str:
        return f"{self.sku} - {self.name}"


class StockItem(models.Model):
    part = models.OneToOneField(
        Part,
        on_delete=models.CASCADE,
        related_name="stock",
        verbose_name="Деталь",
    )
    quantity_on_hand = models.PositiveIntegerField("Остаток на складе", default=0)

    class Meta:
        verbose_name = "Остаток на складе"
        verbose_name_plural = "Остатки на складе"

    def __str__(self) -> str:
        return f"{self.part.sku}: {self.quantity_on_hand}"


class WorkOrder(models.Model):
    number = models.PositiveIntegerField(
        "Номер заявки",
        unique=True,
        blank=True,
        null=True,
        editable=False,
    )
    customer = models.ForeignKey(
        Customer,
        on_delete=models.PROTECT,
        related_name="work_orders",
        verbose_name="Клиент",
    )
    device = models.ForeignKey(
        Device,
        on_delete=models.PROTECT,
        related_name="work_orders",
        verbose_name="Устройство",
    )
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="assigned_work_orders",
        verbose_name="Исполнитель",
        null=True,
        blank=True,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="created_work_orders",
        verbose_name="Оформил",
        null=True,
    )
    status = models.CharField(
        "Статус",
        max_length=32,
        choices=WorkOrderStatus.choices,
        default=WorkOrderStatus.NEW,
        db_index=True,
    )
    received_at = models.DateTimeField("Дата приема", default=timezone.now)
    planned_deadline = models.DateTimeField("Плановый срок", null=True, blank=True)
    completed_at = models.DateTimeField("Фактическое завершение", null=True, blank=True)
    diagnosis_fee = models.DecimalField(
        "Стоимость диагностики",
        max_digits=12,
        decimal_places=2,
        default=Decimal("500.00"),
        validators=[MinValueValidator(Decimal("0"))],
    )
    customer_approved = models.BooleanField("Согласовано заказчиком", null=True, blank=True)
    notes = models.TextField("Примечания менеджера", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-received_at", "-number"]
        verbose_name = "Заявка на ремонт"
        verbose_name_plural = "Заявки на ремонт"

    def save(self, *args, **kwargs):
        if self.number is None:
            last_number = WorkOrder.objects.aggregate(models.Max("number"))["number__max"] or 0
            self.number = last_number + 1
        super().save(*args, **kwargs)

    @property
    def labor_total(self) -> Decimal:
        return sum((item.labor_cost for item in self.work_items.all()), Decimal("0"))

    @property
    def parts_total(self) -> Decimal:
        reserved_total = sum(
            (reservation.total for reservation in self.part_reservations.all() if reservation.sale_unit_price is not None),
            Decimal("0"),
        )
        if reserved_total > Decimal("0"):
            return reserved_total
        return sum((line.total for line in self.part_lines.all()), Decimal("0"))

    @property
    def full_repair_total(self) -> Decimal:
        return self.diagnosis_fee + self.labor_total + self.parts_total

    @property
    def is_overdue(self) -> bool:
        return bool(self.planned_deadline and timezone.now() > self.planned_deadline and self.status != WorkOrderStatus.CLOSED)

    def __str__(self) -> str:
        return f"Заявка #{self.number}"


class DiagnosticResult(models.Model):
    work_order = models.OneToOneField(
        WorkOrder,
        on_delete=models.CASCADE,
        related_name="diagnostic",
        verbose_name="Заявка",
    )
    findings = models.TextField("Результат диагностики")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Результат диагностики"
        verbose_name_plural = "Результаты диагностики"


class WorkItem(models.Model):
    work_order = models.ForeignKey(
        WorkOrder,
        on_delete=models.CASCADE,
        related_name="work_items",
        verbose_name="Заявка",
    )
    title = models.CharField("Наименование работы", max_length=255)
    labor_cost = models.DecimalField(
        "Стоимость работ",
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0"))],
    )

    class Meta:
        verbose_name = "Работа"
        verbose_name_plural = "Работы"

    def __str__(self) -> str:
        return self.title


class OrderPartLine(models.Model):
    work_order = models.ForeignKey(
        WorkOrder,
        on_delete=models.CASCADE,
        related_name="part_lines",
        verbose_name="Заявка",
    )
    part = models.ForeignKey(Part, on_delete=models.PROTECT, verbose_name="Деталь")
    quantity = models.PositiveIntegerField("Количество", validators=[MinValueValidator(1)])
    unit_price = models.DecimalField(
        "Цена за единицу",
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0"))],
    )

    class Meta:
        verbose_name = "Строка заявки (детали)"
        verbose_name_plural = "Строки заявки (детали)"

    @property
    def total(self) -> Decimal:
        return self.unit_price * self.quantity


class PartReservation(models.Model):
    work_order = models.ForeignKey(
        WorkOrder,
        on_delete=models.CASCADE,
        related_name="part_reservations",
        verbose_name="Заявка",
    )
    part = models.ForeignKey(Part, on_delete=models.PROTECT, verbose_name="Деталь")
    quantity = models.PositiveIntegerField("Количество")
    sale_unit_price = models.DecimalField(
        "Цена продажи за единицу",
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0"))],
    )
    status = models.CharField(
        max_length=16,
        choices=ReservationStatus.choices,
        default=ReservationStatus.RESERVED,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    issued_at = models.DateTimeField(null=True, blank=True)
    issued_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="issued_part_reservations",
        verbose_name="Выдал",
    )

    class Meta:
        verbose_name = "Резерв деталей"
        verbose_name_plural = "Резервы деталей"

    @property
    def total(self) -> Decimal:
        if self.sale_unit_price is None:
            return Decimal("0")
        return self.sale_unit_price * self.quantity


class PartUsage(models.Model):
    reservation = models.ForeignKey(
        PartReservation,
        on_delete=models.CASCADE,
        related_name="usages",
        verbose_name="Резерв",
    )
    used_quantity = models.PositiveIntegerField("Списано", validators=[MinValueValidator(1)])
    used_at = models.DateTimeField("Дата списания", default=timezone.now)

    class Meta:
        verbose_name = "Списание детали"
        verbose_name_plural = "Списания деталей"


class Payment(models.Model):
    work_order = models.ForeignKey(
        WorkOrder,
        on_delete=models.CASCADE,
        related_name="payments",
        verbose_name="Заявка",
    )
    amount = models.DecimalField(
        "Сумма",
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    kind = models.CharField("Тип оплаты", max_length=32, choices=PaymentKind.choices)
    paid_at = models.DateTimeField("Дата оплаты", default=timezone.now)
    note = models.CharField("Комментарий", max_length=255, blank=True)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="recorded_payments",
        verbose_name="Принял оплату",
    )

    class Meta:
        verbose_name = "Оплата"
        verbose_name_plural = "Оплаты"


class OrderStatusHistory(models.Model):
    work_order = models.ForeignKey(
        WorkOrder,
        on_delete=models.CASCADE,
        related_name="status_history",
        verbose_name="Заявка",
    )
    from_status = models.CharField("Из статуса", max_length=32, blank=True)
    to_status = models.CharField("В статус", max_length=32)
    changed_at = models.DateTimeField(auto_now_add=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="Пользователь",
    )
    note = models.CharField("Примечание", max_length=255, blank=True)

    class Meta:
        ordering = ["changed_at"]
        verbose_name = "История статуса"
        verbose_name_plural = "История статусов"
