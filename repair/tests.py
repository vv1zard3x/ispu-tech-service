from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.test import Client, TestCase
from django.urls import reverse

from .models import (
    Customer,
    Device,
    OrderPartLine,
    Part,
    PaymentKind,
    StockItem,
    WorkItem,
    WorkOrder,
    WorkOrderStatus,
)
from .services import (
    approve_order,
    assign_technician,
    complete_order,
    register_payment,
    reserve_parts,
    save_diagnostics,
)


class RepairWorkflowTests(TestCase):
    def setUp(self):
        for group_name in ("manager", "technician", "warehouse"):
            Group.objects.get_or_create(name=group_name)

        self.manager = User.objects.create_user("manager", password="pass")
        self.technician = User.objects.create_user("tech", password="pass")
        self.warehouse = User.objects.create_user("warehouse", password="pass")

        self.manager.groups.add(Group.objects.get(name="manager"))
        self.technician.groups.add(Group.objects.get(name="technician"))
        self.warehouse.groups.add(Group.objects.get(name="warehouse"))

        self.customer = Customer.objects.create(name="Test Client", phone="+70000000000")
        self.device = Device.objects.create(
            customer=self.customer,
            brand="Apple",
            model="iPhone 12",
            issue_description="Не включается",
        )
        self.part = Part.objects.create(
            name="Battery",
            sku="iphone12-battery",
            purchase_price=Decimal("2000.00"),
            sale_price=Decimal("2500.00"),
        )
        self.stock = StockItem.objects.create(part=self.part, quantity_on_hand=10)

        self.order = WorkOrder.objects.create(
            customer=self.customer,
            device=self.device,
            created_by=self.manager,
            diagnosis_fee=Decimal("500.00"),
        )

    def test_full_repair_workflow(self):
        assign_technician(self.order, self.technician, self.manager)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, WorkOrderStatus.ASSIGNED)

        save_diagnostics(
            self.order,
            "Требуется замена батареи",
            [("Замена батареи", Decimal("1500.00"))],
            [(self.part, 1)],
            self.technician,
        )
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, WorkOrderStatus.AWAITING_APPROVAL)

        approve_order(self.order, True, self.manager)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, WorkOrderStatus.APPROVED)

        part_line = self.order.part_lines.first()
        reserve_parts(self.order, {part_line.id: Decimal("2500.00")}, self.warehouse)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, WorkOrderStatus.IN_PROGRESS)

        complete_order(self.order, self.technician)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, WorkOrderStatus.COMPLETED)

        payment = register_payment(self.order, self.manager)
        self.order.refresh_from_db()
        self.stock.refresh_from_db()

        self.assertEqual(payment.kind, PaymentKind.FULL_REPAIR)
        self.assertEqual(payment.amount, Decimal("4500.00"))
        self.assertEqual(self.stock.quantity_on_hand, 9)
        self.assertEqual(self.order.status, WorkOrderStatus.CLOSED)

    def test_rejected_order_charges_only_diagnostics(self):
        assign_technician(self.order, self.technician, self.manager)
        save_diagnostics(
            self.order,
            "Повреждена плата",
            [("Диагностика", Decimal("0.00"))],
            [],
            self.technician,
        )
        approve_order(self.order, False, self.manager)
        payment = register_payment(self.order, self.manager)

        self.assertEqual(payment.kind, PaymentKind.DIAGNOSTIC_ONLY)
        self.assertEqual(payment.amount, Decimal("500.00"))


class AccessControlTests(TestCase):
    def setUp(self):
        self.client = Client()
        manager_group, _ = Group.objects.get_or_create(name="manager")
        tech_group, _ = Group.objects.get_or_create(name="technician")
        self.manager = User.objects.create_user(username="manager", password="pass")
        self.tech = User.objects.create_user(username="tech", password="pass")
        self.manager.groups.add(manager_group)
        self.tech.groups.add(tech_group)
        customer = Customer.objects.create(name="Client", phone="+71111111111")
        device = Device.objects.create(customer=customer, model="Phone", issue_description="Broken")
        self.order = WorkOrder.objects.create(customer=customer, device=device, created_by=self.manager)

    def test_technician_cannot_create_order(self):
        self.client.login(username="tech", password="pass")
        response = self.client.get(reverse("repair:order-create"))
        self.assertEqual(response.status_code, 403)

    def test_manager_can_open_create_page(self):
        self.client.login(username="manager", password="pass")
        response = self.client.get(reverse("repair:order-create"))
        self.assertEqual(response.status_code, 200)
