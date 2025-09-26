from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import csrf_protect
from django.db import transaction

from django.shortcuts import render#, redirect
from django.conf import settings
from django.http import JsonResponse
from django.utils.timezone import now
from decimal import Decimal
import requests, logging
    
from .models import Order, Cart, OrderItem, Product, Shipping
from .util import calculate_order_totals

"""
Add this in your settings.py:

if DEBUG:
    PAYPAL_OAUTH_URL = "https://api-m.sandbox.paypal.com/v1/oauth2/token"
    PAYPAL_ORDERS_API_URL = "https://api-m.sandbox.paypal.com/v2/checkout/orders"

    PAYPAL_CLIENT_ID = "your_paypal_sandbox_client_id"
    PAYPAL_SECRET = "your_paypal_sandbox_client_secret"
else:
    PAYPAL_OAUTH_URL = "https://api-m.paypal.com/v1/oauth2/token"
    PAYPAL_ORDERS_API_URL = "https://api-m.paypal.com/v2/checkout/orders"
    
    PAYPAL_CLIENT_ID = "your_paypal_live_client_id"
    PAYPAL_SECRET = "your_paypal_live_client_secret"
"""


"""
# Eg how your payment home view

# For your payment view do not forget to add PAYPAL_CLIENT_ID context variable which retrive your paypal client id from settings
def payment(request):
    totals = calculate_order_totals(request)
    ###############
    load_paypal_js = None
    if request.user.is_authenticated:
        user = request.user

        try:
            # Optional, load the paypal js and view if there's 
            cart = Cart.objects.get(user=user)
            load_paypal_js = cart.items.exists() and hasattr(user, 'shipping')
        except Cart.DoesNotExist:
            load_paypal_js = False

        shipping = Shipping.objects.get(user=request.user)
        context = {
            "totals": totals,
            "cart": Cart.objects.get(user=request.user) if request.user.is_authenticated else None,
            "PAYPAL_CLIENT_ID": settings.PAYPAL_CLIENT_ID,
            "load_paypal_js": load_paypal_js,
            "shipping": shipping if shipping  else None
        }
    else:
        try:
            shipping = request.session.get("guest_shipping")
            if request.session.get("cart")['items'] and shipping:
                load_paypal_js = True
        except:
            shipping = None
            load_paypal_js = False
        context = {"totals": totals, "shipping": shipping,
                   "PAYPAL_CLIENT_ID": settings.PAYPAL_CLIENT_ID,
                   "load_paypal_js": load_paypal_js}
    #######
    response = render(request, "store/cart.html", context)
    if load_paypal_js:
        response['Cross-Origin-Opener-Policy'] = 'same-origin-allow-popups'
    return response

"""


"""
!!! Don't forget to add this lines below in your payment view to avoid Cross Origin issues with your browser

context = {}
response = render(request, "payment.html", context)
response['Cross-Origin-Opener-Policy'] = 'same-origin-allow-popups'
return response
"""

logger = logging.getLogger(__name__)

def user_error_send_email(user, error):
    # Send mail to admin (optional)
    print("There\' an error placing an order for {user}.\n Error: {error}")

def verify_paypal_order(order_id, request):
    """
    This view is to verify the order the user made to aviod payment tempering.
    Retrieve PayPal order details using the Orders API.
    """
    try:
        # Obtain OAuth token from PayPal
        auth_response = requests.post(
            settings.PAYPAL_OAUTH_URL,
            auth=(settings.PAYPAL_CLIENT_ID, settings.PAYPAL_SECRET),
            data={'grant_type': 'client_credentials'},
            timeout=10
        )
        auth_response.raise_for_status()
        access_token = auth_response.json().get('access_token')
        if not access_token:
            raise Exception("Access token not returned")
        
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {access_token}'
        }
        # Retrieve order details from PayPal Orders API
        order_response = requests.get(
            f'{settings.PAYPAL_ORDERS_API_URL}/{order_id}',
            headers=headers,
            timeout=10
        )
        order_response.raise_for_status()
        return order_response.json()
    except Exception as e:
        logger.exception("Error verifying PayPal order: %s", e)
        raise


@require_http_methods(["POST"])
@csrf_protect
@transaction.atomic
def complete_payment(request):
    """
    This is the main view for the payment, You will make an ajax request to this view

    Complete payment processing by verifying PayPal order details,
    comparing captured amounts with the expected total, and (optional) creating order for your model.
    
    -------------------------------------------------------------------------
    Don't forget In case you want only authenticated users:
    if not request.user.is_authenticated:
         return JsonResponse({"error": "Authentication required"}, status=401)
    """
    try:
        order_id = request.POST.get("orderID")
        if not order_id or len(order_id) > 80:
            return JsonResponse({"error": "Invalid order ID"}, status=400)
        
        # Calculate expected total from cart
        totals = calculate_order_totals(request)
        expected_amount = totals['total_amount']
        
        # Verify order details from PayPal
        order_data = None
        try:
            order_data = verify_paypal_order(order_id, request)
        except Exception as e:
            user_error_send_email(user_email, e)
            
        # user_error_send_email(request.user.email, f"Getting paypal order_data: {order_data}. \n\n {order_data.get('status')}")
        if order_data.get("status") != "COMPLETED":
            user_error_send_email(user_email, f"Order not complated; status {order_data.get('status')}")
            logger.warning("Order %s not completed; status: %s", order_id, order_data.get("status"))
            return JsonResponse({"error": "Payment not completed"}, status=400)
        
        # Extract amount from the order details
        purchase_units = order_data.get("purchase_units", [])
        if not purchase_units:
            user_error_send_email(user_email, f"No purchase units in order data for order {order_id}")
            logger.error("No purchase units in order data for order %s", order_id)
            return JsonResponse({"error": "Order data incomplete"}, status=400)
        
        # Get the ordered amount from the purchase unit
        ordered_amount = Decimal(purchase_units[0].get("amount", {}).get("value", "0"))
        
        # Extract the captured amount (from the first capture)
        payments = purchase_units[0].get("payments", {})
        captures = payments.get("captures", [])
        if not captures:
            user_error_send_email(user_email, f"No capture data found for order {order_id}")
            logger.error("No capture data found for order %s", order_id)
            return JsonResponse({"error": "Payment capture not found"}, status=400)
        captured_amount = Decimal(captures[0].get("amount", {}).get("value", "0"))
        
        # Compare the captured amount and ordered amount with the expected amount
        if captured_amount != expected_amount or ordered_amount != expected_amount:
            logger.warning(
                "Amount mismatch for order %s. Captured: %s, Ordered: %s, Expected: %s",
                order_id, captured_amount, ordered_amount, expected_amount
            )
            user_error_send_email(user_email, f"Amount mismatch for order: Amount {captured_amount}, Order amount: {ordered_amount}, Expected: {expected_amount}")
            return JsonResponse({"error": "Paid amount does not match order total"}, status=400)
        
        if request.user.is_authenticated:
            cart = Cart.objects.select_for_update().get(user=request.user)
            shipping = request.user.shipping
        else:
            # Retrieve guest cart and shipping from session
            session_cart = request.session.get('cart', {'items': [], 'total': 0})
            guest_shipping = request.session.get('guest_shipping', {})
            
            # Validate required guest shipping data
            required_fields = ['full_name', 'email', 'phone', 'country', 'city', 'street']
            if not all(field in guest_shipping for field in required_fields):
                return JsonResponse({"error": "Missing shipping information for guest checkout"}, status=400)

"""
        Optional powerful features to add.

        # Create order record for your model
        order = Order.objects.create(
            user=request.user if request.user.is_authenticated else None,
            total_price=totals['total_amount'],
            braintree_transaction_id=order_id,
            payment_status="Paid",
            # Shipping details from appropriate source
            **({
                'full_name': shipping.full_name,
                'email': shipping.email,
                'phone': shipping.phone,
                'country': shipping.country,
                'city': shipping.city,
                'state': shipping.state,
                'street': shipping.street,
                'zipcode': shipping.zipcode,
            } if request.user.is_authenticated else {
                'full_name': guest_shipping['full_name'],
                'email': guest_shipping['email'],
                'phone': guest_shipping['phone'],
                'country': guest_shipping['country'],
                'city': guest_shipping['city'],
                'state': guest_shipping.get('state', ''),  # Optional field
                'street': guest_shipping['street'],
                'zipcode': guest_shipping.get('zipcode', '')#guest_shipping['zipcode'],
            })
        )

        # Create order items for your models
        if request.user.is_authenticated:
            items = cart.items.select_related('product')
            OrderItem.objects.bulk_create([
                OrderItem(
                    order=order,
                    product=item.product,
                    quantity=item.quantity,
                    price=item.product.price
                ) for item in items
            ])
            total_products = items.count()
            # Clear the cart items
            cart.items.all().delete()
        else:
            # Create order items from session cart
            order_items = []
            for item in session_cart['items']:
                try:
                    product = Product.objects.get(id=item['id'])
                    order_items.append(OrderItem(
                        order=order,
                        product=product,
                        quantity=item['quantity'],
                        price=item['price']
                    ))
                except Product.DoesNotExist:
                    user_error_send_email(user_email, f"Product {item['id']} not found\n(Error): {e}")
                    return JsonResponse({'error': f"Product {item['id']} not found"}, status=400)
            
            OrderItem.objects.bulk_create(order_items)
            total_products = len(session_cart['items'])
            
            # ================================
            # Create a guest order for the session user
            request.session['guest_order'] = {
                'date': now().strftime("%Y-%m-%d %H:%M:%S"),
                'total_items': total_products,
                'total': float(totals['total_amount']),
                'status': "Pending (Order Confirmed)",
                
                'full_name': guest_shipping['full_name'],
                'email': guest_shipping['email'],
                'phone': guest_shipping['phone'],
                'country': guest_shipping['country'],
                'city': guest_shipping['city'],
                'state': guest_shipping.get('state', ''),
                'street': guest_shipping['street'],
                'zipcode': guest_shipping.get('zipcode', '')
            }

        """

        return JsonResponse({
            "success": True,
        })
        
    except Cart.DoesNotExist:
        logger.error("Cart not found for user %s", user_email)
        return JsonResponse({"error": "Cart not found"}, status=404)
    except Exception as e:
        logger.exception("Payment processing error: %s", e)
        return JsonResponse({"error": "Payment processing failed"}, status=500)
   