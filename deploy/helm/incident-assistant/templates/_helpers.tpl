{{- define "ia.labels" -}}
app.kubernetes.io/part-of: incident-assistant
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
{{- end }}

{{- define "ia.selector" -}}
app.kubernetes.io/part-of: incident-assistant
app.kubernetes.io/name: {{ . }}
{{- end }}

{{- define "ia.image" -}}
{{ .ctx.Values.image.registry }}{{ .ctx.Values.image.repository }}/{{ .name }}:{{ .ctx.Values.image.tag }}
{{- end }}

{{/* Pod hardening shared by every app container. */}}
{{- define "ia.podSecurity" -}}
securityContext:
  runAsNonRoot: true
  runAsUser: 10001
  runAsGroup: 10001
  fsGroup: 10001
  seccompProfile: { type: RuntimeDefault }
automountServiceAccountToken: false
{{- end }}

{{- define "ia.containerSecurity" -}}
securityContext:
  allowPrivilegeEscalation: false
  readOnlyRootFilesystem: true
  capabilities: { drop: ["ALL"] }
{{- end }}

{{/* A secret key as an env var. */}}
{{- define "ia.secretEnv" -}}
- name: {{ .key }}
  valueFrom:
    secretKeyRef:
      name: {{ .ctx.Values.secrets.existingSecret }}
      key: {{ .key }}
      {{- if .optional }}
      optional: true
      {{- end }}
{{- end }}

{{/* DATABASE_URL built from a password env var defined just before it ($(VAR) expansion). */}}
{{- define "ia.dbUrl" -}}
postgresql://{{ .user }}:$({{ .passKey }})@{{ .ctx.Values.postgres.host }}:{{ .ctx.Values.postgres.port }}/{{ .ctx.Values.postgres.database }}?sslmode={{ .ctx.Values.postgres.sslmode }}
{{- end }}

{{- define "ia.dbEnv" -}}
{{ include "ia.secretEnv" (dict "key" .passKey "ctx" .ctx) }}
- name: {{ .name | default "DATABASE_URL" }}
  value: {{ include "ia.dbUrl" . | quote }}
{{- end }}

{{- define "ia.tmp" -}}
- name: tmp
  emptyDir: { sizeLimit: 64Mi }
{{- end }}


{{/* One NetworkPolicy allowing ingress to pod .to from pods .from on .ports. */}}
{{- define "ia.allow" }}
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: allow-to-{{ .to }}
  labels: {{- include "ia.labels" .ctx | nindent 4 }}
spec:
  podSelector:
    matchLabels: {{- include "ia.selector" .to | nindent 6 }}
  policyTypes: [Ingress]
  ingress:
    - from:
        {{- range .from }}
        - podSelector:
            matchLabels: {{- include "ia.selector" . | nindent 14 }}
        {{- end }}
      ports:
        {{- range .ports }}
        - { port: {{ . }}, protocol: TCP }
        {{- end }}
{{- end }}

{{/*
Init container that waits until this service's database role can log in. Roles get their login
only when the migrate job sets their passwords, so this is exactly "migrations are done".
Without it, services crash-loop until then.
*/}}
{{- define "ia.waitForDb" -}}
- name: wait-for-database
  image: {{ include "ia.image" (dict "name" .image "ctx" .ctx) }}
  imagePullPolicy: {{ .ctx.Values.image.pullPolicy }}
  command:
    - python
    - -c
    - |
      import asyncio, os, sys, time, asyncpg
      async def main():
          for attempt in range(180):
              try:
                  conn = await asyncpg.connect(os.environ["DATABASE_URL"], timeout=5)
                  await conn.close()
                  return
              except Exception as exc:
                  if attempt % 10 == 0:
                      print(f"waiting for the database and migrations: {type(exc).__name__}", flush=True)
                  await asyncio.sleep(2)
          sys.exit("database not ready after 6 minutes")
      asyncio.run(main())
  env:
    {{- include "ia.dbEnv" (dict "user" .user "passKey" .passKey "ctx" .ctx) | nindent 4 }}
  resources: { requests: { cpu: 10m, memory: 64Mi }, limits: { memory: 128Mi } }
  {{- include "ia.containerSecurity" .ctx | nindent 2 }}
{{- end }}

{{/* Init container that waits until a TCP endpoint accepts connections. */}}
{{- define "ia.waitForTcp" -}}
- name: wait-for-{{ .host }}
  image: {{ include "ia.image" (dict "name" .image "ctx" .ctx) }}
  imagePullPolicy: {{ .ctx.Values.image.pullPolicy }}
  command:
    - python
    - -c
    - |
      import socket, sys, time
      for _ in range(180):
          try:
              socket.create_connection(("{{ .host }}", {{ .port }}), timeout=3).close()
              sys.exit(0)
          except OSError:
              time.sleep(2)
      sys.exit("{{ .host }}:{{ .port }} not reachable after 6 minutes")
  resources: { requests: { cpu: 10m, memory: 32Mi }, limits: { memory: 64Mi } }
  {{- include "ia.containerSecurity" .ctx | nindent 2 }}
{{- end }}
