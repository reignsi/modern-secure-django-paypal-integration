from django.urls import path
from . import views

urlpatterns = [
    path('payment/', views.payment, name='payment'),
    path('create-order/', views.create_order, name='create_order'),
    path('complete-payment/', views.complete_payment, name='complete_payment'),
    path('paypal-webhook/', views.paypal_webhook, name='paypal_webhook'),
]
