# Uptime Kuma Kubernetes Operator 🚀

A lightweight Kubernetes Operator built with **Python** and **Kopf** that automatically manages [Uptime Kuma](https://github.com/louislam/uptime-kuma) monitors based on Deployment annotations.

## 🌟 Features

- **Automated Lifecycle**: Creates, updates, and deletes monitors automatically when Deployments change.
- **Cluster-Wide**: Watches all namespaces by default.
- **Support for Multiple Types**: Supports `HTTP`, `TCP (Port)`, `Ping`, `DNS`, `MySQL`, `PostgreSQL`, `MongoDB`, and `Redis`.
- **Notification Integration**: Link monitors to existing Uptime Kuma notification groups by name.
- **Monitor Groups**: Automatically assign monitors to parent groups.
- **Custom Status Codes**: Define accepted HTTP status codes.
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

Add the following annotations to your Deployment:

```yaml
metadata:
  annotations:
    uptime-kuma.io/enabled: "true"
    uptime-kuma.io/type: "http"
    uptime-kuma.io/url: "https://example.com"
    uptime-kuma.io/interval: "60"
    uptime-kuma.io/retries: "3"
    uptime-kuma.io/notifications: "Slack, Email Admin"
    uptime-kuma.io/group: "Web Services"
    uptime-kuma.io/accepted-status-codes: "200-299,401"
```

### Supported Annotations

| Annotation | Description | Default |
| :--- | :--- | :--- |
| `uptime-kuma.io/enabled` | `"true"` to enable monitoring. | `false` |
| `uptime-kuma.io/type` | `http`, `port`, `ping`, `dns`, `mysql`, `postgresql`, `mongodb`, `redis`. | `http` |
| `uptime-kuma.io/url` | Full URL (for `http`). | - |
| `uptime-kuma.io/hostname` | Hostname/IP (for `port`, `ping`, `dns`, `mysql`, `postgresql`, `redis`). | - |
| `uptime-kuma.io/port` | Port number (for `port`, `mysql`, `postgresql`, `redis`). | - |
| `uptime-kuma.io/interval` | Heartbeat interval (sec). | `60` |
| `uptime-kuma.io/retries` | Maximum retries before down. | `3` |
| `uptime-kuma.io/notifications` | Notification names (comma-separated). | - |
| `uptime-kuma.io/group` | Parent group name in Uptime Kuma. | - |
| `uptime-kuma.io/accepted-status-codes` | Accepted HTTP codes (e.g. `200-299`, `200,201`). | `200-299` |
| `uptime-kuma.io/db-user` | Database username. | - |
| `uptime-kuma.io/db-password` | Database password. | - |
| `uptime-kuma.io/db-name` | Database name. | - |
| `uptime-kuma.io/db-connection-string` | Connection string (required for `mongodb`). | - |

## 🛠 Technical Details
- **Framework**: [Kopf](https://kopf.readthedocs.io/)
- **API Client**: [uptime-kuma-api-v2](https://github.com/exaland/uptime-kuma-api-v2)
- **Protocol**: Socket.IO (Persistent connection managed by the operator)

## 🤝 Contributing
Contributions are welcome! Feel free to open an Issue or a Pull Request.

## 📄 License
This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
