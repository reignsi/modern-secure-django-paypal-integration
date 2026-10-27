# Rename this file to the next number in your app's migrations/
# folder, e.g. migrations/0002_rename_paypal_order_id.py, and set
# the dependency below to your actual previous migration.
#
# This is written as two steps on purpose:
#
#   1. RenameField first, with no uniqueness yet. If any existing rows
#      already have duplicate values in this column (which v1 allowed,
#      since there was no constraint), adding unique=True in the same
#      step would fail the migration outright with no clear indication
#      of which rows are the problem.
#
#   2. AlterField second, adding unique=True, only once you've
#      confirmed there are no duplicates (see the check below).
#
# Before running this against a production database with existing
# orders, run the duplicate check first:
#
#   from yourapp.models import Order
#   from django.db.models import Count
#   dupes = (Order.objects
#            .exclude(braintree_transaction_id__isnull=True)
#            .values('braintree_transaction_id')
#            .annotate(c=Count('id'))
#            .filter(c__gt=1))
#   list(dupes)
#
# If that returns anything, those are the replay cases this whole fix
# exists to prevent. Decide manually which row per group is the real
# order before this migration runs, since the unique constraint will
# otherwise simply fail to apply.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('yourapp', '0001_initial'),  # <- replace with your actual dependency
    ]

    operations = [
        migrations.RenameField(
            model_name='order',
            old_name='braintree_transaction_id',
            new_name='paypal_order_id',
        ),
        migrations.AlterField(
            model_name='order',
            name='paypal_order_id',
            field=models.CharField(
                max_length=80, blank=True, null=True, unique=True, db_index=True,
            ),
        ),
    ]
