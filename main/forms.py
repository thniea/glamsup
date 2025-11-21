# main/forms.py
from datetime import date, time as dtime
from decimal import Decimal
from django.utils import timezone
from django import forms
from .models import User
from django import forms
from django.contrib.auth import authenticate
from django.contrib.auth.forms import UsernameField
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from .models import (
    User, Branch, Service, Appointment, StaffSchedule, Review
)

# --------- helpers ----------
def _add_bs_classes(fields, *, input_cls="form-control", select_cls="form-select"):
    for _name, f in fields.items():
        w = f.widget
        if isinstance(w, (forms.Select, forms.SelectMultiple)):
            w.attrs.setdefault("class", select_cls)
        elif isinstance(w, (forms.CheckboxInput, forms.ClearableFileInput, forms.FileInput)):
            # giữ nguyên cho checkbox/file
            pass
        else:
            w.attrs.setdefault("class", input_cls)


def _infer_shift_from_time(t: dtime):
    """
    Suy luận ca làm từ giờ hẹn để kiểm tra StaffSchedule:
    MORNING(06:00–11:59), AFTERNOON(12:00–17:59), EVENING(18:00–22:00)
    """
    if dtime(6, 0) <= t <= dtime(11, 59):
        return StaffSchedule.Shift.MORNING
    if dtime(12, 0) <= t <= dtime(17, 59):
        return StaffSchedule.Shift.AFTERNOON
    return StaffSchedule.Shift.EVENING


# =========================================
# 1) LoginForm (email/username + password)
# =========================================
class LoginForm(forms.Form):
    username = UsernameField(
        label=_("Email or Username"),
        widget=forms.TextInput(attrs={"autofocus": True, "placeholder": _("Email or Username")})
    )
    password = forms.CharField(
        label=_("Password"),
        strip=False,
        widget=forms.PasswordInput(attrs={"placeholder": _("Password")})
    )

    def __init__(self, *args, **kwargs):
        self.request = kwargs.pop("request", None)
        super().__init__(*args, **kwargs)
        _add_bs_classes(self.fields)

    def clean(self):
        cleaned = super().clean()
        username_or_email = cleaned.get("username")
        password = cleaned.get("password")
        if username_or_email and password:
            # Cho phép đăng nhập bằng email hoặc username
            user = authenticate(self.request, username=username_or_email, password=password)
            if not user:
                try:
                    u = User.objects.get(email__iexact=username_or_email)
                except User.DoesNotExist:
                    u = None
                if u:
                    user = authenticate(self.request, username=u.username, password=password)

            if user is None:
                raise ValidationError(_("Invalid credentials."))
            self.user = user
        return cleaned


# =========================================


# =========================================
# 3) BookingForm (đặt lịch — Form thường)
#   - chọn chi nhánh, nhiều dịch vụ, (tuỳ chọn) nhân viên, ngày, giờ
#   - validate: ngày không quá khứ, staff có ca làm phù hợp, không trùng lịch
# =========================================
class BookingForm(forms.Form):
    branch = forms.ModelChoiceField(queryset=Branch.objects.all(), label=_("Branch"))
    services = forms.ModelMultipleChoiceField(
        queryset=Service.objects.filter(is_active=True),
        label=_("Services"),
        widget=forms.SelectMultiple(attrs={"size": 6})
    )
    staff = forms.ModelChoiceField(
        queryset=User.objects.filter(role=User.Role.STAFF),
        label=_("Staff (optional)"),
        required=False
    )
    date = forms.DateField(label=_("Date"), widget=forms.DateInput(attrs={"type": "date"}))
    time = forms.TimeField(label=_("Time"), widget=forms.TimeInput(attrs={"type": "time"}))
    note = forms.CharField(label=_("Note"), required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _add_bs_classes(self.fields)
        # đặt min ngày là hôm nay
        self.fields["date"].widget.attrs["min"] = date.today().isoformat()

    def clean_date(self):
        d = self.cleaned_data["date"]
        if d < date.today():
            raise ValidationError(_("Booking date cannot be in the past."))
        return d

    def clean(self):
        cleaned = super().clean()
        br = cleaned.get("branch")
        staff = cleaned.get("staff")
        d = cleaned.get("date")
        t = cleaned.get("time")

        if not (br and d and t):
            return cleaned

        # Nếu có chọn staff: kiểm tra ca làm phù hợp & không trùng lịch
        if staff:
            # 1) StaffSchedule: có ca làm ở chi nhánh & ngày đó?
            shift = _infer_shift_from_time(t)
            has_schedule = StaffSchedule.objects.filter(
                staff=staff, branch=br, work_date=d, shift=shift,
                status=StaffSchedule.Status.APPROVED
            ).exists()
            if not has_schedule:
                raise ValidationError(
                    _("Selected staff has no approved shift at this branch and time.")
                )

            # 2) Không trùng lịch hẹn cùng thời điểm (đơn giản theo date+time)
            conflict = Appointment.objects.filter(
                staff=staff,  # M2M qua through; filter bằng related name staff__...
                appointment_date=d,
                appointment_time=t,
                status__in=[
                    Appointment.Status.PENDING,
                    Appointment.Status.CONFIRMED,
                    Appointment.Status.IN_PROGRESS
                ]
            ).exists()
            # NOTE: do staff là M2M, filter trực tiếp như trên có thể không chạy ở mọi DB backend.
            # An toàn hơn: tìm theo appointment có staff đã gán:
            if not conflict:
                conflict = Appointment.objects.filter(
                    appointment_date=d,
                    appointment_time=t,
                    status__in=[
                        Appointment.Status.PENDING,
                        Appointment.Status.CONFIRMED,
                        Appointment.Status.IN_PROGRESS
                    ],
                    staff_lines__staff=staff
                ).exists()
            if conflict:
                raise ValidationError(_("Selected staff already has an appointment at this time."))

        return cleaned

    # Helper nhỏ để tính tổng (nếu view muốn dùng)
    def calc_total(self):
        svs = self.cleaned_data.get("services") or []
        total = Decimal("0.00")
        for s in svs:
            total += s.price or Decimal("0.00")
        return total


# =========================================
# 4) FeedbackForm (tuỳ biến theo UI của bạn)
#   - Giữ nguyên trường bạn đang dùng để không vỡ template
# =========================================
class FeedbackForm(forms.Form):
    product_rating = forms.IntegerField(
        min_value=0, max_value=5, initial=0,
        label=_("Product rating")
    )
    service_rating = forms.IntegerField(
        min_value=0, max_value=5, initial=0,
        label=_("Service rating")
    )
    comment = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 6}),
        required=False,
        label=_("Comment")
    )

    # 👇 ĐỔI TÊN CHO KHỚP HTML: photo, video
    photo = forms.ImageField(
        required=False,
        label=_("Photo")
    )
    video = forms.FileField(
        required=False,
        label=_("Photo/Video")
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _add_bs_classes(self.fields)

    def clean(self):
        cleaned = super().clean()
        # Nếu muốn bắt buộc ít nhất 1 ảnh/video thì bỏ comment 2 dòng dưới:
        # if not cleaned.get("photo") and not cleaned.get("video"):
        #     raise ValidationError(_("Please attach at least one photo or video."))
        return cleaned
# =========================================
# 4b) ReviewForm (ModelForm chuẩn cho model Review)
#   - Nếu bạn muốn lưu trực tiếp vào Review (ảnh: ImageField 'image')
# =========================================
class ReviewForm(forms.ModelForm):
    class Meta:
        model = Review
        fields = [
            "rating",
            "comment",
            "image",
            "video",
            "admin_reply",
            "status",
        ]
        widgets = {
            "comment": forms.Textarea(attrs={"rows": 4}),
            "admin_reply": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _add_bs_classes(self.fields)



# =========================================
# 5) EmployeeForm (tạo/sửa nhân viên)
#   - ModelForm của User
#   - Đặt/đổi mật khẩu; gán role STAFF và add vào Group "Staff" nếu tồn tại
# =========================================
class EmployeeForm(forms.ModelForm):
    role_group = forms.ModelChoiceField(
        label=_("Role"),
        # Lọc ra các nhóm mà bạn muốn Admin gán cho nhân viên
        queryset=Group.objects.filter(name__in=['Receptionist', 'Technician']),
        required=True,
        empty_label=None,
    )
    password = forms.CharField(
        label=_("Password"),
        required=False,
        widget=forms.PasswordInput(render_value=False)
    )

    class Meta:
        model = User
        fields = ["username", "full_name", "email", "phone_number", "address", "role_group", "password"]
        widgets = {
            "username": forms.TextInput(attrs={"autocomplete": "off"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _add_bs_classes(self.fields)
        # Định sẵn role
        self.instance.role = User.Role.STAFF
        if self.instance and self.instance.pk:
            current_groups = self.instance.groups.all()
            if current_groups.exists():
                self.initial['role_group'] = current_groups.first()

    def clean_username(self):
        u = self.cleaned_data["username"]
        qs = User.objects.filter(username__iexact=u)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise ValidationError(_("Username already exists."))
        return u

    def clean_email(self):
        e = (self.cleaned_data.get("email") or "").strip()
        if not e:
            return e
        qs = User.objects.filter(email__iexact=e)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise ValidationError(_("Email already exists."))
        return e

    # =======================

    def save(self, commit=True):
        user = super().save(commit=False)
        user.role = User.Role.STAFF
        user.is_staff = True

        pwd = self.cleaned_data.get("password")
        if pwd:
            user.set_password(pwd)

        if commit:
            user.save()
            selected_group = self.cleaned_data.get('role_group')

            if selected_group:
                # 2. Xóa các nhóm cũ (để đảm bảo nhân viên chỉ có 1 vai trò chính)
                user.groups.clear()
                # 3. Thêm nhóm mới
                user.groups.add(selected_group)
        return user

class EmployeeUpdateForm(EmployeeForm):
    is_active = forms.BooleanField(label=_("Active"), required=False)
    password  = forms.CharField(  # đổi nhãn cho dễ hiểu
        label=_("Reset password"),
        required=False,
        widget=forms.PasswordInput(render_value=False)
    )

    class Meta(EmployeeForm.Meta):
        fields = ["username", "full_name", "email", "phone_number", "address", "role_group", "is_active", "password"]
# =========================================
# 6) ServiceForm (tạo/sửa dịch vụ)
# =========================================
class ServiceForm(forms.ModelForm):
    class Meta:
        model = Service
        fields = ["service_name", "description", "price", "image", "is_active", "category", "duration",]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
            "category": forms.Select(),  # render 3 lựa chọn
            "price": forms.NumberInput(attrs={"step": 1000, "min": 0}),
            "duration": forms.NumberInput(attrs={"min": 10, "step": 5}),
        }
        labels = {
            "service_name": _("Name"),
            "is_active": _("Order status"),
            "category": _("Category"),
            "duration": _("Duration (minutes)"),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _add_bs_classes(self.fields)


# =========================================
# 7) ScheduleForm (tạo/điều chỉnh ca làm)
#   - Admin dùng đủ fields (bao gồm status)
# =========================================
class ScheduleForm(forms.ModelForm):
    class Meta:
        model = StaffSchedule
        fields = ["staff", "branch", "work_date", "shift", "status"]
        widgets = {
            "work_date": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Chỉ cho chọn staff có role STAFF
        self.fields["staff"].queryset = User.objects.filter(role=User.Role.STAFF)
        _add_bs_classes(self.fields)

    def clean_work_date(self):
        d = self.cleaned_data["work_date"]
        if d < date.today():
            raise ValidationError(_("Work date cannot be in the past."))
        return d



class StaffSelfScheduleForm(forms.ModelForm):
    """Form cho nhân viên đăng ký ca"""
    class Meta:
        model = StaffSchedule
        fields = ["work_date", "shift"]
        widgets = {"work_date": forms.DateInput(attrs={"type": "date"})}

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)
        _add_bs_classes(self.fields)
        self.fields["work_date"].widget.attrs["min"] = timezone.localdate().isoformat()

    def clean(self):
        cleaned = super().clean()
        d = cleaned.get("work_date")
        sh = cleaned.get("shift")
        u = self.user
        if not (d and sh and u):
            return cleaned

        today = timezone.localdate()
        monday_this = today - timezone.timedelta(days=today.weekday())
        monday_next = monday_this + timezone.timedelta(days=7)
        last_allowed = monday_next + timezone.timedelta(days=6)
        if d < monday_this or d > last_allowed:
            raise ValidationError(_("You can only request shifts for this week or next week."))

        exists = StaffSchedule.objects.filter(
            staff=u, work_date=d, shift=sh,
            status__in=[StaffSchedule.Status.PENDING, StaffSchedule.Status.APPROVED]
        ).exists()
        if exists:
            raise ValidationError(_("You already have a request/approved shift for that slot."))
        return cleaned

    def save(self, commit=True):
        obj = super().save(commit=False)
        obj.staff = self.user
        obj.status = StaffSchedule.Status.PENDING
        obj.branch = None
        obj.approved_by = None
        if commit:
            obj.save()
        return obj

from django.contrib.auth import password_validation

class StaffPasswordChangeForm(forms.Form):
    old_password   = forms.CharField(label=_("Current password"),
                                     widget=forms.PasswordInput(render_value=False))
    new_password1  = forms.CharField(label=_("New password"),
                                     widget=forms.PasswordInput(render_value=False))
    new_password2  = forms.CharField(label=_("Confirm new password"),
                                     widget=forms.PasswordInput(render_value=False))

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)
        _add_bs_classes(self.fields)

    def clean_old_password(self):
        pwd = self.cleaned_data.get("old_password") or ""
        if not self.user or not self.user.check_password(pwd):
            raise ValidationError(_("Current password is incorrect."))
        return pwd

    def clean(self):
        cleaned = super().clean()
        p1 = cleaned.get("new_password1") or ""
        p2 = cleaned.get("new_password2") or ""
        if p1 and p2 and p1 != p2:
            raise ValidationError(_("New passwords do not match."))
        # áp dụng validator của Django (nếu bạn đã cấu hình trong settings)
        if p1:
            password_validation.validate_password(p1, self.user)
        return cleaned

# main/forms.py  (thêm vào cuối file)

from django.contrib.auth.password_validation import validate_password

from django import forms
from django.contrib.auth.forms import UserCreationForm
from .models import User

class SignupForm(UserCreationForm):
    full_name = forms.CharField(max_length=120, required=False)
    email = forms.EmailField(required=False)

    class Meta:
        model = User
        fields = ("username", "full_name", "email", "password1", "password2")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for f in self.fields.values():
            cls = "form-control"
            if isinstance(f.widget, forms.PasswordInput):
                f.widget.attrs["class"] = cls
            else:
                f.widget.attrs["class"] = cls


from .models import Branch

class BranchForm(forms.ModelForm):
    class Meta:
        model = Branch
        fields = ["name", "address", "phone"]
        widgets = {
            "name":    forms.TextInput(attrs={"class": "form-control", "placeholder": "Branch name"}),
            "address": forms.TextInput(attrs={"class": "form-control", "placeholder": "Address"}),
            "phone":   forms.TextInput(attrs={"class": "form-control", "placeholder": "Phone"}),
        }

from .models import ContactMessage
import re
class ContactForm(forms.ModelForm):
    class Meta:
        model = ContactMessage
        fields = ["full_name", "email", "phone", "branch_name", "content"]
        widgets = {
            "full_name": forms.TextInput(attrs={
                "class": "form-control",
                "placeholder": "Full name"
            }),
            "email": forms.EmailInput(attrs={
                "class": "form-control",
                "placeholder": "Your Gmail address"
            }),
            "phone": forms.TextInput(attrs={
                "class": "form-control",
                "placeholder": "Phone number"
            }),
            "branch_name": forms.TextInput(attrs={
                "class": "form-control",
                "placeholder": "Preferred branch (optional)"
            }),
            "content": forms.Textarea(attrs={
                "class": "form-control",
                "rows": 5,
                "placeholder": "Write your question or message here..."
            }),
        }

    # Validate Gmail
    def clean_email(self):
        email = self.cleaned_data.get("email", "").strip()
        if not email.lower().endswith("@gmail.com"):
            raise forms.ValidationError("Please use a valid Gmail address (e.g. example@gmail.com).")
        return email

    # Validate phone number
    def clean_phone(self):
        phone = re.sub(r"\D+", "", self.cleaned_data.get("phone", ""))
        if not (9 <= len(phone) <= 11):
            raise forms.ValidationError("Invalid phone number. Please enter 9–11 digits.")
        return phone
