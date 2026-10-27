from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import csrf_protect, csrf_exempt
from django.db import transaction, IntegrityError
from django.shortcuts import render
from django.conf import settings
from django.core.mail import mail_admins
from django.http import JsonResponse, HttpResponseBadRequest
from django.utils.timezone import now
from decimal import Decimal
import requests
import logging
import json

from .models import Order, Cart, OrderItem, Product, Shipping
from .utils import calculate_order_totals, OrderTotalError

"""
settings.py:

if DEBUG:
    PAYPAL_OAUTH_URL = "https://api-m.sandbox.paypal.com/v1/oauth2/token"
    PAYPAL_ORDERS_API_URL = "https://api-m.sandbox.paypal.com/v2/checkout/orders"
    PAYPAL_WEBHOOK_VERIFY_URL = "https://api-m.sandbox.paypal.com/v1/notifications/verify-webhook-signature"
    PAYPAL_CLIENT_ID = "your_paypal_sandbox_client_id"
    PAYPAL_SECRET = "your_paypal_sandbox_client_secret"
else:
    PAYPAL_OAUTH_URL = "https://api-m.paypal.com/v1/oauth2/token"
    PAYPAL_ORDERS_API_URL = "https://api-m.paypal.com/v2/checkout/orders"
    PAYPAL_WEBHOOK_VERIFY_URL = "https://api-m.paypal.com/v1/notifications/verify-webhook-signature"
    PAYPAL_CLIENT_ID = "your_paypal_live_client_id"
    PAYPAL_SECRET = "your_paypal_live_client_secret"

PAYPAL_CURRENCY = "USD"
PAYPAL_WEBHOOK_ID = "your_webhook_id_from_the_paypal_developer_dashboard"
"""

logger = logging.getLogger(__name__)

REQUIRED_GUEST_FIELDS = ['full_name', 'email', 'phone', 'country', 'city', 'street']


def payment(request):
    totals = calculate_order_totals(request)

    load_paypal_js = False
    shipping = None

    if request.user.is_authenticated:
        try:
            cart = Cart.objects.get(user=request.user)
            load_paypal_js = cart.items.exists() and hasattr(request.user, 'shipping')
        except Cart.DoesNotExist:
            load_paypal_js = False
        shipping = getattr(request.user, 'shipping', None)
    else:
        session_cart = request.session.get('cart', {})
        shipping = request.session.get('guest_shipping')
        load_paypal_js = bool(session_cart.get('items') and shipping)

    context = {
        "totals": totals,
        "shipping": shipping,
        "PAYPAL_CLIENT_ID": settings.PAYPAL_CLIENT_ID,
        "load_paypal_js": load_paypal_js,
    }

    response = render(request, "payment.html", context)

    # Required for the PayPal popup under Django 4.0+, which defaults
    # Cross-Origin-Opener-Policy to same-origin. Without this the popup
    # opens, the user approves, and onApprove silently never fires
    # because the window reference gets severed.
    if load_paypal_js:
        response['Cross-Origin-Opener-Policy'] = 'same-origin-allow-popups'

    return response


def _actor_label(request):
    """Short, log-safe identifier for who triggered a request."""
    if request.user.is_authenticated:
        return request.user.email or f"user#{request.user.id}"
    guest_shipping = request.session.get('guest_shipping') or {}
    return guest_shipping.get('email', 'guest (no email on file)')


def notify_admins(subject, message):
    """
    Best-effort admin notification. Payment processing must not fail
    just because the email backend isn't configured.
    """
    try:
        mail_admins(subject, message, fail_silently=True)
    except Exception:
        logger.exception("Failed to send admin notification: %s", subject)


def get_paypal_access_token():
    """
    Shared by create_order, verify_paypal_order, and the webhook
    verifier, so there's one place that talks to PayPal's OAuth
    endpoint instead of three slightly different copies of the same
    request.
    """
    auth_response = requests.post(
        settings.PAYPAL_OAUTH_URL,
        auth=(settings.PAYPAL_CLIENT_ID, settings.PAYPAL_SECRET),
        data={'grant_type': 'client_credentials'},
        timeout=10,
    )
    auth_response.raise_for_status()
    access_token = auth_response.json().get('access_token')
    if not access_token:
        raise ValueError("PayPal did not return an access token")
    return access_token


@require_http_methods(["POST"])
@csrf_protect
def create_order(request):
    """
    Creates the PayPal order server-side, using the same
    calculate_order_totals() the rest of the app trusts, instead of
    letting the browser's JS pass an amount into
    actions.order.create().

    This is the piece v1 was missing: the amount PayPal ever sees now
    comes from the server on both ends of the transaction, not just
    the end that checks it afterward. The browser's only job is to
    call this, get an order ID back, and hand that ID to the PayPal
    buttons to render the approval flow. It never gets to state a
    price.
    """
    try:
        totals = calculate_order_totals(request)
    except OrderTotalError as exc:
        logger.error("create_order: could not calculate total for %s: %s", _actor_label(request), exc)
        return JsonResponse({"error": "Could not calculate order total"}, status=400)

    if totals['total_amount'] <= Decimal('0.00'):
        return JsonResponse({"error": "Cart is empty"}, status=400)

    currency = getattr(settings, 'PAYPAL_CURRENCY', 'USD')

    try:
        access_token = get_paypal_access_token()
    except Exception as exc:
        logger.exception("create_order: PayPal auth failed: %s", exc)
        return JsonResponse({"error": "Could not reach PayPal"}, status=502)

    payload = {
        "intent": "CAPTURE",
        "purchase_units": [{
            "amount": {
                "currency_code": currency,
                "value": str(totals['total_amount']),
            }
        }],
    }

    response = requests.post(
        settings.PAYPAL_ORDERS_API_URL,
        json=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {access_token}",
        },
        timeout=10,
    )

    if response.status_code not in (200, 201):
        logger.error("create_order: PayPal rejected order creation: %s %s", response.status_code, response.text)
        return JsonResponse({"error": "Could not create PayPal order"}, status=502)

    order_data = response.json()
    return JsonResponse({"id": order_data["id"]})


def verify_paypal_order(order_id):
    """Retrieves the order PayPal actually recorded. Raises on failure."""
    access_token = get_paypal_access_token()
    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {access_token}',
    }
    order_response = requests.get(
        f'{settings.PAYPAL_ORDERS_API_URL}/{order_id}',
        headers=headers,
        timeout=10,
    )
    order_response.raise_for_status()
    return order_response.json()


def _build_local_order(order_id, expected_amount, request=None, user=None,
                        guest_shipping=None, session_cart_items=None,
                        cart=None):
    """
    Creates the Order, OrderItem rows, and clears whichever cart was
    used, all inside one transaction. Shared by complete_payment()
    (the normal path, triggered by the browser) and paypal_webhook()
    (the backstop path, triggered by PayPal directly if the browser
    never checks back in).

    Returns the created Order. Raises ValueError or Cart.DoesNotExist
    on anything that should abort the transaction.
    """
    with transaction.atomic():
        if user is not None:
            cart = cart or Cart.objects.select_for_update().get(user=user)
            shipping = user.shipping

            order = Order.objects.create(
                user=user,
                total_price=expected_amount,
                paypal_order_id=order_id,
                payment_status="Paid",
                full_name=shipping.full_name,
                email=shipping.email,
                phone=shipping.phone,
                country=shipping.country,
                city=shipping.city,
                state=shipping.state,
                street=shipping.street,
                zipcode=shipping.zipcode,
            )

            items = list(cart.items.select_related('product'))
            OrderItem.objects.bulk_create([
                OrderItem(order=order, product=item.product, quantity=item.quantity, price=item.product.price)
                for item in items
            ])
            cart.items.all().delete()

        else:
            if not guest_shipping or not all(f in guest_shipping for f in REQUIRED_GUEST_FIELDS):
                raise ValueError("Missing shipping information for guest checkout")

            order = Order.objects.create(
                user=None,
                total_price=expected_amount,
                paypal_order_id=order_id,
                payment_status="Paid",
                full_name=guest_shipping['full_name'],
                email=guest_shipping['email'],
                phone=guest_shipping['phone'],
                country=guest_shipping['country'],
                city=guest_shipping['city'],
                state=guest_shipping.get('state', ''),
                street=guest_shipping['street'],
                zipcode=guest_shipping.get('zipcode', ''),
            )

            order_items = []
            for item in (session_cart_items or []):
                try:
                    product = Product.objects.get(id=item['id'])
                except Product.DoesNotExist:
                    raise ValueError(f"Product {item['id']} not found")
                # product.price here, not item['price']. The session
                # never held a trustworthy price to begin with (see
                # utils.calculate_order_totals), the order record has
                # to reflect what was actually charged, which is the
                # live price calculate_order_totals just summed, not
                # whatever number happened to be sitting in the
                # session cart.
                order_items.append(OrderItem(
                    order=order, product=product,
                    quantity=item['quantity'], price=product.price,
                ))
            OrderItem.objects.bulk_create(order_items)

            if request is not None:
                request.session['guest_order'] = {
                    'date': now().strftime("%Y-%m-%d %H:%M:%S"),
                    'total_items': len(order_items),
                    'total': float(expected_amount),
                    'status': "Pending (Order Confirmed)",
                    **guest_shipping,
                }
                request.session['cart'] = {'items': [], 'total': 0}

        return order


@require_http_methods(["POST"])
@csrf_protect
def complete_payment(request):
    """
    Verifies a PayPal order server-side and, if it checks out, creates
    the local Order. Three things specifically guarded against:

    1. Replay: the same order_id submitted twice must not create two
       local orders.
    2. Currency: amount.value alone is not a meaningful comparison
       without currency_code.
    3. Partial failure: order creation, order items, and cart clearing
       either all happen or none do.
    """
    order_id = request.POST.get("orderID")
    if not order_id or len(order_id) > 80:
        return JsonResponse({"error": "Invalid order ID"}, status=400)

    actor = _actor_label(request)

    if Order.objects.filter(paypal_order_id=order_id).exists():
        logger.warning("Rejected re-submission of already-processed PayPal order %s", order_id)
        return JsonResponse({"error": "This payment has already been processed"}, status=409)

    try:
        totals = calculate_order_totals(request)
    except OrderTotalError as exc:
        logger.error("Could not calculate order total for %s: %s", actor, exc)
        return JsonResponse({"error": "Could not calculate order total"}, status=400)

    expected_amount = totals['total_amount']
    expected_currency = getattr(settings, 'PAYPAL_CURRENCY', 'USD')

    try:
        order_data = verify_paypal_order(order_id)
    except Exception as exc:
        logger.exception("Error verifying PayPal order %s for %s: %s", order_id, actor, exc)
        notify_admins("PayPal verification failed", f"Could not verify PayPal order {order_id} for {actor}.\n{exc}")
        return JsonResponse({"error": "Could not verify payment with PayPal"}, status=502)

    if order_data.get("status") != "COMPLETED":
        logger.warning("Order %s not completed; status: %s", order_id, order_data.get("status"))
        notify_admins("PayPal order not completed", f"Order {order_id} for {actor} has status {order_data.get('status')}.")
        return JsonResponse({"error": "Payment not completed"}, status=400)

    purchase_units = order_data.get("purchase_units", [])
    if not purchase_units:
        logger.error("No purchase units in order data for order %s", order_id)
        return JsonResponse({"error": "Order data incomplete"}, status=400)

    unit = purchase_units[0]
    ordered_amount_block = unit.get("amount", {})
    ordered_amount = Decimal(ordered_amount_block.get("value", "0"))
    ordered_currency = ordered_amount_block.get("currency_code")

    captures = unit.get("payments", {}).get("captures", [])
    if not captures:
        logger.error("No capture data found for order %s", order_id)
        return JsonResponse({"error": "Payment capture not found"}, status=400)

    captured_amount_block = captures[0].get("amount", {})
    captured_amount = Decimal(captured_amount_block.get("value", "0"))
    captured_currency = captured_amount_block.get("currency_code")

    if (
        captured_amount != expected_amount
        or ordered_amount != expected_amount
        or captured_currency != expected_currency
        or ordered_currency != expected_currency
    ):
        logger.warning(
            "Amount/currency mismatch for order %s. Captured: %s %s, Ordered: %s %s, Expected: %s %s",
            order_id, captured_amount, captured_currency, ordered_amount, ordered_currency,
            expected_amount, expected_currency,
        )
        notify_admins(
            "PayPal amount mismatch",
            f"Order {order_id} for {actor}: captured {captured_amount} {captured_currency}, "
            f"ordered {ordered_amount} {ordered_currency}, expected {expected_amount} {expected_currency}.",
        )
        return JsonResponse({"error": "Paid amount does not match order total"}, status=400)

    guest_shipping = request.session.get('guest_shipping', {}) if not request.user.is_authenticated else None
    session_cart_items = request.session.get('cart', {}).get('items', []) if not request.user.is_authenticated else None

    try:
        order = _build_local_order(
            order_id=order_id,
            expected_amount=expected_amount,
            request=request,
            user=request.user if request.user.is_authenticated else None,
            guest_shipping=guest_shipping,
            session_cart_items=session_cart_items,
        )
    except IntegrityError:
        # The read-then-write check above isn't atomic by itself; this
        # is the actual backstop if two requests for the same order_id
        # land close enough together to both pass that check.
        logger.warning("Race on order_id %s, already processed by a concurrent request", order_id)
        return JsonResponse({"error": "This payment has already been processed"}, status=409)
    except (ValueError, Cart.DoesNotExist) as exc:
        logger.error("Order build failed for %s: %s", order_id, exc)
        notify_admins("Order build failed", f"Order {order_id} for {actor}: {exc}")
        return JsonResponse({"error": str(exc)}, status=400)
    except Exception as exc:
        logger.exception("Unexpected error completing order %s for %s: %s", order_id, actor, exc)
        notify_admins("Unexpected payment error", f"Order {order_id} for {actor}: {exc}")
        return JsonResponse({"error": "Payment processing failed"}, status=500)

    return JsonResponse({"success": True})


@csrf_exempt
@require_http_methods(["POST"])
def paypal_webhook(request):
    """
    Backstop for the case complete_payment() can't cover: PayPal
    captures the payment, but the browser never calls back (tab
    closed, connection dropped, JS error on the client). Without this,
    that's a completed PayPal payment with no local Order, discovered
    only when a customer emails asking where their order is.

    This verifies the webhook signature with PayPal (never trust a
    POST just because it claims to be from PayPal), and on
    PAYMENT.CAPTURE.COMPLETED, builds the local order only if one
    doesn't already exist for that order_id. In the normal case
    complete_payment() already created it and this is a no-op. In the
    dropped-connection case, this is what actually creates the order.

    Point PayPal's webhook configuration (Developer Dashboard > Apps >
    Webhooks) at this URL, subscribed to PAYMENT.CAPTURE.COMPLETED.
    """
    body = request.body
    headers = request.headers

    try:
        event = json.loads(body)
    except json.JSONDecodeError:
        return HttpResponseBadRequest("Invalid JSON")

    try:
        access_token = get_paypal_access_token()
    except Exception:
        logger.exception("Webhook: could not get PayPal access token to verify signature")
        return JsonResponse({"error": "Verification unavailable"}, status=503)

    verify_payload = {
        "transmission_id": headers.get("Paypal-Transmission-Id"),
        "transmission_time": headers.get("Paypal-Transmission-Time"),
        "cert_url": headers.get("Paypal-Cert-Url"),
        "auth_algo": headers.get("Paypal-Auth-Algo"),
        "transmission_sig": headers.get("Paypal-Transmission-Sig"),
        "webhook_id": settings.PAYPAL_WEBHOOK_ID,
        "webhook_event": event,
    }

    verify_response = requests.post(
        settings.PAYPAL_WEBHOOK_VERIFY_URL,
        json=verify_payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {access_token}",
        },
        timeout=10,
    )

    if verify_response.status_code != 200 or verify_response.json().get("verification_status") != "SUCCESS":
        logger.warning("Webhook signature verification failed: %s", verify_response.text)
        return JsonResponse({"error": "Invalid signature"}, status=400)

    event_type = event.get("event_type")

    if event_type != "PAYMENT.CAPTURE.COMPLETED":
        # Acknowledge anything we're not acting on so PayPal stops retrying it.
        return JsonResponse({"received": True})

    resource = event.get("resource", {})
    supplementary = resource.get("supplementary_data", {}).get("related_ids", {})
    order_id = supplementary.get("order_id")

    if not order_id:
        logger.error("Webhook PAYMENT.CAPTURE.COMPLETED had no order_id: %s", resource)
        return JsonResponse({"received": True})

    if Order.objects.filter(paypal_order_id=order_id).exists():
        # Normal case: complete_payment() already handled this.
        return JsonResponse({"received": True})

    # The browser never called complete_payment() for this order, but
    # PayPal confirms the capture happened. This is the dropped
    # connection case. Pull the authoritative order data, the same
    # function complete_payment() uses, and build from that, not from
    # anything in this webhook payload itself, since the webhook body
    # is a notification, not the source of truth.
    logger.warning("Webhook recovering order %s with no matching local Order", order_id)

    try:
        order_data = verify_paypal_order(order_id)
    except Exception:
        logger.exception("Webhook: could not re-verify order %s", order_id)
        return JsonResponse({"error": "Could not verify order"}, status=502)

    if order_data.get("status") != "COMPLETED":
        return JsonResponse({"received": True})

    purchase_units = order_data.get("purchase_units", [])
    if not purchase_units:
        return JsonResponse({"received": True})

    captures = purchase_units[0].get("payments", {}).get("captures", [])
    if not captures:
        return JsonResponse({"received": True})

    captured_amount = Decimal(captures[0].get("amount", {}).get("value", "0"))

    # There is no logged-in user and no session cart available from a
    # webhook request, there's no HTTP session behind it. This
    # recreates the order shell and the correct paid amount, but not
    # line items or shipping details it has no way to know. It logs
    # loudly and notifies admins to reconcile manually. This is a
    # backstop against losing the payment record entirely, not a
    # silent, fully automatic recovery.
    try:
        order, created = Order.objects.get_or_create(
            paypal_order_id=order_id,
            defaults={
                "total_price": captured_amount,
                "payment_status": "Paid",
                "status": "P",
            },
        )
    except IntegrityError:
        return JsonResponse({"received": True})

    if created:
        notify_admins(
            "Order recovered via webhook, needs manual reconciliation",
            f"Order {order_id} had no matching local Order when PAYMENT.CAPTURE.COMPLETED "
            f"arrived. A placeholder Order (id={order.id}) was created for {captured_amount}. "
            f"No cart, shipping, or line items are attached, the browser never completed "
            f"the normal flow for this payment. Check PayPal's transaction details and "
            f"follow up with the customer.",
        )

    return JsonResponse({"received": True})
