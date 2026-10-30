# Annotation examples

Apply these snippets to workload metadata or Pod template metadata. Top-level
metadata wins per key. All three supported workload kinds use the same contract.

HTTP Deployment:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: web
  annotations:
    uptime-kuma.io/enabled: "true"
    uptime-kuma.io/type: "http"
    uptime-kuma.io/url: "https://example.com"
    uptime-kuma.io/status-codes: '["200-299", "301"]'
spec:
  replicas: 0
  selector:
    matchLabels: {app: web}
  template:
    metadata:
      labels: {app: web}
    spec:
      containers:
        - name: web
          image: nginx:1.28
```

TCP StatefulSet metadata (the StatefulSet still needs its ordinary spec):

```yaml
metadata:
  annotations:
    uptime-kuma.io/enabled: "true"
    uptime-kuma.io/type: "tcp"
    uptime-kuma.io/hostname: "redis.database.svc"
    uptime-kuma.io/port: "6379"
```

Legacy equivalent: `type: "port"`, `url: "redis.database.svc:6379"`.

Ping DaemonSet metadata:

```yaml
metadata:
  annotations:
    uptime-kuma.io/enabled: "true"
    uptime-kuma.io/type: "ping"
    uptime-kuma.io/hostname: "10.0.0.1"
```

DNS on any supported workload:

```yaml
metadata:
  annotations:
    uptime-kuma.io/enabled: "true"
    uptime-kuma.io/type: "dns"
    uptime-kuma.io/hostname: "example.com"
```

To associate existing Kuma objects, add `notifications: "Slack, Email Admin"`
and/or `group: "Web Services"` under the annotation prefix. They must exist and
be unambiguous. The operator does not create notification providers or groups.
