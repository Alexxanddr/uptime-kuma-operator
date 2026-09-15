# Uptime Kuma Kubernetes Operator 🚀

A lightweight Kubernetes Operator built with **Python** and **Kopf** that automatically manages [Uptime Kuma](https://github.com/louislam/uptime-kuma) monitors based on Deployment annotations.

## 🌟 Features

- **Automated Lifecycle**: Creates, updates, and deletes monitors automatically when Deployments change.
- **Cluster-Wide**: Watches all namespaces by default.
- **Support for Multiple Types**: Supports `HTTP`, `TCP`, `Ping`, and `DNS`.
- **Group Support**: Assign monitors to parent groups in Uptime Kuma.
- **Notification Integration**: Link monitors to existing Uptime Kuma notification groups by name.
- **Secure**: Credentials handled via Kubernetes Secrets.
- **Idempotent**: Uses a predictable naming convention (`k8s-{namespace}-{name}`) to prevent duplicates.

## 🚀 Installation

### 1. Configure Credentials
Edit `kubernetes/secret.yaml` with your Uptime Kuma URL and login details, then apply:
```bash
kubectl apply -f kubernetes/secret.yaml
```

### 2. Deploy RBAC and Operator
```bash
kubectl apply -f kubernetes/rbac.yaml
kubectl apply -f kubernetes/operator.yaml
```

## 📖 Usage

Add the annotations either to the Deployment metadata (`metadata.annotations`) or to the Pod template (`spec.template.metadata.annotations`):

```yaml
metadata:
  annotations:
    uptime-kuma.io/enabled: "true"
    uptime-kuma.io/name: "My Custom Service Name"
    uptime-kuma.io/type: "http"
    uptime-kuma.io/url: "https://example.com"
    uptime-kuma.io/status-codes: "200-299, 301, 302"
    uptime-kuma.io/interval: "60"
    uptime-kuma.io/retries: "3"
    uptime-kuma.io/notifications: "Slack, Email Admin"
    uptime-kuma.io/group: "Web Services"
```

| Annotation | Description | Default |
| :--- | :--- | :--- |
| `uptime-kuma.io/enabled` | `"true"` to enable monitoring. | `false` |
| `uptime-kuma.io/name` | Custom display name for the monitor. | `k8s-{namespace}-{name}` |
| `uptime-kuma.io/type` | Type: `http`, `tcp`, `ping`, `dns`. | `http` |
| `uptime-kuma.io/url` | Full URL (for `http`). | - |
| `uptime-kuma.io/status-codes` | Accepted HTTP status codes (e.g. `200-299, 301`). | `200-299` |
| `uptime-kuma.io/hostname` | Hostname/IP (for `tcp`, `ping`, `dns`). | - |
| `uptime-kuma.io/port` | Port number (for `tcp`). | `80` |
| `uptime-kuma.io/interval` | Heartbeat interval (sec). | `60` |
| `uptime-kuma.io/retries` | Maximum retries. | `3` |
| `uptime-kuma.io/notifications` | Notification names (comma-separated). | - |
| `uptime-kuma.io/group` | Parent group name or ID in Uptime Kuma. | - |

## 🛠 Technical Details
- **Framework**: [Kopf](https://kopf.readthedocs.io/)
- **API Client**: [uptime-kuma-api2](https://github.com/pbarone/uptime-kuma-api2) (Uptime Kuma v1.x and v2.x support)
- **Protocol**: Socket.IO

## 🤝 Contributing
Contributions are welcome! Feel free to open an Issue or a Pull Request.

## 📄 License
This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
