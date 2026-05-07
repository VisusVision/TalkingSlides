# Kubernetes Autoscale (KEDA) - VidLab

Bu klasor, worker deployment'larini Prometheus metriklerine gore otomatik olceklendirmek icin ornek manifestler icerir.

## Neden?
- Yuk artinca kuyruk birikir, KEDA yeni pod acarak beklemeyi azaltir.
- Yuk dusunce pod sayisini geri indirir, maliyet dusurur.

## Dosyalar
- `namespace.yaml`: `vidlab` namespace'i
- `deployments-workers.yaml`: queue bazli worker deployment ornekleri
- `api-env-example.yaml`: API icin ornek env ConfigMap (`PROMETHEUS_METRICS_TOKEN` dahil)
- `keda/trigger-auth.yaml`: Prometheus auth secret + TriggerAuthentication
- `keda/scaledobjects-workers.yaml`: fast/balanced/quality/avatar icin autoscale kurallari

## Kisa Mantik
- `queue_depth` veya `p95` threshold asarsa: scale up
- Her profil icin farkli min/max replica ve cooldown
- Bu degerler backend'deki autoscale policy endpoint'i ile uyumlu secildi.

## Uygulama
1. `kubectl apply -f infra/k8s/namespace.yaml`
2. `kubectl apply -f infra/k8s/deployments-workers.yaml`
3. `kubectl apply -f infra/k8s/keda/trigger-auth.yaml`
4. `kubectl apply -f infra/k8s/keda/scaledobjects-workers.yaml`

## Notlar
- `serverAddress` alanini kendi Prometheus servis adinla degistir.
- `keda-prometheus-auth` secret degerlerini production credential ile guncelle.
- GPU worker (`worker-avatar`) icin node selector/toleration ekleyebilirsin.
- Production ortamda `PROMETHEUS_METRICS_TOKEN` bos birakma; aksi halde metrics endpoint staff-disinda guvenli sekilde scrape edilemez.
