# -*- coding: utf-8 -*-
import datetime

import wtforms as wtf
from wtforms import validators, ValidationError
from sqlalchemy import func
from pyramid.i18n import TranslationStringFactory

from intranet3 import helpers as h
from intranet3.lib.employee import user_leave
from .times import EmployeeChoices
from intranet3.models import ( DBSession, User, TimeEntry, PresenceEntry,
                               WrongTime, Late, WrongTime )
from .utils import TimeField

_ = TranslationStringFactory('intranet3')
day_start = datetime.time(0, 0, 0)
day_end = datetime.time(23, 59, 59)
hour_9 = datetime.time(9, 0, 0)

class BaseForm(wtf.Form):
    popup_date = wtf.DateField(_(u'Date'), validators=[validators.Required()], format='%d/%m/%Y')
    popup_explanation = wtf.TextAreaField(_(u'Explanation'), validators=[validators.Required()])

class LateJustificationForm(BaseForm):

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop('user')
        super(LateJustificationForm, self).__init__(*args, **kwargs)

    def validate_popup_date(self, field):
        if field.data > datetime.date.today():
            raise ValidationError(_(u'Date have to be from past'))

        date = field.data
        start_date = datetime.datetime.combine(date, day_start)
        end_date = datetime.datetime.combine(date, day_end)
        user_id = self.user.id
        work_start = DBSession.query(func.min(PresenceEntry.ts))\
                        .filter(PresenceEntry.user_id==self.user.id)\
                        .filter(PresenceEntry.ts>=start_date)\
                        .filter(PresenceEntry.ts<=end_date).one()[0]

        if not work_start or work_start.time() < hour_9:
            raise ValidationError(_(u'You don\'t have late on this date !'))

        late = Late.query.filter(Late.date==date)\
                                   .filter(Late.user_id==user_id)\
                                   .filter(Late.deleted==False).first()
        if late:
            raise ValidationError(_(u'You already have justification in this date'))

class LateApplicationForm(BaseForm):

    late_start = TimeField(_(u'From'))
    late_end = TimeField(_(u'To'))
    work_from_home = wtf.BooleanField(_(u'Work From Home'))

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop('user')
        super(LateApplicationForm, self).__init__(*args, **kwargs)

    def validate_late_start(self, field):
        if field.data >= self.late_end.data:
            raise ValidationError(_(u'Invalid time range'))

    def validate_popup_date(self, field):
        date = field.data
        user_id = self.user.id
        late = Late.query.filter(Late.date==date)\
                         .filter(Late.user_id==user_id)\
                         .filter(Late.deleted==False).first()
        if late:
            raise ValidationError(_(u'You already have application in this date'))

        if date < datetime.date.today():
            start_date = datetime.datetime.combine(date, day_start)
            end_date = datetime.datetime.combine(date, day_end)
            work_start = DBSession.query(func.min(PresenceEntry.ts)) \
                .filter(PresenceEntry.user_id==self.user.id) \
                .filter(PresenceEntry.ts>=start_date) \
                .filter(PresenceEntry.ts<=end_date).one()[0]

            if not work_start or work_start.time() < hour_9:
                raise ValidationError(_(u'You don\'t have late on this date !'))

class WrongTimeJustificationForm(BaseForm):

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop('user')
        super(WrongTimeJustificationForm, self).__init__(*args, **kwargs)

    def validate_popup_date(self, field):
        if field.data > datetime.date.today():
            raise ValidationError(_(u'Date have to be from past'))

        date = field.data
        user_id = self.user.id
        wrongtime_count = DBSession.query('count').from_statement("""
            SELECT COUNT(*) as count
            FROM time_entry t
            WHERE t.user_id = :user_id AND t.date = :date AND DATE(t.modified_ts) > t.date
        """).params(user_id=user_id, date=date).one()[0]

        if not wrongtime_count:
            raise ValidationError(_(u'You don\'t have wrong time records on this date !'))

        wrongtime = WrongTime.query.filter(WrongTime.date==date)\
                                   .filter(WrongTime.user_id==user_id)\
                                   .filter(WrongTime.deleted==False).first()
        if wrongtime:
            raise ValidationError(_(u'You already have justification in this date !'))


ABSENCE_TYPES = (
    (u'planowany', _(u'Planned leave')),
    (u'zadanie', _(u'Leave at request')),
    (u'l4', _(u'Illness')),
    (u'okolicznosciowy', _(u'Compassionate leave')),
    (u'inne', _(u'Absence')),
)


class AbsentApplicationForm(wtf.Form):
    popup_date_start = wtf.DateField(_(u'Start'), validators=[validators.Required()], format='%d/%m/%Y', default=datetime.date.today())
    popup_date_end = wtf.DateField(_(u'End'), validators=[validators.Required()], format='%d/%m/%Y')
    popup_type = wtf.SelectField(_(u'Type'), validators=[validators.Required()], choices=ABSENCE_TYPES)
    popup_remarks = wtf.TextAreaField(_(u'Remarks'), validators=[validators.Required()])

    def __init__(self, *args, **kwargs):
        self.request = kwargs.pop('request')
        super(AbsentApplicationForm, self).__init__(*args, **kwargs)

    def validate_popup_type(self, field):
        if field.data == u'zadanie' or field.data == u'l4':
            return
        if self.popup_date_start.data and self.popup_date_end.data:
            if self.popup_date_start.data <= datetime.date.today() or self.popup_date_end.data <= datetime.date.today():
                raise ValidationError(_(u'Choose leave at request'))

    def validate_popup_date_start(self, field):
        if self.popup_date_start.data and self.popup_date_end.data:
            if self.popup_date_start.data > self.popup_date_end.data:
                raise ValidationError(_(u'End < Start'))

    def validate_popup_date_end(self, field):
        if self.popup_date_start.data and self.popup_date_end.data:
            if self.popup_date_start.data > self.popup_date_end.data:
                raise ValidationError(_(u'End < Start'))

    def validate_popup_type(self, field):
        if field.data == u'l4' and not self.request.user.employment_contract:
            raise ValidationError(_(u"Only user on employment contract can submit L4 absence."))
        if self.popup_date_start.data and self.popup_date_end.data and field.data not in (u'inne', u'okolicznosciowe', u'l4'):
            mandated, used, left = user_leave(self.request, self.popup_date_start.data.year)
            days = h.get_working_days(self.popup_date_start.data, self.popup_date_end.data)
            left -= days
            if left < 0:
                raise ValidationError(_(u'There is no leave to use, please choose Absence or conntact with Assistant'))


class AbsenceCreateForm(AbsentApplicationForm):
    popup_user_id = wtf.SelectField(_(u'Employee'), validators=[], choices=EmployeeChoices(first_empty=False))

    def validate_popup_type(self, field):
        if self.popup_date_start.data and \
           self.popup_date_end.data and \
           field.data not in (u'inne', u'okolicznosciowe', u'l4'):

            user = User.query.get(self.popup_user_id.data)
            mandated, used, left = user_leave(
                self.request,
                self.popup_date_start.data.year,
                user=user
            )
            days = h.get_working_days(
                self.popup_date_start.data,
                self.popup_date_end.data
            )
            left -= days
            if left < 0:
                raise ValidationError(_(u'There is no leave to use, please choose Absence or conntact with Assistant'))


class FilterForm(wtf.Form):
    DEFAULT_LIMIT = 50
    date_start = wtf.DateField(_(u'Start'), validators=[validators.Required()], format='%d-%m-%Y')
    date_end = wtf.DateField(_(u'End'), validators=[validators.Required()], format='%d-%m-%Y')
    user_id = wtf.SelectField(_(u'Employee'), validators=[], choices=EmployeeChoices(first_empty=True))
    limit = wtf.IntegerField(_(u'Limit'), validators=[], default=DEFAULT_LIMIT)


class WorkFromHomeFilterForm(wtf.Form):
    user_id = wtf.SelectField(_(u'Employee'), validators=[], choices=EmployeeChoices(first_empty=True))
    coordinator_id = wtf.SelectField(_(u'Coordinator'), choices=EmployeeChoices(first_empty=True))
    verified = wtf.BooleanField(_(u'Verified'))
