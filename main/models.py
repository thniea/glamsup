# main/models.py
from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models
from django.utils.text import slugify
from decimal import Decimal


# ========= Core / Users =========

class User(AbstractUser):
    class Role(models.TextChoices):
        CUSTOMER = "CUSTOMER", "Customer"
        STAFF    = "STAFF", "Staff"
        ADMIN    = "ADMIN", "Admin"

    # username, password, email… có sẵn từ AbstractUser
    full_name    = models.CharField(max_length=120, blank=True)
    phone_number = models.CharField(max_length=20, blank=True)
    address      = models.CharField(max_length=255, blank=True)
    role         = models.CharField(max_length=20, choices=Role.choices, default=Role.CUSTOMER)
    date_of_birth = models.DateField(null=True, blank=True)
    avatar = models.ImageField(upload_to="avatars/", blank=True, null=True)

    @property
    def avatar_url(self):
        try:
            return self.avatar.url
        except Exception:
            return "/static/img/avatar-placeholder.png"
    def __str__(self):
        return self.full_name or self.username


# ========= Catalog =========

class Branch(models.Model):
    name    = models.CharField(max_length=120)
    address = models.CharField(max_length=255)
    phone   = models.CharField(max_length=20, blank=True)

    def __str__(self):
        return self.name


class Service(models.Model):
    class Category(models.TextChoices):
        MANICURE = "MANICURE", "Manicure"
        PEDICURE = "PEDICURE", "Pedicure"
        MORE = "MORE", "More"
    service_name = models.CharField(max_length=150)
    slug         = models.SlugField(max_length=180, unique=True, blank=True)
    description  = models.TextField(blank=True)
    price        = models.DecimalField(
        max_digits=12, decimal_places=2, validators=[MinValueValidator(0)], default=Decimal("0.00")
    )
    # Ảnh upload vào MEDIA_ROOT/services/
    image = models.ImageField(upload_to="services/", blank=True, null=True)
    is_active = models.BooleanField(default=True)
    category = models.CharField(max_length=20, choices=Category.choices, default=Category.MANICURE)
    duration = models.PositiveIntegerField(default=60, help_text="minutes")
    class Meta:
        ordering = ["service_name"]
        indexes = [
            models.Index(fields=["is_active"]),
            models.Index(fields=["category"]),
        ]

    @property
    def avg_rating(self):
        """
        Điểm rating trung bình (0–5) từ các Review đã publish.
        Dùng cho trang list & trang chi tiết dịch vụ.
        """
        from django.db.models import Avg
        from .models import Review  # cùng file nên OK, không bị vòng lặp

        data = self.reviews.filter(
            status=Review.Status.PUBLISHED
        ).aggregate(avg=Avg("rating"))
        return data["avg"] or Decimal("0")

    @property
    def avg_rating_int(self):
        """
        Số sao làm tròn (0–5) để so sánh trong template.
        """
        try:
            return int(round(float(self.avg_rating or 0)))
        except Exception:
            return 0
    def save(self, *args, **kwargs):
        # Tự tạo slug nếu bỏ trống
        if not self.slug:
            base = slugify(self.service_name) or "service"
            candidate = base
            i = 1
            while Service.objects.filter(slug=candidate).exclude(pk=self.pk).exists():
                i += 1
                candidate = f"{base}-{i}"
            self.slug = candidate
        super().save(*args, **kwargs)

    def __str__(self):
        return self.service_name


# ========= Booking / Operations =========

class Appointment(models.Model):
    class Status(models.TextChoices):
        PENDING   = "PENDING", "Pending"
        CONFIRMED = "CONFIRMED", "Confirmed"
        IN_PROGRESS = "IN_PROGRESS", "In progress"
        DONE      = "DONE", "Done"
        CANCELED  = "CANCELED", "Canceled"
        ARRIVED = "ARRIVED", "Đã đến"
        ONGOING = "ONGOING", "Đang thực hiện"

    customer          = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="appointments",
        limit_choices_to={"role": User.Role.CUSTOMER}
    )
    branch            = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="appointments")
    appointment_date  = models.DateField()
    appointment_time  = models.TimeField()
    duration_minutes  = models.PositiveIntegerField(default=60, validators=[MinValueValidator(1)])
    status            = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    total_price       = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    loyalty_awarded = models.BooleanField(default=False, help_text="Đã cộng điểm loyalty cho lịch này chưa?")
    # Services & Staff (many-to-many qua bảng trung gian)
    services = models.ManyToManyField("Service", through="AppointmentService", related_name="appointment_items")
    staff    = models.ManyToManyField(
        settings.AUTH_USER_MODEL, through="AppointmentStaff", related_name="staff_appointments",
        blank=True
    )

    note       = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["appointment_date"]),
            models.Index(fields=["status"]),
        ]
        ordering = ["-appointment_date", "-appointment_time"]

    def __str__(self):
        return f"Appt#{self.pk} - {self.customer} @ {self.branch} {self.appointment_date} {self.appointment_time}"

    # Helper: hợp nhất date+time
    def get_start_datetime(self):
        from datetime import datetime
        return datetime.combine(self.appointment_date, self.appointment_time)

    # Tính lại tổng tiền từ các service_lines
    def recalc_total(self, save=True):
        total = Decimal("0.00")
        for line in self.service_lines.all():
            total += (line.unit_price or Decimal("0.00")) * line.quantity
        self.total_price = total
        if save:
            self.save(update_fields=["total_price"])
        return total


class AppointmentService(models.Model):
    """Bảng nối Appointment - Service (có đơn giá/SL để tính tổng)."""
    appointment = models.ForeignKey(Appointment, on_delete=models.CASCADE, related_name="service_lines")
    service     = models.ForeignKey(Service, on_delete=models.PROTECT, related_name="service_lines")
    quantity    = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)])
    unit_price  = models.DecimalField(
        max_digits=12, decimal_places=2, validators=[MinValueValidator(0)], default=Decimal("0.00")
    )

    class Meta:
        unique_together = ("appointment", "service")

    @property
    def line_total(self):
        return (self.unit_price or Decimal("0.00")) * self.quantity

    def __str__(self):
        return f"{self.service} x{self.quantity} (Appt {self.appointment_id})"


class AppointmentStaff(models.Model):
    """Bảng nối Appointment - Staff."""
    appointment = models.ForeignKey(Appointment, on_delete=models.CASCADE, related_name="staff_lines")
    staff       = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="assigned_lines",
        limit_choices_to={"role": User.Role.STAFF}
    )

    class Meta:
        unique_together = ("appointment", "staff")

    def __str__(self):
        return f"{self.staff} on Appt {self.appointment_id}"


class StaffSchedule(models.Model):
    class Shift(models.TextChoices):
        MORNING   = "MORNING", "Morning"
        AFTERNOON = "AFTERNOON", "Afternoon"
        EVENING   = "EVENING", "Evening"

    class Status(models.TextChoices):
        PENDING   = "PENDING", "Pending"
        APPROVED  = "APPROVED", "Approved"
        REJECTED  = "REJECTED", "Rejected"

    staff       = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        limit_choices_to={"role": User.Role.STAFF}
    )
    work_date   = models.DateField()
    shift       = models.CharField(max_length=20, choices=Shift.choices)
    status      = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    branch      = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="staff_schedules", null=True, blank=True)
    created_at  = models.DateTimeField(auto_now_add=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="approved_schedules"
    )

    class Meta:
        unique_together = ("staff", "work_date", "shift")
        ordering = ["-work_date"]

    def __str__(self):
        return f"{self.staff} {self.work_date} {self.shift} ({self.status})"



# ========= Reviews =========

# ========= Reviews =========

class Review(models.Model):
    class Status(models.TextChoices):
        PUBLISHED = "PUBLISHED", "Published"
        HIDDEN    = "HIDDEN", "Hidden"
        PENDING   = "PENDING", "Pending"

    # mỗi review gắn với 1 lịch + 1 khách + 1 dịch vụ
    appointment = models.ForeignKey(
        Appointment,
        on_delete=models.CASCADE,
        related_name="reviews",
        null=True,
        blank=True,
    )
    customer    = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="reviews",
        limit_choices_to={"role": User.Role.CUSTOMER}
    )
    service     = models.ForeignKey(
        Service,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviews",
    )

    review_date = models.DateField(auto_now_add=True)

    # ⭐ comment – cái này form & template đang dùng, bắt buộc phải có
    comment     = models.TextField(blank=True)

    # rating tổng (dùng để tính trung bình, vẽ sao ở trang chi tiết)
    rating      = models.DecimalField(
        max_digits=2,
        decimal_places=1,
        validators=[MinValueValidator(0), MaxValueValidator(5)],
        default=Decimal("5.0"),
    )

    status      = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PUBLISHED,
    )

    # ảnh / video + điểm chi tiết
    image = models.ImageField(
        upload_to="reviews/photos/",
        blank=True,
        null=True,
    )
    video = models.FileField(
        upload_to="reviews/videos/",
        blank=True,
        null=True,
    )

    product_rating = models.PositiveSmallIntegerField(default=0)
    service_rating = models.PositiveSmallIntegerField(default=0)

    # phản hồi của admin
    admin_reply     = models.TextField(blank=True)
    admin_replied_at = models.DateTimeField(null=True, blank=True)

    @property
    def rating_int(self):
        """
        Rating dạng số nguyên (0–5) để dễ render sao trong template.
        """
        try:
            return int(round(float(self.rating or 0)))
        except Exception:
            return 0
    class Meta:
        ordering = ["-review_date"]

    def __str__(self):
        return f"Review#{self.pk} {self.rating}★ by {self.customer}"

    def save(self, *args, **kwargs):
        """
        Đồng bộ rating tổng = trung bình (product + service)
        để những chỗ đang dùng rating không bị sai.
        """
        values = [v for v in [self.product_rating, self.service_rating] if v]
        if values:
            avg = sum(values) / len(values)
            # ép về khoảng 0–5 và làm tròn 1 chữ số thập phân
            avg = max(0, min(5, avg))
            self.rating = Decimal(str(round(avg, 1)))
        super().save(*args, **kwargs)



# ========= Payments =========

class Payment(models.Model):
    class Method(models.TextChoices):
        CASH    = "CASH", "Cash"
        CARD    = "CARD", "Card"
        ONLINE  = "ONLINE", "Online"

    class Status(models.TextChoices):
        UNPAID   = "UNPAID", "Unpaid"
        PENDING  = "PENDING", "Pending"
        PAID     = "PAID", "Paid"
        REFUNDED = "REFUNDED", "Refunded"

    appointment   = models.ForeignKey(Appointment, on_delete=models.CASCADE, related_name="payments")
    payment_date  = models.DateTimeField(auto_now_add=True)
    amount        = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(0)])
    method        = models.CharField(max_length=20, choices=Method.choices)
    status        = models.CharField(max_length=20, choices=Status.choices, default=Status.UNPAID)
    amount_paid   = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    change_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    transaction_code = models.CharField(max_length=100, blank=True)

    class Meta:
        ordering = ["-payment_date"]

    def __str__(self):
        return f"Payment#{self.pk} {self.method} {self.amount} for Appt#{self.appointment_id}"


# ========= Loyalty =========

class LoyaltyTransaction(models.Model):
    class Type(models.TextChoices):
        EARN   = "EARN", "Earn"
        USE    = "USE", "Use"
        ADJUST = "ADJUST", "Adjust"

    customer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="loyalty_transactions",
        limit_choices_to={"role": User.Role.CUSTOMER},
    )
    appointment = models.ForeignKey(
        Appointment,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="loyalty_transactions",
    )
    type = models.CharField(max_length=20, choices=Type.choices, default=Type.EARN)
    points = models.IntegerField(help_text=">0 là cộng, <0 là trừ")
    balance_after = models.IntegerField(default=0, help_text="Số điểm còn lại sau giao dịch")
    description = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        sign = "+" if self.points > 0 else ""
        return f"{self.customer} {sign}{self.points} pts"

    @property
    def booking_code(self):
        if self.appointment_id:
            return f"BK{self.appointment_id:06d}"
        return ""

class LoyaltyPoints(models.Model):
    customer       = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="loyalty")
    last_updated   = models.DateTimeField(auto_now=True)
    current_points = models.IntegerField(default=0)
    points_used    = models.IntegerField(default=0)
    points_earned  = models.IntegerField(default=0)

    def __str__(self):
        return f"{self.customer} - {self.current_points} pts"
class ContactMessage(models.Model):
    class Status(models.TextChoices):
        NEW = "new", "New"
        IN_PROGRESS = "in_progress", "In progress"
        RESOLVED = "resolved", "Resolved"

    full_name   = models.CharField(max_length=120)
    email       = models.EmailField()
    phone       = models.CharField(max_length=20)
    content     = models.TextField()
    branch_name = models.CharField(max_length=120, blank=True)  # khách có thể chọn/ghi cơ sở nếu muốn
    status      = models.CharField(max_length=20, choices=Status.choices, default=Status.NEW)
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="assigned_contacts"
    )
    created_at  = models.DateTimeField(auto_now_add=True)
    source      = models.CharField(max_length=20, default="web")

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"[{self.get_status_display()}] {self.full_name} - {self.phone}"

class ContactMessage(models.Model):
    class Status(models.TextChoices):
        NEW = "new", "New"
        IN_PROGRESS = "in_progress", "In progress"
        RESOLVED = "resolved", "Resolved"

    full_name   = models.CharField(max_length=120)
    email       = models.EmailField()
    phone       = models.CharField(max_length=20)
    content     = models.TextField()
    branch_name = models.CharField(max_length=120, blank=True)  # khách có thể chọn/ghi cơ sở nếu muốn
    status      = models.CharField(max_length=20, choices=Status.choices, default=Status.NEW)
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="assigned_contacts"
    )
    created_at  = models.DateTimeField(auto_now_add=True)
    source      = models.CharField(max_length=20, default="web")

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"[{self.get_status_display()}] {self.full_name} - {self.phone}"
