# -*- coding: utf-8 -*-
import datetime
import requests
from collections import defaultdict

from pyramid.decorator import reify
from sqlalchemy import Column, ForeignKey, orm, or_
from sqlalchemy.types import String, Boolean, Integer, Date, Enum, Text
from sqlalchemy.schema import UniqueConstraint
from sqlalchemy.dialects import postgresql
from sqlalchemy import not_

from intranet3 import memcache, config
from intranet3.log import ERROR_LOG
from intranet3.models import Base, DBSession
from intranet3.utils import acl


ERROR = ERROR_LOG(__name__)
INFO = ERROR_LOG(__name__)

GOOGLE_ACCESS_TOKEN_MEMCACHE_KEY = 'google-access-token-userid-%s'


class User(Base):
    __tablename__ = 'user'
    LOCATIONS = {
        'poznan': (u'Poznań', 'POZ'),
        'wroclaw': (u'Wrocław', 'WRO'),
        'pila': (u'Piła', 'PIL')
    }
    ROLES = [
        ('ADMIN', 'Admin'),
        ('ACCOUNTANT', 'Accountant'),
        ('BUSINESS DEV', 'Business Development'),
        ('CEO', 'CEO'),
        ('CEO A', 'CEO\'s Assistant'),
        ('CTO', 'CTO'),
        ('MARKETING SPEC', 'Marketing Specialist'),
        ('OFFICE MANAGER', 'Office Manager'),
        ('PROGRAMMER', 'Programmer'),
        ('RECRUITER', 'Recruiter'),
        ('QA LEAD', 'QA Lead'),
        ('TESTER', 'Tester'),
        ('TECH LEAD', 'Tech Lead'),
        ('BUSINESS RESEARCHER', 'Business Researcher'),
    ]

    GROUPS = [
        'employee',
        'admin',
        'sysop',
        'client',
        'scrum master',
        'cron',
        'coordinator',
        'freelancer',
        'hr',
        'business',
    ]

    id = Column(Integer, primary_key=True, nullable=False, index=True)
    email = Column(String, unique=True, nullable=False, index=True)
    name = Column(String, nullable=False)
    admin = Column(Boolean, default=False, nullable=False)
    employment_contract = Column(Boolean, default=False, nullable=False)

    is_active = Column(Boolean, default=True, nullable=False)

    levels = Column(Integer, nullable=False, default=0)

    roles = Column(postgresql.ARRAY(String))
    availability_link = Column(String, nullable=True, default=None)
    tasks_link = Column(String, nullable=True, default=None)
    skype = Column(String, nullable=True, default=None)
    irc = Column(String, nullable=True, default=None)
    phone = Column(String, nullable=True, default=None)
    phone_on_desk = Column(String, nullable=True, default=None)
    location = Column(
        Enum(*LOCATIONS.keys(), name='user_location_enum'), nullable=True,
        default='poznan',
    )

    start_work = Column(
        Date, nullable=False,
        default=lambda: datetime.date.today(),
    )
    start_full_time_work = Column(
        Date,
        nullable=True,
        default=None,
    )

    start_work_experience = Column(
        Date,
        nullable=True,
        default=None,
    )

    stop_work = Column(Date, nullable=True, default=None)
    description = Column(String, nullable=True, default=None)
    date_of_birth = Column(Date, nullable=True, default=None)

    presences = orm.relationship('PresenceEntry', backref='user', lazy='dynamic')
    credentials = orm.relationship('TrackerCredentials', backref='user', lazy='dynamic')
    time_entries = orm.relationship('TimeEntry', backref='user', lazy='dynamic')
    coordinated_projects = orm.relationship('Project', backref='coordinator', lazy='dynamic')
    leaves = orm.relationship('Leave', backref='user', lazy='dynamic')
    lates = orm.relationship('Late', backref='user', lazy='dynamic')

    groups = Column(postgresql.ARRAY(String))

    notify_blacklist = Column(postgresql.ARRAY(Integer), default=[])

    refresh_token = Column(String, nullable=False)
    _access_token = None

    @property
    def user_groups(self):
        return ", ".join([group for group in self.groups])

    @property
    def user_roles(self):
        ROLES_DICT = dict(self.ROLES)
        return ", ".join([ROLES_DICT[role] for role in self.roles])

    @reify
    def all_perms(self):
        """
        Combine all perms for a user for js layer.
        """
        acls = acl.Root.to_js()
        user_groups = self.groups
        perms = [
            perm for group, perms in acls.iteritems()
            if group in user_groups for perm in perms
        ]
        perms = list(set(perms))
        return perms

    @reify
    def access_token(self):
        access_token = memcache.get(GOOGLE_ACCESS_TOKEN_MEMCACHE_KEY % self.id)
        if access_token:
            INFO('Found access token %s for user %s' % (
                access_token, self.name
            ))
        if not access_token:
            args = dict(
                client_id=config['GOOGLE_CLIENT_ID'],
                client_secret=config['GOOGLE_CLIENT_SECRET'],
                refresh_token=self.refresh_token,
                grant_type='refresh_token',
            )
            response = requests.post('https://accounts.google.com/o/oauth2/token', data=args, verify=False)
            data = response.json()
            if 'access_token' not in data:
                ERROR('There is no token in google response %s, status_code: %s, refresh_token: %s, user.email: %s' % (response.json, response.status_code, self.refresh_token, self.email))
                return None

            INFO('Received response with access_token for user %s: %s' % (
                self.name, data
            ))
            access_token = data['access_token']
            expires = data['expires_in']
            expires = int(expires / 2)
            INFO('Saving access_token %s for user %s in memcached for %s s' % (
                access_token, self.name, expires,
            ))
            memcache.set(
                GOOGLE_ACCESS_TOKEN_MEMCACHE_KEY % self.id,
                access_token,
                expires
            )

        return access_token

    def get_leave(self, year):
        leave = Leave.query.filter(Leave.user_id==self.id).filter(Leave.year==year).first()
        if leave:
            return leave.number
        else:
            return 0

    @property
    def avatar_url(self):
        if self.id:
            return '/api/images/users/%s' % self.id
        else:
            return None

    def get_location(self, short=False):
        if short:
            return self.LOCATIONS[self.location][1]
        else:
            return self.LOCATIONS[self.location][0]


    def get_client(self):
        from intranet3.models import Client
        email = self.email
        client = Client.query.filter(Client.emails.contains(email)).first() or None
        return client

    @reify
    def client(self):
        return self.get_client()

    @classmethod
    def is_not_client(cls):
        # used in queries i.e. User.query.filter(User.is_not_client()).filter(...
        # <@ = http://www.postgresql.org/docs/8.3/static/functions-array.html
        return not_(cls.is_client())

    @classmethod
    def is_client(cls):
        # used in queries i.e. User.query.filter(User.is_client()).filter(...
        # <@ = http://www.postgresql.org/docs/8.3/static/functions-array.html
        return User.groups.op('@>')('{client}')

    @classmethod
    def is_freelancer(cls):
        return User.groups.op('@>')('{freelancer}')

    @classmethod
    def is_not_freelancer(cls):
        return not_(cls.is_freelancer())

    def get_locations(self):
        """
        Returns sorted locations with the user's location at the first place
        """
        locations = self.LOCATIONS.keys()
        my_location_index = locations.index(self.location)
        my_location = locations.pop(my_location_index)

        locations.sort()
        locations.insert(0, my_location)
        locations_names = [
            (name, self.LOCATIONS[name]) for name in locations
        ]
        return locations_names

    def to_dict(self, full=False):
        result =  {
            'id': self.id,
            'name': self.name,
            'img': self.avatar_url
        }
        if full:
            location = self.LOCATIONS[self.location]
            result.update({
            'email': self.email,
            'is_active': self.is_active,
            'freelancer': 'freelancer' in self.groups,
            'is_client': 'client' in self.groups,
            'tasks_link': self.tasks_link,
            'availability_link': self.availability_link,
            'skype': self.skype,
            'irc': self.irc,
            'phone': self.phone,
            'phone_on_desk': self.phone_on_desk,
            'location': (self.location, location[0], location[1]),
            'start_work': self.start_work.isoformat() if self.start_work else None,
            'start_full_time_work': self.start_full_time_work.isoformat() if self.start_full_time_work else None,
            'stop_work': self.stop_work.isoformat() if self.stop_work else None,
            'date_of_birth': self.date_of_birth.isoformat() if self.date_of_birth else None,
            'groups': self.groups,
            'roles': self.roles,
            'avatar_url': '/api/images/users/%s' % self.id,
            'all_perms': self.all_perms
        })
        return result

class Leave(Base):
    __tablename__ = 'leave'
    id = Column(Integer, primary_key=True, nullable=False, index=True)
    user_id = Column(Integer, ForeignKey(User.id), nullable=False, index=True)
    year = Column(Integer, nullable=False)
    number = Column(Integer, nullable=False)
    remarks = Column(Text, nullable=False, index=False)

    __table_args__ = (UniqueConstraint('user_id', 'year', name='_user_year_uc'), {})

    @classmethod
    def get_for_year(cls, year):
        leaves = Leave.query.filter(Leave.year==year).all()
        result = defaultdict(lambda: (0, u''))
        for leave in leaves:
            result[leave.user_id] = (leave.number, leave.remarks)
        return result

    @classmethod
    def get_used_for_year(cls, year):
        used_entries = DBSession.query('user_id', 'days').from_statement("""
            SELECT t.user_id, sum(t.time)/8 as days
            FROM time_entry t
            WHERE deleted = false AND
                  t.project_id = 86 AND
                  date_part('year', t.date) = :year
            GROUP BY user_id
        """).params(year=year).all()
        used = defaultdict(lambda: 0)
        for u in used_entries:
            used[u[0]] = int(u[1])
        return used
