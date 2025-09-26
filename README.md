# Django PayPal Secure Payment Integration

A robust, secure, and production-ready PayPal payment integration for Django applications that supports both authenticated users and guest checkout.

![Django](https://img.shields.io/badge/Django-4.2%2B-green)
![PayPal](https://img.shields.io/badge/PayPal-API-blue)
![Security](https://img.shields.io/badge/Security-A%2B-brightgreen)

## 🚀 Overview

This Django application provides a complete PayPal payment integration solution with advanced security features, comprehensive error handling, and support for both sandbox and live environments. The implementation follows PayPal's best practices and includes protection against common payment vulnerabilities.

## ✨ Key Features

### 🔒 Security-First Approach
- **CSRF Protection** - All payment endpoints are protected against Cross-Site Request Forgery
- **Payment Amount Verification** - Ensures captured amount matches expected order total
- **Order Status Validation** - Verifies PayPal order completion status
- **Atomic Transactions** - Database operations are wrapped in atomic transactions
- **Input Validation** - Comprehensive validation of all incoming data

### 💳 Payment Processing
- **Dual Environment Support** - Seamless switching between Sandbox and Live modes
- **Guest Checkout** - Support for both authenticated users and guest customers
- **Real-time Amount Verification** - Prevents payment tampering by comparing amounts
- **Comprehensive Error Handling** - Detailed logging and error reporting

### 🛡️ Advanced Protection
- **Cross-Origin Policy Management** - Proper headers for PayPal JavaScript integration
- **Database Integrity** - Prevents partial updates with atomic transactions
- **Email Notifications** - Admin alerts for payment processing issues
- **Session Management** - Secure handling of guest user data

## 📋 Prerequisites

- Django 4.2+
- Python 3.8+
- PayPal Business Account
- SSL Certificate (for production)

## ⚙️ Installation & Configuration

### 1. Add to Your Django Project

Copy the provided `views.py` to your Django app and ensure all required models are implemented.

### 2. Configure PayPal Settings

Add the following to your `settings.py`:

```python
# PayPal Configuration
if settings.DEBUG:
    PAYPAL_OAUTH_URL = "https://api-m.sandbox.paypal.com/v1/oauth2/token"
    PAYPAL_ORDERS_API_URL = "https://api-m.sandbox.paypal.com/v2/checkout/orders"
    PAYPAL_CLIENT_ID = "your_paypal_sandbox_client_id"
    PAYPAL_SECRET = "your_paypal_sandbox_client_secret"
else:
    PAYPAL_OAUTH_URL = "https://api-m.paypal.com/v1/oauth2/token"
    PAYPAL_ORDERS_API_URL = "https://api-m.paypal.com/v2/checkout/orders"
    PAYPAL_CLIENT_ID = "your_paypal_live_client_id"
    PAYPAL_SECRET = "your_paypal_live_client_secret"
```

### 3. URL Configuration

Add the payment endpoint to your `urls.py`:

```python
from django.urls import path
from . import views

urlpatterns = [
    path('complete-payment/', views.complete_payment, name='complete_payment'),
    # ... other URLs
]
```

## 🏗️ Model Structure (Optional Reference)

The integration works with these core models (customize as needed):

```python
# models.py (Reference Implementation)
class Order(models.Model):
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    total_price = models.DecimalField(max_digits=10, decimal_places=2)
    braintree_transaction_id = models.CharField(max_length=100, unique=True)
    payment_status = models.CharField(max_length=20, choices=PAYMENT_STATUS)
    # ... shipping fields

class Cart(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    items = models.ManyToManyField('Product', through='CartItem')
    
class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE)
    product = models.ForeignKey('Product', on_delete=models.CASCADE)
    quantity = models.PositiveIntegerField()
    price = models.DecimalField(max_digits=10, decimal_places=2)

class Shipping(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    full_name = models.CharField(max_length=100)
    email = models.EmailField()
    # ... address fields
```

## 🔧 Implementation Guide

### Payment View Setup

```python
def payment(request):
    totals = calculate_order_totals(request)
    
    # Determine if PayPal JS should be loaded
    load_paypal_js = False
    if request.user.is_authenticated:
        try:
            cart = Cart.objects.get(user=request.user)
            load_paypal_js = cart.items.exists() and hasattr(request.user, 'shipping')
        except Cart.DoesNotExist:
            load_paypal_js = False
    else:
        # Guest user logic
        load_paypal_js = bool(
            request.session.get('cart', {}).get('items') and 
            request.session.get('guest_shipping')
        )
    
    context = {
        "totals": totals,
        "PAYPAL_CLIENT_ID": settings.PAYPAL_CLIENT_ID,
        "load_paypal_js": load_paypal_js,
        # ... other context variables
    }
    
    response = render(request, "payment/payment.html", context)
    
    # Essential for PayPal popup functionality
    if load_paypal_js:
        response['Cross-Origin-Opener-Policy'] = 'same-origin-allow-popups'
    
    return response
```

### Frontend Integration

Checkout the payment.htm file for reference

## 🔒 Security Features

### 1. Payment Verification
```python
def verify_paypal_order(order_id, request):
    """
    Securely verify PayPal order details to prevent payment tampering
    """
    # OAuth token retrieval with timeout
    # Order status validation
    # Amount comparison between PayPal and local calculation
```

### 2. Atomic Transactions
```python
@transaction.atomic
def complete_payment(request):
    """
    Ensures database integrity during payment processing
    """
    # All database operations succeed or fail together
```

### 3. Comprehensive Validation
- Order ID length and format validation
- Amount matching between multiple sources
- User authentication state handling
- Guest shipping information completeness

## 🚨 Error Handling & Logging

The implementation includes robust error handling:

```python
try:
    # Payment processing logic
except Cart.DoesNotExist:
    logger.error("Cart not found for user %s", user_email)
    return JsonResponse({"error": "Cart not found"}, status=404)
except Exception as e:
    logger.exception("Payment processing error: %s", e)
    return JsonResponse({"error": "Payment processing failed"}, status=500)
```

## 📊 Response Handling

### Success Response
```json
{
    "success": true
}
```

### Error Responses
```json
{
    "error": "Invalid order ID"
}
```

```json
{
    "error": "Payment not completed"
}
```

```json
{
    "error": "Paid amount does not match order total"
}
```

## 🌐 Cross-Origin Policy

For proper PayPal popup functionality, ensure your payment view includes:

```python
response['Cross-Origin-Opener-Policy'] = 'same-origin-allow-popups'
```

## 🔄 Flow Diagram

```
User Checkout → PayPal Authorization → Order Verification → 
Amount Validation → Database Update → Success Response
```

## 🧪 Testing

### Sandbox Testing
1. Set `DEBUG = True` in your settings
2. Use PayPal sandbox credentials
3. Test with sandbox buyer accounts

### Production Checklist
- [ ] SSL certificate installed
- [ ] Live PayPal credentials configured
- [ ] Error email notifications tested
- [ ] Cross-browser compatibility verified
- [ ] Mobile responsiveness confirmed

## 📈 Monitoring & Analytics

Implement additional monitoring by extending the `user_error_send_email` function:

```python
def user_error_send_email(user, error):
    # Extend with your preferred monitoring service
    # Sentry, Loggly, or custom analytics
    print(f"Payment Error for {user}: {error}")
```

## 🤝 Contributing

Feel free to submit issues and enhancement requests! Please ensure all security considerations are maintained.

## 📄 License

This project is licensed under the MIT License 

---

**Note**: This implementation provides a solid foundation but should be customized based on your specific business requirements and security policies. Always conduct thorough security testing before deploying to production.

For questions or support, please open an issue in the GitHub repository.