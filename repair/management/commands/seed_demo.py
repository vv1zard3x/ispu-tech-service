from django.contrib.auth.models import Group, User
from django.core.management.base import BaseCommand

from repair.models import (
    Customer,
    DeviceCategory,
    DeviceModel,
    Part,
    StockItem,
    WorkCatalogItem,
    ensure_profile,
)


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
        admin_profile = ensure_profile(admin)
        admin_profile.full_name = "Администратор Системы"
        admin_profile.require_password_change = False
        admin_profile.save()

        def upsert(username: str, password: str, group: str) -> User:
            user, _ = User.objects.get_or_create(username=username)
            user.set_password(password)
            user.is_staff = False
            user.save()
            user.groups.clear()
            user.groups.add(Group.objects.get(name=group))
            profile = ensure_profile(user)
            profile.full_name = username.capitalize()
            profile.require_password_change = False
            profile.save()
            return user

        upsert("manager", "manager12345", "manager")
        upsert("technician", "technician12345", "technician")
        upsert("warehouse", "warehouse12345", "warehouse")

        customer, _ = Customer.objects.get_or_create(
            phone="+79991234567",
            defaults={"name": "Иванов Иван Иванович", "email": "client@example.com"},
        )

        phones, _ = DeviceCategory.objects.get_or_create(name="Смартфоны")
        printers, _ = DeviceCategory.objects.get_or_create(name="Принтеры")
        laptops, _ = DeviceCategory.objects.get_or_create(name="Ноутбуки")

        redmi, _ = DeviceModel.objects.get_or_create(
            category=phones, brand="Xiaomi", model="Redmi Note 12"
        )
        iphone, _ = DeviceModel.objects.get_or_create(
            category=phones, brand="Apple", model="iPhone 12"
        )
        hp_lj, _ = DeviceModel.objects.get_or_create(
            category=printers, brand="HP", model="LaserJet 1020"
        )
        canon, _ = DeviceModel.objects.get_or_create(
            category=printers, brand="Canon", model="i-SENSYS"
        )

        battery, _ = Part.objects.get_or_create(
            sku="battery-rn12",
            defaults={
                "name": "Аккумулятор Redmi Note 12",
                "category": phones,
                "purchase_price": "1800.00",
                "sale_price": "2400.00",
            },
        )
        battery.compatible_models.add(redmi)

        usb, _ = Part.objects.get_or_create(
            sku="usb-rn12",
            defaults={
                "name": "Разъем зарядки Redmi Note 12",
                "category": phones,
                "purchase_price": "900.00",
                "sale_price": "1400.00",
            },
        )
        usb.compatible_models.add(redmi)

        toner, _ = Part.objects.get_or_create(
            sku="toner-hp12a",
            defaults={
                "name": "Картридж HP 12A",
                "category": printers,
                "purchase_price": "2500.00",
                "sale_price": "3500.00",
            },
        )
        toner.compatible_models.add(hp_lj)

        StockItem.objects.get_or_create(part=battery, defaults={"quantity_on_hand": 10})
        StockItem.objects.get_or_create(part=usb, defaults={"quantity_on_hand": 20})
        StockItem.objects.get_or_create(part=toner, defaults={"quantity_on_hand": 5})

        WorkCatalogItem.objects.get_or_create(
            title="Замена аккумулятора", defaults={"default_labor_cost": "800.00", "category": phones}
        )
        WorkCatalogItem.objects.get_or_create(
            title="Чистка после залития", defaults={"default_labor_cost": "1500.00", "category": phones}
        )
        WorkCatalogItem.objects.get_or_create(
            title="Замена картриджа", defaults={"default_labor_cost": "500.00", "category": printers}
        )

        self.stdout.write(self.style.SUCCESS("Стартовые данные созданы."))
