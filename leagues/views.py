"""The site admin: create leagues and decide who manages them.

Separate from the league dashboards on purpose. A league manager runs one
league; whoever runs the site creates leagues and hands them over. Both are
ordinary Django accounts - the difference is `is_superuser`.
"""
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.models import User
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render

from .access import superuser_required
from .forms import LeagueForm, ManagerForm
from .models import League


def login_view(request):
    if request.user.is_authenticated and request.user.is_superuser:
        return redirect('leagues:index')
    form = AuthenticationForm(request, data=request.POST or None)
    if request.method == 'POST' and form.is_valid():
        user = form.get_user()
        if not user.is_superuser:
            form.add_error(None, 'That account is not a site admin.')
        else:
            login(request, user)
            return redirect('leagues:index')
    return render(request, 'leagues/login.html', {'form': form})


@superuser_required
def index(request):
    from main.models import LeagueSettings
    rows = []
    for league in League.objects.all():
        settings = LeagueSettings.for_league(league)
        rows.append({
            'league': league,
            'members': league.members.exclude(profile__is_bot=True).count(),
            'managers': list(league.managers.values_list('username', flat=True)),
            'week': settings.week,
            'publish': settings.publish,
            'auto': settings.auto_enabled,
        })
    return render(request, 'leagues/index.html', {'rows': rows})


def _make_manager(league, username, email, password):
    user = User.objects.create_user(username=username, email=email or '', password=password)
    user.profile.league = league
    user.profile.role = 'manager'
    user.profile.preseason_submitted = True
    user.profile.save()
    return user


@superuser_required
def create(request):
    form = LeagueForm(request.POST or None)
    manager_form = ManagerForm(request.POST or None)
    if request.method == 'POST' and form.is_valid() and manager_form.is_valid():
        from main.intro_seeds import seed_intro_templates
        from main.models import LeagueSettings
        with transaction.atomic():
            league = League.objects.create(
                name=form.cleaned_data['name'], slug=form.cleaned_data['slug'],
                is_active=form.cleaned_data['is_active'])
            LeagueSettings.for_league(league)
            seed_intro_templates(league)
            if manager_form.wants_account:
                _make_manager(league, manager_form.cleaned_data['username'],
                              manager_form.cleaned_data['email'],
                              manager_form.cleaned_data['password'])
        messages.success(request, f'{league.name} created. Join code {league.join_code}.')
        return redirect('leagues:edit', slug=league.slug)
    return render(request, 'leagues/form.html', {
        'form': form, 'manager_form': manager_form, 'creating': True,
    })


@superuser_required
def edit(request, slug):
    league = get_object_or_404(League, slug=slug)
    form = LeagueForm(instance=league)
    manager_form = ManagerForm()

    if request.method == 'POST':
        if 'save' in request.POST:
            form = LeagueForm(request.POST, instance=league)
            if form.is_valid():
                league.name = form.cleaned_data['name']
                league.slug = form.cleaned_data['slug']
                league.is_active = form.cleaned_data['is_active']
                league.save()
                messages.success(request, 'Saved.')
                return redirect('leagues:edit', slug=league.slug)
        elif 'rotate_code' in request.POST:
            league.rotate_join_code()
            messages.success(request, f'New join code: {league.join_code}')
            return redirect('leagues:edit', slug=league.slug)
        elif 'add_manager' in request.POST:
            user = league.members.filter(username=request.POST.get('username', '').strip()).first()
            if user is None:
                messages.error(request, 'No member of this league has that username.')
            else:
                user.profile.role = 'manager'
                user.profile.save(update_fields=['role'])
                messages.success(request, f'{user.username} is now a manager.')
            return redirect('leagues:edit', slug=league.slug)
        elif 'remove_manager' in request.POST:
            user = league.managers.filter(username=request.POST.get('username', '')).first()
            if user is not None:
                user.profile.role = 'member'
                user.profile.save(update_fields=['role'])
                messages.success(request, f'{user.username} is now a member.')
            return redirect('leagues:edit', slug=league.slug)
        elif 'create_manager' in request.POST:
            manager_form = ManagerForm(request.POST)
            if manager_form.is_valid() and manager_form.wants_account:
                user = _make_manager(league, manager_form.cleaned_data['username'],
                                     manager_form.cleaned_data['email'],
                                     manager_form.cleaned_data['password'])
                messages.success(request, f'Manager {user.username} created.')
                return redirect('leagues:edit', slug=league.slug)
            elif manager_form.is_valid():
                manager_form.add_error('username', 'A username is required.')

    return render(request, 'leagues/form.html', {
        'form': form, 'manager_form': manager_form, 'creating': False,
        'target': league,
        'managers': league.managers,
        'member_count': league.members.exclude(profile__is_bot=True).count(),
    })
