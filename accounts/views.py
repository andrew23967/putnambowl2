from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login, authenticate, logout
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.models import User
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from .forms import RegisterForm, ProfileForm
from main.models import SiteSettings, SeasonRecord


def register(request):
    if request.user.is_authenticated:
        return redirect('main:home', week=1)
    form = RegisterForm(request.POST or None)
    if form.is_valid():
        user = form.save()
        login(request, user)
        return redirect('main:home', week=1)
    return render(request, 'accounts/register.html', {'form': form})


def login_view(request):
    if request.user.is_authenticated:
        return redirect('main:home', week=1)
    form = AuthenticationForm(data=request.POST or None)
    if request.method == 'POST' and form.is_valid():
        user = form.get_user()
        login(request, user)
        return redirect('main:home', week=1)
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
        request.user.save()
        messages.success(request, 'Profile updated.')
        return redirect('accounts:user_profile')
    return render(request, 'accounts/user_profile.html', {'form': form})


def public_profile(request, username):
    player = get_object_or_404(User, username=username)
    settings = SiteSettings.get()
    player_seasons = []
    for record in SeasonRecord.objects.all():
        for i, entry in enumerate(record.final_standings):
            if entry.get('username') == player.username:
                player_seasons.append({
                    'year': record.year,
                    'rank': i + 1,
                    'score': entry.get('score', 0),
                    'winner': record.winner_username,
                })
                break
    return render(request, 'accounts/public_profile.html', {
        'player': player,
        'week': settings.week,
        'player_seasons': player_seasons,
    })
