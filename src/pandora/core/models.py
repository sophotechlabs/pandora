from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from django.db import DEFAULT_DB_ALIAS, models, transaction
from django.utils import timezone


class TokenSource(models.TextChoices):
    AM = "am", "Alertmanager"
    SDK = "sdk", "SDK"
    LOG = "log", "Log lines"
    CRON = "cron", "Cron check-ins"
    OTLP = "otlp", "OpenTelemetry"
    CI = "ci", "CI"


class TokenScope(models.TextChoices):
    INGEST = "ingest", "Ingest"
    ARTIFACTS = "artifacts", "Artifacts"
    DEPLOY = "deploy", "Deploy"
    READ = "read", "Read"
    PAYLOAD = "payload", "Payload"


class Project(models.Model):
    slug = models.SlugField(max_length=100, unique=True)
    name = models.CharField(max_length=200)
    resolve_on_deploy = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ("slug",)

    def __str__(self) -> str:
        return self.name


class DsnKey(models.Model):
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="dsn_keys",
    )
    public_key = models.CharField(max_length=64, unique=True)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        indexes = [
            models.Index(fields=["project", "active"], name="core_dsnkey_proj_active"),
        ]

    def __str__(self) -> str:
        return f"{self.project.slug}/{self.public_key[:8]}"


class IngestTokenManager(models.Manager["IngestToken"]):
    def create(self, **kwargs: Any) -> IngestToken:
        legacy_scope = kwargs.pop("scope", None)
        scopes = kwargs.pop("scopes", None)
        if legacy_scope is not None and scopes is not None:
            raise ValueError("set scope or scopes, not both")
        database = self.db
        with transaction.atomic(using=database):
            token = super().create(**kwargs)
            if scopes is not None:
                token.set_scopes(scopes)
                return token
            if legacy_scope == TokenScope.PAYLOAD:
                token.set_scopes((TokenScope.READ, TokenScope.PAYLOAD))
                return token
            if legacy_scope == TokenScope.INGEST:
                token.set_scopes((TokenScope.INGEST, TokenScope.ARTIFACTS))
                return token
            if legacy_scope is not None:
                token.set_scopes((legacy_scope,))
                return token
            token.set_scopes((TokenScope.INGEST, TokenScope.ARTIFACTS))
            return token


class IngestToken(models.Model):
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="tokens",
    )
    name = models.CharField(max_length=200)
    token = models.CharField(max_length=128, unique=True)
    source = models.CharField(
        max_length=8,
        choices=TokenSource.choices,
        default=TokenSource.AM,
    )
    environment = models.CharField(max_length=100, blank=True, default="")
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(default=timezone.now)
    objects = IngestTokenManager()

    class Meta:
        indexes = [
            models.Index(
                fields=["source", "active"],
                name="core_token_src_active",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.project.slug}/{self.name}"

    def has_scope(self, scope: TokenScope | str) -> bool:
        value = str(scope)
        annotation = f"_has_{value}_scope"
        if hasattr(self, annotation):
            return bool(getattr(self, annotation))
        return self.scope_grants.filter(scope=value).exists()

    def set_scopes(self, scopes: Sequence[TokenScope | str]) -> None:
        values = {str(scope) for scope in scopes}
        unknown = values.difference(TokenScope.values)
        if unknown:
            raise ValueError(
                f"unknown token capabilities: {', '.join(sorted(unknown))}"
            )
        database = self._state.db
        if database is None:
            database = DEFAULT_DB_ALIAS
        with transaction.atomic(using=database):
            self.scope_grants.exclude(scope__in=values).delete()
            existing = set(
                self.scope_grants.filter(scope__in=values).values_list(
                    "scope",
                    flat=True,
                )
            )
            TokenScopeGrant.objects.using(database).bulk_create(
                [
                    TokenScopeGrant(token=self, scope=scope)
                    for scope in values - existing
                ]
            )

    @property
    def scopes(self) -> tuple[str, ...]:
        return tuple(
            self.scope_grants.order_by("scope").values_list("scope", flat=True)
        )


class TokenScopeGrant(models.Model):
    token = models.ForeignKey(
        IngestToken,
        on_delete=models.CASCADE,
        related_name="scope_grants",
    )
    scope = models.CharField(max_length=16, choices=TokenScope.choices)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["token", "scope"],
                name="core_token_scope_uq",
            ),
        ]
        indexes = [
            models.Index(fields=["scope", "token"], name="core_scope_token"),
        ]

    def __str__(self) -> str:
        return f"{self.token_id}/{self.scope}"


class ServiceLink(models.Model):
    name = models.CharField(max_length=100)
    url_template = models.TextField()
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="service_links",
        null=True,
        blank=True,
    )
    ordering = models.IntegerField(default=100)
    active = models.BooleanField(default=True)

    class Meta:
        indexes = [
            models.Index(
                fields=["active", "ordering"],
                name="core_link_active_order",
            ),
        ]
        ordering = ("ordering", "name")

    def __str__(self) -> str:
        return self.name
