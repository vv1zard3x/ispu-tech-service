from django.urls import path

from . import views

app_name = "repair"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("orders/", views.order_list, name="order-list"),
    path("orders/new/", views.order_create, name="order-create"),
    path("orders/<int:pk>/", views.order_detail, name="order-detail"),
    path("orders/<int:pk>/assign/", views.assign_order, name="order-assign"),
    path("orders/<int:pk>/diagnostics/", views.diagnostics, name="order-diagnostics"),
    path("orders/<int:pk>/approval/", views.approval, name="order-approval"),
    path("orders/<int:pk>/reserve/", views.reserve_order_parts, name="order-reserve"),
    path("orders/<int:pk>/complete/", views.complete, name="order-complete"),
    path("orders/<int:pk>/payment/", views.payment, name="order-payment"),
    path("stock/", views.stock_list, name="stock-list"),
    path("stock/adjust/", views.stock_adjust, name="stock-adjust"),
    path("stock/part/<int:pk>/edit/", views.part_edit, name="part-edit"),
]
