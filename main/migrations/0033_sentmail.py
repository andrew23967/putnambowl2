from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('leagues', '0002_default_league'),
        ('main', '0032_tick_every_minute'),
    ]

    operations = [
        migrations.CreateModel(
            name='SentMail',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('batch', models.CharField(blank=True, default='', max_length=120)),
                ('kind', models.CharField(blank=True, default='', max_length=20)),
                ('subject', models.CharField(max_length=200)),
                ('to_address', models.CharField(max_length=254)),
                ('ok', models.BooleanField(default=False)),
                ('detail', models.CharField(blank=True, default='', max_length=200)),
                ('transport', models.CharField(blank=True, default='', max_length=10)),
                ('sent_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('league', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='sent_mail', to='leagues.league')),
            ],
            options={
                'ordering': ['-sent_at', '-id'],
            },
        ),
    ]
