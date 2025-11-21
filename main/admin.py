from django.contrib import admin
from .models import (
    User, Branch, Service, Appointment, AppointmentService, AppointmentStaff,
    StaffSchedule, Review, Payment, LoyaltyPoints
)


# ========== USER ==========
@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = ("id", "username", "full_name", "email", "role", "phone_number", "is_active")
    list_filter  = ("role", "is_active", "is_staff", "is_superuser")
    search_fields = ("username", "full_name", "email", "phone_number")
    ordering = ("id",)
    fieldsets = (
        ("Thông tin cơ bản", {"fields": ("username", "password", "role")}),
        ("Thông tin cá nhân", {"fields": ("full_name", "email", "phone_number", "address")}),
        ("Phân quyền hệ thống", {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
    )
    filter_horizontal = ("groups", "user_permissions")


# ========== BRANCH ==========
@admin.register(Branch)
class BranchAdmin(admin.ModelAdmin):
    list_display = ("name", "address", "phone")
    search_fields = ("name", "address")


# ========== SERVICE ==========
@admin.register(Service)
class ServiceAdmin(admin.ModelAdmin):
    list_display  = ("service_name", "price", "is_active", "slug")
    search_fields = ("service_name", "description")
    list_filter   = ("is_active",)
    prepopulated_fields = {"slug": ("service_name",)}
    readonly_fields = ("slug",)
    fieldsets = (
        ("Thông tin dịch vụ", {
            "fields": ("service_name", "slug", "description", "price", "image", "is_active")
        }),
    )


# ========== INLINE ==========
class AppointmentServiceInline(admin.TabularInline):
    model = AppointmentService
    extra = 0


class AppointmentStaffInline(admin.TabularInline):
    model = AppointmentStaff
    extra = 0


class PaymentInline(admin.TabularInline):
    model = Payment
    extra = 0
    readonly_fields = ("payment_date",)


class ReviewInline(admin.TabularInline):
    model = Review
    extra = 0
    readonly_fields = ("review_date",)


# ========== APPOINTMENT ==========
@admin.register(Appointment)
class AppointmentAdmin(admin.ModelAdmin):
    list_display = ("id", "customer", "branch", "appointment_date", "appointment_time", "status", "total_price")
    list_filter  = ("status", "branch", "appointment_date")
    search_fields = ("customer__username", "customer__full_name", "branch__name")
    date_hierarchy = "appointment_date"
    inlines = [AppointmentServiceInline, AppointmentStaffInline, PaymentInline, ReviewInline]
    readonly_fields = ("created_at",)
    fieldsets = (
        ("Thông tin lịch hẹn", {
            "fields": (
                "customer", "branch", "appointment_date", "appointment_time",
                "duration_minutes", "status", "total_price", "note"
            )
        }),
        ("Khác", {"fields": ("created_at",)}),
    )


# ========== STAFF SCHEDULE ==========
@admin.register(StaffSchedule)
class StaffScheduleAdmin(admin.ModelAdmin):
    list_display = ("staff", "branch", "work_date", "shift", "status", "approved_by")
    list_filter  = ("status", "branch", "shift")
    search_fields = ("staff__username", "branch__name")
    ordering = ("-work_date",)


# ========== REVIEW ==========
@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display  = ("id", "service", "customer", "rating", "status", "review_date")
    list_filter   = ("status", "rating", "review_date")
    search_fields = ("customer__username", "service__service_name", "comment")
    readonly_fields = ("review_date",)
    ordering = ("-review_date",)


# ========== PAYMENT ==========
@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display  = ("id", "appointment", "method", "status", "amount", "payment_date")
    list_filter   = ("method", "status", "payment_date")
    search_fields = ("appointment__id", "appointment__customer__username")
    readonly_fields = ("payment_date",)
    ordering = ("-payment_date",)


# ========== LOYALTY ==========
@admin.register(LoyaltyPoints)
class LoyaltyAdmin(admin.ModelAdmin):
    list_display = ("customer", "current_points", "points_earned", "points_used", "last_updated")
    search_fields = ("customer__username",)
    readonly_fields = ("last_updated",)
    ordering = ("-last_updated",)
