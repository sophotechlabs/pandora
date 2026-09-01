{{- define "pandora.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "pandora.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name (include "pandora.name" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{- define "pandora.labels" -}}
app.kubernetes.io/name: {{ include "pandora.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" }}
{{- end -}}

{{- define "pandora.selectorLabels" -}}
app.kubernetes.io/name: {{ include "pandora.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "pandora.secretName" -}}
{{- default (printf "%s-secrets" (include "pandora.fullname" .)) .Values.secrets.existingSecret -}}
{{- end -}}

{{- define "pandora.claimName" -}}
{{- default (printf "%s-data" (include "pandora.fullname" .)) .Values.persistence.existingClaim -}}
{{- end -}}

{{- define "pandora.image" -}}
{{- printf "%s:%s" .Values.image.repository (default .Chart.AppVersion .Values.image.tag) -}}
{{- end -}}

{{- define "pandora.databaseUrl" -}}
{{- if .Values.database.url -}}
{{- .Values.database.url -}}
{{- else -}}
{{- printf "sqlite:///%s" .Values.database.path -}}
{{- end -}}
{{- end -}}

{{- define "pandora.env" -}}
- name: DJANGO_DEBUG
  value: {{ ternary "True" "False" .Values.settings.debug | quote }}
- name: DJANGO_SECURE_COOKIES
  value: {{ ternary "1" "0" .Values.settings.secureCookies | quote }}
- name: DJANGO_TRUST_PROXY_HEADER
  value: {{ ternary "1" "0" .Values.settings.trustProxyHeader | quote }}
- name: POD_IP
  valueFrom:
    fieldRef:
      fieldPath: status.podIP
- name: DJANGO_ALLOWED_HOSTS
  value: "{{ .Values.host }},{{ include "pandora.fullname" . }}.{{ .Release.Namespace }}.svc.cluster.local,$(POD_IP)"
- name: DJANGO_CSRF_TRUSTED_ORIGINS
  value: "https://{{ .Values.host }}"
- name: PANDORA_BASE_URL
  value: "https://{{ .Values.host }}"
- name: PANDORA_ENV
  value: {{ default .Release.Namespace .Values.settings.environment | quote }}
- name: PANDORA_LOG_LEVEL
  value: {{ .Values.settings.logLevel | quote }}
- name: PANDORA_RETENTION_DAYS
  value: {{ .Values.settings.retentionDays | quote }}
- name: PANDORA_ENVELOPE_RETENTION_DAYS
  value: {{ .Values.settings.envelopeRetentionDays | quote }}
- name: PANDORA_INGEST_MAX_BYTES
  value: {{ .Values.settings.ingestMaxBytes | int | quote }}
- name: PANDORA_CORRELATION_KEYS
  value: {{ .Values.settings.correlationKeys | quote }}
- name: DATABASE_URL
  value: {{ include "pandora.databaseUrl" . | quote }}
- name: PROMETHEUS_MULTIPROC_DIR
  value: /tmp/prometheus
{{- if .Values.persistence.enabled }}
- name: PANDORA_ARTIFACT_DIR
  value: /data/artifacts
{{- end }}
{{- if .Values.settings.grafanaUrl }}
- name: PANDORA_GRAFANA_URL
  value: {{ .Values.settings.grafanaUrl | quote }}
{{- end }}
{{- if .Values.settings.lokiQueryUrl }}
- name: PANDORA_LOKI_QUERY_URL
  value: {{ .Values.settings.lokiQueryUrl | quote }}
{{- end }}
{{- if .Values.oidc.issuer }}
- name: PANDORA_OIDC_ISSUER
  value: {{ .Values.oidc.issuer | quote }}
- name: PANDORA_OIDC_CLIENT_ID
  value: {{ .Values.oidc.clientId | quote }}
- name: PANDORA_OIDC_SCOPES
  value: {{ .Values.oidc.scopes | quote }}
- name: PANDORA_OIDC_GROUPS_CLAIM
  value: {{ .Values.oidc.groupsClaim | quote }}
- name: PANDORA_OIDC_TEAM
  value: {{ .Values.oidc.team | quote }}
- name: PANDORA_OIDC_OWNER_GROUP
  value: {{ .Values.oidc.ownerGroup | quote }}
- name: PANDORA_OIDC_MEMBER_GROUP
  value: {{ .Values.oidc.memberGroup | quote }}
- name: PANDORA_OIDC_VIEWER_GROUP
  value: {{ .Values.oidc.viewerGroup | quote }}
- name: PANDORA_OIDC_DEFAULT_ROLE
  value: {{ .Values.oidc.defaultRole | quote }}
{{- end }}
{{- if .Values.alertmanager.url }}
- name: PANDORA_AM_URL
  value: {{ .Values.alertmanager.url | quote }}
- name: PANDORA_RECONCILE_IGNORE
  value: {{ .Values.alertmanager.ignore | quote }}
{{- end }}
{{- if .Values.alertmanager.caBundle.secretName }}
- name: PANDORA_AM_CA_BUNDLE
  value: /etc/pandora/ca/{{ .Values.alertmanager.caBundle.key }}
{{- end }}
{{- if .Values.otel.endpoint }}
- name: OTEL_EXPORTER_OTLP_ENDPOINT
  value: {{ .Values.otel.endpoint | quote }}
- name: OTEL_EXPORTER_OTLP_PROTOCOL
  value: {{ .Values.otel.protocol | quote }}
- name: OTEL_PYTHON_DJANGO_EXCLUDED_URLS
  value: "health,metrics"
{{- end }}
{{- with .Values.extraEnv }}
{{- toYaml . | nindent 0 }}
{{- end }}
{{- end -}}

{{- define "pandora.volumes" -}}
- name: tmp
  emptyDir: {}
- name: prometheus-multiproc
  emptyDir: {}
{{- if .Values.persistence.enabled }}
- name: data
  persistentVolumeClaim:
    claimName: {{ include "pandora.claimName" . }}
{{- end }}
{{- if .Values.alertmanager.caBundle.secretName }}
- name: alertmanager-ca
  secret:
    secretName: {{ .Values.alertmanager.caBundle.secretName }}
{{- end }}
{{- end -}}

{{- define "pandora.volumeMounts" -}}
- name: tmp
  mountPath: /tmp
- name: prometheus-multiproc
  mountPath: /tmp/prometheus
{{- if .Values.persistence.enabled }}
- name: data
  mountPath: /data
{{- end }}
{{- if .Values.alertmanager.caBundle.secretName }}
- name: alertmanager-ca
  mountPath: /etc/pandora/ca
  readOnly: true
{{- end }}
{{- end -}}
