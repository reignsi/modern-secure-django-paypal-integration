# Add to settings.py. Get PAYPAL_WEBHOOK_ID from PayPal Developer
# Dashboard > Apps & Credentials > your app > Webhooks, after you've
# added the webhook URL there (see WEBHOOK-SETUP in SHIPPING.md).

import os

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

PAYPAL_CURRENCY = "USD"

# mail_admins() in views.py needs this to actually send anything.
ADMINS = [("Your Name", "you@example.com")]
EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"  # or your provider's backend
