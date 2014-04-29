# -*- coding: utf-8 -*-
from __future__ import with_statement
import __builtin__
import calendar
import datetime
from collections import defaultdict

from pyramid.httpexceptions import HTTPFound, HTTPBadRequest
from pyramid.view import view_config

from intranet3.utils.views import BaseView, MonthMixin
from intranet3.models import User, Holiday, DBSession
from intranet3.forms.times import HoursWorkedReportFormBase
from intranet3.helpers import previous_month, next_month, dates_between
from intranet3.utils import excuses
from intranet3 import helpers as h

from intranet3.log import INFO_LOG, WARN_LOG, ERROR_LOG, DEBUG_LOG, EXCEPTION_LOG

LOG = INFO_LOG(__name__)
WARN = WARN_LOG(__name__)
ERROR = ERROR_LOG(__name__)
DEBUG = DEBUG_LOG(__name__)
EXCEPTION = EXCEPTION_LOG(__name__)


@view_config(route_name='times_report_current_pivot', permission='can_add_timeentry')
class CurrentPivot(BaseView):
    def get(self):
        today = datetime.datetime.now().date()
        url = self.request.url_for('/times/report/pivot', month=today.strftime('%m.%Y'))
        return HTTPFound(location=url)


@view_config(route_name='times_report_pivot', permission='can_add_timeentry')
class Pivot(MonthMixin, BaseView):
    def get(self):
        month_start, month_end = self._get_month()

        entries = DBSession.query('user_id', 'date', 'time', 'late_count').from_statement("""
        SELECT t.user_id as "user_id", t.date as "date", (
            SELECT COALESCE(SUM(h.time), 0.0) FROM
            time_entry h
            WHERE h.user_id = t.user_id
            AND h.date = t.date
            AND h.deleted = FALSE
        ) as "time", (
            SELECT COUNT(*)
            FROM time_entry s
            WHERE s.user_id = t.user_id
            AND s.date = t.date
            AND DATE(s.modified_ts) > s.date
        ) as "late_count"
        FROM time_entry t
        WHERE t.date >= :month_start
          AND t.date <= :month_end
        GROUP BY t.user_id, t.date;
        """).params(month_start=month_start, month_end=month_end)

        if not self.request.has_perm('can_see_users_times'):
            users = [self.request.user] # TODO do we need to constrain entries also?
            locations = {
                self.request.user.location: ('', 1)
            }
        else:
            location_names = self.request.user.get_locations()

            users = {}
            for name, (fullname, shortcut) in location_names:
                usersq = User.query.filter(User.is_not_client()) \
                                   .filter(User.is_active==True) \
                                   .filter(User.location==name) \
                                   .order_by(User.is_freelancer(), User.name)
                users[name] = usersq.all()

            locations = {
                name: [User.LOCATIONS[name][0], len(users)]
                for name, users in users.iteritems()
            }

            locations[self.request.user.location][1] -= 1

            ordered_users = []
            for name, (fullname, shortcut) in location_names:
                ordered_users.extend(users[name])

            users = ordered_users

        # move current user to the front of the list
        current_user_index = None
        for i, user in enumerate(users):
            if user.id == self.request.user.id:
                current_user_index = i

        users.insert(0, users.pop(current_user_index))

        today = datetime.date.today()
        grouped = defaultdict(lambda: defaultdict(lambda: 0.0))
        late = defaultdict(lambda: defaultdict(lambda: False))
        sums = defaultdict(lambda: 0.0)
        daily_sums = defaultdict(lambda: 0.0)
        for user_id, date, time, late_count in entries:
            grouped[user_id][date] = time
            if date <= today:
                sums[user_id] += time
                daily_sums[date] += time
            late[user_id][date] = late_count > 0

        holidays = Holiday.all()
        count_of_required_month_hours = {}
        count_of_required_hours_to_today = {}

        for user in users:
            sftw = user.start_full_time_work
            if sftw:
                if sftw > month_end:
                    start_work = datetime.date(today.year+10, 1, 1)
                elif sftw < month_start:
                    start_work = month_start
                else:
                    start_work = sftw

                month_hours = h.get_working_days(start_work, month_end) * 8

                today_ = today if today < month_end else month_end
                hours_to_today = h.get_working_days(start_work, today_) * 8

                count_of_required_month_hours[user.id] = month_hours
                count_of_required_hours_to_today[user.id] = hours_to_today
            else:
                count_of_required_month_hours[user.id] = 0
                count_of_required_hours_to_today[user.id] = 0

        return dict(
            entries=grouped, users=users, sums=sums, late=late, excuses=excuses.wrongtime(),
            daily_sums=daily_sums, monthly_sum=sum(daily_sums.values()),
            dates=__builtin__.list(dates_between(month_start, month_end)),
            is_holiday=lambda date: Holiday.is_holiday(date, holidays=holidays),
            month_start=month_start,
            prev_date=previous_month(month_start),
            next_date=next_month(month_start),
            today=today,
            count_of_required_month_hours=count_of_required_month_hours,
            count_of_required_hours_to_today=count_of_required_hours_to_today,
            locations=locations,
        )


class HoursWorkedMixin(object):
    def _get_start_end_of_month(self, date):
        year = date.year
        month = date.month
        try:
            start_date = datetime.date(year, month, 1)
        except ValueError:
            raise HTTPBadRequest()
        else:
            day_of_week, days_in_month = calendar.monthrange(year, month)
            end_date = datetime.date(year, month, days_in_month)
        return start_date, end_date

    def _worked_hours(self, date):
        month_start, month_end = self._get_start_end_of_month(date)
        holidays = Holiday.all()
        today = datetime.date.today()
        count_of_required_month_hours = count_of_required_hours_to_today = 0
        for date in dates_between(month_start, month_end):
            if not Holiday.is_holiday(date, holidays=holidays):
                count_of_required_month_hours += 8
                if date <= today:
                    count_of_required_hours_to_today += 8
        return count_of_required_month_hours, count_of_required_hours_to_today

    _quarters = [{1: 1, 2: 1, 3: 1, 4: 2, 5: 2, 6: 2,
                  7: 3, 8: 3, 9: 3, 10: 4, 11: 4, 12: 4},
                 {1: (1, 2, 3),
                  2: (4, 5, 6),
                  3: (7, 8, 9),
                  4: (10, 11, 12)}]

    def _previous_quarter(self):
        today = datetime.date.today()
        year = today.year
        quarter = self._quarters[0][today.month] -1
        if quarter == 0:
            quarter = 4
            year -= 1

        start_month = self._quarters[1][quarter][0]
        end_month = self._quarters[1][quarter][2]

        start_date = datetime.date(year, start_month, 1)
        end_date = datetime.date(year, end_month,\
            calendar.monthrange(year, end_month)[1])
        return start_date, end_date

    def _current_quarter(self):
        today = datetime.date.today()
        quarter = self._quarters[0][today.month]
        start_month = self._quarters[1][quarter][0]
        end_month = today.month

        start_date = datetime.date(today.year, start_month, 1)
        return start_date, today

    def _current_year(self):
        today = datetime.date.today()
        start_date = datetime.date(today.year, 1, 1)
        return start_date, today

    def _previous_year(self):
        today = datetime.date.today()
        year = today.year - 1

        start_date = datetime.date(year, 1, 1)
        end_date = datetime.date(year, 12, calendar.monthrange(year, 12)[1])
        return start_date, end_date

    def _hours_worked_report(self, user_id, group_by_month, start_date,
                             end_date, date_range=0, only_fully_employed=True):
        if date_range != 0:
            try:
                start_date, end_date = {1: self._current_quarter,
                                        2: self._previous_quarter,
                                        3: self._current_year,
                                        4: self._previous_year
                                       }[date_range]()
            except KeyError:
                date_range = 0

        rows = ['time', 'diff', 'user_id', 'email', 'name']
        params = {'date_start': start_date, 'date_end': end_date}

        if group_by_month:
            sql_group = "date_part('month',time_entry.date), date_part('year',time_entry.date)"
            sql_order = "date_part('month',time_entry.date), date_part('year',time_entry.date), \"user\".name"
            sql_cols = "date_part('month',time_entry.date) AS month, date_part('year',time_entry.date) AS year"
            rows.append('month')
            rows.append('year')
        else:
            sql_group = 'time_entry.date'
            sql_order = 'time_entry.date, "user".name'
            sql_cols = "time_entry.date"
            rows.append('date')

        sql_user = ''
        if user_id and user_id != 'None':
            user_id = [uid for uid in user_id if uid.isdigit()]
            sql_user = "AND user_id IN (:user_id)"
            sql_user = sql_user.replace(':user_id', ','.join(user_id))
            #params['user_id'] = ','.join(user_id)

        if only_fully_employed:
            sql_user += 'AND ("user".start_full_time_work IS NULL OR "user".start_full_time_work < NOW())'

        sql = """SELECT round(CAST(sum(time_entry.time) AS numeric), 2) AS time,
                        round(CAST(sum(time_entry.time)-8 AS Numeric), 2) AS diff,
                        time_entry.user_id,
                        "user".email,
                        "user".name,
                        {sql_cols}
                 FROM time_entry
                 JOIN "user" ON "user".id = time_entry.user_id
                     AND "user".is_active = True
                 WHERE
                        time_entry.deleted = FALSE AND
                        time_entry.date BETWEEN :date_start AND :date_end
                        {sql_user}
                 GROUP BY
                         time_entry.user_id,
                         "user".email,
                         "user".name,
                         {sql_group}
                 ORDER BY {sql_order}""".format(sql_cols=sql_cols, sql_group=sql_group, sql_user=sql_user, sql_order=sql_order)

        query = DBSession.query(*rows).from_statement(sql).params(**params)
        data = query.all()

        result = []

        if group_by_month:
            for d in data:
                row = list(d[:-2])
                year, month = int(d[-1]), int(d[-2])
                row.append("%d-%02d" % (year, month))

                excepted_hours = self._worked_hours(datetime.date(year, month, 1))[0]
                row[1] = row[0] - excepted_hours
                row.append(excepted_hours)
                result.append(row)
        else:
            result = data

        return result


@view_config(route_name='report_worked_hours_monthly', permission='can_see_users_times')
class Monthly(HoursWorkedMixin, BaseView):

    def dispatch(self):
        form = HoursWorkedReportFormBase(self.request.POST)
        result = []
        group_by_month = False
        if form.date_range.data != '0' or form.validate():
            user_id = form.user_id.data
            group_by_month = form.group_by_month.data
            start_date = form.start_date.data
            end_date = form.end_date.data

            try:
                date_range = int(form.date_range.data)
            except ValueError:
                date_range = 0

            only_fully_employed = form.only_fully_employed.data

            result = self._hours_worked_report(user_id, group_by_month,
                start_date, end_date,
                date_range, only_fully_employed)

            if form.only_red.data:
                result = [row for row in result if row[1] < 0]

        return dict(
            data=result,
            form=form,
            group_by_month=group_by_month,
            type="normal",
        )

