For k8s-dashboard: 


Be connected to VPN (since it's using the private ingress gateway)
Get the login token:
kubectl get secret dashboard-admin-token -n kubernetes-dashboard -o jsonpath='{.data.token}' | base64 -d

