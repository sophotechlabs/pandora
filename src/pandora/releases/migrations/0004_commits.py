import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0008_project_auto_resolve"),
        ("releases", "0003_deploy_identity"),
    ]

    operations = [
        migrations.CreateModel(
            name="Repository",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("name", models.CharField(max_length=200)),
                (
                    "provider",
                    models.CharField(
                        choices=[
                            ("github", "GitHub"),
                            ("gitlab", "GitLab"),
                            ("bitbucket", "Bitbucket"),
                            ("git", "Plain git"),
                        ],
                        default="git",
                        max_length=16,
                    ),
                ),
                ("url", models.CharField(blank=True, default="", max_length=500)),
                ("active", models.BooleanField(default=True)),
                (
                    "project",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="repositories",
                        to="core.project",
                    ),
                ),
            ],
            options={
                "ordering": ("name",),
            },
        ),
        migrations.CreateModel(
            name="Commit",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("key", models.CharField(max_length=64)),
                ("message", models.TextField(blank=True, default="")),
                (
                    "author_name",
                    models.CharField(blank=True, default="", max_length=200),
                ),
                (
                    "author_email",
                    models.CharField(blank=True, default="", max_length=254),
                ),
                (
                    "committed_at",
                    models.DateTimeField(default=django.utils.timezone.now),
                ),
                (
                    "repository",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="commits",
                        to="releases.repository",
                    ),
                ),
            ],
            options={
                "ordering": ("-committed_at", "-pk"),
            },
        ),
        migrations.CreateModel(
            name="CodeMapping",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "stack_root",
                    models.CharField(blank=True, default="", max_length=200),
                ),
                (
                    "source_root",
                    models.CharField(blank=True, default="", max_length=200),
                ),
                ("default_branch", models.CharField(default="main", max_length=100)),
                ("ordering", models.IntegerField(default=100)),
                ("active", models.BooleanField(default=True)),
                (
                    "project",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="code_mappings",
                        to="core.project",
                    ),
                ),
                (
                    "repository",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="code_mappings",
                        to="releases.repository",
                    ),
                ),
            ],
            options={
                "ordering": ("ordering", "pk"),
            },
        ),
        migrations.CreateModel(
            name="CommitFile",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("path", models.CharField(max_length=500)),
                (
                    "change",
                    models.CharField(
                        choices=[("A", "Added"), ("M", "Modified"), ("D", "Deleted")],
                        default="M",
                        max_length=1,
                    ),
                ),
                (
                    "commit",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="files",
                        to="releases.commit",
                    ),
                ),
            ],
            options={
                "ordering": ("path",),
                "indexes": [models.Index(fields=["path"], name="releases_commit_path")],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("commit", "path"), name="releases_commit_file_uq"
                    )
                ],
            },
        ),
        migrations.CreateModel(
            name="ReleaseCommit",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("order", models.PositiveIntegerField(default=0)),
                (
                    "commit",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="release_commits",
                        to="releases.commit",
                    ),
                ),
                (
                    "release",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="release_commits",
                        to="releases.release",
                    ),
                ),
            ],
            options={
                "ordering": ("order", "pk"),
                "constraints": [
                    models.UniqueConstraint(
                        fields=("release", "commit"), name="releases_release_commit_uq"
                    )
                ],
            },
        ),
        migrations.AddConstraint(
            model_name="repository",
            constraint=models.UniqueConstraint(
                fields=("project", "name"), name="releases_repository_uq"
            ),
        ),
        migrations.AddIndex(
            model_name="commit",
            index=models.Index(fields=["-committed_at"], name="releases_commit_when"),
        ),
        migrations.AddConstraint(
            model_name="commit",
            constraint=models.UniqueConstraint(
                fields=("repository", "key"), name="releases_commit_uq"
            ),
        ),
        migrations.AddIndex(
            model_name="codemapping",
            index=models.Index(
                fields=["active", "ordering"], name="releases_mapping_order"
            ),
        ),
    ]
