"""
Replace "yourapp" in the @patch targets below with your actual app
label before running these (wherever complete_payment lives, e.g.
"store.views.requests.get").

Tests for complete_payment(). PayPal itself is mocked throughout, no
real network calls are made. Each test maps to one specific thing the
original implementation got wrong:

  test_rejects_tampered_amount        -> amount check existed, kept as a baseline
  test_rejects_currency_mismatch      -> currency_code was never checked
  test_replay_is_rejected             -> same order_id could create two orders
  test_db_constraint_blocks_duplicate -> the application-level replay
                                          check has a race window; the
                                          unique constraint is the real
                                          backstop
  test_successful_payment_creates_order -> happy path end to end
"""
import json
from decimal import Decimal
from unittest.mock import patch, MagicMock

from django.contrib.auth.models import User, AnonymousUser
from django.db import IntegrityError
from django.test import TestCase, Client, RequestFactory
from django.urls import reverse

from .models import Product, Cart, CartItem, Shipping, Order


def mock_paypal_response(status="COMPLETED", amount="90.00", currency="USD",
                          captured_amount=None, captured_currency=None):
    """
    Builds a fake PayPal Orders API response. By default the captured
    amount/currency mirror the ordered ones; tests that need them to
    differ pass captured_amount / captured_currency explicitly.
    """
    if captured_amount is None:
        captured_amount = amount
    if captured_currency is None:
        captured_currency = currency

    return {
        "status": status,
        "purchase_units": [{
            "amount": {"value": amount, "currency_code": currency},
            "payments": {
                "captures": [{
                    "amount": {"value": captured_amount, "currency_code": captured_currency}
                }]
            },
        }],
    }


class CompletePaymentTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username="buyer", password="x", email="buyer@example.com")
        self.client.force_login(self.user)

        self.product = Product.objects.create(name="Widget", price=Decimal("90.00"), quantity=10)
        self.cart = Cart.objects.create(user=self.user)
        CartItem.objects.create(cart=self.cart, product=self.product, quantity=1)
        Shipping.objects.create(
            user=self.user, full_name="A Buyer", email="buyer@example.com",
            phone="0000000000", country="US", city="Metropolis", street="1 Main St",
        )
        self.url = reverse('complete_payment')

    def _post(self, order_id="ORDER123"):
        return self.client.post(self.url, {"orderID": order_id})

    @patch("yourapp.views.requests.get")
    @patch("yourapp.views.requests.post")
    def test_rejects_tampered_amount(self, mock_auth, mock_get):
        mock_auth.return_value = MagicMock(
            status_code=200, json=lambda: {"access_token": "t"}, raise_for_status=lambda: None
        )
        # Cart total is 90.00; PayPal is told to report 1.00 instead.
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: mock_paypal_response(amount="1.00"),
            raise_for_status=lambda: None,
        )
        response = self._post()
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Order.objects.count(), 0)

    @patch("yourapp.views.requests.get")
    @patch("yourapp.views.requests.post")
    def test_rejects_currency_mismatch(self, mock_auth, mock_get):
        mock_auth.return_value = MagicMock(
            status_code=200, json=lambda: {"access_token": "t"}, raise_for_status=lambda: None
        )
        # Same numeric amount, wrong currency. This is the check the
        # original code was missing entirely.
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: mock_paypal_response(amount="90.00", currency="EUR"),
            raise_for_status=lambda: None,
        )
        response = self._post()
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Order.objects.count(), 0)

    @patch("yourapp.views.requests.get")
    @patch("yourapp.views.requests.post")
    def test_successful_payment_creates_order(self, mock_auth, mock_get):
        mock_auth.return_value = MagicMock(
            status_code=200, json=lambda: {"access_token": "t"}, raise_for_status=lambda: None
        )
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: mock_paypal_response(amount="90.00", currency="USD"),
            raise_for_status=lambda: None,
        )
        response = self._post(order_id="ORDER_OK")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["success"])

        order = Order.objects.get(paypal_order_id="ORDER_OK")
        self.assertEqual(order.total_price, Decimal("90.00"))
        self.assertEqual(order.items.count(), 1)
        self.assertEqual(self.cart.items.count(), 0)  # cart was cleared

    @patch("yourapp.views.requests.get")
    @patch("yourapp.views.requests.post")
    def test_replay_is_rejected(self, mock_auth, mock_get):
        mock_auth.return_value = MagicMock(
            status_code=200, json=lambda: {"access_token": "t"}, raise_for_status=lambda: None
        )
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: mock_paypal_response(amount="90.00", currency="USD"),
            raise_for_status=lambda: None,
        )

        first = self._post(order_id="ORDER_REPLAY")
        self.assertEqual(first.status_code, 200)
        self.assertEqual(Order.objects.filter(paypal_order_id="ORDER_REPLAY").count(), 1)

        # Re-add an item to cart to simulate the browser re-submitting
        # the same approved order (refresh, double-click, or deliberate
        # replay), and send the exact same order_id again.
        CartItem.objects.create(cart=self.cart, product=self.product, quantity=1)
        second = self._post(order_id="ORDER_REPLAY")

        self.assertEqual(second.status_code, 409)
        self.assertEqual(Order.objects.filter(paypal_order_id="ORDER_REPLAY").count(), 1)

    def test_db_constraint_blocks_duplicate(self):
        """
        The application-level check in complete_payment() reads, then
        later writes, which is not atomic on its own. This confirms
        the real backstop: the database will not allow a second Order
        with the same paypal_order_id even if that read/write race is
        hit, because IntegrityError isn't optional, it's enforced by
        the column itself.
        """
        Order.objects.create(user=self.user, total_price=Decimal("90.00"), paypal_order_id="DUPLICATE_ID")
        with self.assertRaises(IntegrityError):
            Order.objects.create(user=self.user, total_price=Decimal("90.00"), paypal_order_id="DUPLICATE_ID")


class GuestCartPricingTests(TestCase):
    """
    calculate_order_totals() must price a guest cart from the current
    database price, not whatever was in the session when the item was
    added. These simulate a price change happening after add-to-cart
    but before checkout.
    """
    def setUp(self):
        self.product = Product.objects.create(name="Widget", price=Decimal("90.00"), quantity=10)

    def test_stale_session_price_is_ignored(self):
        from .utils import calculate_order_totals
        request = RequestFactory().get('/')
        request.user = AnonymousUser()
        request.session = self.client.session
        # Session says 1.00, as if added to the cart before a price
        # change, or tampered with directly. The database says 90.00.
        request.session['cart'] = {'items': [{'id': self.product.id, 'quantity': 1, 'price': '1.00'}]}
        request.session.save()

        totals = calculate_order_totals(request)
        self.assertEqual(totals['base_amount'], Decimal("90.00"))

    def test_removed_product_raises_instead_of_silently_dropping(self):
        from .utils import calculate_order_totals, OrderTotalError
        request = RequestFactory().get('/')
        request.user = AnonymousUser()
        request.session = self.client.session
        request.session['cart'] = {'items': [{'id': 999999, 'quantity': 1, 'price': '10.00'}]}
        request.session.save()

        with self.assertRaises(OrderTotalError):
            calculate_order_totals(request)


class CreateOrderTests(TestCase):
    """
    create_order() is what replaced the client-side
    actions.order.create() call. These confirm the amount sent to
    PayPal comes from the server's own calculation, with nothing in
    the request for a client to override.
    """
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username="buyer2", password="x", email="buyer2@example.com")
        self.client.force_login(self.user)
        self.product = Product.objects.create(name="Widget", price=Decimal("90.00"), quantity=10)
        self.cart = Cart.objects.create(user=self.user)
        CartItem.objects.create(cart=self.cart, product=self.product, quantity=1)
        Shipping.objects.create(
            user=self.user, full_name="A Buyer", email="buyer2@example.com",
            phone="0000000000", country="US", city="Metropolis", street="1 Main St",
        )
        self.url = reverse('create_order')

    @patch("yourapp.views.requests.post")
    def test_order_created_with_server_calculated_amount(self, mock_post):
        auth_resp = MagicMock(status_code=200, json=lambda: {"access_token": "t"}, raise_for_status=lambda: None)
        order_resp = MagicMock(status_code=201, json=lambda: {"id": "PAYPAL_NEW_ORDER"})
        mock_post.side_effect = [auth_resp, order_resp]

        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["id"], "PAYPAL_NEW_ORDER")

        # The second call to requests.post is the one that actually
        # creates the PayPal order, confirm the amount in that
        # payload matches the server's own total (90.00), there is no
        # path by which a client-supplied number reaches this call.
        _, kwargs = mock_post.call_args_list[1]
        sent_amount = kwargs["json"]["purchase_units"][0]["amount"]["value"]
        self.assertEqual(sent_amount, "90.00")

    def test_rejects_empty_cart(self):
        self.cart.items.all().delete()
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 400)


class PaypalWebhookTests(TestCase):
    """
    paypal_webhook() is the backstop for a payment PayPal captured but
    the browser never confirmed back to complete_payment(). These
    cover: a bad signature is rejected outright, and a verified
    capture with no matching local Order gets recovered rather than
    silently lost.
    """
    def setUp(self):
        self.client = Client()
        self.url = reverse('paypal_webhook')
        self.event_body = json.dumps({
            "event_type": "PAYMENT.CAPTURE.COMPLETED",
            "resource": {
                "supplementary_data": {"related_ids": {"order_id": "ORPHANED_ORDER"}},
                "amount": {"value": "90.00", "currency_code": "USD"},
            },
        })

    @patch("yourapp.views.requests.post")
    def test_rejects_unverified_signature(self, mock_post):
        auth_resp = MagicMock(status_code=200, json=lambda: {"access_token": "t"}, raise_for_status=lambda: None)
        verify_resp = MagicMock(status_code=200, json=lambda: {"verification_status": "FAILURE"})
        mock_post.side_effect = [auth_resp, verify_resp]

        response = self.client.post(self.url, data=self.event_body, content_type="application/json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Order.objects.count(), 0)

    @patch("yourapp.views.requests.get")
    @patch("yourapp.views.requests.post")
    def test_recovers_orphaned_capture(self, mock_post, mock_get):
        auth_resp = MagicMock(status_code=200, json=lambda: {"access_token": "t"}, raise_for_status=lambda: None)
        verify_resp = MagicMock(status_code=200, json=lambda: {"verification_status": "SUCCESS"})
        mock_post.side_effect = [auth_resp, verify_resp, auth_resp]  # auth, verify, then auth again inside verify_paypal_order

        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: mock_paypal_response(amount="90.00", currency="USD"),
            raise_for_status=lambda: None,
        )

        response = self.client.post(self.url, data=self.event_body, content_type="application/json")
        self.assertEqual(response.status_code, 200)

        order = Order.objects.get(paypal_order_id="ORPHANED_ORDER")
        self.assertEqual(order.total_price, Decimal("90.00"))

    @patch("yourapp.views.requests.post")
    def test_does_nothing_if_order_already_exists(self, mock_post):
        Order.objects.create(total_price=Decimal("90.00"), paypal_order_id="ORPHANED_ORDER")

        auth_resp = MagicMock(status_code=200, json=lambda: {"access_token": "t"}, raise_for_status=lambda: None)
        verify_resp = MagicMock(status_code=200, json=lambda: {"verification_status": "SUCCESS"})
        mock_post.side_effect = [auth_resp, verify_resp]

        response = self.client.post(self.url, data=self.event_body, content_type="application/json")
        self.assertEqual(response.status_code, 200)
        # Still exactly one order, the webhook did not create a second one.
        self.assertEqual(Order.objects.filter(paypal_order_id="ORPHANED_ORDER").count(), 1)
