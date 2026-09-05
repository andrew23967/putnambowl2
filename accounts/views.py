from django.shortcuts import render, redirect
from django.contrib.auth import login, logout
from django.contrib.auth.forms import AuthenticationForm
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from .forms import RegisterForm, ProfileForm


def register(request, code=None):
    """Create an account in the league whose join code was given.

    `/join/<code>/` pre-fills the code so an invite link needs no typing.
    """
    from leagues.models import League

    if request.user.is_authenticated:
        return redirect('main:home')
    initial = {'join_code': code} if code else None
    form = RegisterForm(request.POST or None, initial=initial)
    if form.is_valid():
        user = form.save()
        user.profile.league = form.league
        user.profile.role = 'member'
        user.profile.save(update_fields=['league', 'role'])
        login(request, user)
        return redirect('main:home')
    join_league = League.objects.filter(join_code=code, is_active=True).first() if code else None
    return render(request, 'accounts/register.html', {
        'form': form, 'join_code': code or '', 'join_league': join_league,
    })


def _safe_next(request):
    from django.utils.http import url_has_allowed_host_and_scheme
    nxt = request.POST.get('next') or request.GET.get('next') or ''
    if nxt and url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}):
        return nxt
    return None


def _after_login(request, user):
    # A superuser with no league of their own belongs on the site admin.
    profile = getattr(user, 'profile', None)
    if user.is_superuser and (profile is None or profile.league_id is None):
        return redirect('leagues:index')
    return redirect(_safe_next(request) or 'main:home')


def login_view(request):
    if request.user.is_authenticated:
        return _after_login(request, request.user)
    form = AuthenticationForm(request, data=request.POST or None)
    if request.method == 'POST' and form.is_valid():
        user = form.get_user()
        login(request, user)
        return _after_login(request, user)
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
