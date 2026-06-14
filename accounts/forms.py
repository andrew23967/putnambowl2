from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from main.teams import TEAMS


class RegisterForm(UserCreationForm):
    email = forms.EmailField(required=True)

    class Meta:
        model = User
        fields = ('username', 'email', 'password1', 'password2')

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
    bio = forms.CharField(widget=forms.Textarea(attrs={'rows': 4}), required=False)
    theme = forms.CharField(widget=forms.TextInput(attrs={'type': 'color'}))

    def __init__(self, user, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['real_name'].initial = user.profile.real_name
        self.fields['email'].initial = user.email
        self.fields['favorite_team'].initial = user.profile.favorite_team
        self.fields['bio'].initial = user.profile.bio
        self.fields['theme'].initial = user.profile.theme
