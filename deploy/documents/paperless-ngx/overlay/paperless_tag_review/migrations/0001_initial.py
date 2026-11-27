import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('documents', '0030_note_format'),
    ]

    operations = [
        migrations.CreateModel(
            name='TagDecision',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('batch', models.UUIDField(db_index=True, null=True)),
                ('score', models.FloatField(null=True)),
                ('minimum_score', models.PositiveSmallIntegerField(null=True)),
                ('membership', models.BigIntegerField(null=True)),
                ('added_parents', models.JSONField(default=dict)),
                ('status', models.CharField(default='removed', max_length=12)),
                ('created', models.DateTimeField(auto_now_add=True)),
                ('document', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='documents.document')),
                ('tag', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='documents.tag')),
            ],
            options={
                'constraints': [models.UniqueConstraint(fields=('document', 'tag'), name='usl_document_tag_decision')],
            },
        ),
        migrations.CreateModel(
            name='TagRule',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('minimum_score', models.PositiveSmallIntegerField()),
                ('tag', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, to='documents.tag')),
            ],
            options={
                'constraints': [models.CheckConstraint(condition=models.Q(('minimum_score__lte', 100)), name='usl_tag_score_at_most_100')],
            },
        ),
    ]
