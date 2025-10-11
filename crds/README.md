# Custom Resource Definitions (CRDs)

This directory contains Custom Resource Definitions required for the Pulsar deployment.

## Prometheus Operator CRDs

The `prometheus-operator-crds.yaml` file contains all CRDs needed for the kube-prometheus-stack monitoring.

**Current Version**: v0.86.0

### Updating CRDs

To update to the latest version:

```bash
# Get the latest release version
PROM_VERSION=$(curl -fs -o /dev/null -w '%{redirect_url}' https://github.com/prometheus-operator/prometheus-operator/releases/latest | xargs basename)
echo "Latest version: $PROM_VERSION"

# Download the CRDs (stripped-down version without validation schemas)
curl -sL "https://github.com/prometheus-operator/prometheus-operator/releases/download/$PROM_VERSION/stripped-down-crds.yaml" \
  -o crds/prometheus-operator-crds.yaml

echo "✓ Updated to $PROM_VERSION"
```

### Why Local CRDs?

We maintain local copies of CRDs to ensure:
- **Reproducibility**: Deployments work even if upstream URLs change
- **Version Control**: Track CRD changes alongside code
- **Offline Capability**: Deploy without internet dependency on upstream repos
- **Stability**: Avoid breaking changes from upstream modifications

### What's Included

The `prometheus-operator-crds.yaml` file includes:
- `Prometheus` - Prometheus server deployment
- `Alertmanager` - Alert routing and management
- `ServiceMonitor` - Service-level metrics scraping
- `PodMonitor` - Pod-level metrics scraping (used by Pulsar components)
- `PrometheusRule` - Recording and alerting rules
- `Probe` - Blackbox probing
- `ThanosRuler` - Thanos rule evaluation
- `AlertmanagerConfig` - Alertmanager configuration

### References

- [Prometheus Operator GitHub](https://github.com/prometheus-operator/prometheus-operator)
- [Prometheus Operator Documentation](https://prometheus-operator.dev/)
- [Release Notes](https://github.com/prometheus-operator/prometheus-operator/releases)
- [kube-prometheus-stack Helm Chart](https://github.com/prometheus-community/helm-charts/tree/main/charts/kube-prometheus-stack)
