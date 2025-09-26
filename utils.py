from django.db.models import F, Sum
from decimal import Decimal, ROUND_HALF_UP

def calculate_order_totals(request):
    cart_type = 'authenticated' if request.user.is_authenticated else 'session'
    raw_total = Decimal('0.00')

    if request.user.is_authenticated:
        # Get the user's ONLY active cart
        cart = request.user.carts.first()  # Simple check
        
        if cart:  # If cart exists
            db_total = cart.items.aggregate(
                total=Sum(F('product__price') * F('quantity'))
            )['total'] or Decimal('0.00')
            raw_total = Decimal(db_total)
    else:
        session_cart = request.session.get('cart', {'items': []})
        raw_total = sum(
            Decimal(item['price']) * item['quantity']
            for item in session_cart['items']
            if 'price' in item and 'quantity' in item
        )

    try:
        # Shipping calculation remains the same
        base_amount = raw_total.quantize(Decimal('0.00'), rounding=ROUND_HALF_UP)
    
        if base_amount < Decimal('80.00'):
            shipping_cost = Decimal('10.00')
            shipping_message = "$10.00"
            if base_amount == Decimal("0.00"):
                total_amount = 0
            else:
                total_amount = base_amount + shipping_cost
        else:
            shipping_cost = Decimal('0.00')
            shipping_message = "Free Shipping"
            total_amount = base_amount

        return {
            'base_amount': base_amount,
            # 'shipping_cost': shipping_cost,
            'shipping_message': shipping_message,
            'total_amount': total_amount,
            'cart_type': cart_type
        }
    except:
        pass
    