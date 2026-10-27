from django.db.models import F, Sum
from decimal import Decimal, ROUND_HALF_UP

from .models import Product


class OrderTotalError(Exception):
    """Raised when the order total cannot be calculated reliably."""
    pass


def calculate_order_totals(request):
    """
    Calculates the order total the same way for an authenticated user
    (from the database) and a guest (from the session), then applies
    shipping. This value is the one complete_payment() checks the PayPal
    response against, so it has to fail loudly, not silently, if the
    input is bad.
    """
    cart_type = 'authenticated' if request.user.is_authenticated else 'session'
    raw_total = Decimal('0.00')

    if request.user.is_authenticated:
        cart = request.user.carts.first()
        if cart:
            db_total = cart.items.aggregate(
                total=Sum(F('product__price') * F('quantity'))
            )['total'] or Decimal('0.00')
            raw_total = Decimal(db_total)
    else:
        # Guest carts are keyed by product id and quantity only. The
        # price is never read from the session, a stored session price
        # only reflects what the product cost when it was added to the
        # cart, and could be hours or days stale by checkout. Pricing
        # a guest cart off a stale number is the same trust mistake as
        # trusting a client-supplied amount, it just arrives indirectly
        # instead of in the payment request itself.
        session_cart = request.session.get('cart', {'items': []})
        product_ids = [item.get('id') for item in session_cart.get('items', []) if item.get('id')]
        live_prices = {
            p.id: p.price for p in Product.objects.filter(id__in=product_ids)
        }

        for item in session_cart.get('items', []):
            product_id = item.get('id')
            quantity = item.get('quantity')
            if product_id is None or quantity is None:
                continue
            if product_id not in live_prices:
                # Product was removed or deactivated since it was added
                # to the cart. Fail loudly here rather than silently
                # pricing it at zero or skipping it without telling
                # anyone, an empty basket total because every item
                # quietly vanished is a worse bug than this error.
                raise OrderTotalError(
                    f"Product {product_id} in cart is no longer available"
                )
            try:
                raw_total += live_prices[product_id] * int(quantity)
            except (ValueError, TypeError, ArithmeticError) as exc:
                raise OrderTotalError(
                    f"Bad cart item in session: {item!r}"
                ) from exc

    base_amount = raw_total.quantize(Decimal('0.00'), rounding=ROUND_HALF_UP)

    if base_amount == Decimal('0.00'):
        total_amount = Decimal('0.00')
        shipping_cost = Decimal('0.00')
        shipping_message = "N/A"
    elif base_amount < Decimal('80.00'):
        shipping_cost = Decimal('10.00')
        shipping_message = "$10.00"
        total_amount = base_amount + shipping_cost
    else:
        shipping_cost = Decimal('0.00')
        shipping_message = "Free Shipping"
        total_amount = base_amount

    return {
        'base_amount': base_amount,
        'shipping_cost': shipping_cost,
        'shipping_message': shipping_message,
        'total_amount': total_amount,
        'cart_type': cart_type,
    }
