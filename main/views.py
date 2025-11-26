# main/views.py
from datetime import time as dtime
from datetime import date as _date
from .models import ContactMessage
from .models import LoyaltyPoints, LoyaltyTransaction
from django.views.decorators.http import require_POST
from decimal import Decimal
from django.views.decorators.cache import never_cache
from django.contrib.auth import update_session_auth_hash
from .forms import StaffPasswordChangeForm
from .models import LoyaltyPoints
from types import SimpleNamespace
from datetime import date, timedelta, datetime
from django.contrib import messages
from django.contrib.auth import login
from django.db import models
from django.db.models import Avg
from django.http import Http404
from .forms import StaffSelfScheduleForm, BranchForm

from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.contrib import messages
from django.db.models import Q, Count
from .models import User, Branch, StaffSchedule
from .forms import ScheduleForm
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth import password_validation

from .forms import (
    LoginForm, ContactForm, BookingForm, FeedbackForm,
    EmployeeForm, ServiceForm, ScheduleForm
)
from .models import (
    Service, Branch, Review, Appointment, AppointmentService, AppointmentStaff,
    Payment, User, StaffSchedule
)

# ===================== Helpers giữ đúng biến template =====================

PLACEHOLDER = "/static/images/placeholder.png"
BANK = {
    "account_name": "NGUYEN GIA HUY",
    "account_number": "1035669514",
    "bank_name": "Vietcombank – Joint Stock Commercial Bank for Foreign Trade of Vietnam",
    # ảnh QR tĩnh trong static của bạn
    "qr_url": "/static/images/qr.png",
}
# ====== AUTH HELPERS ======
from django.contrib.auth.decorators import login_required, user_passes_test

def _in_group(user, name):
    try:
        return user.groups.filter(name=name).exists()
    except Exception:
        return False

def is_admin(user):
    return user.is_authenticated and (
        user.is_superuser
        or user.is_staff
        or getattr(user, "role", "") == "ADMIN"
        or _in_group(user, "Admin")
    )

def is_receptionist(user):
    return user.is_authenticated and user.groups.filter(name='Receptionist').exists()

def is_technician(user):
    return user.is_authenticated and user.groups.filter(name='Technician').exists()

def is_staff_user(user):
    return user.is_authenticated and (
        user.is_staff
        or getattr(user, "role", "") == "STAFF"
        or is_receptionist(user)
        or is_technician(user)
        or _in_group(user, "Staff")
    )

def is_customer(user):
    # đã đăng nhập và KHÔNG thuộc Admin/Staff
    return user.is_authenticated and not is_admin(user) and not is_staff_user(user)

def _service_to_card(s: Service) -> dict:
    """Map Service -> dict cho thẻ dịch vụ của template (giữ nguyên + bổ sung keys)."""
    avg = Review.objects.filter(
        service_id=s.id,
        status=Review.Status.PUBLISHED
    ).aggregate(avg=Avg("rating"))["avg"]

    return {
        "id": s.id,  # <-- thêm để dùng cho link Edit, v.v.
        "name": s.service_name,
        "price": float(s.price or 0),
        "duration": int(getattr(s, "duration", 60) or 60),  # giữ UI 60'
        "thumbnail_url": (s.image.url if getattr(s, "image", None) else PLACEHOLDER),
        "slug": (s.slug or str(s.id)),  # fallback nếu slug trống
        "avg_rating": round(float(avg), 1) if avg is not None else 0,

        # bổ sung để admin list và client filter dùng
        "is_active": bool(getattr(s, "is_active", True)),
        "category": (getattr(s, "category", "MORE") or "MORE").lower(),  # 'manicure'/'pedicure'/'more'
    }

def _service_to_detail(s: Service):
    """Map Service -> dict cho trang chi tiết (giữ nguyên keys)."""
    avg = Review.objects.filter(service=s, status=Review.Status.PUBLISHED)\
                        .aggregate(avg=Avg("rating"))["avg"] or 0
    return {
        "id": s.id,  # <<< THÊM
        "slug": s.slug,
        "name": s.service_name,
        "price": float(s.price or 0),
        "duration": int(getattr(s, "duration", 60) or 60),
        "image_url": (s.image.url if getattr(s, "image", None) else PLACEHOLDER),
        "avg_rating": round(float(avg), 1) if avg else 0,
        "description": s.description or "",
    }

def _make_booking_code(appt_id: int) -> str:
    return f"BK{appt_id:06d}"

def _parse_booking_code(code: str) -> int:
    try:
        return int(code.replace("BK", "").replace("bk", ""))
    except Exception as e:
        raise Http404("Invalid booking code") from e

def _default_free_slots():
    # giữ format template: [{'value': '09:00', 'label': '09:00'}, ...]
    base = ["09:00", "10:00", "11:00", "13:00", "14:00", "15:00"]
    return [{"value": h, "label": h} for h in base]

def _infer_shift_from_time(t: dtime):
    if dtime(6, 0) <= t <= dtime(11, 59):
        return StaffSchedule.Shift.MORNING
    if dtime(12, 0) <= t <= dtime(17, 59):
        return StaffSchedule.Shift.AFTERNOON
    return StaffSchedule.Shift.EVENING

def _map_appointment_for_receptionist(appt: Appointment):
    """Maps a single Appointment object to the context dictionary for the receptionist dashboard."""

    # 1. Get Assigned Staff (assuming one staff per appointment)
    assigned_staff_link = getattr(appt, 'appointmentstaff_set', None)
    staff_name = "Chưa gán"

    if assigned_staff_link:
        staff_link = assigned_staff_link.all().first()
        if staff_link and staff_link.staff:
            staff_user = staff_link.staff
            staff_name = staff_user.full_name or staff_user.username

    # 2. Get Service Name (assuming the first service line)
    service_name = "N/A"
    service_lines = getattr(appt, 'appointmentservice_set', None)
    if service_lines:
        first_service_line = service_lines.all().first()
        if first_service_line and first_service_line.service:
            service_name = first_service_line.service.service_name

    # 3. Get Booking Code
    booking_code = _make_booking_code(appt.id) if appt.id else "NEW"

    return {
        "id": appt.id,
        "code": booking_code,
        "appointment_time": appt.appointment_time,
        "status": appt.status,
        "branch": {"name": appt.branch.name if appt.branch else "N/A"},
        "customer": {
            "full_name": appt.customer.full_name if appt.customer else "Khách vãng lai",
            "username": appt.customer.username if appt.customer else "",
        },
        "staff_assigned": {"full_name": staff_name},
        "service_name": service_name
    }
# ===================== Public / Customer =====================

def home(request):
    qs = Service.objects.filter(is_active=True).order_by("service_name")[:6]
    featured_services = [_service_to_card(s) for s in qs]
    # 12 item cho OUR COLLECTION
    coll_qs = Service.objects.filter(is_active=True).order_by("-id")[:12]
    collection = [{
        "name": s.service_name,
        "img": (s.image.url if getattr(s, "image", None) else PLACEHOLDER),
        "slug": (s.slug or str(s.id)),
        "price": float(s.price or 0),
    } for s in coll_qs]

    return render(
        request,
        "customer/home.html",
        {
            "featured_services": featured_services,
            "collection": collection,
        }
    )


def login_view(request):
    form = LoginForm(request=request, data=request.POST or None)
    if request.method == "POST" and form.is_valid():
        login(request, form.user)
        messages.success(request, "Đăng nhập thành công.")
        # điều hướng nhẹ: admin/staff/customer
        if request.user.is_superuser or request.user.groups.filter(name="Admin").exists():
            return redirect('main:admin_dashboard')
        if request.user.is_staff or request.user.groups.filter(name="Staff").exists():
            # ĐỔI staff_dashboard -> staff_account
            return redirect('main:staff_account')
        return redirect('main:customer_account')
    return render(request, 'login.html', {'form': form})

from django.contrib.auth import authenticate, login as auth_login
from .forms import SignupForm

# ...

def signup_view(request):
    if request.user.is_authenticated:
        return redirect('main:customer_account')

    form = SignupForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save(commit=False)
        # gán role CUSTOMER (nếu dùng custom User có field role)
        from .models import User
        user.role = getattr(User.Role, "CUSTOMER", "CUSTOMER")
        user.is_staff = False
        user.save()
        auth_login(request, user, backend='django.contrib.auth.backends.ModelBackend')
        messages.success(request, "Đăng ký thành công. Chào mừng bạn đến GlamUp Nails!")
        nxt = request.GET.get("next") or request.POST.get("next")
        return redirect(nxt or 'main:customer_account')

    return render(request, "signup.html", {"form": form, "next": request.GET.get("next", "")})

def services(request):
    """
    Trang danh sách dịch vụ:
      - Lấy tất cả service đang active
      - Tính rating trung bình và số feedback
      - Chuẩn hóa dữ liệu để JS filter/sort ở frontend
    """
    qs = (
        Service.objects
        .filter(is_active=True)
        .annotate(
            rating_avg=Avg("reviews__rating"),   # KHÔNG dùng tên avg_rating để khỏi đụng property
            review_cnt=Count("reviews"),
        )
    )

    services_data = []
    for s in qs:
        img_url = s.image.url if getattr(s, "image", None) else PLACEHOLDER
        services_data.append({
            "id": s.id,
            "name": s.service_name,
            "slug": s.slug,
            "price": s.price,
            "thumbnail_url": img_url,
            "category": (s.category or "").lower(),
            # value này template đang dùng: s.avg_rating
            "avg_rating": float(s.rating_avg or 0),
            "total_reviews": s.review_cnt,
        })

    return render(request, "customer/services.html", {
        "services": services_data,
    })


from django.db.models import Avg  # thêm import này

def service_detail(request, slug):
    s = get_object_or_404(Service, slug=slug, is_active=True)
    service = _service_to_detail(s)

    rv_qs = (
        Review.objects
        .filter(service=s, status=Review.Status.PUBLISHED)
        .select_related("customer")
    )
    total_reviews = rv_qs.count()
    avg_rating = rv_qs.aggregate(avg=Avg('rating'))['avg']  # None nếu chưa có

    reviews = [{
        "user": (rv.customer.full_name or rv.customer.username),
        "rating": float(rv.rating),
        "comment": rv.comment,
        "created_at": rv.review_date,
        "admin_reply": rv.admin_reply,  # 👈 thêm dòng này
    } for rv in rv_qs[:10]]

    addon_qs = Service.objects.filter(is_active=True).exclude(pk=s.pk)[:2]
    addons = [{"name": a.service_name, "price": float(a.price or 0)} for a in addon_qs]

    return render(
        request,
        'customer/service_details.html',
        {
            'service': service,
            'reviews': reviews,
            'addons': addons,
            'total_reviews': total_reviews,
            'avg_rating': avg_rating,
            "id": s.id,  # 👈 thêm
            "slug": (s.slug or str(s.id)),
            # để template dùng khi service chưa có avg
        }
    )

# views.py
from datetime import datetime, timedelta, time as dtime
from decimal import Decimal
from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.db.models import Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from .models import (
    User, Branch, Service, Appointment, AppointmentService, AppointmentStaff,
    StaffSchedule, Payment
)

PLACEHOLDER = "/static/images/placeholder.png"
def _vnd(n):
    try:
        n = int(Decimal(n))
    except Exception:
        n = 0
    # 240000 -> "240.000₫"
    return f"{n:,}".replace(",", ".") + "₫"

def _to_time(s: str):
    return datetime.strptime(s, "%H:%M").time()

def _shift_from_time(t: dtime):
    if dtime(6, 0) <= t <= dtime(11, 59): return StaffSchedule.Shift.MORNING
    if dtime(12, 0) <= t <= dtime(17, 59): return StaffSchedule.Shift.AFTERNOON
    return StaffSchedule.Shift.EVENING

def _overlap(a_start, a_dur_min, b_start, b_dur_min):
    """Hai khoảng thời gian (start + duration phút) có giao nhau?"""
    day = timezone.localdate()
    a0 = datetime.combine(day, a_start); a1 = a0 + timedelta(minutes=int(a_dur_min or 0))
    b0 = datetime.combine(day, b_start); b1 = b0 + timedelta(minutes=int(b_dur_min or 0))
    return not (a1 <= b0 or b1 <= a0)

@login_required
@user_passes_test(is_customer)
def book_now(request):
    """
    GET : nhận ?service=<slug|id> -> render form, prefill ảnh/tên/giá
    POST: kiểm tra ca làm & trùng lịch (theo duration), auto-assign staff;
          nếu thanh toán ONLINE => tạo Payment PAID và chuyển qua trang payment (trạng thái ĐÃ THANH TOÁN);
          nếu PAY AT STORE => Payment UNPAID và trang payment hiển thị CHƯA THANH TOÁN.
    """
    # --- lấy service từ slug hoặc id ---
    svc_param = (request.GET.get("service") or "").strip() or (request.POST.get("service_id") or "").strip()
    svc_obj = None
    if svc_param:
        svc_obj = Service.objects.filter(slug=svc_param, is_active=True).first()
        if not svc_obj and svc_param.isdigit():
            svc_obj = Service.objects.filter(pk=int(svc_param), is_active=True).first()

    if request.method != "POST":
        branches = list(Branch.objects.all().values("id", "name", "address"))
        default_date = timezone.localdate().isoformat()
        min_date = default_date

        selected_service = {}
        if svc_obj:
            selected_service = {
                "id": svc_obj.id,
                "name": svc_obj.service_name,
                "price": float(svc_obj.price or 0),
                "price_vnd": _vnd(svc_obj.price or 0),
                "image_url": (svc_obj.image.url if getattr(svc_obj, "image", None) else PLACEHOLDER),
            }

        ctx = {
            "branches": branches,
            "default_date": default_date,
            "min_date": min_date,
            "free_slots": [],
            "booked_times": [],
            "selected_service": selected_service,
            "quantity": 1,
        }
        return render(request, "customer/book_now.html", ctx)

    # ===== POST =====
    if not svc_obj:
        messages.error(request, "Dịch vụ không hợp lệ hoặc đã ngừng bán.")
        return redirect("main:services")

    branch_id = request.POST.get("branch_id")
    date_str  = request.POST.get("date")
    time_str  = request.POST.get("time_slot")
    note      = (request.POST.get("note") or "").strip()
    pay_method = (request.POST.get("payment_method") or "ONLINE").upper()

    if not (branch_id and date_str and time_str):
        messages.error(request, "Vui lòng chọn đủ chi nhánh, ngày và giờ.")
        back_qs = f"?service={svc_obj.slug or svc_obj.id}"
        return redirect(request.path + back_qs)

    branch     = get_object_or_404(Branch, pk=branch_id)
    appt_date  = datetime.strptime(date_str, "%Y-%m-%d").date()
    start_time = _to_time(time_str)

    total_minutes = int(getattr(svc_obj, "duration", 60) or 60)
    total_price   = Decimal(svc_obj.price or 0)

    # --- tìm staff rảnh trong ca APPROVED ---
    shift = _shift_from_time(start_time)
    candidates = (
        User.objects.filter(role=User.Role.STAFF)
        .filter(staffschedule__work_date=appt_date,
                staffschedule__shift=shift,
                staffschedule__status=StaffSchedule.Status.APPROVED,
                staffschedule__branch=branch)
        .distinct()
    )

    busy_qs = (
        Appointment.objects
        .filter(
            appointment_date=appt_date,
            status__in=[Appointment.Status.PENDING, Appointment.Status.CONFIRMED, Appointment.Status.IN_PROGRESS],
            staff_lines__staff__in=candidates,
        )
        .annotate(dur=Sum("service_lines__service__duration"))
        .values("appointment_time", "dur", "staff_lines__staff")
    )

    busy_map = {}
    for r in busy_qs:
        sid = r["staff_lines__staff"]
        busy_map.setdefault(sid, []).append((r["appointment_time"], int(r["dur"] or 60)))

    def staff_is_free(st):
        for (t_busy, d_busy) in busy_map.get(st.id, []):
            if _overlap(start_time, total_minutes, t_busy, d_busy):
                return False
        return True

    free_staff = [st for st in candidates if staff_is_free(st)]
    if not free_staff:
        messages.error(request, "Hiện không có nhân viên phù hợp khung giờ này. Vui lòng chọn giờ khác.")
        back_qs = f"?service={svc_obj.slug or svc_obj.id}"
        return redirect(request.path + back_qs)

    def day_load(st):
        return sum(d for _, d in busy_map.get(st.id, []))

    chosen = sorted(free_staff, key=day_load)[0]

    # --- tạo appointment ---
    appt = Appointment.objects.create(
        customer=request.user,
        branch=branch,
        appointment_date=appt_date,
        appointment_time=start_time,
        duration_minutes=total_minutes,
        status=Appointment.Status.PENDING,
        note=note,
        total_price=total_price
    )
    AppointmentService.objects.create(
        appointment=appt, service=svc_obj, quantity=1, unit_price=svc_obj.price
    )
    AppointmentStaff.objects.create(appointment=appt, staff=chosen)

    # --- Payment theo phương thức ---
    if pay_method == "ONLINE":
        # TẠM THỜI CHƯA set PAID; đợi bấm nút trên trang thanh toán
        Payment.objects.create(
            appointment=appt,
            amount=appt.total_price,
            method=Payment.Method.ONLINE,
            status=Payment.Status.UNPAID
        )
        # KHÔNG messages ở đây để tránh 2 banner
    else:
        Payment.objects.create(
            appointment=appt,
            amount=appt.total_price,
            method=Payment.Method.CASH,
            status=Payment.Status.UNPAID
        )
        # KHÔNG messages ở đây; sẽ thông báo sau khi bấm nút "Xác nhận đặt lịch"

    # “thông báo” cho Lễ tân & KTV (stub)
    try:
        print(
            f"[ALERT] Receptionist: Booking mới #{appt.id} {appt.appointment_date} {appt.appointment_time} – Khách: {request.user.username}")
        print(f"[ALERT] Technician: {chosen.username} được phân công cho booking #{appt.id}.")
    except Exception:
        pass

    code = _make_booking_code(appt.id)
    return redirect(reverse("main:payment", kwargs={"code": code}))


def _make_booking_code(appt_id: int) -> str:
    return f"BK{appt_id:06d}"

def _parse_booking_code(code: str) -> int | None:
    try:
        if code and code.startswith("BK"):
            return int(code[2:])
    except Exception:
        pass
    return None


def _code_to_pk(code: str) -> int:
    # BK000123 -> 123 (đúng với _make_booking_code bạn đang dùng)
    return int(code.replace("BK", "").lstrip("0") or "0")


@login_required
def payment(request, code: str):
    appt_pk = _code_to_pk(code)
    appt = get_object_or_404(Appointment, pk=appt_pk, customer=request.user)
    pay  = get_object_or_404(Payment, appointment=appt)
    lines = (AppointmentService.objects
             .filter(appointment=appt)
             .select_related("service"))

    subtotal = sum((l.unit_price or 0) * (l.quantity or 1) for l in lines)
    u = request.user

    ctx = {
        "appointment": appt,
        "payment": pay,
        "lines": lines,
        "amount_vnd": int(appt.total_price or 0),
        "subtotal_vnd": int(subtotal or 0),
        "code": code,
        "bank": BANK,  # dict cố định
        "prefill_email": (u.email or getattr(u, "phone_number", "") or ""),
        "prefill_username": (getattr(u, "full_name", "") or u.username),
    }
    # KHÔNG render messages ở trang payment
    return render(request, "customer/payment.html", ctx)


from django.views.decorators.http import require_POST

@require_POST
@login_required
def payment_complete(request, code: str):
    appt_pk = _code_to_pk(code)
    appt = get_object_or_404(Appointment, pk=appt_pk, customer=request.user)
    pay  = get_object_or_404(Payment, appointment=appt)

    if pay.method == Payment.Method.ONLINE:
        # Giả lập thanh toán thành công
        pay.status = Payment.Status.PAID
        pay.save(update_fields=["status"])
        appt.status = Appointment.Status.CONFIRMED
        appt.save(update_fields=["status"])
        messages.success(request, "Thanh toán thành công. Lịch hẹn đã được xác nhận.")
    else:
        # Trả tại quầy: CHƯA thanh toán, vẫn PENDING
        messages.success(request, "Đặt lịch thành công. Vui lòng thanh toán tại quầy khi đến cửa hàng.")

    return redirect("main:order_result", code=code)


@login_required
def order_result(request, code: str):
    appt_pk = _code_to_pk(code)
    appt = get_object_or_404(Appointment, pk=appt_pk, customer=request.user)
    pay  = get_object_or_404(Payment, appointment=appt)
    lines = AppointmentService.objects.filter(appointment=appt).select_related("service")
    subtotal = sum((l.unit_price or 0) * (l.quantity or 1) for l in lines)

    ctx = {
        "appt": appt,                     # <<< để booking_success.html dùng {{ appt.* }}
        "appointment": appt,              # (giữ cả 2 nếu lỡ template khác dùng)
        "payment": pay,
        "lines": lines,
        "subtotal_vnd": int(subtotal or 0),
        "amount_vnd": int(appt.total_price or 0),
        "code": code,
        "paid": (pay.status == Payment.Status.PAID),  # <<< banner trên cùng
    }
    return render(request, "customer/booking_sucess.html", ctx)



from django.http import JsonResponse

@login_required
def api_free_slots(request):
    d = request.GET.get("date")
    b = request.GET.get("branch_id")
    if not (d and b):
        return JsonResponse({"available": [], "booked": []})
    d = datetime.strptime(d, "%Y-%m-%d").date()
    ap_qs = Appointment.objects.filter(appointment_date=d, branch_id=b)
    booked = [a.appointment_time.strftime("%H:%M") for a in ap_qs]
    return JsonResponse({"available": [], "booked": booked})



PLACEHOLDER = "/static/images/placeholder.png"

def _service_thumb(svc):
    try:
        return svc.image.url
    except Exception:
        return PLACEHOLDER

@never_cache
@login_required
@user_passes_test(is_customer)
def my_appointments(request):
    """
    Trang lịch hẹn của KH (dữ liệu thật):
      - Hiển thị 20 lịch hẹn gần nhất (mới nhất lên đầu)
      - Cho phép hủy nếu còn ≥ 3h và trạng thái còn PENDING/CONFIRMED
      - Hiển thị nút Feedback nếu DONE mà chưa có đánh giá
    """
    qs = (
        Appointment.objects
        .select_related("branch")
        .prefetch_related("service_lines__service")
        .filter(customer=request.user)
        .order_by("-appointment_date", "-appointment_time")[:20]
    )

    now = timezone.now()
    bookings = []
    for appt in qs:
        # thời điểm bắt đầu (aware)
        start_dt = timezone.make_aware(
            datetime.combine(appt.appointment_date, appt.appointment_time),
            timezone.get_current_timezone()
        )

        # được hủy nếu > 3h và đang chờ/xác nhận
        can_cancel = (
            (start_dt - now).total_seconds() > 3 * 3600 and
            appt.status in [Appointment.Status.PENDING, Appointment.Status.CONFIRMED]
        )

        # dòng service đầu (đủ cho thẻ)
        first_line = appt.service_lines.select_related("service").first()
        svc = first_line.service if first_line else None
        price = (first_line.unit_price or 0) if first_line else 0

        # trạng thái thanh toán (nếu có)
        pay = Payment.objects.filter(appointment=appt).first()
        pay_status = pay.status if pay else None

        # đã đánh giá?
        has_review = Review.objects.filter(appointment=appt).exists()

        bookings.append({
            "id": appt.id,
            "code": f"BK{appt.id:06d}",
            "service": {
                "name": svc.service_name if svc else "Service",
                "image_url": _service_thumb(svc) if svc else PLACEHOLDER,
                "price": int(price),
            },
            "branch": {"name": appt.branch.name if appt.branch else "—"},
            "start_at": start_dt,
            "status": appt.status,                # 'PENDING' / 'CONFIRMED' / ...
            "status_label": appt.get_status_display(),
            "payment_status": pay_status,         # 'PAID' / 'UNPAID' / None
            "has_review": has_review,
            "can_cancel": can_cancel,
            "cancel_url": reverse("main:cancel_appointment", args=[appt.id]) if can_cancel else "",
        })

    return render(request, "customer/my_appointments.html", {
        "bookings": bookings,
        "now": now,  # dùng cho JS demo nếu cần
    })


@never_cache
@login_required
@user_passes_test(is_customer)
def cancel_appointment(request, pk: int):
    """
    Hủy lịch (KH của chính mình, trước ≥ 3h, trạng thái cho phép).
    """
    appt = get_object_or_404(Appointment, pk=pk, customer=request.user)

    start_dt = timezone.make_aware(
        datetime.combine(appt.appointment_date, appt.appointment_time),
        timezone.get_current_timezone()
    )
    now = timezone.now()

    if (start_dt - now).total_seconds() < 3 * 3600:
        messages.error(request, "Bạn chỉ có thể hủy lịch trước tối thiểu 3 giờ.")
        return redirect("main:my_appointments")

    if appt.status not in [Appointment.Status.PENDING, Appointment.Status.CONFIRMED]:
        messages.error(request, "Lịch ở trạng thái hiện tại không thể hủy.")
        return redirect("main:my_appointments")

    appt.status = Appointment.Status.CANCELED
    appt.save(update_fields=["status"])
    messages.success(request, "Đã hủy lịch hẹn.")
    return redirect("main:my_appointments")

# === FEEDBACK / REVIEWS ===




@never_cache
@login_required
@user_passes_test(is_customer)
def customer_feedback(request, booking_id: int):
    """
    KH viết (hoặc sửa) feedback cho 1 lịch hẹn:
    - Chỉ cho phép khi lịch đã DONE
    - Mỗi lịch hẹn / 1 customer chỉ có 1 review
    - Form tự prefill thông tin lịch + dịch vụ
    """
    appt = get_object_or_404(Appointment, pk=booking_id, customer=request.user)

    # Chỉ cho feedback khi đã hoàn tất
    if appt.status != Appointment.Status.DONE:
        messages.error(request, "Chỉ có thể đánh giá khi lịch hẹn đã hoàn tất.")
        return redirect("main:my_appointments")

    first_line = appt.service_lines.select_related("service").first()
    main_service = first_line.service if first_line else None
    price = (first_line.unit_price or 0) if first_line else 0

    # Review hiện có (để cho sửa)
    existing = Review.objects.filter(appointment=appt, customer=request.user).first()

    if request.method == "POST":
        form = FeedbackForm(request.POST, request.FILES)
        if form.is_valid():
            cd = form.cleaned_data

            review = existing or Review(
                appointment=appt,
                customer=request.user,
                service=main_service,
            )
            review.product_rating = cd["product_rating"]
            review.service_rating = cd["service_rating"]
            review.comment = cd.get("comment", "").strip()

            # Tự tính rating tổng (0–5, 1 số thập phân)
            nums = [review.product_rating, review.service_rating]
            avg = sum(nums) / len(nums) if nums else 0
            review.rating = Decimal(str(round(max(0, min(5, avg)), 1)))

            # Media (ghi đè nếu có upload)
            if cd.get("photo"):
                review.image = cd["photo"]
            if cd.get("video"):
                review.video = cd["video"]

            # Trạng thái xuất bản luôn (nếu bạn muốn kiểm duyệt thì set PENDING)
            review.status = Review.Status.PUBLISHED
            review.save()

            messages.success(request, "Cảm ơn bạn đã gửi đánh giá 💚")
            return redirect("main:service_detail", slug=main_service.slug) if main_service else redirect("main:my_appointments")
    else:
        init = {}
        if existing:
            init = {
                "product_rating": existing.product_rating,
                "service_rating": existing.service_rating,
                "comment": existing.comment,
            }
        form = FeedbackForm(initial=init)

    # Data cho ORDER SUMMARY trong template
    order = {
        "image_url": getattr(main_service.image, "url", "") if main_service else "",
        "service_name": main_service.service_name if main_service else "Service",
        "price": int(price),
        "code": f"BK{appt.id:06d}",
        "branch_name": appt.branch.name if appt.branch else "—",
        "start_date": appt.appointment_date,
        "start_time": appt.appointment_time,
        "total": int(appt.total_price or 0),
    }

    return render(request, "customer/customer_feedback.html", {
        "form": form,
        "appointment": appt,
        "service": main_service,
        "order": order,
        "existing_photo_url": (existing.image.url if existing and existing.image else ""),
        "existing_video_url": (existing.video.url if existing and existing.video else ""),
        # <- template đang dùng key này
    })


# ===== Admin: danh sách feedback =====
@login_required
@user_passes_test(is_admin)
def admin_feedback(request):
    qs = (
        Review.objects
        .select_related("customer", "service", "appointment")
        .order_by("-review_date", "-id")
    )
    return render(request, "admin_site/feedback.html", {
        "feedback_list": qs,
    })


# ===== Admin: phản hồi feedback =====
@login_required
@user_passes_test(is_admin)
def admin_feedback_reply(request, pk: int):
    review = get_object_or_404(Review, pk=pk)
    if request.method == "POST":
        review.admin_reply = (request.POST.get("admin_reply") or "").strip()
        review.admin_replied_at = timezone.now()
        # Nếu bạn muốn kiểm duyệt sau khi reply thì:
        # review.status = Review.Status.PUBLISHED
        review.save(update_fields=["admin_reply", "admin_replied_at"])
        messages.success(request, "Đã lưu phản hồi.")
        return redirect("main:admin_feedback")

    return render(request, "admin_site/feedback_reply.html", {"review": review})


# ===== Service details: hiển thị review công khai =====
@login_required  # nếu trang public thì bỏ decorator này
def service_detail(request, slug: str):
    svc = get_object_or_404(Service, slug=slug, is_active=True)

    # Reviews xuất bản
    reviews_qs = (
        Review.objects
        .select_related("customer")
        .filter(service=svc, status=Review.Status.PUBLISHED)
        .order_by("-review_date", "-id")
    )

    # Tính trung bình
    ratings = list(reviews_qs.values_list("rating", flat=True))
    avg_rating = round(sum(ratings) / len(ratings), 1) if ratings else None
    total_reviews = len(ratings)

    return render(request, "customer/service_details.html", {
        "service": svc,
        "reviews": reviews_qs,
        "avg_rating": avg_rating,
        "total_reviews": total_reviews,
    })



@never_cache
@login_required
@user_passes_test(is_customer)

def customer_account(request):
    u = request.user

    # --------- POST: xử lý 2 modal ----------
    if request.method == "POST":
        action = request.POST.get("action")

        # 1) Lưu Account (full_name, email)
        if action == "save_account":
            full_name = (request.POST.get("full_name") or "").strip()
            email     = (request.POST.get("email") or "").strip()

            # Check trùng email (loại trừ chính mình)
            if email and User.objects.exclude(pk=u.pk).filter(email__iexact=email).exists():
                messages.error(request, "Email đã tồn tại, vui lòng dùng email khác.")
            else:
                u.full_name = full_name
                u.email = email
                u.save(update_fields=["full_name", "email"])
                messages.success(request, "Cập nhật tài khoản thành công.")
                # ----- Đổi mật khẩu (nếu user nhập) -----
            old_pwd = (request.POST.get("old_password") or "").strip()
            new1 = (request.POST.get("new_password1") or "").strip()
            new2 = (request.POST.get("new_password2") or "").strip()

            if old_pwd or new1 or new2:
                # bắt buộc đủ 3 ô
                if not (old_pwd and new1 and new2):
                    messages.error(request, "Vui lòng điền đủ 3 trường đổi mật khẩu.")
                elif not u.check_password(old_pwd):
                    messages.error(request, "Mật khẩu hiện tại không đúng.")
                elif new1 != new2:
                    messages.error(request, "Mật khẩu mới nhập lại không khớp.")
                else:
                    try:
                        password_validation.validate_password(new1, user=u)
                    except Exception as e:
                        # gộp thông điệp validator
                        messages.error(request, " ".join([str(x) for x in e]))
                    else:
                        u.set_password(new1)
                        u.save(update_fields=["password"])
                        update_session_auth_hash(request, u)  # giữ đăng nhập
                        messages.success(request, "Đổi mật khẩu thành công.")

            return redirect("main:customer_account")
        # 2) Lưu Personal (phone, address, dob)
        elif action == "save_personal":
            phone = (request.POST.get("phone_number") or "").strip()
            addr  = (request.POST.get("address") or "").strip()
            dob   = (request.POST.get("date_of_birth") or "").strip()

            update_fields = []
            u.phone_number = phone; update_fields.append("phone_number")
            u.address = addr;       update_fields.append("address")
            if dob:
                try:
                    u.date_of_birth = datetime.strptime(dob, "%Y-%m-%d").date()
                except ValueError:
                    messages.error(request, "Ngày sinh không hợp lệ. Định dạng YYYY-MM-DD.")
                    # đi tiếp để render
                else:
                    update_fields.append("date_of_birth")
            else:
                u.date_of_birth = None
                update_fields.append("date_of_birth")

            u.save(update_fields=update_fields)
            messages.success(request, "Cập nhật thông tin cá nhân thành công.")
        if action == "upload_avatar":
            file = request.FILES.get("avatar")
            if not file:
                messages.error(request, "Vui lòng chọn ảnh.")
                return redirect('main:customer_account')

            # validate cơ bản
            allowed = {"image/jpeg", "image/png", "image/webp"}
            if getattr(file, "content_type", "").lower() not in allowed:
                messages.error(request, "Chỉ chấp nhận JPG, PNG hoặc WEBP.")
                return redirect('main:customer_account')

            if file.size > 2 * 1024 * 1024:  # 2MB
                messages.error(request, "Ảnh quá lớn (tối đa 2MB).")
                return redirect('main:customer_account')

            # lưu
            u.avatar = file
            u.save(update_fields=["avatar"])
            messages.success(request, "Đã cập nhật ảnh đại diện.")
            return redirect('main:customer_account')
        # Tránh resubmit form khi refresh
        return redirect("main:customer_account")


    # --------- Loyalty & Upcoming ----------
    lp, _ = LoyaltyPoints.objects.get_or_create(customer=u)
    tier, discount = _membership_from_points(lp.current_points)

    today = timezone.localdate()
    now_t = timezone.localtime().time()
    appt = (
        Appointment.objects
        .filter(customer=u,
                status__in=[Appointment.Status.PENDING,
                            Appointment.Status.CONFIRMED,
                            Appointment.Status.IN_PROGRESS])
        .filter(models.Q(appointment_date__gt=today) |
                models.Q(appointment_date=today, appointment_time__gte=now_t))
        .select_related("branch")
        .prefetch_related("service_lines__service")
        .order_by("appointment_date", "appointment_time")
        .first()
    )

    next_booking = None
    if appt:
        first_line = appt.service_lines.first()
        start_dt = timezone.make_aware(
            datetime.combine(appt.appointment_date, appt.appointment_time),
            timezone.get_current_timezone()
        )
        next_booking = {
            "service_name": first_line.service.service_name if first_line else "Service",
            "start_at": start_dt,
            "branch_name": appt.branch.name,
        }

    ctx = {
        "loyalty": {"points": lp.current_points, "tier": tier, "discount": discount},
        "next_booking": next_booking,
    }
    return render(request, "customer/customer_account.html", ctx)

#Xh thêm vào
def contact_view(request):
    if request.method == "POST":
        form = ContactForm(request.POST)
        if form.is_valid():
            form.save()  # lưu vào ContactMessage.status = 'new' → lễ tân xem & xử lý
            messages.success(request, "Đã gửi thắc mắc! Lễ tân sẽ liên hệ bạn sớm nhất.")
            return redirect("main:contact")
    else:
        form = ContactForm()

    # Dữ liệu tĩnh hiển thị (giữ đúng BRAND đã thống nhất)
    brand = {
        "phone_display": "0896 210 570",
        "phone_tel": "0896210570",
        "email": "support@glamup.vn",  # đổi sang email thật của bạn
        "hours": "Mon – Sun: 9:00 AM – 9:00 PM",
        "addresses": [
            "02 Nguyễn Văn Thoại, Đà Nẵng",
            "97 Ngô Văn Sở, Đà Nẵng",
            "25 Lê Văn Hiến, Đà Nẵng",
        ],
        "partners": [
            # (logo_path, tên) — có thể thay bằng ảnh thật trong static
            ("images/brands/opnail.svg", "OP Nail"),
            ("images/brands/gelx.svg", "GelX"),
            ("images/brands/daisy.svg", "Daisy"),
        ],
    }
    return render(request, "customer/contact.html", {"form": form, "brand": brand})


# ===================== Staff (Phase 2 sẽ nối DB) =====================
def _map_appt_for_rx(appt):
    """Map 1 Appointment → dict cho UI lễ tân."""
    # tên dịch vụ: lấy dòng đầu tiên
    first_line = appt.service_lines.select_related("service").first()
    service_name = first_line.service.service_name if first_line else "Service"

    staff_link = appt.staff_lines.select_related("staff").first()
    staff_name = staff_link.staff.full_name if (staff_link and staff_link.staff) else "Chưa gán"

    return {
        "id": appt.id,
        "code": _make_booking_code(appt.id),
        "appointment_time": appt.appointment_time,
        "branch": appt.branch,
        "customer": appt.customer,
        "service_name": service_name,
        "staff_assigned": {"full_name": staff_name},
        "status": appt.status,
    }


@never_cache
@login_required
@user_passes_test(is_receptionist)
def receptionist_dashboard(request):
    """
    Lễ tân: hiển thị lịch hẹn từ hôm nay đến 7 ngày tới, trừ DONE/CANCELED.
    """
    today = timezone.localdate()
    end = today + timedelta(days=7)

    qs = (Appointment.objects
          .filter(appointment_date__range=(today, end))
          .exclude(status__in=[Appointment.Status.DONE, Appointment.Status.CANCELED])
          .select_related("branch", "customer")
          # ✅ đúng related_name
          .prefetch_related("service_lines__service", "staff_lines__staff")
          .order_by("appointment_date", "appointment_time"))

    appointments_ctx = [_map_appt_for_rx(ap) for ap in qs]

    return render(request, "staff/receptionist_dashboard.html", {
        "appointments": appointments_ctx,
        "today": today,
        "is_receptionist": True,
        "is_technician": False,
    })


@never_cache
@login_required
@user_passes_test(is_receptionist)
def receptionist_appointments(request):
    """
    Trang danh sách tất cả lịch hẹn sắp tới cho lễ tân (>= hôm nay).
    """
    today = timezone.localdate()
    qs = (Appointment.objects
          .filter(appointment_date__gte=today)
          .exclude(status__in=[Appointment.Status.DONE, Appointment.Status.CANCELED])
          .select_related("branch", "customer")
          # ✅ đúng related_name
          .prefetch_related("service_lines__service", "staff_lines__staff")
          .order_by("appointment_date", "appointment_time"))

    items = [_map_appt_for_rx(ap) for ap in qs]
    return render(request, "staff/receptionist_appointments.html", {
        "items": items,
        "today": today,
        "is_receptionist": True,
        "is_technician": False,
    })


@never_cache
@login_required
@user_passes_test(is_receptionist)
def check_in(request, pk: int):
    """
    Lễ tân xác nhận khách đã đến → ARRIVED.
    Hiển thị message rồi quay về dashboard.
    """
    appt = get_object_or_404(Appointment, pk=pk)
    appt.status = Appointment.Status.ARRIVED
    appt.save(update_fields=["status"])
    messages.success(request, f"Đã check-in cho lịch hẹn { _make_booking_code(appt.id) }.")
    # quay lại trang trước (nếu có)
    return redirect(request.META.get("HTTP_REFERER") or "main:receptionist_dashboard")


@never_cache
@login_required
@user_passes_test(is_receptionist)
def check_out(request, pk: int):
    """
    Lễ tân kết thúc lịch hẹn → DONE.
    (Nếu muốn thu tiền tại quầy khi UNPAID, bạn có thể cập nhật Payment ở đây.)
    """
    appt = get_object_or_404(Appointment, pk=pk)
    appt.status = Appointment.Status.DONE
    appt.save(update_fields=["status"])
    messages.success(request, f"Đã check-out và hoàn tất lịch hẹn { _make_booking_code(appt.id) }.")
    return redirect(request.META.get("HTTP_REFERER") or "main:receptionist_dashboard")
@never_cache
@login_required
@user_passes_test(is_staff_user)
def staff_dashboard(request):
    return render(request, 'staff/staff_account.html', {"is_receptionist": is_receptionist(request.user),
        "is_technician": is_technician(request.user)})
@never_cache
@login_required
@user_passes_test(is_staff_user)
def staff_account(request):
    if not request.user.is_authenticated:
        return redirect('main:login')

    u = request.user

    if request.method == "POST":
        action = request.POST.get("action")

        # 1) Lưu Account information
        if action == "save_account":
            u.full_name = (request.POST.get("full_name") or "").strip()
            email = (request.POST.get("email") or "").strip()

            # check trùng email (không tính chính mình)
            if email and User.objects.exclude(pk=u.pk).filter(email__iexact=email).exists():
                messages.error(request, "Email đã tồn tại.")
            else:
                u.email = email
                u.save(update_fields=["full_name", "email"])
                messages.success(request, "Cập nhật tài khoản thành công.")
            return redirect('main:staff_account')

        # 2) Lưu Personal information
        if action == "save_personal":
            update_fields = []

            phone = (request.POST.get("phone_number") or "").strip()
            addr  = (request.POST.get("address") or "").strip()
            if hasattr(u, "phone_number"):
                u.phone_number = phone
                update_fields.append("phone_number")
            if hasattr(u, "address"):
                u.address = addr
                update_fields.append("address")

            # ngày sinh (nếu model có field này)
            if hasattr(u, "date_of_birth"):
                dob_raw = (request.POST.get("date_of_birth") or "").strip()
                try:
                    u.date_of_birth = _date.fromisoformat(dob_raw) if dob_raw else None
                    update_fields.append("date_of_birth")
                except ValueError:
                    messages.error(request, "Ngày sinh không hợp lệ (định dạng YYYY-MM-DD).")
                    return redirect('main:staff_account')

            if update_fields:
                u.save(update_fields=update_fields)
                messages.success(request, "Cập nhật thông tin cá nhân thành công.")
            else:
                messages.info(request, "Không có trường nào để cập nhật.")
            return redirect('main:staff_account')
        #Đổi mật khẩu
        if action == "change_password":
            form_pwd = StaffPasswordChangeForm(request.POST, user=u)
            if form_pwd.is_valid():
                new_pwd = form_pwd.cleaned_data["new_password1"]
                u.set_password(new_pwd)
                u.save(update_fields=["password"])
                # giữ người dùng đăng nhập sau khi đổi mật khẩu
                update_session_auth_hash(request, u)
                messages.success(request, "Đã đổi mật khẩu thành công.")
            else:
                # gom lỗi để hiển thị bằng messages
                for errs in form_pwd.errors.values():
                    messages.error(request, " ".join(errs))
            return redirect('main:staff_account')
        # 3) Upload avatar
        if action == "upload_avatar":
            file = request.FILES.get("avatar")
            if not file:
                messages.error(request, "Vui lòng chọn ảnh.")
                return redirect('main:staff_account')

            # validate cơ bản
            allowed = {"image/jpeg", "image/png", "image/webp"}
            if getattr(file, "content_type", "").lower() not in allowed:
                messages.error(request, "Chỉ chấp nhận JPG, PNG hoặc WEBP.")
                return redirect('main:staff_account')

            if file.size > 2 * 1024 * 1024:  # 2MB
                messages.error(request, "Ảnh quá lớn (tối đa 2MB).")
                return redirect('main:staff_account')

            if hasattr(u, "avatar"):
                u.avatar = file
                u.save(update_fields=["avatar"])
                messages.success(request, "Đã cập nhật ảnh đại diện.")
            else:
                messages.error(request, "Tài khoản chưa hỗ trợ trường avatar.")
            return redirect('main:staff_account')

    # Build dữ liệu hiển thị (an toàn)
    avatar_url = ""
    if getattr(u, "avatar", None) and getattr(u.avatar, "url", None):
        avatar_url = u.avatar.url
    else:
        avatar_url = "/static/img/avatar-placeholder.png"

    staff_ctx = {
        "display_name": getattr(u, "full_name", "") or u.username,
        "full_name": getattr(u, "full_name", "") or "",
        "email": getattr(u, "email", "") or "",
        "phone": getattr(u, "phone_number", "") or "",
        "address": getattr(u, "address", "") or "",
        "dob": getattr(u, "date_of_birth", None),  # trong template: {{ staff.dob|date:"d/m/Y" }}
        "avatar_url": avatar_url,
    }

    return render(request, "staff/staff_account.html", {
        "staff": staff_ctx,
        "upcoming": [],
        "is_receptionist": is_receptionist(request.user),
        "is_technician": is_technician(request.user)
    })
# ====== RECEPTIONIST: Inbox (xem liên hệ khách gửi) ======

@never_cache
@login_required
@user_passes_test(is_receptionist)  # chỉ lễ tân vào
def staff_inbox(request):
    q = (request.GET.get("q") or "").strip()
    status = (request.GET.get("status") or "").strip()

    qs = ContactMessage.objects.all().order_by("-created_at")
    if q:
        qs = qs.filter(
            Q(full_name__icontains=q) |
            Q(phone__icontains=q) |
            Q(email__icontains=q) |
            Q(content__icontains=q)
        )
    if status:
        qs = qs.filter(status=status)

    # KPI nhỏ trên đầu trang
    counts = {
        "new": qs.filter(status="new").count(),
        "in_progress": qs.filter(status="in_progress").count(),
        "resolved": qs.filter(status="resolved").count(),
        "total": qs.count(),
    }

    paginator = Paginator(qs, 12)
    page_obj = paginator.get_page(request.GET.get("page"))

    return render(request, "staff/inbox.html", {
        "items": page_obj.object_list,
        "page_obj": page_obj,
        "paginator": paginator,
        "is_paginated": page_obj.has_other_pages(),
        "q": q,
        "status": status,
        "counts": counts,
        "is_receptionist": is_receptionist(request.user),
        "is_technician": is_technician(request.user),
    })


@never_cache
@login_required
@user_passes_test(is_receptionist)
def staff_inbox_detail(request, pk: int):
    from django.forms import modelform_factory
    item = get_object_or_404(ContactMessage, pk=pk)

    # ModelForm gọn cho status/assigned_to
    HandleForm = modelform_factory(ContactMessage, fields=["status", "assigned_to"])

    if request.method == "POST":
        form = HandleForm(request.POST, instance=item)
        # NEW: chỉ nhận assignee là lễ tân
        receptionist_qs = User.objects.filter(groups__name="Receptionist").order_by("full_name", "username")
        # nếu gửi assigned_to nhưng không thuộc lễ tân, bỏ qua
        assignee_id = request.POST.get("assigned_to")
        if assignee_id and not receptionist_qs.filter(id=assignee_id).exists():
            form.data = form.data.copy()
            form.data["assigned_to"] = ""  # reset

        if form.is_valid():
            obj = form.save(commit=False)
            if not obj.assigned_to:
                obj.assigned_to = request.user  # auto gán người mở xử lý
            obj.save(update_fields=["status", "assigned_to"])
            messages.success(request, "Updated message status successfully.")
            return redirect("main:staff_inbox_detail", pk=pk)
    else:
        form = HandleForm(instance=item)

    # NEW: set queryset cho dropdown = chỉ lễ tân
    receptionist_qs = User.objects.filter(groups__name="Receptionist").order_by("full_name", "username")
    form.fields["assigned_to"].queryset = receptionist_qs
    form.fields["assigned_to"].label_from_instance = lambda u: u.get_full_name() or u.username

    return render(request, "staff/inbox_detail.html", {
        "item": item,
        "form": form,
        "is_receptionist": is_receptionist(request.user),
        "is_technician": is_technician(request.user),
    })


@never_cache
@login_required
@user_passes_test(is_receptionist)
def staff_inbox_update(request, pk: int):
    item = get_object_or_404(ContactMessage, pk=pk)
    if request.method == "POST":
        # Hỗ trợ quick-action (hidden status) hoặc form đầy đủ
        new_status = request.POST.get("status")
        assigned = request.POST.get("assigned_to")
        changed = False
        if new_status in {"new", "in_progress", "resolved"}:
            item.status = new_status
            changed = True
        if assigned:
            try:
                item.assigned_to_id = int(assigned)
                changed = True
            except ValueError:
                pass
        if not item.assigned_to:
            item.assigned_to = request.user  # auto gán người xử lý lần đầu
            changed = True
        if changed:
            item.save(update_fields=["status", "assigned_to"])
            messages.success(request, "Updated message status successfully.")
        else:
            messages.info(request, "No changes.")
    return redirect("main:staff_inbox_detail", pk=pk)

def _monday(d: date) -> date:
    return d - timedelta(days=d.weekday())
@never_cache
@login_required
@user_passes_test(is_staff_user)
def staff_schedule(request):
    # ---- tuần đang xem ----
    try:
        qs_start = request.GET.get("start")
        week_start = date.fromisoformat(qs_start) if qs_start else _monday(timezone.localdate())
    except Exception:
        week_start = _monday(timezone.localdate())
    week_end   = week_start + timedelta(days=6)
    prev_start = (week_start - timedelta(days=7)).isoformat()
    next_start = (week_start + timedelta(days=7)).isoformat()
    days = [week_start + timedelta(days=i) for i in range(7)]

    # ---- POST: nhân viên gửi yêu cầu ca ----
    if request.method == "POST":
        form = StaffSelfScheduleForm(request.POST, user=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, "Đã gửi yêu cầu ca làm (chờ admin duyệt).")
            return redirect(f"{request.path}?start={week_start.isoformat()}")
        else:
            messages.error(request, "; ".join([" ".join(v) for v in form.errors.values()]))

    # ---- lấy lịch của nhân viên tuần này ----
    sched = (
        StaffSchedule.objects
        .select_related("branch")
        .filter(staff=request.user, work_date__range=(week_start, week_end))
    )
    # map: (date, shift) -> object
    sched_map = {(s.work_date, s.shift): s for s in sched}

    # chuẩn bị dữ liệu cho template
    shifts = [
        ("MORNING",   "Morning"),
        ("AFTERNOON", "Afternoon"),
        ("EVENING",   "Evening"),
    ]
    rows = []
    for key, label in shifts:
        slots = []
        for d in days:
            slots.append({"day": d, "obj": sched_map.get((d, key))})
        rows.append({"key": key, "label": label, "slots": slots})

    ctx = {
        "week_start": week_start,
        "week_end": week_end,
        "prev_start": prev_start,
        "next_start": next_start,
        "days": days,
        "rows": rows,                                 # <— dùng ở template
        "self_form": StaffSelfScheduleForm(user=request.user),
        "STATUS": StaffSchedule.Status,
        "is_receptionist": is_receptionist(request.user),
        "is_technician": is_technician(request.user)
    }
    return render(request, "staff/staff_schedule.html", ctx)

@never_cache
@login_required
@user_passes_test(is_staff_user)
def staff_appointments(request):
    """
    KTV xem các lịch hẹn mình được phân công.
    Cho phép đánh dấu Completed / Uncompleted.
    """
    staff = request.user

    # Các lịch của chính nhân viên đang đăng nhập
    appt_qs = (
        Appointment.objects
        .filter(staff_lines__staff=staff)                     # <-- dùng staff_lines
        .select_related("branch", "customer")
        .prefetch_related("service_lines__service", "payments")
        .order_by("appointment_date", "appointment_time")
    )

    appts = []
    for ap in appt_qs:
        # Lấy 1 dòng dịch vụ để hiển thị tên/ảnh/giá
        svc_line = ap.service_lines.select_related("service").first()   # <-- service_lines
        service = getattr(svc_line, "service", None)

        # Lấy 1 bản ghi thanh toán (nếu có)
        pay = Payment.objects.filter(appointment=ap).first()

        appts.append({
            "id": ap.id,
            "service_image_url": (getattr(service, "image", None).url
                                  if service and getattr(service, "image", None) else None),
            "service_name": getattr(service, "service_name", "Service"),
            "price": f"{int(ap.total_price or 0):,} VND",
            "customer_name": (ap.customer.full_name or ap.customer.username) if ap.customer else "—",
            "phone": getattr(ap.customer, "phone_number", "—") if ap.customer else "—",
            "branch_name": ap.branch.name if ap.branch else "—",
            "time_display": f"{ap.appointment_date.strftime('%d/%m')} {ap.appointment_time.strftime('%H:%M')}",
            "payment_status": (pay.status if pay else "UNPAID"),   # 'PAID' / 'UNPAID'
            "status": ap.status,
        })

    # Hành động đánh dấu trạng thái
    if request.method == "POST":
        action = request.POST.get("action")
        appt_id = request.POST.get("id")

        # ràng buộc: chỉ đụng được lịch mà chính mình được phân công
        appt = get_object_or_404(Appointment, pk=appt_id, staff_lines__staff=staff)  # <-- staff_lines

        if action == "mark_completed":
            appt.status = Appointment.Status.DONE
            appt.save(update_fields=["status"])
            _award_loyalty_for_appointment(appt)
            messages.success(request, "Đã đánh dấu hoàn thành.")
        elif action == "mark_uncompleted":
            appt.status = Appointment.Status.ONGOING
            appt.save(update_fields=["status"])
            messages.info(request, "Đã chuyển lại trạng thái đang làm.")
        return redirect("main:staff_appointments")

    return render(request, "staff/appointments.html", {
        "appointments": appts,
        "is_receptionist": is_receptionist(request.user),
        "is_technician": is_technician(request.user),
    })

# ===================== Admin site =====================
@never_cache
@login_required
@user_passes_test(is_admin)

def admin_dashboard(request):
    """
    Admin Dashboard:
      - Filter theo chi nhánh + khoảng thời gian (week / month / custom)
      - KPI: tổng số booking, tổng doanh thu
      - Chart:
          + Bookings by day (timeseries)
          + Revenue by service (chỉ tính appointment đã PAID)
          + Popular services (share theo số lượng)
      - Latest feedback: 5 review mới nhất (PUBLISHED)
      - Export CSV: danh sách appointment trong khoảng filter
    """
    # ===== 1. Đọc filter từ query string =====
    period = request.GET.get("period", "week")       # week | month | custom
    branch_id = (request.GET.get("branch") or "").strip()
    from_str = (request.GET.get("from") or "").strip()
    to_str   = (request.GET.get("to") or "").strip()

    today = timezone.localdate()

    # ===== 2. Tính khoảng ngày (date_start, date_end) =====
    if period == "month":
        # Tháng hiện tại: từ ngày 1 đến hôm nay
        date_start = today.replace(day=1)
        date_end = today
    elif period == "custom" and from_str and to_str:
        try:
            date_start = _date.fromisoformat(from_str)
            date_end   = _date.fromisoformat(to_str)
        except ValueError:
            # fallback về tuần hiện tại nếu nhập sai
            date_start = today - timedelta(days=today.weekday())
            date_end = today
            period = "week"
    else:
        # Mặc định: tuần hiện tại (từ thứ 2 đến hôm nay)
        date_start = today - timedelta(days=today.weekday())
        date_end = today
        period = "week"

    # để fill lại input type="date"
    date_from = date_start.isoformat()
    date_to   = date_end.isoformat()

    # ===== 3. Base queryset: Appointment trong khoảng thời gian =====
    appt_qs = Appointment.objects.filter(
        appointment_date__range=(date_start, date_end)
    )

    if branch_id:
        appt_qs = appt_qs.filter(branch_id=branch_id)

    # loại lịch bị hủy
    appt_qs = appt_qs.exclude(status=Appointment.Status.CANCELED)

    # ===== 4. KPI: total_bookings, total_revenue =====
    total_bookings = appt_qs.count()

    paid_payments = Payment.objects.filter(
        appointment__in=appt_qs,
        status=Payment.Status.PAID
    )

    total_revenue = paid_payments.aggregate(
        total=Sum("amount")
    )["total"] or Decimal("0")

    # ===== 4b. Export CSV nếu có ?export=1 =====
    if request.GET.get("export") == "1":
        filename = f"appointments_{date_start.strftime('%Y%m%d')}_{date_end.strftime('%Y%m%d')}.csv"
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'

        writer = csv.writer(response)
        writer.writerow([
            "Appointment ID",
            "Date",
            "Time",
            "Branch",
            "Customer",
            "Status",
            "Total price",
            "Paid amount",
        ])

        # chuẩn bị map tiền đã trả cho từng appointment
        paid_map = {
            row["appointment_id"]: row["total"]
            for row in paid_payments
            .values("appointment_id")
            .annotate(total=Sum("amount"))
        }

        for appt in appt_qs.select_related("branch", "customer"):
            writer.writerow([
                appt.id,
                appt.appointment_date.isoformat(),
                appt.appointment_time.strftime("%H:%M") if appt.appointment_time else "",
                appt.branch.name if appt.branch else "",
                appt.customer.get_full_name() or appt.customer.username,
                appt.get_status_display(),
                float(appt.total_price or 0),
                float(paid_map.get(appt.id) or 0),
            ])

        return response

    # ===== 5. Timeseries: bookings by day =====
    ts_qs = (
        appt_qs.values("appointment_date")
        .annotate(count=Count("id"))
        .order_by("appointment_date")
    )
    ts_labels = [row["appointment_date"].strftime("%d/%m") for row in ts_qs]
    ts_values = [row["count"] for row in ts_qs]

    # ===== 6. Revenue by service & Popular services =====
    paid_appt_ids = paid_payments.values_list("appointment_id", flat=True).distinct()

    as_qs = (
        AppointmentService.objects
        .filter(appointment_id__in=paid_appt_ids)
        .select_related("service")
    )

    # 6.1 Revenue by service (dùng line_total của model)
    rev_map = {}  # {service_name: Decimal}
    for line in as_qs:
        name = line.service.service_name if line.service else "Service"
        rev_map.setdefault(name, Decimal("0"))
        rev_map[name] += line.line_total

    rev_items = sorted(rev_map.items(), key=lambda x: x[1], reverse=True)
    revenue_labels = [name for name, _ in rev_items[:6]]
    revenue_values = [float(val) for _, val in rev_items[:6]]

    # 6.2 Popular services (đếm số line)
    pop_qs = (
        as_qs.values("service__service_name")
        .annotate(cnt=Count("id"))
        .order_by("-cnt")
    )
    popular_labels = [
        row["service__service_name"] or "Service"
        for row in pop_qs[:6]
    ]
    popular_counts = [row["cnt"] for row in pop_qs[:6]]

    # ===== 7. Latest feedback (5 review mới nhất, PUBLISHED) =====
    rv_qs = (
        Review.objects
        .filter(status=Review.Status.PUBLISHED)
        .select_related("customer", "service")
        .order_by("-review_date")[:5]
    )

    latest_feedback = [
        SimpleNamespace(
            date=rv.review_date,
            user=rv.customer,
            service=rv.service,
            comment=rv.comment,
            rating=float(rv.rating or 0),
            service_slug=(rv.service.slug if rv.service and rv.service.slug else ""),
        )
        for rv in rv_qs
    ]

    # ===== 8. Branch list + branch_label =====
    branches = Branch.objects.all().order_by("name")
    branch_label = "All branches"

    if branch_id:
        branch_obj = branches.filter(pk=branch_id).first()
        if branch_obj:
            branch_label = branch_obj.name
        else:
            branch_id = ""

    # ===== 9. Context =====
    ctx = {
        "branches": branches,
        "current_branch": str(branch_id),
        "period": period,
        "date_from": date_from,
        "date_to": date_to,

        "total_bookings": total_bookings,
        "total_revenue": total_revenue,
        "date_start": date_start,
        "date_end": date_end,
        "branch_label": branch_label,

        "latest_feedback": latest_feedback,

        "ts_labels": ts_labels,
        "ts_values": ts_values,
        "revenue_labels": revenue_labels,
        "revenue_values": revenue_values,
        "popular_labels": popular_labels,
        "popular_counts": popular_counts,
    }

    return render(request, "admin_site/dashboard.html", ctx)


@never_cache
@login_required
@user_passes_test(is_admin)
def admin_service(request):
    """
    Backoffice: danh sách dịch vụ + form tạo nhanh (giữ nguyên layout).
    Hỗ trợ: tìm kiếm (?q=), tổng số, phân trang.
    """
    # Create (modal)
    if request.method == "POST":
        form = ServiceForm(request.POST, request.FILES)
        if form.is_valid():
            form.save()
            messages.success(request, "Đã tạo dịch vụ.")
            return redirect('main:admin_service')
        else:
            messages.error(request, "Tạo dịch vụ thất bại. Vui lòng kiểm tra lại.")
    else:
        form = ServiceForm()

    # Search + query
    q = (request.GET.get("q") or "").strip()
    qs = Service.objects.all().order_by("service_name")
    if q:
        qs = qs.filter(Q(service_name__icontains=q) | Q(description__icontains=q))

    total = qs.count()

    # Pagination
    paginator = Paginator(qs, 12)
    page_obj = paginator.get_page(request.GET.get("page"))

    # Map sang dict cho template
    services_for_ui = [
        {**_service_to_card(s), "id": s.id, "is_active": getattr(s, "is_active", True)}
        for s in page_obj.object_list
    ]

    return render(request, 'admin_site/service.html', {
        'form': form,
        'q': q,
        'total': total,
        'page_obj': page_obj,
        'services': services_for_ui,
    })

@never_cache
@login_required
@user_passes_test(is_admin)
def admin_service_edit(request, pk):
    """
    Trang sửa dịch vụ (giữ UI tối giản, không phá layout toàn site).
    """
    obj = get_object_or_404(Service, pk=pk)

    if request.method == "POST":
        form = ServiceForm(request.POST, request.FILES, instance=obj)
        if form.is_valid():
            form.save()
            messages.success(request, "Đã cập nhật dịch vụ.")
            return redirect('main:admin_service')
        else:
            messages.error(request, "Cập nhật thất bại. Vui lòng kiểm tra lại.")
    else:
        form = ServiceForm(instance=obj)

    # dùng lại card/ảnh để hiển thị preview
    preview = {**_service_to_card(obj), "id": obj.id, "is_active": getattr(obj, "is_active", True)}

    return render(request, 'admin_site/service_edit.html', {
        'form': form,
        'service': preview,   # cho hiển thị thông tin nhanh
        'obj': obj,           # nếu bạn muốn dùng trực tiếp obj.image.url, ...
    })

def _monday(d: date) -> date:
    """Trả về thứ Hai trong tuần của ngày d (Mon=0..Sun=6)."""
    return d - timedelta(days=d.weekday())

def _get_choice_values(model_cls, field_name):
    """
    Đọc choices (value, label) của field và trả lại:
      - choices: list[(value, label)]
      - ns: SimpleNamespace có thuộc tính OPEN / ASSIGNED / OFF trỏ tới đúng *value* (nếu có)
    """
    fld = model_cls._meta.get_field(field_name)
    # choices có thể nằm ở .choices (list/tuple) hoặc .flatchoices (list)
    raw = getattr(fld, "choices", None) or getattr(fld, "flatchoices", [])  # <- KHÔNG .items()
    choices = list(raw)  # [(value, label), ...]

    def resolve(key):
        if not choices:
            return None
        key_up = str(key).upper()
        # Ưu tiên khớp theo value
        for val, label in choices:
            if str(val).upper() == key_up:
                return val
        # Sau đó khớp theo label bắt đầu bằng key
        for val, label in choices:
            if str(label).upper().startswith(key_up):
                return val
        # fallback: giá trị đầu tiên
        return choices[0][0]

    ns = SimpleNamespace(
        OPEN=resolve("OPEN"),
        ASSIGNED=resolve("ASSIGNED"),
        OFF=resolve("OFF"),
    )
    return choices, ns


@never_cache
@login_required
@user_passes_test(is_admin)
def admin_service_delete(request, pk):
    obj = get_object_or_404(Service, pk=pk)
    if request.method != "POST":
        raise Http404("Invalid method")
    obj.delete()
    messages.success(request, "Đã xoá dịch vụ.")
    return redirect('main:admin_service')

from collections import defaultdict
@never_cache
@login_required
@user_passes_test(is_admin)
def admin_schedule(request):
    """
    Trang quản trị lịch:
    - Hiển thị lịch ĐÃ DUYỆT theo dạng 3 hàng × 7 cột (branch xuất hiện dưới ca).
    - Vẫn giữ nút Approve next-week (duyệt hàng loạt) và cập nhật từng ô nếu cần.
    """
    form = ScheduleForm()

    # --- lọc theo nhân viên (tùy chọn) ---
    staff_qs = User.objects.filter(role=User.Role.STAFF).order_by("full_name", "username")
    staff_id = request.GET.get("staff")
    active_staff = get_object_or_404(User, pk=staff_id, role=User.Role.STAFF) if staff_id else None

    # --- khoảng tuần đang xem ---
    try:
        qs_start = request.GET.get("start")
        week_start = date.fromisoformat(qs_start) if qs_start else _monday(date.today())
    except Exception:
        week_start = _monday(date.today())
    week_end   = week_start + timedelta(days=6)
    prev_start = (week_start - timedelta(days=7)).isoformat()
    next_start = (week_start + timedelta(days=7)).isoformat()
    days = [week_start + timedelta(days=i) for i in range(7)]

    # --- dữ liệu dùng cho form ---
    branches = list(Branch.objects.all().order_by("name"))
    shift_choices, SHIFT   = _get_choice_values(StaffSchedule, "shift")
    status_choices, STATUS = _get_choice_values(StaffSchedule, "status")
    shifts = [(val, label) for (val, label) in shift_choices]   # [('MORNING','Morning'),...]

    # ========== ACTION: approve next-week ==========
    if request.method == "POST" and request.POST.get("action") == "approve":
        # modal gửi ?start={{ next_start }} nên week_start lúc này đã là tuần muốn duyệt
        next_ws = week_start
        next_we = next_ws + timedelta(days=6)
        pending_qs = StaffSchedule.objects.filter(
            work_date__range=(next_ws, next_we),
            status=getattr(STATUS, "PENDING", None),
        )
        count = pending_qs.count()
        pending_qs.update(
            status=getattr(STATUS, "APPROVED", None),
            approved_by=request.user,
        )
        messages.success(
            request,
            f"Đã phê duyệt {count} ca làm tuần {next_ws:%d/%m}–{next_we:%d/%m}."
        )
        return redirect(f"{request.path}?start={week_start.isoformat()}")

    # ========== ACTION: save single cell ==========
    if request.method == "POST" and request.POST.get("action") != "approve":
        form = ScheduleForm(request.POST)
        if form.is_valid():
            staff     = form.cleaned_data["staff"]
            work_date = form.cleaned_data["work_date"]
            shift     = form.cleaned_data["shift"]
            status    = form.cleaned_data["status"]
            branch    = form.cleaned_data.get("branch")

            obj, _ = StaffSchedule.objects.get_or_create(
                staff=staff, work_date=work_date, shift=shift,
                defaults={"status": status},
            )

            obj.status = status
            if status == getattr(STATUS, "APPROVED", None):
                obj.branch = branch
                obj.approved_by = request.user
            else:
                obj.branch = None
                obj.approved_by = None
            obj.save()

            messages.success(
                request,
                f"Đã cập nhật {staff.full_name or staff.username} – {work_date} ({shift})."
            )
            back = f"{request.path}?start={week_start.isoformat()}"
            if active_staff:
                back += f"&staff={active_staff.id}"
            return redirect(back)
        else:
            messages.error(request, "Cập nhật thất bại. Vui lòng kiểm tra lại các trường.")

    # --- lịch trong tuần (để render/ghi chú trên lưới theo staff nếu cần) ---
    sched_qs = (
        StaffSchedule.objects
        .select_related("staff", "branch")
        .filter(work_date__range=(week_start, week_end))
    )
    if active_staff:
        sched_qs = sched_qs.filter(staff=active_staff)

    # map nhanh: (staff_id, 'YYYY-MM-DD', shift) -> schedule object
    schedule_map = {
        (s.staff_id, s.work_date.isoformat(), s.shift): s
        for s in sched_qs
    }

    # --- cell (dùng cho layout 3 hàng × 7 cột hiển thị lịch ĐÃ DUYỆT) ---
    # key: (day, shift) -> list of {"branch": "...", "staff": "..."}
    cell = defaultdict(list)
    approved_qs = StaffSchedule.objects.select_related("branch", "staff").filter(
        work_date__range=(week_start, week_end),
        status=StaffSchedule.Status.APPROVED
    )
    for s in approved_qs:
        cell[(s.work_date, s.shift)].append({
            "branch": s.branch.name if s.branch else "",
            "staff":  getattr(s.staff, "full_name", "") or s.staff.username
        })
    grid_rows = []
    for code, label in shifts:  # [('MORNING','Morning'), ...]
        cols = []
        for d in days:
            cols.append({
                "day": d,
                "entries": cell.get((d, code), [])  # list[{branch, staff}]
            })
        grid_rows.append({"code": code, "label": label, "cols": cols})
    # --- số yêu cầu PENDING của tuần kế tiếp (badge ở nút Approve) ---
    next_ws = week_start + timedelta(days=7)
    next_we = next_ws + timedelta(days=6)
    pending = StaffSchedule.objects.filter(
        work_date__range=(next_ws, next_we),
        status=getattr(STATUS, "PENDING", None),
    ).count()

    ctx = {
        "form": form,
        "branches": branches,
        "staff_list": staff_qs,
        "active_staff": active_staff,

        "week_start": week_start,
        "week_end": week_end,
        "prev_start": prev_start,
        "next_start": next_start,

        "days": days,
        "shifts": shifts,
        "pending": pending,
        "STATUS": STATUS,

        # cho template kiểu “bảng theo staff” (nếu còn dùng):
        "schedule_map": schedule_map,
        # cho template kiểu “3 hàng × 7 cột” (hiển thị branch dưới ca):
        "cell": cell,
        "grid_rows": grid_rows,
    }
    return render(request, "admin_site/schedule.html", ctx)
@never_cache
@login_required
@user_passes_test(is_admin)
def admin_schedule_review(request):
    """
    Trang REVIEW/APPROVE các ca PENDING của TUẦN ĐANG XEM (thường là next week).
    URL: /backoffice/schedule/review/?start=YYYY-MM-DD  (start là thứ Hai của tuần muốn duyệt)
    """
    # --- tuần đang xem ---
    try:
        qs_start = request.GET.get("start")
        week_start = date.fromisoformat(qs_start) if qs_start else _monday(date.today()) + timedelta(days=7)
    except Exception:
        week_start = _monday(date.today()) + timedelta(days=7)
    week_end   = week_start + timedelta(days=6)
    prev_start = (week_start - timedelta(days=7)).isoformat()
    next_start = (week_start + timedelta(days=7)).isoformat()
    days = [week_start + timedelta(days=i) for i in range(7)]

    # --- choices & dữ liệu phụ ---
    branches = list(Branch.objects.all().order_by("name"))
    shift_choices, SHIFT   = _get_choice_values(StaffSchedule, "shift")
    status_choices, STATUS = _get_choice_values(StaffSchedule, "status")
    shifts = [(val, label) for (val, label) in shift_choices]

    # --- POST: duyệt 1 ca ---
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "approve_one":
            staff_id  = int(request.POST["staff"])
            work_date = date.fromisoformat(request.POST["work_date"])
            shift     = request.POST["shift"]
            branch_id = int(request.POST.get("branch") or 0)

            obj, created = StaffSchedule.objects.get_or_create(
                staff_id=staff_id, work_date=work_date, shift=shift,
                defaults={"status": StaffSchedule.Status.PENDING}
            )
            if branch_id <= 0:
                messages.error(request, "Vui lòng chọn chi nhánh trước khi phê duyệt.")
            else:
                obj.branch = Branch.objects.get(pk=branch_id)
                obj.status = StaffSchedule.Status.APPROVED
                obj.approved_by = request.user
                obj.save(update_fields=["branch", "status", "approved_by"])
                messages.success(request, "Đã phê duyệt ca làm.")
            return redirect(f"{request.path}?start={week_start.isoformat()}")

        elif action == "reject_one":
            staff_id  = int(request.POST["staff"])
            work_date = date.fromisoformat(request.POST["work_date"])
            shift     = request.POST["shift"]
            obj = StaffSchedule.objects.filter(
                staff_id=staff_id, work_date=work_date, shift=shift
            ).first()
            if obj:
                obj.status = StaffSchedule.Status.REJECTED
                obj.branch = None
                obj.approved_by = request.user
                obj.save(update_fields=["status", "branch", "approved_by"])
                messages.info(request, "Đã từ chối ca làm.")
            return redirect(f"{request.path}?start={week_start.isoformat()}")

    # --- lấy danh sách PENDING của tuần đang xem ---
    pending_qs = (
        StaffSchedule.objects
        .select_related("staff")
        .filter(work_date__range=(week_start, week_end),
                status=StaffSchedule.Status.PENDING)
        .order_by("work_date", "shift", "staff__full_name", "staff__username")
    )

    # bucket: (day, shift) -> list items (mỗi item là 1 yêu cầu cần duyệt)
    bucket = defaultdict(list)
    for s in pending_qs:
        bucket[(s.work_date, s.shift)].append({
            "staff_id": s.staff_id,
            "staff_name": (getattr(s.staff, "full_name", "") or s.staff.username),
            "has_row": True,
        })

    # build grid_rows => template chỉ lặp
    grid_rows = []
    for code, label in shifts:
        cols = []
        for d in days:
            cols.append({
                "day": d,
                "items": bucket.get((d, code), [])  # list các request cần duyệt
            })
        grid_rows.append({"code": code, "label": label, "cols": cols})

    ctx = {
        "week_start": week_start, "week_end": week_end,
        "prev_start": prev_start, "next_start": next_start,
        "days": days,
        "branches": branches,
        "shifts": shifts,
        "grid_rows": grid_rows,
        "STATUS": STATUS,
    }
    return render(request, "admin_site/schedule_review.html", ctx)
@never_cache
@login_required
@user_passes_test(is_admin)
def admin_feedback(request):
    q = (request.GET.get("q") or "").strip()

    fb_qs = (
        Review.objects
        .select_related("customer", "service", "appointment")
        .order_by("-review_date")
    )

    if q:
        fb_qs = fb_qs.filter(
            Q(customer__full_name__icontains=q) |
            Q(customer__username__icontains=q) |
            Q(service__service_name__icontains=q) |
            Q(comment__icontains=q)
        )

    return render(request, "admin_site/feedback.html", {
        "feedback_list": fb_qs,
        "q": q,
    })
@never_cache
@login_required
@user_passes_test(is_admin)
def admin_feedback_reply(request, pk: int):
    review = get_object_or_404(
        Review.objects.select_related("customer", "service", "appointment"),
        pk=pk
    )

    if request.method == "POST":
        reply = (request.POST.get("admin_reply") or "").strip()
        review.admin_reply = reply
        review.admin_replied_at = timezone.now()
        review.save(update_fields=["admin_reply", "admin_replied_at"])
        messages.success(request, "Đã lưu phản hồi.")
        return redirect("main:admin_feedback")

    return render(request, "admin_site/feedback_reply.html", {"review": review})


# --- imports cần dùng ---
from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from .models import User
from .forms import EmployeeForm



# =========================
# 1) Danh sách + Tạo mới
# =========================
@never_cache
@login_required
@user_passes_test(is_admin)
def admin_employee(request):
    """
    Backoffice: danh sách nhân viên + modal tạo mới bằng EmployeeForm.
    - Chống trùng username/email đã xử lý trong EmployeeForm.clean_*.
    """
    # Create (modal)
    if request.method == "POST":
        form = EmployeeForm(request.POST)
        if form.is_valid():
            user = form.save(commit=True)  # set role=STAFF, is_staff=True, add Group "Staff"
            messages.success(request, f"Đã tạo nhân viên: {user.username}")
            return redirect("main:admin_employee")
        else:
            messages.error(request, "Tạo nhân viên thất bại. Vui lòng kiểm tra lại.")
    else:
        form = EmployeeForm()

    # Search + query
    q = (request.GET.get("q") or "").strip()
    qs = User.objects.filter(role=User.Role.STAFF).order_by("-id")
    if q:
        qs = qs.filter(
            Q(username__icontains=q)
            | Q(full_name__icontains=q)
            | Q(email__icontains=q)
            | Q(phone_number__icontains=q)
        )

    total = qs.count()
    paginator = Paginator(qs, 12)
    staff_page = paginator.get_page(request.GET.get("page"))

    return render(
        request,
        "admin_site/employee.html",
        {
            "q": q,
            "total": total,
            "staff": staff_page,  # đúng tên biến template đang dùng
            "form": form,
        },
    )


# =========================================
# 2) Sửa / Đổi mật khẩu / Khoá / Xoá
# =========================================
@never_cache
@login_required
@user_passes_test(is_admin)
def admin_employee_edit(request, user_id: int):
    """
    Trang chỉnh sửa một nhân viên:
      - POST action=save         : cập nhật thông tin (dùng lại EmployeeForm, password optional)
      - POST action=set_password : đổi mật khẩu (new_password1, new_password2)
      - POST action=toggle_active: khoá/mở (is_active)
      - POST action=delete       : xoá tài khoản (chặn tự xoá chính mình)
    """
    obj = get_object_or_404(User, pk=user_id, role=User.Role.STAFF)

    if request.method == "POST":
        action = request.POST.get("action", "save")

        # 2.1 Lưu thông tin chung
        if action == "save":
            form = EmployeeForm(request.POST, instance=obj)
            if form.is_valid():
                form.save()
                messages.success(request, "Đã cập nhật thông tin nhân viên.")
                return redirect("main:admin_employee")
            else:
                messages.error(request, "Cập nhật thất bại. Vui lòng kiểm tra lại.")
        # 2.2 Đổi mật khẩu
        elif action == "set_password":
            pwd1 = (request.POST.get("new_password1") or "").strip()
            pwd2 = (request.POST.get("new_password2") or "").strip()
            if not pwd1:
                messages.error(request, "Vui lòng nhập mật khẩu mới.")
            elif pwd1 != pwd2:
                messages.error(request, "Mật khẩu nhập lại không khớp.")
            else:
                obj.set_password(pwd1)
                obj.save(update_fields=["password"])
                messages.success(request, "Đã đổi mật khẩu cho nhân viên.")
                return redirect("main:admin_employee_edit", user_id=obj.id)
            form = EmployeeForm(instance=obj)  # hiển thị lại form với lỗi
        # 2.3 Khoá / Mở
        elif action == "toggle_active":
            obj.is_active = not obj.is_active
            obj.save(update_fields=["is_active"])
            messages.success(
                request,
                ("Đã mở khoá tài khoản." if obj.is_active else "Đã khoá tài khoản nhân viên."),
            )
            return redirect("main:admin_employee_edit", user_id=obj.id)
        # 2.4 Xoá
        elif action == "delete":
            if obj.id == request.user.id:
                messages.error(request, "Bạn không thể tự xoá chính mình.")
            else:
                obj.delete()
                messages.success(request, "Đã xoá nhân viên.")
                return redirect("main:admin_employee")
            form = EmployeeForm(instance=obj)
        else:
            form = EmployeeForm(instance=obj)  # fallback
    else:
        form = EmployeeForm(instance=obj)

    return render(
        request,
        "admin_site/employee_edit.html",
        {
            "form": form,
            "obj": obj,
        },
    )

# main/views.py  (thêm import)
@login_required
@user_passes_test(is_admin)
def branch_list(request):
    q = (request.GET.get("q") or "").strip()
    qs = Branch.objects.all().order_by("id")
    if q:
        qs = qs.filter(name__icontains=q) | qs.filter(address__icontains=q) | qs.filter(phone__icontains=q)

    paginator = Paginator(qs, 10)
    page_obj = paginator.get_page(request.GET.get("page"))

    ctx = {
        "page_obj": page_obj,
        "q": q,
        "total": qs.count(),
    }
    return render(request, "admin_site/branch_list.html", ctx)


@login_required
@user_passes_test(is_admin)
def branch_create(request):
    if request.method == "POST":
        form = BranchForm(request.POST)
        if form.is_valid():
            branch = form.save()
            messages.success(request, "Created branch successfully.")
            return redirect("main:branch_detail", pk=branch.pk)
    else:
        form = BranchForm()
    return render(request, "admin_site/branch_form.html", {"form": form, "is_create": True})


@login_required
@user_passes_test(is_admin)
def branch_update(request, pk):
    branch = get_object_or_404(Branch, pk=pk)
    if request.method == "POST":
        form = BranchForm(request.POST, instance=branch)
        if form.is_valid():
            form.save()
            messages.success(request, "Updated branch successfully.")
            return redirect("main:branch_detail", pk=branch.pk)
    else:
        form = BranchForm(instance=branch)
    return render(request, "admin_site/branch_form.html", {"form": form, "branch": branch, "is_create": False})


@login_required
@user_passes_test(is_admin)
def branch_detail(request, pk):
    branch = get_object_or_404(Branch, pk=pk)
    return render(request, "admin_site/branch_detail.html", {"branch": branch})

@never_cache
@login_required
@user_passes_test(is_customer)
def loyalty_history(request):
    u = request.user
    lp, _ = LoyaltyPoints.objects.get_or_create(customer=u)

    txs = (
        LoyaltyTransaction.objects
        .filter(customer=u)
        .select_related("appointment")
        .order_by("-created_at")
    )

    return render(request, "customer/loyalty_history.html", {
        "loyalty": lp,
        "transactions": txs,
    })
def _membership_from_points(points: int):
    """Trả về (tier, discount_percent) theo điểm loyalty hiện tại."""
    if points >= 300:
        return "Diamond", 15
    if points >= 200:
        return "Gold", 10
    if points >= 100:
        return "Silver", 5
    return "Member", 0

def _award_loyalty_for_appointment(appt: Appointment):
    """
    Cộng điểm loyalty cho lịch hẹn đã hoàn tất nếu:
    - Có customer
    - Chưa cộng trước đó (loyalty_awarded = False)
    Quy tắc demo: 1 điểm cho mỗi 10.000đ của tổng tiền.
    """
    if not appt.customer or appt.loyalty_awarded:
        return

    total = int(appt.total_price or 0)
    # Quy tắc tích điểm: 1 điểm / 10.000₫ (bạn muốn thì đổi lại)
    pts = total // 10000
    if pts <= 0:
        return

    lp, _ = LoyaltyPoints.objects.get_or_create(customer=appt.customer)
    lp.current_points += pts
    lp.points_earned += pts
    lp.save(update_fields=["current_points", "points_earned", "last_updated"])

    tx = LoyaltyTransaction.objects.create(
        customer=appt.customer,
        appointment=appt,
        type=LoyaltyTransaction.Type.EARN,
        points=pts,
        balance_after=lp.current_points,
        description=f"Tích {pts} điểm cho lịch {f'BK{appt.id:06d}'}",
    )

    appt.loyalty_awarded = True
    appt.save(update_fields=["loyalty_awarded"])

