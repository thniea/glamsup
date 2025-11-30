from django.urls import path
from django.contrib.auth import views as auth_views
from . import views

app_name = 'main'

urlpatterns = [
    # customer
    path('', views.home, name='home'),
    path('login/', views.login_view, name='login'),
    path('signup/', views.signup_view, name='signup'),
    path('password-reset/', auth_views.PasswordResetView.as_view(
        template_name="registration/password_form.html",
        email_template_name="registration/password_reset_email.txt",
        subject_template_name="registration/password_reset_subject.txt",
        success_url="/password-reset/done/"
    ), name='password_reset'),
    path('password-reset/done/', auth_views.PasswordResetDoneView.as_view(
        template_name="registration/password_done.html"
    ), name='password_reset_done'),
    path('reset/<uidb64>/<token>/', auth_views.PasswordResetConfirmView.as_view(
        template_name="registration/password_confirm.html",
        success_url="/reset/complete/"
    ), name='password_reset_confirm'),
    path('reset/complete/', auth_views.PasswordResetCompleteView.as_view(
        template_name="registration/password_complete.html"
    ), name='password_reset_complete'),
    # <-- mới
    path('services/', views.services, name='services'),
    path('services/<slug:slug>/', views.service_detail, name='service_detail'),
    path('book/', views.book_now, name='book'),
    path("payment/<str:code>/", views.payment, name="payment"),
    path("payment/<str:code>/complete/", views.payment_complete, name="payment_complete"),
    path("order-result/<str:code>/", views.order_result, name="order_result"),
    path('my-appointments/', views.my_appointments, name='my_appointments'),
    path('my-appointments/<int:pk>/cancel/', views.cancel_appointment, name='cancel_appointment'),
    path('feedback/<int:booking_id>/', views.customer_feedback, name='customer_feedback'),
    path('account/', views.customer_account, name='customer_account'),
    path("account/points/", views.loyalty_history, name="loyalty_history"),
    path("contact/", views.contact_view, name="contact"),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'), # <-- THÊM
    path("promotion/", views.promotion, name="promotion"),
    # staff
    path('employee/account/', views.staff_account, name='staff_account'),  # Account setting
    path('employee/schedule/', views.staff_schedule, name='staff_schedule'),  # Lịch làm việc
    path('employee/appointments/', views.staff_appointments, name='staff_appointments'),
# Lễ tân (Receptionist)
    path('receptionist/dashboard/', views.receptionist_dashboard, name='receptionist_dashboard'),
    path('receptionist/appointments/', views.receptionist_dashboard, name='receptionist_appointments'),
    # Receptionist actions
    path("receptionist/check-in/<int:pk>/", views.check_in, name="check_in"),
    path("receptionist/check-out/<int:pk>/", views.check_out, name="check_out"),
    path("staff/inbox/", views.staff_inbox, name="staff_inbox"),
    path("staff/inbox/<int:pk>/", views.staff_inbox_detail, name="staff_inbox_detail"),
    path("staff/inbox/<int:pk>/update/", views.staff_inbox_update, name="staff_inbox_update"),
    # admin site (tránh trùng với django.contrib.admin)
    path('backoffice/', views.admin_dashboard, name='admin_dashboard'),
    path('backoffice/services/', views.admin_service, name='admin_service'),
    path('backoffice/services/<int:pk>/edit/', views.admin_service_edit, name='admin_service_edit'),
    path('backoffice/services/<int:pk>/delete/', views.admin_service_delete, name='admin_service_delete'),# <-- THÊM DÒNG NÀY
    path('backoffice/schedule/', views.admin_schedule, name='admin_schedule'),
    path('backoffice/schedule/review/', views.admin_schedule_review, name='admin_schedule_review'),
    path('backoffice/feedback/', views.admin_feedback, name='admin_feedback'),
    path('backoffice/feedback/<int:pk>/reply/', views.admin_feedback_reply, name='admin_feedback_reply'),
    path('backoffice/employees/', views.admin_employee, name='admin_employee'),
    path('backoffice/employees/<int:user_id>/edit/', views.admin_employee_edit, name='admin_employee_edit'),
    path("backoffice/branches/", views.branch_list, name="branch_list"),
    path("backoffice/branches/add/", views.branch_create, name="branch_create"),
    path("backoffice/branches/<int:pk>/", views.branch_detail, name="branch_detail"),
    path("backoffice/branches/<int:pk>/edit/", views.branch_update, name="branch_update"),
]
