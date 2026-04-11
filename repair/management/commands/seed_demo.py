from django.contrib.auth.models import Group, User
from django.core.management.base import BaseCommand

from repair.models import Customer, Device, Part, StockItem


GROUPS = ("manager", "technician", "warehouse")


class Command(BaseCommand):
    help = "Создает стартовые роли, пользователей и демонстрационные данные."

    def handle(self, *args, **options):
        for group_name in GROUPS:
            Group.objects.get_or_create(name=group_name)

        admin, created = User.objects.get_or_create(
            username="admin",
            defaults={"is_staff": True, "is_superuser": True, "email": "admin@example.com"},
        )
        if created:
            admin.set_password("admin12345")
            admin.save()

        manager, _ = User.objects.get_or_create(username="manager")
        manager.set_password("manager12345")
        manager.is_staff = True
        manager.save()
        manager.groups.add(Group.objects.get(name="manager"))

        technician, _ = User.objects.get_or_create(username="technician")
        technician.set_password("technician12345")
        technician.is_staff = True
        technician.save()
        technician.groups.add(Group.objects.get(name="technician"))

        warehouse, _ = User.objects.get_or_create(username="warehouse")
        warehouse.set_password("warehouse12345")
        warehouse.is_staff = True
        warehouse.save()
        warehouse.groups.add(Group.objects.get(name="warehouse"))

        customer, _ = Customer.objects.get_or_create(
            phone="+79991234567",
            defaults={"name": "Иванов Иван Иванович", "email": "client@example.com"},
        )
        Device.objects.get_or_create(
            customer=customer,
            model="Redmi Note 12",
            defaults={"brand": "Xiaomi", "serial_number": "RN12-001", "issue_description": "Не заряжается"},
        )

        part1, _ = Part.objects.get_or_create(
            sku="battery-rn12",
            defaults={"name": "Аккумулятор Redmi Note 12", "purchase_price": "1800.00", "sale_price": "2400.00"},
        )
        part2, _ = Part.objects.get_or_create(
            sku="usb-rn12",
            defaults={"name": "Разъем зарядки Redmi Note 12", "purchase_price": "900.00", "sale_price": "1400.00"},
        )

        StockItem.objects.get_or_create(part=part1, defaults={"quantity_on_hand": 10})
        StockItem.objects.get_or_create(part=part2, defaults={"quantity_on_hand": 20})

        self.stdout.write(self.style.SUCCESS("Стартовые данные созданы."))
