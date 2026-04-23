from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.test import Client, TestCase
from django.urls import reverse

from .models import (
    Customer,
    DeviceCategory,
    DeviceModel,
    Part,
    PaymentKind,
    ProcurementStatus,
    StockItem,
    WorkCatalogItem,
    WorkOrder,
    WorkOrderStatus,
    ensure_profile,
)
from .services import (
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


def make_roles():
    for name in ("manager", "technician", "warehouse"):
        Group.objects.get_or_create(name=name)


class RepairWorkflowTests(TestCase):
    def setUp(self):
        make_roles()

        self.manager = User.objects.create_user("manager", password="pass")
        self.technician = User.objects.create_user("tech", password="pass")
        self.warehouse = User.objects.create_user("warehouse", password="pass")

        self.manager.groups.add(Group.objects.get(name="manager"))
        self.technician.groups.add(Group.objects.get(name="technician"))
        self.warehouse.groups.add(Group.objects.get(name="warehouse"))

        self.customer = Customer.objects.create(name="Test Client", phone="+70000000000")
        self.category = DeviceCategory.objects.create(name="Смартфоны")
        self.device_model = DeviceModel.objects.create(
            category=self.category, brand="Apple", model="iPhone 12"
        )
        self.part = Part.objects.create(
            name="Battery",
            sku="iphone12-battery",
            category=self.category,
            purchase_price=Decimal("2000.00"),
            sale_price=Decimal("2500.00"),
        )
        self.part.compatible_models.add(self.device_model)
        self.stock = StockItem.objects.create(part=self.part, quantity_on_hand=10)

        self.order = WorkOrder.objects.create(
            customer=self.customer,
            device_model=self.device_model,
            serial_number="SN-001",
            issue_description="Не включается",
            created_by=self.manager,
            manager=self.manager,
            diagnosis_fee=Decimal("500.00"),
        )

    def test_full_repair_workflow(self):
        assign_technician(self.order, self.technician, self.manager)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, WorkOrderStatus.ASSIGNED)
        self.assertEqual(self.order.technician, self.technician)
        self.assertEqual(self.order.manager, self.manager)
        self.assertEqual(self.order.current_assignee, self.technician)

        save_diagnostics(
            self.order,
            "Требуется замена батареи",
            [("Замена батареи", Decimal("1500.00"), True)],
            [(self.part, 1)],
            self.technician,
        )
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, WorkOrderStatus.AWAITING_APPROVAL)
        self.assertTrue(WorkCatalogItem.objects.filter(title="Замена батареи").exists())
        self.assertEqual(self.order.current_assignee, self.manager)

        approve_order(self.order, True, self.manager)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, WorkOrderStatus.APPROVED)
        self.assertEqual(self.order.current_assignee, None)  # warehouse_keeper ещё не назначен

        part_line = self.order.part_lines.first()
        reserve_parts(self.order, {part_line.id: Decimal("2500.00")}, self.warehouse)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, WorkOrderStatus.IN_PROGRESS)
        self.assertEqual(self.order.warehouse_keeper, self.warehouse)

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
            [("Диагностика", Decimal("0.00"), False)],
            [],
            self.technician,
        )
        approve_order(self.order, False, self.manager)
        payment = register_payment(self.order, self.manager)

        self.assertEqual(payment.kind, PaymentKind.DIAGNOSTIC_ONLY)
        self.assertEqual(payment.amount, Decimal("500.00"))

    def test_procurement_path_warehouse_approves(self):
        assign_technician(self.order, self.technician, self.manager)
        save_diagnostics(
            self.order,
            "Нужен оригинальный разъём",
            [("Пайка", Decimal("700.00"), False)],
            [],
            self.technician,
            procurement_items=[
                {"name": "Разъём iPhone 12", "quantity": 1, "note": "оригинал"},
            ],
        )
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, WorkOrderStatus.AWAITING_PROCUREMENT)
        self.assertEqual(self.order.current_assignee, self.order.warehouse_keeper)  # None пока, но роль верная

        req = self.order.procurement_requests.get()
        decisions = {
            req.id: {
                "purchase_price": Decimal("500.00"),
                "sale_price": Decimal("900.00"),
                "stock_qty": 1,
            }
        }
        approve_procurement(self.order, decisions, self.warehouse)
        self.order.refresh_from_db()
        req.refresh_from_db()

        self.assertEqual(self.order.status, WorkOrderStatus.AWAITING_APPROVAL)
        self.assertEqual(req.status, ProcurementStatus.APPROVED)
        self.assertIsNotNone(req.resulting_part)
        self.assertEqual(self.order.warehouse_keeper, self.warehouse)
        self.assertEqual(self.order.part_lines.count(), 1)
        part = req.resulting_part
        self.assertEqual(part.stock.quantity_on_hand, 1)

    def test_procurement_rejected_to_unrepairable(self):
        assign_technician(self.order, self.technician, self.manager)
        save_diagnostics(
            self.order,
            "Сгорел контроллер, аналогов нет",
            [("Замена контроллера", Decimal("1000.00"), False)],
            [],
            self.technician,
            procurement_items=[{"name": "Экзотический чип", "quantity": 1, "note": ""}],
        )
        reject_procurement(self.order, self.warehouse, reason="Нет у поставщиков")
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, WorkOrderStatus.PROCUREMENT_REJECTED)
        self.assertEqual(self.order.current_assignee, self.manager)

        mark_unrepairable(self.order, self.manager, reason="Клиенту сообщили")
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, WorkOrderStatus.UNREPAIRABLE)

    def test_unrepairable_close_with_diagnostic_charge(self):
        assign_technician(self.order, self.technician, self.manager)
        save_diagnostics(
            self.order,
            "Не чинится",
            [("Диагностика", Decimal("0.00"), False)],
            [],
            self.technician,
            procurement_items=[{"name": "Редкая деталь", "quantity": 1, "note": ""}],
        )
        reject_procurement(self.order, self.warehouse, reason="Нет")
        mark_unrepairable(self.order, self.manager)

        payment = register_payment(self.order, self.manager, charge_diagnostic=True)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, WorkOrderStatus.CLOSED)
        self.assertIsNotNone(payment)
        self.assertEqual(payment.kind, PaymentKind.DIAGNOSTIC_ONLY)
        self.assertEqual(payment.amount, Decimal("500.00"))

    def test_unrepairable_close_without_charge(self):
        assign_technician(self.order, self.technician, self.manager)
        save_diagnostics(
            self.order,
            "Не чинится",
            [],
            [],
            self.technician,
            procurement_items=[{"name": "X", "quantity": 1, "note": ""}],
        )
        reject_procurement(self.order, self.warehouse)
        mark_unrepairable(self.order, self.manager)

        payment = register_payment(self.order, self.manager, charge_diagnostic=False)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, WorkOrderStatus.CLOSED)
        self.assertIsNone(payment)
        self.assertFalse(self.order.payments.exists())


class RoleReassignmentTests(TestCase):
    def setUp(self):
        make_roles()
        self.manager_a = User.objects.create_user("man_a", password="pass")
        self.manager_b = User.objects.create_user("man_b", password="pass")
        self.tech_a = User.objects.create_user("t_a", password="pass")
        self.tech_b = User.objects.create_user("t_b", password="pass")
        for u in (self.manager_a, self.manager_b):
            u.groups.add(Group.objects.get(name="manager"))
        for u in (self.tech_a, self.tech_b):
            u.groups.add(Group.objects.get(name="technician"))

        customer = Customer.objects.create(name="C", phone="+7")
        category = DeviceCategory.objects.create(name="X")
        model = DeviceModel.objects.create(category=category, model="M")
        self.order = WorkOrder.objects.create(
            customer=customer,
            device_model=model,
            manager=self.manager_a,
            technician=self.tech_a,
            created_by=self.manager_a,
        )

    def test_manager_reassigns_technician_anytime(self):
        reassign_role(self.order, "technician", self.tech_b, self.manager_a)
        self.order.refresh_from_db()
        self.assertEqual(self.order.technician, self.tech_b)

    def test_manager_takes_over(self):
        reassign_role(self.order, "manager", self.manager_b, self.manager_b)
        self.order.refresh_from_db()
        self.assertEqual(self.order.manager, self.manager_b)


class AccessControlTests(TestCase):
    def setUp(self):
        self.client = Client()
        make_roles()
        self.manager = User.objects.create_user(username="manager", password="pass")
        self.tech = User.objects.create_user(username="tech", password="pass")
        self.manager.groups.add(Group.objects.get(name="manager"))
        self.tech.groups.add(Group.objects.get(name="technician"))
        customer = Customer.objects.create(name="Client", phone="+71111111111")
        category = DeviceCategory.objects.create(name="Phones")
        device_model = DeviceModel.objects.create(category=category, brand="", model="Phone")
        self.order = WorkOrder.objects.create(
            customer=customer,
            device_model=device_model,
            issue_description="Broken",
            created_by=self.manager,
        )

    def test_technician_cannot_create_order(self):
        self.client.login(username="tech", password="pass")
        response = self.client.get(reverse("repair:order-create"))
        self.assertEqual(response.status_code, 403)

    def test_manager_can_open_create_page(self):
        self.client.login(username="manager", password="pass")
        response = self.client.get(reverse("repair:order-create"))
        self.assertEqual(response.status_code, 200)

    def test_non_staff_cannot_open_users_page(self):
        self.client.login(username="manager", password="pass")
        response = self.client.get(reverse("repair:user-list"))
        self.assertEqual(response.status_code, 302)


class ForcePasswordChangeTests(TestCase):
    def setUp(self):
        self.client = Client()
        make_roles()
        self.user = User.objects.create_user(username="freshie", password="initial12345")
        self.user.groups.add(Group.objects.get(name="manager"))
        profile = ensure_profile(self.user)
        profile.require_password_change = True
        profile.save()

    def test_login_redirects_to_password_change(self):
        self.client.login(username="freshie", password="initial12345")
        response = self.client.get(reverse("repair:dashboard"), follow=False)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.endswith(reverse("repair:profile-password")))

    def test_password_change_clears_flag(self):
        self.client.login(username="freshie", password="initial12345")
        response = self.client.post(
            reverse("repair:profile-password"),
            {
                "old_password": "initial12345",
                "new_password1": "VeryStrongPwd9!",
                "new_password2": "VeryStrongPwd9!",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.user.refresh_from_db()
        self.user.profile.refresh_from_db()
        self.assertFalse(self.user.profile.require_password_change)


class UserManagementTests(TestCase):
    def setUp(self):
        self.client = Client()
        make_roles()
        self.admin = User.objects.create_superuser("root", email="", password="rootpass")
        profile = ensure_profile(self.admin)
        profile.require_password_change = False
        profile.save()

    def test_admin_can_create_user_with_force_flag(self):
        self.client.login(username="root", password="rootpass")
        response = self.client.post(
            reverse("repair:user-create"),
            {
                "username": "ivan",
                "full_name": "Иванов Иван",
                "email": "i@example.com",
                "phone": "+7",
                "role": "technician",
                "initial_password": "initial9876",
            },
        )
        self.assertEqual(response.status_code, 302)
        new_user = User.objects.get(username="ivan")
        self.assertTrue(new_user.groups.filter(name="technician").exists())
        self.assertTrue(new_user.profile.require_password_change)
