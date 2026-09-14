# Loki OS Log Management — Python Reporting Dashboard

Dashboard Python sederhana untuk query dan reporting Loki.

## Fungsi

- Loki connectivity / `/ready` check
- Prometheus-like LogQL query console
- Total server berdasarkan heartbeat
- Online server berdasarkan heartbeat 5 menit
- Total application
- Total IP
- OS log records
- Application vs server distribution
- Site / OS / Server Role / Environment distribution
- Log distribution by source
- Server inventory
- Label browser
- Raw Loki JSON
- Log explorer
- CSV export
- Diagnostic queries

## Arsitektur

```text
Fluent Bit
    |
    | HTTP
    v
Nginx :80
    |
    v
Loki :3100
    |
    +--> Python Streamlit Dashboard
```

Tidak menggunakan Prometheus.

## Label yang digunakan

```text
job
host
ip
os
environment
app_group
server_role
site
source
```

Heartbeat:

```text
source=heartbeat
```

OS log:

```text
source!=heartbeat
```

## Instalasi

### Linux / RHEL

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
streamlit run loki_report_dashboard.py --server.address 0.0.0.0 --server.port 8501
```

Buka:

```text
http://<IP_SERVER>:8501
```

### Windows

```cmd
py -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
streamlit run loki_report_dashboard.py --server.address 0.0.0.0 --server.port 8501
```

## Konfigurasi Loki

Default:

```text
http://192.168.114.75
```

Bisa diubah langsung dari sidebar.

Jika dashboard dijalankan di server Loki dan ingin bypass Nginx:

```text
http://127.0.0.1:3100
```

Namun pada desain saat ini endpoint yang direkomendasikan adalah:

```text
http://192.168.114.75
```

karena Nginx menerima request pada port 80.

## Troubleshooting

### 1. Loki READY gagal

Dari server dashboard:

```bash
curl -v http://192.168.114.75/ready
```

Expected:

```text
ready
```

### 2. Test API Loki

```bash
curl -G http://192.168.114.75/loki/api/v1/query \
  --data-urlencode 'query=count(count_over_time({job="os-logs"}[5m]))'
```

### 3. Cek heartbeat

```bash
curl -G http://192.168.114.75/loki/api/v1/query \
  --data-urlencode 'query=count(sum by (host) (count_over_time({job="os-logs",source="heartbeat"}[5m])))'
```

### 4. Cek label

```bash
curl http://192.168.114.75/loki/api/v1/labels
```

### 5. Cek value host

```bash
curl http://192.168.114.75/loki/api/v1/label/host/values
```

## Catatan penting tentang inventory

Inventory 30 hari menggunakan heartbeat:

```logql
count(
  sum by (host) (
    count_over_time(
      {job="os-logs",source="heartbeat"}[30d]
    )
  )
)
```

Status online 5 menit:

```logql
count(
  sum by (host) (
    count_over_time(
      {job="os-logs",source="heartbeat"}[5m]
    )
  )
)
```

Ini menunjukkan status reporting agent. Tidak boleh dianggap sebagai bukti absolut bahwa operating system mati/hidup.

## Catatan tentang time range

Panel log mengikuti periode yang dipilih di sidebar.

Inventory heartbeat menggunakan 30 hari agar jumlah server tidak berubah hanya karena user memilih Last 15m / Last 6h / Last 24h.

## Security

Dashboard ini hanya membaca Loki HTTP API. Tidak melakukan write/delete terhadap Loki.

Untuk production, sebaiknya:
- gunakan HTTPS di Nginx,
- tambahkan authentication,
- batasi akses port Streamlit,
- jangan expose Loki API langsung ke internet.
