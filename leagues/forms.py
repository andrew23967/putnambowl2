from django import forms
from django.contrib.auth.models import User
from django.utils.text import slugify

from .models import League


class LeagueForm(forms.Form):
    name = forms.CharField(max_length=80)
    slug = forms.SlugField(max_length=40, required=False,
                           help_text='Leave blank to derive it from the name.')
    is_active = forms.BooleanField(required=False, initial=True)

    def __init__(self, *args, instance=None, **kwargs):
        self.instance = instance
        super().__init__(*args, **kwargs)
        if instance is not None:
            self.fields['name'].initial = instance.name
            self.fields['slug'].initial = instance.slug
            self.fields['is_active'].initial = instance.is_active

    def clean_slug(self):
        slug = self.cleaned_data.get('slug') or slugify(self.cleaned_data.get('name', ''))
        if not slug:
            raise forms.ValidationError('A slug is required.')
        qs = League.objects.filter(slug=slug)
        if self.instance is not None:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError(f'"{slug}" is already taken.')
        return slug


class ManagerForm(forms.Form):
    """Create a manager account for a league. All three fields or none."""
    username = forms.CharField(max_length=150, required=False)
    email = forms.EmailField(required=False)
    password = forms.CharField(required=False, min_length=8, widget=forms.PasswordInput)

    def clean(self):
        data = super().clean()
        username = (data.get('username') or '').strip()
        if not username:
            return data
        if User.objects.filter(username=username).exists():
            raise forms.ValidationError(f'Username "{username}" is already taken.')
        if not data.get('password'):
            raise forms.ValidationError('A password is required for a new manager.')
        data['username'] = username
        return data

    @property
    def wants_account(self):
        return bool(self.cleaned_data.get('username'))
