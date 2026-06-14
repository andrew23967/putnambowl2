from django import forms
from .teams import TEAMS, NFC_TEAMS, AFC_TEAMS


class GameForm(forms.Form):
    underdog = forms.ChoiceField(choices=TEAMS)
    favorite = forms.ChoiceField(choices=TEAMS)
    underdog_moneyline = forms.FloatField(initial=0, required=False, label='Underdog ML (e.g. 150)')
    favorite_moneyline = forms.FloatField(initial=0, required=False, label='Favorite ML (e.g. -150)')
    favorite_is_home = forms.BooleanField(initial=True, required=False)
    date = forms.CharField(max_length=50, required=False,
                           widget=forms.TextInput(attrs={'type': 'date'}))


class PreseasonForm(forms.Form):
    big_loser = forms.ChoiceField(choices=TEAMS, label='Biggest Loser (worst team)')
    nfc_champ = forms.ChoiceField(choices=TEAMS, label='NFC Champion')
    afc_champ = forms.ChoiceField(choices=TEAMS, label='AFC Champion')
    superbowl_winner = forms.ChoiceField(choices=TEAMS, label='Super Bowl Winner')

    def __init__(self, user, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['big_loser'].initial = user.profile.big_loser
        self.fields['nfc_champ'].initial = user.profile.nfc_champ
        self.fields['afc_champ'].initial = user.profile.afc_champ
        self.fields['superbowl_winner'].initial = user.profile.superbowl_winner

    def clean(self):
        data = super().clean()
        sb = data.get('superbowl_winner')
        nfc = data.get('nfc_champ')
        afc = data.get('afc_champ')
        if sb and nfc and afc and sb not in (nfc, afc):
            raise forms.ValidationError(
                'Super Bowl winner must be either your NFC or AFC champion.'
            )
        return data


class AnnouncementForm(forms.Form):
    message = forms.CharField(widget=forms.Textarea(attrs={'rows': 3}))


class AdjustScoreForm(forms.Form):
    username = forms.CharField(max_length=150)
    amount = forms.FloatField(label='Points to add (use negative to subtract)')


class BugForm(forms.Form):
    description = forms.CharField(
        widget=forms.Textarea(attrs={'rows': 5}),
        label='Describe the bug'
    )


class SaveSeasonForm(forms.Form):
    year = forms.IntegerField(label='Season Year')
    notes = forms.CharField(widget=forms.Textarea(attrs={'rows': 3}), required=False)
