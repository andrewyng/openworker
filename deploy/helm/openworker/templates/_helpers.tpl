{{- define "ow.name" -}}
{{- printf "%s-openworker" .Release.Name | trunc 50 | trimSuffix "-" -}}
{{- end -}}
{{- define "ow.labels" -}}
app.kubernetes.io/name: openworker
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}
{{- define "ow.security" -}}
runAsNonRoot: true
runAsUser: 1000
runAsGroup: 1000
allowPrivilegeEscalation: false
readOnlyRootFilesystem: true
capabilities:
  drop: ["ALL"]
seccompProfile:
  type: RuntimeDefault
{{- end -}}
