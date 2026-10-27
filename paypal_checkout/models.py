"""
Reference models for the PayPal integration. Minimal but complete enough
to run migrations against, which the original file was not (it referenced
Category and MinValueValidator without importing or defining either).
"""
from django.db import models
from django.contrib.auth.models import User
from django.conf import settings
from django.core.validators import MinValueValidator
from django_countries.fields import CountryField  # pip install django-countries


class Category(models.Model):
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, unique=True, blank=True, null=True)

    class Meta:
        verbose_name_plural = "Categories"

    def __str__(self):
        return self.name


class Product(models.Model):
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=250, null=True, blank=True)
    description = models.TextField(null=True, blank=True, verbose_name="Description")
    image = models.ImageField(upload_to='products_images/', verbose_name="First Image")
    image_1 = models.ImageField(upload_to='products_images/', verbose_name="Second Image", blank=True, null=True)
    image_2 = models.ImageField(upload_to='products_images/', verbose_name="Third Image", blank=True, null=True)
    categories = models.ManyToManyField(Category, related_name='products', verbose_name="Categories")
    quantity = models.PositiveIntegerField(default=1, verbose_name="Stock Quantity")
    price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(0)],
        verbose_name="Price",
        db_index=True,
    )
    created_at = models.DateTimeField(auto_now_add=True, blank=True, null=True)

    def __str__(self):
        return self.name


class Cart(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        null=True, blank=True, related_name="carts"
    )
    session_key = models.CharField(max_length=255, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def get_total_cost(self):
        return sum(item.quantity * item.product.price for item in self.items.all())

    def __str__(self):
        return f"Cart ({'User: ' + str(self.user) if self.user else 'Session: ' + str(self.session_key)})"


class CartItem(models.Model):
    cart = models.ForeignKey(Cart, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    quantity = models.PositiveIntegerField(default=1)
    added_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.product.name} x {self.quantity}"

    @property
    def total_price(self):
        return self.product.price * self.quantity


class Order(models.Model):
    STATUS_CHOICES = (
        ('P', 'Pending (Order Confirmed)'),
        ('S', 'Shipped (Order Shipped)'),
        ('C', 'Completed (Delivered)'),
        ('CXL', 'Cancelled (Order Cancelled)'),
    )
    PAYMENT_STATUS_CHOICES = (
        ('Pending', 'Pending'),
        ('Paid', 'Paid'),
        ('Failed', 'Failed'),
    )

    user = models.ForeignKey(User, on_delete=models.CASCADE, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    status = models.CharField(max_length=3, choices=STATUS_CHOICES, default='P')
    total_price = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)

    # Renamed from braintree_transaction_id (leftover from a different
    # payment provider) to paypal_order_id, and made unique so the same
    # PayPal order can never be used to create a second Order. unique=True
    # on a nullable field still allows multiple NULLs in Postgres/SQLite,
    # so pending/failed attempts with no ID yet are unaffected.
    paypal_order_id = models.CharField(max_length=80, blank=True, null=True, unique=True, db_index=True)

    payment_status = models.CharField(max_length=10, choices=PAYMENT_STATUS_CHOICES, default='Pending')

    full_name = models.CharField(max_length=100, verbose_name="Full Name", blank=True, null=True)
    email = models.EmailField(max_length=100, verbose_name="Email Address", blank=True, null=True)
    phone = models.CharField(max_length=15, verbose_name="Phone Number", blank=True, null=True)
    country = CountryField(verbose_name="Country", blank=True, null=True)
    city = models.CharField(max_length=255, verbose_name="City", blank=True, null=True)
    state = models.CharField(max_length=255, verbose_name="State", blank=True, null=True)
    street = models.CharField(max_length=255, verbose_name="Street", blank=True, null=True)
    zipcode = models.CharField(max_length=20, verbose_name="Zip/Postal code", blank=True, null=True)
    tracking_number = models.CharField(max_length=255, null=True, blank=True)
    tracking_url = models.URLField(null=True, blank=True)

    def __str__(self):
        return f"Order #{self.id}"


class OrderItem(models.Model):
    order = models.ForeignKey(Order, related_name='items', on_delete=models.CASCADE)
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    quantity = models.PositiveIntegerField()
    price = models.DecimalField(max_digits=10, decimal_places=2)

    def __str__(self):
        return f"{self.product.name} (x{self.quantity})"


class Shipping(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="shipping", blank=True, null=True)
    full_name = models.CharField(max_length=80, verbose_name="Full Name")
    email = models.EmailField(max_length=100, verbose_name="Email Address")
    phone = models.CharField(max_length=15, verbose_name="Phone Number")
    profile_photo = models.ImageField(upload_to="profile_pics/", blank=True, null=True, verbose_name="Profile Photo")
    country = CountryField(verbose_name="Country")
    city = models.CharField(max_length=80, verbose_name="City")
    state = models.CharField(max_length=80, verbose_name="State", null=True, blank=True)
    street = models.CharField(max_length=255, verbose_name="Street")
    zipcode = models.CharField(max_length=20, verbose_name="Zip/Postal code", blank=True, null=True)

    class Meta:
        verbose_name_plural = "Shipping Address"

    def __str__(self):
        return f"Shipping Address - {self.full_name if self.full_name else self.user.username}"
