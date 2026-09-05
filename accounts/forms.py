from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from main.teams import TEAMS


class RegisterForm(UserCreationForm):
    email = forms.EmailField(required=True)
    # The league to join. Every account belongs to one, so an account cannot be
    # created without a valid code - there is no default league to fall into.
    join_code = forms.CharField(max_length=12)

    class Meta:
        model = User
        fields = ('username', 'email', 'password1', 'password2')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.league = None

    def clean_join_code(self):
        from leagues.models import League
        code = (self.cleaned_data.get('join_code') or '').strip().upper()
        league = League.objects.filter(join_code=code, is_active=True).first()
        if league is None:
            raise forms.ValidationError('That join code does not match a league.')
        self.league = league
        return code

    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data['email']
        if commit:
            user.save()
        return user


class ProfileForm(forms.Form):
    real_name = forms.CharField(max_length=50, required=False)
    email = forms.EmailField(max_length=200)
    favorite_team = forms.ChoiceField(choices=TEAMS)
    bio = forms.CharField(widget=forms.Textarea(attrs={'rows': 4}), required=False, max_length=300)
    email_weekly = forms.BooleanField(required=False)
    email_reminder = forms.BooleanField(required=False)

    def __init__(self, user, *args, **kwargs):
        super().__init__(*args, **kwargs)
        p = user.profile
        self.fields['real_name'].initial = p.real_name
        self.fields['email'].initial = user.email
        self.fields['favorite_team'].initial = p.favorite_team
        self.fields['bio'].initial = p.bio
        self.fields['email_weekly'].initial = p.email_weekly
        self.fields['email_reminder'].initial = p.email_reminder
