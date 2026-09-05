from django.shortcuts import render, redirect
from django.contrib.auth import login, logout
from django.contrib.auth.forms import AuthenticationForm
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from .forms import RegisterForm, ProfileForm


def register(request):
    if request.user.is_authenticated:
        return redirect('main:home')
    form = RegisterForm(request.POST or None)
    if form.is_valid():
        user = form.save()
        login(request, user)
        return redirect('main:home')
    return render(request, 'accounts/register.html', {'form': form})


def login_view(request):
    if request.user.is_authenticated:
        return redirect('main:home')
    form = AuthenticationForm(data=request.POST or None)
    if request.method == 'POST' and form.is_valid():
        user = form.get_user()
        login(request, user)
        return redirect('main:home')
    return render(request, 'accounts/login.html', {'form': form})


def logout_view(request):
    logout(request)
    return redirect('accounts:login')


@login_required
def user_profile(request):
    form = ProfileForm(request.user, request.POST or None)
    if form.is_valid():
        request.user.profile.real_name = form.cleaned_data['real_name']
        request.user.email = form.cleaned_data['email']
        request.user.profile.favorite_team = form.cleaned_data['favorite_team']
        request.user.profile.bio = form.cleaned_data['bio']
        request.user.profile.theme = form.cleaned_data['theme']
        # Both rows, explicitly. A post_save signal used to re-save the profile
        # whenever the user saved, which hid every place that forgot to.
        request.user.save()
        request.user.profile.save()
        messages.success(request, 'Profile updated.')
        return redirect('accounts:user_profile')
    return render(request, 'accounts/user_profile.html', {'form': form})
