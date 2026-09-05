"""One theme for the site. The per-member accent colour, chosen with a colour
picker on the profile page, went with the dark mode it was designed against."""
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [('accounts', '0010_backfill_league_role')]
    operations = [migrations.RemoveField(model_name='profile', name='theme')]
