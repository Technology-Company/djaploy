{{- define "django-app.fullname" -}}
{{- default .Release.Name .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "django-app.labels" -}}
app.kubernetes.io/name: django-app
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Values.image.tag | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version }}
{{- end -}}

{{/* Call with (dict "root" . "component" "web") */}}
{{- define "django-app.selectorLabels" -}}
app.kubernetes.io/name: django-app
app.kubernetes.io/instance: {{ .root.Release.Name }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{- define "django-app.image" -}}
{{ required "image.repository is required" .Values.image.repository }}:{{ required "image.tag is required (the app's CI sets it)" .Values.image.tag }}
{{- end -}}

{{- define "django-app.primaryHost" -}}
{{ required "hostnames needs at least one entry" (first .Values.hostnames) }}
{{- end -}}

{{/*
The environment contract every app reads (see templates/snippets/settings_env.py and docs/04-chart-reference.md).
*/}}
{{- define "django-app.env" -}}
{{- $origins := list -}}
{{- range .Values.hostnames }}{{ $origins = append $origins (printf "https://%s" .) }}{{ end -}}
- name: DJANGO_ALLOWED_HOSTS
  value: {{ join "," .Values.hostnames | quote }}
- name: DJANGO_CSRF_TRUSTED_ORIGINS
  value: {{ join "," $origins | quote }}
- name: DJANGO_BASE_URL
  value: {{ printf "https://%s" (include "django-app.primaryHost" .) | quote }}
- name: DJANGO_DATA_DIR
  value: /data
- name: MEDIA_ROOT
  value: /data/media
{{- with .Values.django.settingsModule }}
- name: DJANGO_SETTINGS_MODULE
  value: {{ . | quote }}
{{- end }}
{{- if eq .Values.database.engine "sqlite" }}
- name: SQLITE_PATH
  value: {{ .Values.database.sqlite.path | quote }}
{{- else if eq .Values.database.engine "postgres" }}
- name: DATABASE_URL
  valueFrom:
    secretKeyRef:
      name: {{ required "database.postgres.secretName is required" .Values.database.postgres.secretName }}
      key: {{ .Values.database.postgres.secretKey }}
{{- else }}
{{- fail "database.engine must be sqlite or postgres" }}
{{- end }}
{{- range $name, $value := .Values.django.env }}
- name: {{ $name }}
  value: {{ $value | quote }}
{{- end }}
{{- range $name, $ref := .Values.onePassword.env }}
- name: {{ $name }}
  valueFrom:
    secretKeyRef:
      name: {{ include "django-app.opSecretName" (dict "root" $ "item" (include "django-app.opItem" $ref)) }}
      key: {{ include "django-app.opField" $ref }}
{{- end }}
{{- end -}}

{{/* "<item>/<field>" → item / field. Item titles may themselves contain "/". */}}
{{- define "django-app.opItem" -}}
{{- $parts := splitList "/" . -}}
{{- if lt (len $parts) 2 }}{{ fail (printf "onePassword.env value %q must be \"<item>/<field>\"" .) }}{{ end -}}
{{- initial $parts | join "/" -}}
{{- end -}}

{{- define "django-app.opField" -}}
{{- last (splitList "/" .) -}}
{{- end -}}

{{/* Secret synced from one 1Password item. Call with (dict "root" . "item" "<title>"). */}}
{{- define "django-app.opSecretName" -}}
{{- printf "%s-op-%s" (include "django-app.fullname" .root) (regexReplaceAll "[^a-z0-9]+" (lower .item) "-" | trimAll "-") | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/* env (+ envFrom) for a container, indented by the caller. */}}
{{- define "django-app.containerEnv" -}}
env:
  {{- include "django-app.env" . | nindent 2 }}
{{- with .Values.envFromSecret }}
envFrom:
  - secretRef:
      name: {{ . }}
{{- end }}
{{- end -}}

{{- define "django-app.podCommon" -}}
{{- if .Values.onePassword.pullSecretItem }}
imagePullSecrets:
  - name: ghcr-pull
{{- end }}
securityContext:
  {{- toYaml .Values.podSecurityContext | nindent 2 }}
{{- end -}}

{{- define "django-app.dataVolume" -}}
- name: data
  persistentVolumeClaim:
    claimName: {{ default (printf "%s-data" (include "django-app.fullname" .)) .Values.storage.existingClaim }}
{{- end -}}

{{/* Host + X-Forwarded-Proto so ALLOWED_HOSTS and SECURE_SSL_REDIRECT accept the probe. */}}
{{- define "django-app.probe" -}}
httpGet:
  path: {{ .Values.probes.path }}
  port: gunicorn
  httpHeaders:
    - name: Host
      value: {{ include "django-app.primaryHost" . | quote }}
    - name: X-Forwarded-Proto
      value: https
{{- end -}}
