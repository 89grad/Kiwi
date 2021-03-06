# Generated by Django 2.1.2 on 2018-10-24 21:07

from django.conf import settings
from django.db import migrations, models

import tcms.core.models.base


TAG_ID_COLUMN = 'tag_id'
if settings.DATABASES['default']['ENGINE'].find('sqlite') > -1:
    TAG_ID_COLUMN = ''


def forwards_add_initial_data(apps, schema_editor):
    priority_model = apps.get_model('management', 'Priority')
    priority_model.objects.bulk_create([
        priority_model(value='P1', sortkey=1),
        priority_model(value='P2', sortkey=2),
        priority_model(value='P3', sortkey=3),
        priority_model(value='P4', sortkey=4),
        priority_model(value='P5', sortkey=5),
    ])


def reverse_remove_initial_data(apps, schema_editor):
    priority_model = apps.get_model('management', 'Priority')
    priority_model.objects.filter(value__in=['P1', 'P2', 'P3', 'P4', 'P5']).delete()


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='Classification',
            fields=[
                ('id', models.AutoField(primary_key=True, serialize=False)),
                ('name', models.CharField(max_length=64, unique=True)),
                ('description', models.TextField(blank=True)),
                ('sortkey', models.IntegerField(default=0)),
            ],
            bases=(models.Model, tcms.core.models.base.UrlMixin),
        ),
        migrations.CreateModel(
            name='Component',
            fields=[
                ('id', models.AutoField(max_length=5, primary_key=True, serialize=False)),
                ('name', models.CharField(max_length=64)),
                ('description', models.TextField()),
                ('initial_owner', models.ForeignKey(db_column='initialowner', null=True,
                                                    on_delete=models.deletion.CASCADE,
                                                    related_name='initialowner',
                                                    to=settings.AUTH_USER_MODEL)),
                ('initial_qa_contact', models.ForeignKey(blank=True, db_column='initialqacontact',
                                                         null=True,
                                                         on_delete=models.deletion.CASCADE,
                                                         related_name='initialqacontact',
                                                         to=settings.AUTH_USER_MODEL)),
            ],
            bases=(models.Model, tcms.core.models.base.UrlMixin),
        ),
        migrations.CreateModel(
            name='Priority',
            fields=[
                ('id', models.AutoField(max_length=5, primary_key=True, serialize=False)),
                ('value', models.CharField(max_length=64, unique=True)),
                ('sortkey', models.IntegerField(default=0)),
                ('is_active', models.BooleanField(db_column='isactive', default=True)),
            ],
            options={
                'verbose_name_plural': 'priorities',
            },
            bases=(models.Model, tcms.core.models.base.UrlMixin),
        ),
        migrations.CreateModel(
            name='Product',
            fields=[
                ('id', models.AutoField(max_length=5, primary_key=True, serialize=False)),
                ('name', models.CharField(max_length=64, unique=True)),
                ('description', models.TextField(blank=True)),
                ('classification', models.ForeignKey(
                    on_delete=models.deletion.CASCADE, to='management.Classification')),
            ],
            bases=(models.Model, tcms.core.models.base.UrlMixin),
        ),
        migrations.CreateModel(
            name='Build',
            fields=[
                ('build_id', models.AutoField(max_length=10, primary_key=True,
                                              serialize=False, unique=True)),
                ('name', models.CharField(max_length=255)),
                ('description', models.TextField(blank=True)),
                ('is_active', models.BooleanField(db_column='isactive', default=True)),
                ('product', models.ForeignKey(on_delete=models.deletion.CASCADE,
                                              related_name='build', to='management.Product')),
            ],
            options={
                'verbose_name': 'build',
                'verbose_name_plural': 'builds',
            },
            bases=(models.Model, tcms.core.models.base.UrlMixin),
        ),
        migrations.CreateModel(
            name='Tag',
            fields=[
                ('id', models.AutoField(db_column=TAG_ID_COLUMN, max_length=10,
                                        primary_key=True, serialize=False)),
                ('name', models.CharField(db_column='tag_name', max_length=255)),
            ],
            options={
                'verbose_name': 'tag',
                'verbose_name_plural': 'tags',
            },
            bases=(models.Model, tcms.core.models.base.UrlMixin),
        ),
        migrations.CreateModel(
            name='Version',
            fields=[
                ('id', models.AutoField(primary_key=True, serialize=False)),
                ('value', models.CharField(max_length=192)),
                ('product', models.ForeignKey(on_delete=models.deletion.CASCADE,
                                              related_name='version', to='management.Product')),
            ],
            bases=(models.Model, tcms.core.models.base.UrlMixin),
        ),
        migrations.AddField(
            model_name='component',
            name='product',
            field=models.ForeignKey(on_delete=models.deletion.CASCADE,
                                    related_name='component', to='management.Product'),
        ),
        migrations.AlterUniqueTogether(
            name='version',
            unique_together={('product', 'value')},
        ),
        migrations.AlterUniqueTogether(
            name='build',
            unique_together={('product', 'name')},
        ),
        migrations.AlterUniqueTogether(
            name='component',
            unique_together={('product', 'name')},
        ),

        migrations.RunPython(forwards_add_initial_data, reverse_remove_initial_data),
    ]
