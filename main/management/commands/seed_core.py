# main/management/commands/seed_core.py
from django.core.management.base import BaseCommand
from django.db import transaction
from django.contrib.auth.models import Group
from django.contrib.auth import get_user_model
from django.conf import settings
from django.utils import timezone

from main.models import (Branch, Service, StaffSchedule, LoyaltyPoints, User)

from pathlib import Path
from decimal import Decimal
from datetime import timedelta
from django.core.files import File


class Command(BaseCommand):
    help = "Seed core data: Groups, Branches, Services (with placeholder image if found), demo users, and optional schedules."

    def add_arguments(self, parser):
        parser.add_argument(
            "--with-schedule",
            action="store_true",
            help="Also create a simple staff schedule for the next 7 days.",
        )

    # ---- Helpers ------------------------------------------------------------
    def _ensure_groups(self):
        groups = ["Admin", "Staff", "Customer"]
        for g in groups:
            Group.objects.get_or_create(name=g)
        self.stdout.write(self.style.SUCCESS(f"✓ Groups ensured: {', '.join(groups)}"))

    def _find_placeholder(self):
        """
        Tìm file ảnh placeholder trong các path hay gặp của nhóm:
        - main/static/images/placeholder.png
        - main/static/img/placeholder.png
        - static/images/placeholder.png (project-level nếu có)
        Trả về Path hoặc None nếu không tìm thấy.
        """
        candidates = [
            Path(settings.BASE_DIR) / "main" / "static" / "images" / "placeholder.png",
            Path(settings.BASE_DIR) / "main" / "static" / "img" / "placeholder.png",
            Path(settings.BASE_DIR) / "static" / "images" / "placeholder.png",
            Path(settings.BASE_DIR) / "static" / "img" / "placeholder.png",
        ]
        for p in candidates:
            if p.exists():
                return p
        return None

    def _attach_placeholder(self, service, placeholder_path: Path):
        """
        Gắn ảnh placeholder vào Service.image nếu tìm thấy file.
        """
        if not placeholder_path:
            return
        try:
            with placeholder_path.open("rb") as f:
                service.image.save(placeholder_path.name, File(f), save=True)
        except Exception as e:
            self.stdout.write(self.style.WARNING(f"! Could not attach image for {service.service_name}: {e}"))

    def _ensure_loyalty(self, user):
        LoyaltyPoints.objects.get_or_create(customer=user)

    # ---- Main seeding -------------------------------------------------------
    @transaction.atomic
    def handle(self, *args, **options):
        UserModel = get_user_model()

        # 1) Groups
        self._ensure_groups()

        # 2) Branches
        branches_seed = [
            ("CSI Branch", "97 Ngô Văn Sở, Đà Nẵng", "0236-000-000"),
            ("Brand 1 - Q1", "12 Nguyễn Huệ, Quận 1, TP.HCM", "028-111-222"),
        ]
        branches = []
        for name, addr, phone in branches_seed:
            br, _ = Branch.objects.get_or_create(name=name, defaults={"address": addr, "phone": phone})
            if br.address != addr or br.phone != phone:
                br.address, br.phone = addr, phone
                br.save(update_fields=["address", "phone"])
            branches.append(br)
        self.stdout.write(self.style.SUCCESS(f"✓ Branches ensured: {', '.join(b.name for b in branches)}"))

        # 3) Services (with optional placeholder image)
        placeholder = self._find_placeholder()
        services_seed = [
            ("Manicure Basic",  "Chăm sóc móng tay cơ bản.",   Decimal("120000")),
            ("Pedicure Spa",    "Chăm sóc móng chân thư giãn.", Decimal("180000")),
            ("Gel Polish",      "Sơn gel bền màu.",             Decimal("80000")),
            ("Nail Art Mini",   "Vẽ móng cơ bản.",              Decimal("100000")),
            ("Nail Removal",    "Tháo gel/an toàn.",            Decimal("50000")),
        ]
        created_names = []
        for name, desc, price in services_seed:
            sv, created = Service.objects.get_or_create(
                service_name=name,
                defaults={"description": desc, "price": price, "is_active": True},
            )
            # Cập nhật nếu giá/desc thay đổi
            dirty = False
            if sv.description != desc:
                sv.description = desc
                dirty = True
            if sv.price != price:
                sv.price = price
                dirty = True
            if dirty:
                sv.save()

            # Gắn placeholder nếu có và service chưa có ảnh
            if placeholder and not sv.image:
                self._attach_placeholder(sv, placeholder)

            created_names.append(sv.service_name)

        self.stdout.write(self.style.SUCCESS(f"✓ Services ensured: {', '.join(created_names)}"))
        if not placeholder:
            self.stdout.write(self.style.WARNING("! Placeholder image not found. Skipped attaching images."))

        # 4) Demo Users
        # Staff user
        staff_username = "staff1"
        staff_defaults = {
            "email": "staff1@example.com",
            "full_name": "Nhân viên Demo",
            "role": User.Role.STAFF if hasattr(User, "Role") else "STAFF",
            "is_staff": True,
            "is_active": True,
        }
        staff_user, created_staff = UserModel.objects.get_or_create(username=staff_username, defaults=staff_defaults)
        if created_staff:
            staff_user.set_password("12345678")
            staff_user.save()
        else:
            # ensure role/is_staff in case schema changed
            updated = False
            if hasattr(staff_user, "role") and staff_user.role != staff_defaults["role"]:
                staff_user.role = staff_defaults["role"]
                updated = True
            if not staff_user.is_staff:
                staff_user.is_staff = True
                updated = True
            if updated:
                staff_user.save()

        staff_group = Group.objects.get(name="Staff")
        staff_user.groups.add(staff_group)
        self._ensure_loyalty(staff_user)  # không bắt buộc, nhưng giữ đồng nhất cấu trúc

        # Customer user
        customer_username = "customer1"
        customer_defaults = {
            "email": "customer1@example.com",
            "full_name": "Khách hàng Demo",
            "role": User.Role.CUSTOMER if hasattr(User, "Role") else "CUSTOMER",
            "is_active": True,
        }
        customer_user, created_cus = UserModel.objects.get_or_create(username=customer_username, defaults=customer_defaults)
        if created_cus:
            customer_user.set_password("12345678")
            customer_user.save()
        else:
            if hasattr(customer_user, "role") and customer_user.role != customer_defaults["role"]:
                customer_user.role = customer_defaults["role"]
                customer_user.save()

        customer_group = Group.objects.get(name="Customer")
        customer_user.groups.add(customer_group)
        self._ensure_loyalty(customer_user)

        self.stdout.write(self.style.SUCCESS("✓ Demo users ensured: staff1 / customer1 (password: 12345678)"))

        # 5) Optional: create a simple schedule for next 7 days
        if options.get("with_schedule"):
            today = timezone.localdate()
            first_branch = branches[0] if branches else None
            if first_branch:
                created_cnt = 0
                for i in range(7):
                    d = today + timedelta(days=i)
                    for shift in [StaffSchedule.Shift.MORNING, StaffSchedule.Shift.AFTERNOON]:
                        obj, created = StaffSchedule.objects.get_or_create(
                            staff=staff_user, work_date=d, shift=shift,
                            defaults={
                                "status": StaffSchedule.Status.APPROVED,
                                "branch": first_branch,
                                "approved_by": None,
                            },
                        )
                        if created:
                            created_cnt += 1
                self.stdout.write(self.style.SUCCESS(f"✓ Staff schedule created/ensured for next 7 days: {created_cnt} rows"))
            else:
                self.stdout.write(self.style.WARNING("! No branch found to attach schedules."))

        self.stdout.write(self.style.SUCCESS("🎉 Seed done."))
