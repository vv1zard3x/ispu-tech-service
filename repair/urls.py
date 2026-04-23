from django.urls import path

from . import views

app_name = "repair"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("orders/", views.order_list, name="order-list"),
    path("orders/my/", views.my_order_list, name="my-order-list"),
    path("orders/new/", views.order_create, name="order-create"),
    path("orders/<int:pk>/", views.order_detail, name="order-detail"),
    path("orders/<int:pk>/assign/", views.assign_order, name="order-assign"),
    path("orders/<int:pk>/reassign/", views.order_reassign, name="order-reassign"),
    path("orders/<int:pk>/takeover/", views.order_take_over, name="order-take-over"),
    path("orders/<int:pk>/diagnostics/", views.diagnostics, name="order-diagnostics"),
    path("orders/<int:pk>/approval/", views.approval, name="order-approval"),
    path("orders/<int:pk>/reserve/", views.reserve_order_parts, name="order-reserve"),
    path("orders/<int:pk>/complete/", views.complete, name="order-complete"),
    path("orders/<int:pk>/payment/", views.payment, name="order-payment"),
    path("orders/<int:pk>/procurement/", views.procurement_approve, name="order-procurement-approve"),
    path("orders/<int:pk>/unrepairable/", views.order_unrepairable, name="order-unrepairable"),
    path("orders/<int:pk>/unrepairable/close/", views.order_unrepairable_close, name="order-unrepairable-close"),
    path("procurement/", views.procurement_queue, name="procurement-queue"),
    path("stock/", views.stock_list, name="stock-list"),
    path("stock/adjust/", views.stock_adjust, name="stock-adjust"),
    path("stock/part/<int:pk>/edit/", views.part_edit, name="part-edit"),
    path("users/", views.user_list, name="user-list"),
    path("users/new/", views.user_create, name="user-create"),
    path("users/<int:pk>/edit/", views.user_edit, name="user-edit"),
    path("users/<int:pk>/reset-password/", views.user_reset_password, name="user-reset-password"),
    path("profile/", views.profile, name="profile"),
    path("profile/password/", views.profile_password, name="profile-password"),
]
