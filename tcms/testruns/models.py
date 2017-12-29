# -*- coding: utf-8 -*-
import datetime
from collections import namedtuple

from django.conf import settings
from django.core.cache import cache
from django.urls import reverse
from django.db import models
from django.db.models import Q, Count
from django.db.models.signals import post_save, post_delete, pre_save

from tcms.core.contrib.linkreference.models import LinkReference
from tcms.core.models.fields import DurationField
from tcms.core.models import TCMSActionModel
from tcms.core.utils import is_int
from tcms.core.utils.timedeltaformat import format_timedelta
from tcms.testcases.models import TestCaseBug, TestCaseText, NoneText
from tcms.testruns import signals as run_watchers


TestCaseRunStatusSubtotal = namedtuple('TestCaseRunStatusSubtotal',
                                       'StatusSubtotal '
                                       'CaseRunsTotalCount '
                                       'CompletedPercentage '
                                       'FailurePercentage')


class TestRun(TCMSActionModel):
    run_id = models.AutoField(primary_key=True)

    product_version = models.ForeignKey('management.Version', related_name='version_run',
                                        on_delete=models.CASCADE)
    plan_text_version = models.IntegerField()

    start_date = models.DateTimeField(auto_now_add=True, db_index=True)
    stop_date = models.DateTimeField(null=True, blank=True, db_index=True)
    summary = models.TextField()
    notes = models.TextField(blank=True)
    estimated_time = DurationField(default=0)

    plan = models.ForeignKey('testplans.TestPlan', related_name='run',
                             on_delete=models.CASCADE)
    environment_id = models.IntegerField(default=0)
    build = models.ForeignKey('management.TestBuild', related_name='build_run',
                              on_delete=models.CASCADE)
    manager = models.ForeignKey('auth.User', related_name='manager', on_delete=models.CASCADE)
    default_tester = models.ForeignKey('auth.User',
                                       null=True, blank=True,
                                       related_name='default_tester',
                                       on_delete=models.CASCADE)

    env_value = models.ManyToManyField('management.TCMSEnvValue',
                                       through='testruns.TCMSEnvRunValueMap')

    tag = models.ManyToManyField('management.TestTag',
                                 through='testruns.TestRunTag')

    cc = models.ManyToManyField('auth.User', through='testruns.TestRunCC')
    auto_update_run_status = models.BooleanField(default=False)

    class Meta:
        db_table = u'test_runs'
        unique_together = ('run_id', 'product_version', 'plan_text_version')

    def __str__(self):
        return self.summary

    @classmethod
    def to_xmlrpc(cls, query=None):
        from tcms.xmlrpc.serializer import TestRunXMLRPCSerializer
        from tcms.xmlrpc.utils import distinct_filter

        _query = query or {}
        qs = distinct_filter(TestRun, _query).order_by('pk')
        s = TestRunXMLRPCSerializer(model_class=cls, queryset=qs)
        return s.serialize_queryset()

    @classmethod
    def list(cls, query):
        conditions = []

        mapping = {
            'search': lambda value: Q(run_id__icontains=value) | Q(summary__icontains=value),
            'summary': lambda value: Q(summary__icontains=value),
            'product': lambda value: Q(build__product=value),
            'product_version': lambda value: Q(product_version=value),
            'plan': lambda value:
            Q(plan__plan_id=int(value)) if is_int(value) else Q(plan__name__icontains=value),
            'build': lambda value: Q(build=value),
            'env_group': lambda value: Q(plan__env_group=value),
            'people_id': lambda value: Q(manager__id=value) | Q(default_tester__id=value),
            'manager': lambda value: Q(manager=value),
            'default_tester': lambda value: Q(default_tester=value),
            'tag__name__in': lambda value: Q(tag__name__in=value),
            'env_value__value__in': lambda value: Q(env_value__value__in=value),
            'case_run__assignee': lambda value: Q(case_run__assignee=value),
            'status': lambda value: {
                'running': Q(stop_date__isnull=True),
                'finished': Q(stop_date__isnull=False),
            }[value.lower()],
            'people': lambda value: {
                'default_tester': Q(default_tester=value),
                'manager': Q(manager=value),
                'people': Q(manager=value) | Q(default_tester=value),
                # TODO: Remove first one after upgrade to newer version.
                # query.set can return either '' or None sometimes, so
                # currently keeping these two lines here is a workaround.
                '': Q(manager=value) | Q(default_tester=value),
                None: Q(manager=value) | Q(default_tester=value),
            }[query.get('people_type')],
        }

        conditions = [mapping[key](value) for key, value in query.items()
                      if value and key in mapping]

        runs = cls.objects.filter(*conditions)

        value = query.get('sortby')
        if value:
            runs = runs.order_by(value)

        return runs.distinct()

    def belong_to(self, user):
        if self.manager == user or self.plan.author == user:
            return True

        return False

    def clear_estimated_time(self):
        """Converts a integer to time"""
        return format_timedelta(self.estimated_time)

    def check_all_case_runs(self, case_run_id=None):
        tcrs = self.case_run.all()
        tcrs = tcrs.select_related('case_run_status')

        for tcr in tcrs:
            if not tcr.is_finished():
                return False

        return True

    def get_absolute_url(self, request=None):
        # Upward compatibility code
        if request:
            return request.build_absolute_uri(
                reverse('testruns-get', args=[self.pk, ])
            )

        return self.get_url(request)

    def get_notify_addrs(self):
        """
        Get the all related mails from the run
        """
        to = [self.manager.email]
        to.extend(self.cc.values_list('email', flat=True))
        if self.default_tester_id:
            to.append(self.default_tester.email)

        for tcr in self.case_run.select_related('assignee').all():
            if tcr.assignee_id:
                to.append(tcr.assignee.email)
        return list(set(to))

    def get_url_path(self):
        return reverse('testruns-get', args=[self.pk, ])

    # FIXME: rewrite to use multiple values INSERT statement
    def add_case_run(self, case, case_run_status=1, assignee=None,
                     case_text_version=None, build=None,
                     notes=None, sortkey=0):
        _case_text_version = case_text_version
        if not _case_text_version:
            _case_text_version = case.latest_text(
                text_required=False).case_text_version

        _assignee = assignee \
            or (case.default_tester_id and case.default_tester) \
            or (self.default_tester_id and self.default_tester)

        get_caserun_status = TestCaseRunStatus.objects.get
        _case_run_status = isinstance(case_run_status, int) \
            and get_caserun_status(id=case_run_status) \
            or case_run_status

        return self.case_run.create(case=case,
                                    assignee=_assignee,
                                    tested_by=None,
                                    case_run_status=_case_run_status,
                                    case_text_version=_case_text_version,
                                    build=build or self.build,
                                    notes=notes,
                                    sortkey=sortkey,
                                    environment_id=self.environment_id,
                                    running_date=None,
                                    close_date=None)

    def add_tag(self, tag):
        return TestRunTag.objects.get_or_create(
            run=self,
            tag=tag
        )

    def add_cc(self, user):
        return TestRunCC.objects.get_or_create(
            run=self,
            user=user,
        )

    def add_env_value(self, env_value):
        return TCMSEnvRunValueMap.objects.get_or_create(run=self,
                                                        value=env_value)

    def remove_tag(self, tag):
        TestRunTag.objects.filter(run=self, tag=tag).delete()

    def remove_cc(self, user):
        TestRunCC.objects.filter(run=self, user=user).delete()

    def remove_env_value(self, env_value):
        TCMSEnvRunValueMap.objects.filter(run=self, value=env_value).delete()

    def mail(self, template, subject, context, to=[], request=None):
        from tcms.core.utils.mailto import mailto

        to = self.get_notify_addrs()
        mailto(template, subject, to, context, request)

    def get_bug_count(self):
        """
            Return the count of distinct bug numbers recorded for
            this particular TestRun.
        """
        # note fom Django docs: A count() call performs a SELECT COUNT(*)
        # behind the scenes !!!
        return TestCaseBug.objects.filter(
            case_run__run=self.pk
        ).values('bug_id').distinct().count()

    def get_percentage(self, count):
        case_run_count = self.total_num_caseruns
        if case_run_count == 0:
            return 0
        percent = float(count) / case_run_count * 100
        percent = round(percent, 2)
        return percent

    def _get_completed_case_run_percentage(self):
        ids = TestCaseRunStatus._get_completed_status_ids()
        completed_caserun = self.case_run.filter(
            case_run_status__in=ids)

        percentage = self.get_percentage(completed_caserun.count())
        return percentage

    completed_case_run_percent = property(_get_completed_case_run_percentage)

    def _get_total_case_run_num(self):
        return self.case_run.count()

    total_num_caseruns = property(_get_total_case_run_num)

    def update_completion_status(self, is_auto_updated, is_finish=None):
        if is_auto_updated and self.auto_update_run_status:
            if self.completed_case_run_percent == 100.0:
                self.stop_date = datetime.datetime.now()
            else:
                self.stop_date = None
            self.save()
        if not is_auto_updated and not self.auto_update_run_status:
            if is_finish:
                self.stop_date = datetime.datetime.now()
            else:
                self.stop_date = None
            self.save()

    def env_values_str(self):
        """
            Return a string representation of environment properties
            used for display purposes.
        """
        result = ""
        for ev in self.env_value.all():
            result += "%s:%s\n" % (ev.property.name, ev.value)
        return result

    def stats_caseruns_status(self, case_run_statuses=None):
        """Get statistics based on case runs' status

        @param case_run_statuss: iterable object containing TestCaseRunStatus
            objects representing PASS, FAIL, WAIVED, etc.
        @type case_run_statuses: iterable object
        @return: the statistics including the number of each status mapping,
            total number of case runs, complete percent, and failure percent.
        @rtype: namedtuple
        """
        if case_run_statuses is None:
            case_run_statuses = TestCaseRunStatus.objects.only('pk', 'name').order_by('pk')

        rows = TestCaseRun.objects.filter(
            run=self.pk
        ).values(
            'case_run_status'
        ).annotate(status_count=Count('case_run_status'))

        caserun_statuses_subtotal = dict((status.pk, [0, status])
                                         for status in case_run_statuses)

        for row in rows:
            status_pk = row['case_run_status']
            caserun_statuses_subtotal[status_pk][0] = row['status_count']

        complete_count = 0
        failure_count = 0
        caseruns_total_count = 0

        for status_pk, total_info in caserun_statuses_subtotal.items():
            status_caseruns_count, caserun_status = total_info
            status_name = caserun_status.name

            caseruns_total_count += status_caseruns_count

            if status_name in TestCaseRunStatus.complete_status_names:
                complete_count += status_caseruns_count
            if status_name in TestCaseRunStatus.failure_status_names:
                failure_count += status_caseruns_count

        # Final calculation
        complete_percent = .0
        if caseruns_total_count:
            complete_percent = complete_count * 100.0 / caseruns_total_count
        failure_percent = .0
        if complete_count:
            failure_percent = failure_count * 100.0 / complete_count

        return TestCaseRunStatusSubtotal(caserun_statuses_subtotal,
                                         caseruns_total_count,
                                         complete_percent,
                                         failure_percent)


# FIXME: replace TestCaseRunStatus' internal cache with Django's cache
# machanism


class TestCaseRunStatus(TCMSActionModel):
    complete_status_names = ('PASSED', 'ERROR', 'FAILED', 'WAIVED')
    failure_status_names = ('ERROR', 'FAILED')
    idle_status_names = ('IDLE',)

    id = models.AutoField(db_column='case_run_status_id', primary_key=True)
    name = models.CharField(max_length=60, blank=True, unique=True)
    sortkey = models.IntegerField(null=True, blank=True, default=0)
    description = models.TextField(null=True, blank=True)
    auto_blinddown = models.BooleanField(default=True)

    class Meta:
        db_table = u'test_case_run_status'

    def __str__(self):
        return self.name

    cache_key_names = 'case_run_status__names'

    @classmethod
    def get_names(cls):
        '''Get all status names in mapping between id and name'''
        names = cache.get(cls.cache_key_names)
        if names is None:
            names = dict(cls.objects.values_list('pk', 'name').iterator())
            cache.set(cls.cache_key_names, names)
        return names

    @classmethod
    def get_names_ids(cls):
        '''Get all status names in reverse mapping between name and id'''
        return dict((name, _id) for _id, name in cls.get_names().items())

    def is_finished(self):
        if self.name in ['PASSED', 'FAILED', 'ERROR', 'WAIVED']:
            return True
        return False

    @classmethod
    def get_IDLE(cls):
        key = 'IDLE'
        result = cls.cache_get(key)
        if result is None:
            result = cls.cache_set(key, cls.objects.get(name='IDLE'))
        return result

    @classmethod
    def id_to_string(cls, _id):
        key = 'id_to_string_' + str(_id)
        result = cls.cache_get(key)
        if result is None:
            try:
                result = cls.objects.get(id=_id).name
            except cls.DoesNotExist:
                result = None
            cls.cache_set(key, result)
        return result

    @classmethod
    def _status_to_id(cls, status):
        status = status.upper()
        key = 'status_to_id_' + status
        result = cls.cache_get(key)
        if result is None:
            try:
                result = cls.objects.get(name=status).pk
            except cls.DoesNotExist:
                result = None
            cls.cache_set(key, result)
        return result

    @classmethod
    def _get_completed_status_ids(cls):
        '''
        There are some status indicate that
        the testcaserun is completed.
        Return IDs of these statuses.
        '''
        key = 'completed_status_ids'
        result = cls.cache_get(key)
        if result is None:
            completed_status = cls.objects.filter(name__in=(
                'FAILED', 'PASSED', 'ERROR', 'WAIVED'
            ))

            result = completed_status.values_list('pk', flat=True)
            result = cls.cache_set(key, list(result))
        return result

    @classmethod
    def _get_failed_status_ids(cls):
        '''
        There are some status indicate that
        the testcaserun is failed.
        Return IDs of these statuses.
        '''
        key = 'failed_status_ids'
        result = cls.cache_get(key)
        if result is None:
            statuses = cls.objects.all()
            failed_status = statuses.filter(name__in=(
                'FAILED', 'ERROR'
            ))

            result = failed_status.values_list('pk', flat=True)
            result = cls.cache_set(key, list(result))
        return result

    # TODO: gather following id_xxx into one method

    @classmethod
    def id_passed(cls):
        key = 'id_passed'
        result = cls.cache_get(key)
        if result is None:
            return cls.cache_set(key, cls._status_to_id('passed'))
        return result

    @classmethod
    def id_failed(cls):
        key = 'id_failed'
        result = cls.cache_get(key)
        if result is None:
            return cls.cache_set(key, cls._status_to_id('failed'))
        return result

    @classmethod
    def id_blocked(cls):
        key = 'id_blocked'
        result = cls.cache_get(key)
        if result is None:
            return cls.cache_set(key, cls._status_to_id('blocked'))
        return result

    @classmethod
    def _get_cache(cls):
        """A dictionary used to cache statuses.

        The caching implementation in here is a
        dedicated cache for this class. There are
        limited few number of statuses, and they
        are needed frequently enough to be cached.
        """
        key_cache = '_cache'
        cache = getattr(cls, key_cache, {})
        if not hasattr(cls, key_cache):
            setattr(cls, key_cache, cache)

        return cache

    @classmethod
    def cache_get(cls, key):
        cache = cls._get_cache()
        return cache.get(key, None)

    @classmethod
    def cache_set(cls, key, value):
        cache = cls._get_cache()
        if len(cache) > 1000:  # Prevent overflow
            cache.clear()
        cache[key] = value
        return value

    def save(self, *args, **kwargs):
        """Overrides save() only to outdate the cached statuses."""
        cache = getattr(self.__class__, '_cache', {})
        cache.clear()

        result = super(self.__class__, self).save(*args, **kwargs)
        if self.cache_key_names in cache:
            del cache[self.cache_key_names]
        return result


class TestCaseRunManager(models.Manager):
    def get_automated_case_count(self):
        return self.filter(case__is_automated=1).count()

    def get_manual_case_count(self):
        return self.filter(case__is_automated=0).count()

    def get_both(self):
        count1 = self.get_automated_case_count()
        count2 = self.get_manual_case_count()
        return self.count() - count1 - count2


class TestCaseRun(TCMSActionModel):
    objects = TestCaseRunManager()

    case_run_id = models.AutoField(primary_key=True)
    assignee = models.ForeignKey('auth.User', blank=True, null=True,
                                 related_name='case_run_assignee',
                                 on_delete=models.CASCADE)
    tested_by = models.ForeignKey('auth.User', blank=True, null=True,
                                  related_name='case_run_tester',
                                  on_delete=models.CASCADE)
    case_text_version = models.IntegerField()
    running_date = models.DateTimeField(null=True, blank=True)
    close_date = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(null=True, blank=True)
    sortkey = models.IntegerField(null=True, blank=True)

    run = models.ForeignKey(TestRun, related_name='case_run', on_delete=models.CASCADE)
    case = models.ForeignKey('testcases.TestCase', related_name='case_run',
                             on_delete=models.CASCADE)
    case_run_status = models.ForeignKey(TestCaseRunStatus, on_delete=models.CASCADE)
    build = models.ForeignKey('management.TestBuild', on_delete=models.CASCADE)
    environment_id = models.IntegerField(default=0)

    class Meta:
        db_table = u'test_case_runs'
        unique_together = ('case', 'run', 'case_text_version')

    def links(self):
        """
            Returns all links attached to this object!
        """
        return LinkReference.objects.filter(test_case_run=self.pk)

    def __str__(self):
        return '%s: %s' % (self.pk, self.case_id)

    @classmethod
    def to_xmlrpc(cls, query={}):
        from tcms.xmlrpc.serializer import TestCaseRunXMLRPCSerializer
        from tcms.xmlrpc.utils import distinct_filter

        qs = distinct_filter(TestCaseRun, query).order_by('pk')
        s = TestCaseRunXMLRPCSerializer(model_class=cls, queryset=qs)
        return s.serialize_queryset()

    @classmethod
    def mail_scene(cls, objects, field=None, value=None, ctype=None,
                   object_pk=None):
        tr = objects[0].run
        # scence_templates format:
        # template, subject, context
        tcrs = objects.select_related()
        scence_templates = {
            'assignee': {
                'template_name': 'mail/change_case_run_assignee.txt',
                'subject': 'Assignee of run %s has been changed' % tr.run_id,
                'recipients': tr.get_notify_addrs(),
                'context': {'test_run': tr, 'test_case_runs': tcrs},
            }
        }

        return scence_templates.get(field)

    def add_bug(self, bug_id, bug_system_id,
                summary=None, description=None, bz_external_track=False):
        return self.case.add_bug(
            bug_id=bug_id,
            bug_system_id=bug_system_id,
            summary=summary,
            description=description,
            case_run=self,
            bz_external_track=bz_external_track
        )

    def remove_bug(self, bug_id, run_id=None):
        self.case.remove_bug(bug_id=bug_id, run_id=run_id)

    def is_finished(self):
        return self.case_run_status.is_finished()

    def get_bugs(self):
        return TestCaseBug.objects.filter(
            case_run__case_run_id=self.case_run_id)

    def get_bugs_count(self):
        return self.get_bugs().count()

    def get_text_versions(self):
        return TestCaseText.objects.filter(
            case__pk=self.case.pk
        ).values_list('case_text_version', flat=True)

    def get_text_with_version(self, case_text_version=None):
        if case_text_version:
            try:
                return TestCaseText.objects.get(
                    case__case_id=self.case_id,
                    case_text_version=case_text_version
                )
            except TestCaseText.DoesNotExist:
                return NoneText
        try:
            return TestCaseText.objects.get(
                case__case_id=self.case_id,
                case_text_version=self.case_text_version
            )
        except TestCaseText.DoesNotExist:
            return NoneText

    def get_previous_or_next(self):
        ids = list(self.run.case_run.values_list('case_run_id', flat=True))
        current_idx = ids.index(self.case_run_id)
        prev = TestCaseRun.objects.get(case_run_id=ids[current_idx - 1])
        try:
            next = TestCaseRun.objects.get(case_run_id=ids[current_idx + 1])
        except IndexError:
            next = TestCaseRun.objects.get(case_run_id=ids[0])

        return (prev, next)

    def latest_text(self):
        try:
            return TestCaseText.objects.filter(
                case__case_id=self.case_id
            ).order_by('-case_text_version')[0]
        except IndexError:
            return NoneText

    def get_absolute_url(self):
        # NOTE: this returns the URL to the TestRun containing this TestCaseRun!
        return reverse('testruns-get', args=[self.run_id])

    def get_url_path(self, request=None):
        return self.get_absolute_url()


class TestRunTag(models.Model):
    tag = models.ForeignKey('management.TestTag', on_delete=models.CASCADE)
    run = models.ForeignKey(TestRun, related_name='tags', on_delete=models.CASCADE)
    user = models.IntegerField(db_column='userid', default='0')

    class Meta:
        db_table = u'test_run_tags'


class TestRunCC(models.Model):
    run = models.ForeignKey(TestRun, related_name='cc_list', on_delete=models.CASCADE)
    user = models.ForeignKey('auth.User', db_column='who', on_delete=models.CASCADE)

    class Meta:
        db_table = u'test_run_cc'
        unique_together = ('run', 'user')


class TCMSEnvRunValueMap(models.Model):
    run = models.ForeignKey(TestRun, on_delete=models.CASCADE)
    value = models.ForeignKey('management.TCMSEnvValue', on_delete=models.CASCADE)

    class Meta:
        db_table = u'tcms_env_run_value_map'


# Signals handler
def _run_listen():
    post_save.connect(run_watchers.post_run_saved, sender=TestRun)
    post_save.connect(run_watchers.post_case_run_saved, sender=TestCaseRun,
                      dispatch_uid='tcms.testruns.models.TestCaseRun')
    post_delete.connect(run_watchers.post_case_run_deleted, sender=TestCaseRun,
                        dispatch_uid='tcms.testruns.models.TestCaseRun')
    pre_save.connect(run_watchers.pre_save_clean, sender=TestRun)


if settings.LISTENING_MODEL_SIGNAL:
    _run_listen()
