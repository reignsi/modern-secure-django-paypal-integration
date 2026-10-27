# Django PayPal Secure Payment Integration

A robust, secure, and production-ready PayPal payment integration for Django applications that supports both authenticated users and guest checkout.

![Django](https://img.shields.io/badge/Django-4.2%2B-green) ![PayPal](https://img.shields.io/badge/PayPal-API-blue) ![Security](https://img.shields.io/badge/Security-A%2B-brightgreen)

## 🚀 Overview

This Django application provides a complete PayPal payment integration with server-side order creation, idempotent payment processing, currency-aware amount verification, and a webhook-based reconciliation backstop. The client never states a price at any point in the flow, every amount PayPal sees originates on the server.

## ✨ Key Features

### 🔒 Security-First Approach

- **Server-Side Order Creation** — the client never supplies a payment amount. `create_order` builds the PayPal order using a server-calculated total; the browser only receives an order ID to approve.
- **Idempotent by Design** — a unique database constraint on the PayPal order ID makes duplicate order creation impossible, not just unlikely, backed by an application-level check for a clean error response.
- **Currency-Aware Verification** — amount comparisons check `currency_code` alongside the numeric value, on both the order and the capture.
- **CSRF Protection** — all payment endpoints are protected against Cross-Site Request Forgery.
- **Webhook Reconciliation** — a signature-verified PayPal webhook listener recovers payments that captured successfully but never received a client-side confirmation (dropped connection, closed tab).
- **Atomic Transactions** — order creation, order items, and cart clearing happen inside one database transaction, with row-level locking on the cart.

### 💳 Payment Processing

- **Dual Environment Support** — seamless switching between Sandbox and Live modes.
- **Guest Checkout** — supports both authenticated users and guest customers, with guest cart items priced from the live database at checkout time rather than a stored session value.
- **Comprehensive Error Handling** — structured logging and admin email alerts on verification failures, mismatches, and recovered orders.

### 🛡️ Advanced Protection

- **Cross-Origin Policy Management** — correct headers for the PayPal JS popup under Django 4.0+'s default `Cross-Origin-Opener-Policy`.
- **Database Integrity** — a unique, indexed `paypal_order_id` field prevents the same PayPal payment from ever being attached to two local orders.
- **Email Notifications** — admin alerts for mismatches, verification failures, and webhook-recovered orders needing manual reconciliation.
- **Session Management** — secure handling of guest shipping and cart data.

## 📋 Prerequisites

- Django 4.2+
- Python 3.8+
- PayPal Business Account (works for personal accounts too)
- SSL certificate (for production, required for PayPal webhooks)

## ⚙️ Installation & Configuration

### 1. Add to Your Django Project

Copy `views.py`, `utils.py`, and `models.py` into your app, and `payment.html` into your templates directory. Make sure the `Order`, `OrderItem`, `Cart`, `CartItem`, `Product`, and `Shipping` models match what's in `models.py` (or adapt the field names in `views.py` to your own models).

### 2. Configure PayPal Settings

Add the following to your `settings.py` (use environment variables for all credentials, never commit them):

```python
if DEBUG:
    PAYPAL_OAUTH_URL = "https://api-m.sandbox.paypal.com/v1/oauth2/token"
    PAYPAL_ORDERS_API_URL = "https://api-m.sandbox.paypal.com/v2/checkout/orders"
    PAYPAL_WEBHOOK_VERIFY_URL = "https://api-m.sandbox.paypal.com/v1/notifications/verify-webhook-signature"
    PAYPAL_CLIENT_ID = os.environ["PAYPAL_SANDBOX_CLIENT_ID"]
    PAYPAL_SECRET = os.environ["PAYPAL_SANDBOX_SECRET"]
    PAYPAL_WEBHOOK_ID = os.environ["PAYPAL_SANDBOX_WEBHOOK_ID"]
else:
    PAYPAL_OAUTH_URL = "https://api-m.paypal.com/v1/oauth2/token"
    PAYPAL_ORDERS_API_URL = "https://api-m.paypal.com/v2/checkout/orders"
    PAYPAL_WEBHOOK_VERIFY_URL = "https://api-m.paypal.com/v1/notifications/verify-webhook-signature"
    PAYPAL_CLIENT_ID = os.environ["PAYPAL_LIVE_CLIENT_ID"]
    PAYPAL_SECRET = os.environ["PAYPAL_LIVE_SECRET"]
    PAYPAL_WEBHOOK_ID = os.environ["PAYPAL_LIVE_WEBHOOK_ID"]

PAYPAL_CURRENCY = "USD"  # defaults to USD if omitted

# Required for admin alert emails (mismatches, verification failures, recovered orders)
ADMINS = [("Your Name", "you@example.com")]
EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
```

### 3. URL Configuration

```python
from django.urls import path
from . import views

urlpatterns = [
    path('payment/', views.payment, name='payment'),
    path('create-order/', views.create_order, name='create_order'),
    path('complete-payment/', views.complete_payment, name='complete_payment'),
    path('paypal-webhook/', views.paypal_webhook, name='paypal_webhook'),
]
```

### 4. Run Migrations

`Order.paypal_order_id` is a unique, indexed field. If you're adding this to an existing project with an existing orders table, check for duplicate values in your equivalent column before migrating:

```python
from django.db.models import Count
Order.objects.values('paypal_order_id').annotate(c=Count('id')).filter(c__gt=1, paypal_order_id__isnull=False)
```

Resolve any duplicates manually, then run `makemigrations` and `migrate` as usual.

### 5. Configure the PayPal Webhook

In the [PayPal Developer Dashboard](https://developer.paypal.com) → your app → Webhooks:

1. Add webhook URL: `https://your-domain.com/paypal-webhook/`
2. Subscribe to `PAYMENT.CAPTURE.COMPLETED`
3. Copy the generated Webhook ID into `PAYPAL_WEBHOOK_ID`

This endpoint requires a publicly reachable HTTPS URL, it will not work against `localhost`. Use the PayPal sandbox and a tool like ngrok for local testing.

## 🏗️ Model Structure (Reference)

See `models.py` for the full reference models: `Product`, `Category`, `Cart`, `CartItem`, `Order`, `OrderItem`, and `Shipping`. The field to note is `Order.paypal_order_id`, unique and indexed, this is what makes duplicate order creation from the same PayPal payment impossible at the database level, not just checked for at the application level.

## 🔧 Implementation Guide

### Checkout Flow

```
Client requests checkout
        ↓
create_order()  →  server calculates total, creates PayPal order with that amount
        ↓
Client approves via PayPal popup (nothing to tamper with, no amount was sent to the client)
        ↓
complete_payment()  →  re-verifies the captured order and amount+currency against PayPal directly
        ↓
Order created atomically, cart cleared
        ↓
(backstop) paypal_webhook()  →  if complete_payment never ran for a completed capture, recovers it
```

### Payment View Setup

```python
def payment(request):
    totals = calculate_order_totals(request)

    load_paypal_js = False
    if request.user.is_authenticated:
        try:
            cart = Cart.objects.get(user=request.user)
            load_paypal_js = cart.items.exists() and hasattr(request.user, 'shipping')
        except Cart.DoesNotExist:
            load_paypal_js = False
    else:
        load_paypal_js = bool(
            request.session.get('cart', {}).get('items') and
            request.session.get('guest_shipping')
        )

    context = {
        "totals": totals,
        "PAYPAL_CLIENT_ID": settings.PAYPAL_CLIENT_ID,
        "load_paypal_js": load_paypal_js,
    }

    response = render(request, "payment.html", context)

    if load_paypal_js:
        response['Cross-Origin-Opener-Policy'] = 'same-origin-allow-popups'

    return response
```

### Frontend Integration

See `payment.html`. The key difference from a typical PayPal Buttons integration: `createOrder` calls the server's `/create-order/` endpoint and uses the returned order ID, it does not call `actions.order.create()` with a client-side amount.

```javascript
createOrder: function (data, actions) {
  return fetch("/create-order/", { method: "POST", headers: { "X-CSRFToken": csrftoken } })
    .then(res => res.json())
    .then(orderData => orderData.id);
}
```

## 🔒 Security Features

### 1. Server-Side Order Creation

```python
def create_order(request):
    """
    The amount PayPal ever sees comes from the server's own
    calculation, both when the order is created and again when it's
    verified. The client supplies no price at any point.
    """
    totals = calculate_order_totals(request)
    payload = {
        "intent": "CAPTURE",
        "purchase_units": [{
            "amount": {"currency_code": currency, "value": str(totals['total_amount'])}
        }],
    }
    # ... create order with PayPal, return its ID
```

### 2. Idempotent Payment Processing

```python
# Application-level check, for a fast, clean error response
if Order.objects.filter(paypal_order_id=order_id).exists():
    return JsonResponse({"error": "This payment has already been processed"}, status=409)

# Database-level guarantee, the actual backstop against a read/write race
class Order(models.Model):
    paypal_order_id = models.CharField(max_length=80, unique=True, db_index=True, null=True, blank=True)
```

### 3. Currency-Aware Amount Verification

```python
if (
    captured_amount != expected_amount
    or ordered_amount != expected_amount
    or captured_currency != expected_currency
    or ordered_currency != expected_currency
):
    return JsonResponse({"error": "Paid amount does not match order total"}, status=400)
```

### 4. Atomic Transactions

```python
with transaction.atomic():
    cart = Cart.objects.select_for_update().get(user=user)
    order = Order.objects.create(...)
    OrderItem.objects.bulk_create(...)
    cart.items.all().delete()
```

### 5. Webhook Reconciliation

```python
def paypal_webhook(request):
    """
    Backstop for a PayPal capture that completed but whose approval
    never reached complete_payment (dropped connection, closed tab).
    Verifies PayPal's webhook signature, then checks for a matching
    local order. If none exists, re-verifies the order directly with
    PayPal (never trusts the webhook body as the source of truth) and
    builds a recovery order, flagged for manual reconciliation since
    no cart or shipping data is available outside of a browser session.
    """
```

## 🚨 Error Handling & Logging

```python
try:
    # Payment processing logic
except IntegrityError:
    logger.warning("Race on order_id %s, already processed concurrently", order_id)
    return JsonResponse({"error": "This payment has already been processed"}, status=409)
except Exception as exc:
    logger.exception("Unexpected error completing order %s: %s", order_id, exc)
    notify_admins("Unexpected payment error", f"Order {order_id}: {exc}")
    return JsonResponse({"error": "Payment processing failed"}, status=500)
```

## 📊 Response Handling

### Success Response

```json
{ "success": true }
```

### Error Responses

```json
{ "error": "Invalid order ID" }
{ "error": "Payment not completed" }
{ "error": "Paid amount does not match order total" }
{ "error": "This payment has already been processed" }
```

## 🧪 Testing

A full test suite is included in `tests.py`, covering:

- Tampered amount rejection
- Currency mismatch rejection
- Replay protection (application check and database constraint, tested separately)
- Server-side order creation (confirms the amount sent to PayPal matches the server's calculation, not anything client-supplied)
- Webhook signature rejection and orphaned-capture recovery
- Guest cart pricing against live database values

### Sandbox Testing

1. Set `DEBUG = True` and use PayPal sandbox credentials.
2. Run the test suite: adjust the `@patch` targets in `tests.py` to match your app's import path first.
3. Use the PayPal sandbox webhook simulator to send a `PAYMENT.CAPTURE.COMPLETED` event for an order with no matching local order, and confirm the recovery path and admin notification both fire.

### Production Checklist

- [ ] SSL certificate installed
- [ ] Live PayPal credentials configured via environment variables
- [ ] Webhook URL registered in the PayPal dashboard and `PAYPAL_WEBHOOK_ID` set
- [ ] Admin email notifications tested
- [ ] Duplicate check run against existing data before migrating, if adding to an existing project

## 🤝 Contributing

Issues and enhancement requests are welcome. Please keep security considerations front and center in any PR, this integration handles real payments.

## 📄 License

This project is licensed under the MIT License.

---

**Note**: This implementation provides a solid foundation but should be customized based on your specific business requirements and security policies. Always conduct thorough security testing against the PayPal sandbox before deploying to production.

For questions or support, please open an issue in the GitHub repository.
