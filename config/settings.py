import sys

import environ
import dj_database_url
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(DEBUG=(bool, True))
environ.Env.read_env(BASE_DIR / '.env')

SECRET_KEY = env('SECRET_KEY', default='dev-only-insecure-key-change-in-production')
DEBUG = env('DEBUG')
ALLOWED_HOSTS = env.list('ALLOWED_HOSTS', default=['*'])
CSRF_TRUSTED_ORIGINS = env.list('CSRF_TRUSTED_ORIGINS', default=[])

# ── Security (production only) ──────────────────────────────────────────────
# Applied whenever DEBUG is off, so local development is unaffected.
if not DEBUG:
    # Railway terminates TLS at its edge and forwards X-Forwarded-Proto. Without
    # this Django thinks every request is plain HTTP, which breaks is_secure()
    # and would make SECURE_SSL_REDIRECT loop forever.
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    X_FRAME_OPTIONS = 'DENY'
    # HSTS is deliberately not enabled here. It is sticky in browsers for the
    # full max-age and cannot be called back, so it should be a separate,
    # considered decision rather than a side effect of turning DEBUG off.

    if SECRET_KEY.startswith('dev-only-'):
        raise ImproperlyConfigured(
            'SECRET_KEY is still the public development default while DEBUG is '
            'off. Anyone with the repository could forge session cookies. Set a '
            'real SECRET_KEY environment variable.'
        )

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'whitenoise.runserver_nostatic',
    'django.contrib.staticfiles',
    'accounts',
    'main',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'main.context_processors.site_settings',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'

DATABASES = {
    'default': dj_database_url.config(
        default=f'sqlite:///{BASE_DIR}/db.sqlite3',
        conn_max_age=600,
    )
}

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'America/New_York'
USE_I18N = True
USE_TZ = True

STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

LOGIN_URL = '/login/'
LOGIN_REDIRECT_URL = '/'
LOGOUT_REDIRECT_URL = '/login/'

# ── Email (Resend) ──────────────────────────────────────────────────────────
RESEND_API_KEY = env('RESEND_API_KEY', default='')
RESEND_FROM = env('RESEND_FROM', default='onboarding@resend.dev')
SITE_URL = env('SITE_URL', default='http://localhost:8000')
GEMINI_API_KEY = env('GEMINI_API_KEY', default='')

# ── Inbound email (the Emails feed) ─────────────────────────────────────────
# Mail the commissioner sends to the league, copied to this mailbox, is polled by
# the worker and published on the site. Unset host/user/password disables it
# entirely — nothing is ingested and nothing errors. See main/inbound_email.py.
IMAP_HOST = env('IMAP_HOST', default='')
IMAP_PORT = env.int('IMAP_PORT', default=993)
IMAP_USER = env('IMAP_USER', default='')
IMAP_PASSWORD = env('IMAP_PASSWORD', default='')
IMAP_FOLDER = env('IMAP_FOLDER', default='INBOX')
IMAP_MARK_SEEN = env('IMAP_MARK_SEEN', default='true')
# Gmail delivers user+tag@gmail.com to user@gmail.com and keeps the tag in the
# headers, which gives one mailbox two purposes without a mailing list: mail to
# the plain address from someone allowed to publish is a league announcement,
# mail to the +tag address is a pick submission. Ballots set Reply-To to the
# tagged address, so replying to one submits picks even for the commissioner —
# whose announcements would otherwise be published, picks and all.
PICKS_ADDRESS_TAG = env('PICKS_ADDRESS_TAG', default='picks')
# Only turn this off against a local test mailbox. With it off, a forged From
# header is enough to publish to the home page.
INBOUND_REQUIRE_AUTH = env('INBOUND_REQUIRE_AUTH', default='true')

# ── Outbound via the league mailbox (SMTP) ──────────────────────────────────
# Preferred over Resend whenever it is configured. The same Google app password
# used for IMAP works for SMTP, so this needs no extra credential — and unlike
# Resend's sandbox sender, which only delivers to the account owner until a domain
# is verified, Gmail reaches the whole league immediately. A reply to a member
# also comes *from* the address they wrote to, so it threads in their client.
SMTP_HOST = env('SMTP_HOST', default='smtp.gmail.com')
SMTP_PORT = env.int('SMTP_PORT', default=587)
SMTP_USER = env('SMTP_USER', default='') or IMAP_USER
SMTP_PASSWORD = env('SMTP_PASSWORD', default='') or IMAP_PASSWORD

# This project sends through Resend and smtplib directly rather than Django's mail
# framework, so the test runner's locmem backend does not protect it. Without this
# flag the suite really did deliver mail to its own fixture addresses the moment
# SMTP was configured. See email_utils.outbound_suppressed().
TESTING = 'test' in sys.argv
