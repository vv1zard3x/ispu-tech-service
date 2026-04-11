from django.contrib import admin

from .models import (
    Customer,
    Device,
    DiagnosticResult,
    OrderPartLine,
    OrderStatusHistory,
    Part,
    PartReservation,
    PartUsage,
    Payment,
    StockItem,
    WorkItem,
    WorkOrder,
)


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ("name", "phone", "email")
    search_fields = ("name", "phone", "email")


@admin.register(Device)
class DeviceAdmin(admin.ModelAdmin):
    list_display = ("customer", "brand", "model", "serial_number")
    search_fields = ("customer__name", "brand", "model", "serial_number")


class WorkItemInline(admin.TabularInline):
    model = WorkItem
    extra = 0


class PartLineInline(admin.TabularInline):
    model = OrderPartLine
    extra = 0


@admin.register(WorkOrder)
class WorkOrderAdmin(admin.ModelAdmin):
    list_display = ("number", "customer", "status", "assigned_to", "planned_deadline", "completed_at")
    list_filter = ("status", "assigned_to")
    search_fields = ("number", "customer__name", "device__model")
    inlines = [WorkItemInline, PartLineInline]


admin.site.register([Part, StockItem, PartReservation, PartUsage, Payment, DiagnosticResult, OrderStatusHistory])
