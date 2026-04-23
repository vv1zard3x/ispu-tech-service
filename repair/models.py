from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone
from django.utils.text import slugify


class WorkOrderStatus(models.TextChoices):
    NEW = "new", "Новая"
    ASSIGNED = "assigned", "Назначена исполнителю"
    DIAGNOSED = "diagnosed", "Диагностика выполнена"
    AWAITING_PROCUREMENT = "awaiting_procurement", "Ждёт согласования закупки"
    PROCUREMENT_REJECTED = "procurement_rejected", "Склад отказал в закупке"
    UNREPAIRABLE = "unrepairable", "Невозможно починить"
    AWAITING_APPROVAL = "awaiting_approval", "Ожидает согласования клиентом"
    APPROVED = "approved", "Согласовано клиентом"
    REJECTED = "rejected", "Отказ от ремонта"
    IN_PROGRESS = "in_progress", "В работе"
    COMPLETED = "completed", "Ремонт выполнен"
    CLOSED = "closed", "Закрыта"


class PaymentKind(models.TextChoices):
    DIAGNOSTIC_ONLY = "diagnostic_only", "Только диагностика (отказ)"
    FULL_REPAIR = "full_repair", "Полный ремонт"
    WAIVED = "waived", "Без оплаты"


class ProcurementStatus(models.TextChoices):
    PENDING = "pending", "Ожидает согласования"
    APPROVED = "approved", "Одобрено"
    REJECTED = "rejected", "Отклонено"


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


class DeviceCategory(models.Model):
    name = models.CharField("Название", max_length=128, unique=True)
    slug = models.SlugField("Слаг", max_length=140, unique=True, blank=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "Категория устройств"
        verbose_name_plural = "Категории устройств"

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.name, allow_unicode=False) or "category"
            unique = base
            counter = 1
            while DeviceCategory.objects.filter(slug=unique).exclude(pk=self.pk).exists():
                counter += 1
                unique = f"{base}-{counter}"
            self.slug = unique
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.name


class DeviceModel(models.Model):
    category = models.ForeignKey(
        DeviceCategory,
        on_delete=models.PROTECT,
        related_name="models",
        verbose_name="Категория",
    )
    brand = models.CharField("Бренд", max_length=128, blank=True)
    model = models.CharField("Модель", max_length=128)

    class Meta:
        ordering = ["category__name", "brand", "model"]
        unique_together = [("category", "brand", "model")]
        verbose_name = "Модель устройства"
        verbose_name_plural = "Модели устройств"

    def __str__(self) -> str:
        label = f"{self.brand} {self.model}".strip()
        return label or self.model


class Part(models.Model):
    name = models.CharField("Наименование", max_length=255)
    sku = models.SlugField("Артикул", max_length=64, unique=True)
    category = models.ForeignKey(
        DeviceCategory,
        on_delete=models.PROTECT,
        related_name="parts",
        verbose_name="Категория устройств",
    )
    compatible_models = models.ManyToManyField(
        DeviceModel,
        blank=True,
        related_name="compatible_parts",
        verbose_name="Совместимые модели",
        help_text="Если пусто — совместимо со всеми моделями категории.",
    )
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


class WorkCatalogItem(models.Model):
    title = models.CharField("Наименование работы", max_length=255, unique=True)
    default_labor_cost = models.DecimalField(
        "Стоимость по умолчанию",
        max_digits=12,
        decimal_places=2,
        default=Decimal("0"),
        validators=[MinValueValidator(Decimal("0"))],
    )
    category = models.ForeignKey(
        DeviceCategory,
        on_delete=models.SET_NULL,
        related_name="work_catalog",
        verbose_name="Категория",
        null=True,
        blank=True,
    )

    class Meta:
        ordering = ["title"]
        verbose_name = "Справочник работ"
        verbose_name_plural = "Справочник работ"

    def __str__(self) -> str:
        return self.title


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
    device_model = models.ForeignKey(
        DeviceModel,
        on_delete=models.PROTECT,
        related_name="work_orders",
        verbose_name="Устройство",
    )
    serial_number = models.CharField("Серийный номер", max_length=128, blank=True)
    issue_description = models.TextField("Описание неисправности", blank=True)
    manager = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="managed_work_orders",
        verbose_name="Менеджер",
        null=True,
        blank=True,
    )
    technician = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="technician_work_orders",
        verbose_name="Техник",
        null=True,
        blank=True,
    )
    warehouse_keeper = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="warehouse_work_orders",
        verbose_name="Кладовщик",
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

    @property
    def current_assignee(self):
        """Кто сейчас должен действовать — аналог assignee в Jira."""
        status = self.status
        if status in {
            WorkOrderStatus.ASSIGNED,
            WorkOrderStatus.IN_PROGRESS,
        }:
            return self.technician
        if status in {
            WorkOrderStatus.AWAITING_PROCUREMENT,
            WorkOrderStatus.APPROVED,
        }:
            return self.warehouse_keeper
        if status in {
            WorkOrderStatus.NEW,
            WorkOrderStatus.DIAGNOSED,
            WorkOrderStatus.AWAITING_APPROVAL,
            WorkOrderStatus.PROCUREMENT_REJECTED,
            WorkOrderStatus.UNREPAIRABLE,
            WorkOrderStatus.REJECTED,
            WorkOrderStatus.COMPLETED,
        }:
            return self.manager
        return None

    def current_assignee_role(self) -> str:
        """Какой ролевой слот сейчас активен — для бейджа в UI."""
        if self.current_assignee is None:
            return ""
        if self.current_assignee_id == getattr(self, "technician_id", None):
            return "technician"
        if self.current_assignee_id == getattr(self, "warehouse_keeper_id", None):
            return "warehouse"
        if self.current_assignee_id == getattr(self, "manager_id", None):
            return "manager"
        return ""

    @property
    def current_assignee_id(self):
        value = self.current_assignee
        return getattr(value, "id", None)

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


class UserProfile(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="profile",
        verbose_name="Пользователь",
    )
    full_name = models.CharField("ФИО", max_length=255, blank=True)
    phone = models.CharField("Телефон", max_length=32, blank=True)
    require_password_change = models.BooleanField(
        "Требуется смена пароля",
        default=False,
        help_text="Выставляется при создании/сбросе учётки. Снимается после смены.",
    )

    class Meta:
        verbose_name = "Профиль пользователя"
        verbose_name_plural = "Профили пользователей"

    def __str__(self) -> str:
        return self.full_name or self.user.get_username()


def ensure_profile(user) -> "UserProfile":
    profile, _ = UserProfile.objects.get_or_create(user=user)
    return profile


class ProcurementRequest(models.Model):
    work_order = models.ForeignKey(
        WorkOrder,
        on_delete=models.CASCADE,
        related_name="procurement_requests",
        verbose_name="Заявка",
    )
    name = models.CharField("Наименование", max_length=255)
    quantity = models.PositiveIntegerField("Количество")
    note = models.TextField("Примечание", blank=True)
    category = models.ForeignKey(
        DeviceCategory,
        on_delete=models.SET_NULL,
        related_name="procurement_requests",
        verbose_name="Категория устройств",
        null=True,
        blank=True,
    )
    status = models.CharField(
        "Статус",
        max_length=16,
        choices=ProcurementStatus.choices,
        default=ProcurementStatus.PENDING,
    )
    purchase_price = models.DecimalField(
        "Закупочная цена",
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0"))],
    )
    sale_price = models.DecimalField(
        "Цена продажи",
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0"))],
    )
    resolution_note = models.CharField("Комментарий склада", max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_procurement_requests",
        verbose_name="Запросил",
    )
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="resolved_procurement_requests",
        verbose_name="Решил",
    )
    resulting_part = models.ForeignKey(
        Part,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="procurement_origins",
        verbose_name="Созданная деталь",
    )

    class Meta:
        verbose_name = "Запрос на закупку"
        verbose_name_plural = "Запросы на закупку"

    def __str__(self) -> str:
        return f"{self.name} x{self.quantity}"
