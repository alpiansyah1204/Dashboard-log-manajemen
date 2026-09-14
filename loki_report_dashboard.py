import json
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin

import pandas as pd
import requests
import streamlit as st


# ============================================================
# Loki Report Dashboard
# Centralized OS Log Management - Loki only
#
# Default Loki endpoint:
#   http://192.168.114.75
#
# Expected labels:
#   job, host, ip, os, environment, app_group,
#   server_role, site, source
#
# Heartbeat:
#   source="heartbeat"
#
# This application is intentionally simple:
# - Python + Streamlit
# - requests -> Loki HTTP API
# - pandas -> report tables
# - no Prometheus required
# ============================================================

st.set_page_config(
    page_title="Loki Report Dashboard",
    page_icon="📊",
    layout="wide",
)

DEFAULT_LOKI_URL = "http://192.168.114.75"
DEFAULT_TIMEOUT = 30

COMMON_LABELS = [
    "job",
    "host",
    "ip",
    "os",
    "environment",
    "app_group",
    "server_role",
    "site",
    "source",
]


# -----------------------------
# Helpers
# -----------------------------
def normalize_loki_url(url: str) -> str:
    url = (url or "").strip().rstrip("/")
    if not url:
        return DEFAULT_LOKI_URL
    return url


def api_url(base_url: str, endpoint: str) -> str:
    return f"{normalize_loki_url(base_url)}{endpoint}"


def fmt_number(value):
    try:
        return f"{int(value):,}"
    except Exception:
        return str(value)


def iso_utc(dt):
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def ns_from_datetime(dt):
    return str(int(dt.timestamp() * 1_000_000_000))


def execute_loki(base_url, query, query_type="instant",
                 start=None, end=None, limit=5000, timeout=DEFAULT_TIMEOUT):
    """
    Execute a Loki HTTP API query.

    query_type:
      instant -> /loki/api/v1/query
      range   -> /loki/api/v1/query_range
    """
    base_url = normalize_loki_url(base_url)

    if query_type == "range":
        endpoint = "/loki/api/v1/query_range"
        params = {
            "query": query,
            "start": ns_from_datetime(start),
            "end": ns_from_datetime(end),
            "limit": limit,
            "direction": "backward",
        }
    else:
        endpoint = "/loki/api/v1/query"
        params = {"query": query, "limit": limit}

    url = api_url(base_url, endpoint)

    started = time.perf_counter()
    try:
        response = requests.get(
            url,
            params=params,
            timeout=timeout,
        )
        elapsed = time.perf_counter() - started

        content_type = response.headers.get("content-type", "")
        try:
            data = response.json()
        except Exception:
            data = {"raw_text": response.text}

        return {
            "ok": response.ok,
            "status_code": response.status_code,
            "elapsed": elapsed,
            "url": response.url,
            "data": data,
            "error": None if response.ok else response.text[:2000],
            "content_type": content_type,
        }

    except requests.exceptions.Timeout:
        return {
            "ok": False,
            "status_code": None,
            "elapsed": time.perf_counter() - started,
            "url": url,
            "data": {},
            "error": f"Timeout after {timeout} seconds.",
            "content_type": "",
        }

    except requests.exceptions.ConnectionError as exc:
        return {
            "ok": False,
            "status_code": None,
            "elapsed": time.perf_counter() - started,
            "url": url,
            "data": {},
            "error": f"Connection error: {exc}",
            "content_type": "",
        }

    except Exception as exc:
        return {
            "ok": False,
            "status_code": None,
            "elapsed": time.perf_counter() - started,
            "url": url,
            "data": {},
            "error": f"{type(exc).__name__}: {exc}",
            "content_type": "",
        }


def health_check(base_url, timeout=10):
    """Check Loki readiness endpoint."""
    url = api_url(base_url, "/ready")
    started = time.perf_counter()
    try:
        r = requests.get(url, timeout=timeout)
        return {
            "ok": r.ok,
            "status_code": r.status_code,
            "elapsed": time.perf_counter() - started,
            "text": r.text.strip(),
            "url": r.url,
        }
    except Exception as exc:
        return {
            "ok": False,
            "status_code": None,
            "elapsed": time.perf_counter() - started,
            "text": str(exc),
            "url": url,
        }


def parse_metric_result(data):
    """
    Convert Loki metric query result into a DataFrame.

    Expected Loki response:
      data.resultType = vector
      data.result = [
        {"metric": {...}, "value": [timestamp, "123"]}
      ]
    """
    rows = []

    payload = data.get("data", data)
    results = payload.get("result", []) if isinstance(payload, dict) else []

    for item in results:
        metric = item.get("metric", {})
        value = item.get("value", [])

        row = dict(metric)
        if value:
            row["timestamp"] = value[0]
            try:
                row["value"] = float(value[1])
            except Exception:
                row["value"] = value[1]
        rows.append(row)

    return pd.DataFrame(rows)


def parse_stream_result(data):
    """
    Convert Loki log stream response into a DataFrame.
    """
    payload = data.get("data", data)
    results = payload.get("result", []) if isinstance(payload, dict) else []

    rows = []

    for stream in results:
        labels = stream.get("stream", {})
        for item in stream.get("values", []):
            if len(item) < 2:
                continue

            ts, line = item[0], item[1]
            row = dict(labels)
            row["timestamp_ns"] = ts
            row["timestamp"] = pd.to_datetime(
                int(ts), unit="ns", utc=True, errors="coerce"
            )
            row["log"] = line
            rows.append(row)

    if not rows:
        return pd.DataFrame()

    return pd.DataFrame(rows)


def metric_value(result):
    df = parse_metric_result(result.get("data", {}))
    if df.empty:
        return 0
    try:
        return float(df.iloc[0]["value"])
    except Exception:
        return 0


def run_metric(base_url, query, timeout=DEFAULT_TIMEOUT):
    return execute_loki(
        base_url,
        query,
        query_type="instant",
        timeout=timeout,
    )


def run_range(base_url, query, start, end, limit=5000, timeout=DEFAULT_TIMEOUT):
    return execute_loki(
        base_url,
        query,
        query_type="range",
        start=start,
        end=end,
        limit=limit,
        timeout=timeout,
    )


def selector(filters, include_heartbeat=True):
    parts = ['job="os-logs"']

    for key, value in filters.items():
        if value and value != "All":
            escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
            parts.append(f'{key}=~"{escaped}"')

    if not include_heartbeat:
        parts.append('source!="heartbeat"')

    return "{" + ",".join(parts) + "}"


def build_filters(environment, site, app_group, server_role, os_name, host, ip):
    return {
        "environment": environment,
        "site": site,
        "app_group": app_group,
        "server_role": server_role,
        "os": os_name,
        "host": host,
        "ip": ip,
    }


def distinct_count_query(stream_selector, label, window="30d"):
    return (
        f'count(sum by ({label}) '
        f'(count_over_time({stream_selector}[{window}])))'
    )


def grouped_count_query(stream_selector, group_label, window="30d"):
    return (
        f'sum by ({group_label}) '
        f'(count_over_time({stream_selector}[{window}]))'
    )


# -----------------------------
# Sidebar
# -----------------------------
st.sidebar.title("⚙️ Loki Connection")

loki_url = st.sidebar.text_input(
    "Loki / Nginx URL",
    value=st.session_state.get("loki_url", DEFAULT_LOKI_URL),
    help="Example: http://192.168.114.75",
)

st.session_state["loki_url"] = normalize_loki_url(loki_url)

timeout = st.sidebar.number_input(
    "HTTP Timeout (seconds)",
    min_value=5,
    max_value=300,
    value=DEFAULT_TIMEOUT,
    step=5,
)

st.sidebar.markdown("---")
st.sidebar.subheader("Report Filters")

environment = st.sidebar.selectbox(
    "Environment",
    ["All", "prod", "dmz"],
)

site = st.sidebar.selectbox(
    "Site",
    ["All", "dc1", "dc2"],
)

os_name = st.sidebar.selectbox(
    "OS",
    ["All", "rhel", "windows"],
)

server_role = st.sidebar.selectbox(
    "Server Role",
    ["All", "app", "web", "db"],
)

app_group = st.sidebar.text_input(
    "Application Group",
    value="",
    placeholder="Leave blank for all",
)

host = st.sidebar.text_input(
    "Hostname",
    value="",
    placeholder="hostname or regex",
)

ip = st.sidebar.text_input(
    "IP Address",
    value="",
    placeholder="IP or regex",
)

st.sidebar.markdown("---")
st.sidebar.subheader("Report Period")

period = st.sidebar.selectbox(
    "Period",
    ["Last 15 minutes", "Last 1 hour", "Last 6 hours",
     "Last 24 hours", "Last 7 days", "Last 30 days", "Custom"],
    index=2,
)

now = datetime.now(timezone.utc)

if period == "Last 15 minutes":
    start = now - timedelta(minutes=15)
elif period == "Last 1 hour":
    start = now - timedelta(hours=1)
elif period == "Last 6 hours":
    start = now - timedelta(hours=6)
elif period == "Last 24 hours":
    start = now - timedelta(hours=24)
elif period == "Last 7 days":
    start = now - timedelta(days=7)
elif period == "Last 30 days":
    start = now - timedelta(days=30)
else:
    c1, c2 = st.sidebar.columns(2)
    start_date = c1.date_input(
        "Start",
        value=(now - timedelta(hours=6)).date(),
    )
    end_date = c2.date_input(
        "End",
        value=now.date(),
    )

    start = datetime.combine(
        start_date,
        datetime.min.time(),
        tzinfo=timezone.utc,
    )
    end = datetime.combine(
        end_date,
        datetime.max.time(),
        tzinfo=timezone.utc,
    )

if period != "Custom":
    end = now

st.sidebar.caption(
    f"UTC: {start.strftime('%Y-%m-%d %H:%M:%S')} → "
    f"{end.strftime('%Y-%m-%d %H:%M:%S')}"
)


# -----------------------------
# Header
# -----------------------------
st.title("📊 Loki OS Log Management — Query & Reporting")
st.caption(
    "Loki-only reporting console. Designed for troubleshooting, "
    "inventory reporting, application/server counts, and LogQL testing."
)

health = health_check(st.session_state["loki_url"])

if health["ok"]:
    st.success(
        f"Loki READY — HTTP {health['status_code']} — "
        f"{health['elapsed']:.3f}s — {health['text']}"
    )
else:
    st.error(
        f"Loki is not reachable/ready. "
        f"HTTP={health['status_code']} URL={health['url']} "
        f"Error={health['text']}"
    )


# -----------------------------
# Base selectors
# -----------------------------
filters = build_filters(
    environment,
    site,
    app_group,
    server_role,
    os_name,
    host,
    ip,
)

all_stream = selector(filters, include_heartbeat=True)
log_stream = selector(filters, include_heartbeat=False)
heartbeat_stream = selector(filters, include_heartbeat=True)
heartbeat_stream = heartbeat_stream[:-1] + ',source="heartbeat"}'


# -----------------------------
# Tabs
# -----------------------------
tab_report, tab_query, tab_labels, tab_logs, tab_diag = st.tabs(
    [
        "📈 Report",
        "🔎 LogQL Query",
        "🏷️ Labels",
        "📜 Logs",
        "🛠️ Diagnostics",
    ]
)


# ============================================================
# REPORT
# ============================================================
with tab_report:
    st.subheader("Management Report")

    st.info(
        "Inventory uses heartbeat over 30 days. "
        "Log-volume panels exclude source=heartbeat."
    )

    # Top KPI row
    q_total_servers = distinct_count_query(
        heartbeat_stream,
        "host",
        "30d",
    )

    q_online_servers = (
        f'count(sum by (host) '
        f'(count_over_time({heartbeat_stream}[5m])))'
    )

    q_total_apps = distinct_count_query(
        heartbeat_stream,
        "app_group",
        "30d",
    )

    q_total_logs = (
        f'sum(count_over_time({log_stream}[{max(1, int((end-start).total_seconds()))}s]))'
    )

    q_total_ips = distinct_count_query(
        heartbeat_stream,
        "ip",
        "30d",
    )

    results = {}
    for name, query in {
        "total_servers": q_total_servers,
        "online_servers": q_online_servers,
        "total_apps": q_total_apps,
        "total_ips": q_total_ips,
        "total_logs": q_total_logs,
    }.items():
        results[name] = run_metric(
            st.session_state["loki_url"],
            query,
            timeout,
        )

    k1, k2, k3, k4, k5 = st.columns(5)

    values = {
        k: metric_value(v)
        for k, v in results.items()
    }

    k1.metric("Total Servers", fmt_number(values["total_servers"]))
    k2.metric("Online / Heartbeat", fmt_number(values["online_servers"]))
    k3.metric("Total Applications", fmt_number(values["total_apps"]))
    k4.metric("Total IP Addresses", fmt_number(values["total_ips"]))
    k5.metric("OS Log Records", fmt_number(values["total_logs"]))

    st.markdown("---")

    # Application report
    st.subheader("Applications / Server Distribution")

    q_apps = (
        f'sum by (app_group) '
        f'(sum by (host, app_group) '
        f'(count_over_time({heartbeat_stream}[30d])))'
    )

    # The expression above counts heartbeat events, not servers.
    # Use count by app_group over unique host series instead.
    q_apps = (
        f'count by (app_group) '
        f'(sum by (host, app_group) '
        f'(count_over_time({heartbeat_stream}[30d])))'
    )

    app_result = run_metric(
        st.session_state["loki_url"],
        q_apps,
        timeout,
    )

    if app_result["ok"]:
        app_df = parse_metric_result(app_result["data"])
        if not app_df.empty:
            app_df = app_df[["app_group", "value"]].copy()
            app_df["value"] = app_df["value"].astype(int)
            app_df = app_df.rename(
                columns={
                    "app_group": "Application",
                    "value": "Servers",
                }
            ).sort_values("Servers", ascending=False)

            st.dataframe(
                app_df,
                use_container_width=True,
                hide_index=True,
            )

            st.download_button(
                "⬇️ Download Application Report CSV",
                app_df.to_csv(index=False).encode("utf-8"),
                "application_server_report.csv",
                "text/csv",
            )
        else:
            st.warning("No application heartbeat data found.")
    else:
        st.error(app_result["error"])

    st.markdown("---")

    # Site report
    st.subheader("Site / OS / Role Distribution")

    distribution_queries = {
        "Site": (
            f'count by (site) '
            f'(sum by (host, site) '
            f'(count_over_time({heartbeat_stream}[30d])))'
        ),
        "OS": (
            f'count by (os) '
            f'(sum by (host, os) '
            f'(count_over_time({heartbeat_stream}[30d])))'
        ),
        "Server Role": (
            f'count by (server_role) '
            f'(sum by (host, server_role) '
            f'(count_over_time({heartbeat_stream}[30d])))'
        ),
        "Environment": (
            f'count by (environment) '
            f'(sum by (host, environment) '
            f'(count_over_time({heartbeat_stream}[30d])))'
        ),
    }

    cols = st.columns(4)

    for col, (title, query) in zip(cols, distribution_queries.items()):
        with col:
            st.markdown(f"**{title}**")
            result = run_metric(
                st.session_state["loki_url"],
                query,
                timeout,
            )

            if result["ok"]:
                df = parse_metric_result(result["data"])
                if not df.empty:
                    label = [c for c in df.columns if c not in ["timestamp", "value"]]
                    if label:
                        out = df[[label[0], "value"]].copy()
                        out["value"] = out["value"].astype(int)
                        out.columns = [title, "Servers"]
                        st.dataframe(
                            out.sort_values("Servers", ascending=False),
                            use_container_width=True,
                            hide_index=True,
                        )
                    else:
                        st.write("No data")
                else:
                    st.write("No data")
            else:
                st.error(result["error"])

    st.markdown("---")

    # Source report
    st.subheader("OS Log Distribution by Source")

    q_source = (
        f'sum by (source) '
        f'(count_over_time({log_stream}[{max(1, int((end-start).total_seconds()))}s]))'
    )

    source_result = run_metric(
        st.session_state["loki_url"],
        q_source,
        timeout,
    )

    if source_result["ok"]:
        source_df = parse_metric_result(source_result["data"])
        if not source_df.empty:
            source_df = source_df[["source", "value"]].copy()
            source_df["value"] = source_df["value"].astype(int)
            source_df.columns = ["Source", "Records"]
            source_df = source_df.sort_values(
                "Records",
                ascending=False,
            )
            st.dataframe(
                source_df,
                use_container_width=True,
                hide_index=True,
            )
            st.download_button(
                "⬇️ Download Source Report CSV",
                source_df.to_csv(index=False).encode("utf-8"),
                "log_source_report.csv",
                "text/csv",
            )
        else:
            st.warning("No OS log records found in the selected period.")
    else:
        st.error(source_result["error"])

    st.markdown("---")

    # Server inventory
    st.subheader("Server Inventory")

    q_inventory = (
        f'sum by (host, ip, os, environment, app_group, '
        f'server_role, site) '
        f'(count_over_time({heartbeat_stream}[30d]))'
    )

    inv_result = run_metric(
        st.session_state["loki_url"],
        q_inventory,
        timeout,
    )

    if inv_result["ok"]:
        inv_df = parse_metric_result(inv_result["data"])
        if not inv_df.empty:
            wanted = [
                "host",
                "ip",
                "app_group",
                "server_role",
                "os",
                "environment",
                "site",
            ]
            available = [c for c in wanted if c in inv_df.columns]
            inv_df = inv_df[available].drop_duplicates()

            inv_df.columns = [
                {
                    "host": "Hostname",
                    "ip": "IP Address",
                    "app_group": "Application",
                    "server_role": "Server Role",
                    "os": "OS",
                    "environment": "Environment",
                    "site": "Site",
                }.get(c, c)
                for c in inv_df.columns
            ]

            st.dataframe(
                inv_df.sort_values(
                    ["Site", "Application", "Hostname"],
                    na_position="last",
                ),
                use_container_width=True,
                hide_index=True,
            )

            st.download_button(
                "⬇️ Download Server Inventory CSV",
                inv_df.to_csv(index=False).encode("utf-8"),
                "server_inventory.csv",
                "text/csv",
            )
        else:
            st.warning("No heartbeat inventory found.")
    else:
        st.error(inv_result["error"])


# ============================================================
# LOGQL QUERY
# ============================================================
with tab_query:
    st.subheader("LogQL Query Console")

    default_query = (
        '{job="os-logs",source!="heartbeat"} '
        '|~ "(?i)(error|failed|failure|critical)"'
    )

    query = st.text_area(
        "LogQL",
        value=st.session_state.get("query", default_query),
        height=150,
        key="query_editor",
    )

    c1, c2, c3 = st.columns([2, 1, 1])

    with c1:
        query_type = st.selectbox(
            "Query Type",
            ["instant", "range"],
        )

    with c2:
        query_limit = st.number_input(
            "Limit",
            min_value=10,
            max_value=100000,
            value=5000,
            step=100,
        )

    with c3:
        run_button = st.button(
            "▶ Run Query",
            type="primary",
            use_container_width=True,
        )

    st.code(query, language="text")

    if run_button:
        if query_type == "range":
            result = run_range(
                st.session_state["loki_url"],
                query,
                start,
                end,
                limit=query_limit,
                timeout=timeout,
            )
        else:
            result = run_metric(
                st.session_state["loki_url"],
                query,
                timeout,
            )

        st.session_state["last_query_result"] = result

    result = st.session_state.get("last_query_result")

    if result:
        m1, m2, m3 = st.columns(3)
        m1.metric("HTTP", result["status_code"] or "ERR")
        m2.metric("Latency", f'{result["elapsed"]:.3f}s')
        m3.metric(
            "Result Type",
            result.get("data", {}).get("data", {}).get(
                "resultType", "-"
            )
            if isinstance(result.get("data"), dict)
            else "-",
        )

        if result["ok"]:
            if query_type == "range":
                df = parse_stream_result(result["data"])
                if not df.empty:
                    st.dataframe(
                        df,
                        use_container_width=True,
                        hide_index=True,
                    )
                    st.download_button(
                        "⬇️ Download Query Result CSV",
                        df.to_csv(index=False).encode("utf-8"),
                        "loki_query_result.csv",
                        "text/csv",
                    )
                else:
                    st.info("Query returned no log streams.")
            else:
                df = parse_metric_result(result["data"])
                if not df.empty:
                    st.dataframe(
                        df,
                        use_container_width=True,
                        hide_index=True,
                    )
                    st.download_button(
                        "⬇️ Download Query Result CSV",
                        df.to_csv(index=False).encode("utf-8"),
                        "loki_metric_result.csv",
                        "text/csv",
                    )
                else:
                    st.info("Query returned no metric/vector result.")

            with st.expander("Raw Loki JSON"):
                st.json(result["data"])

            with st.expander("HTTP Request Details"):
                st.code(result["url"], language="text")

        else:
            st.error(result["error"])
            with st.expander("Raw Response"):
                st.json(result["data"])


# ============================================================
# LABELS
# ============================================================
with tab_labels:
    st.subheader("Loki Label Browser")

    st.caption(
        "Uses Loki's /loki/api/v1/labels and /label/<name>/values APIs."
    )

    labels_url = api_url(
        st.session_state["loki_url"],
        "/loki/api/v1/labels",
    )

    try:
        r = requests.get(labels_url, timeout=timeout)
        if r.ok:
            labels_data = r.json()
            labels = labels_data.get("data", [])

            st.write(f"**Labels found:** {len(labels)}")
            st.code("\n".join(labels) if labels else "(none)")

            selected_label = st.selectbox(
                "Select label",
                labels if labels else COMMON_LABELS,
            )

            if selected_label:
                values_url = api_url(
                    st.session_state["loki_url"],
                    f"/loki/api/v1/label/{selected_label}/values",
                )

                vr = requests.get(values_url, timeout=timeout)

                if vr.ok:
                    values = vr.json().get("data", [])
                    st.write(
                        f"**{selected_label} values:** "
                        f"{len(values)}"
                    )
                    st.dataframe(
                        pd.DataFrame(
                            {selected_label: sorted(values)}
                        ),
                        use_container_width=True,
                        hide_index=True,
                    )
                else:
                    st.error(
                        f"HTTP {vr.status_code}: {vr.text[:2000]}"
                    )
        else:
            st.error(
                f"HTTP {r.status_code}: {r.text[:2000]}"
            )

    except Exception as exc:
        st.error(f"{type(exc).__name__}: {exc}")


# ============================================================
# LOGS
# ============================================================
with tab_logs:
    st.subheader("OS Log Explorer")

    log_query = st.text_area(
        "LogQL",
        value='{job="os-logs",source!="heartbeat"}',
        height=100,
        key="log_explorer_query",
    )

    log_limit = st.number_input(
        "Maximum log entries",
        min_value=100,
        max_value=50000,
        value=5000,
        step=500,
        key="log_limit",
    )

    if st.button("🔍 Search Logs", type="primary"):
        result = run_range(
            st.session_state["loki_url"],
            log_query,
            start,
            end,
            limit=log_limit,
            timeout=timeout,
        )

        st.session_state["log_result"] = result

    result = st.session_state.get("log_result")

    if result:
        if result["ok"]:
            df = parse_stream_result(result["data"])

            if not df.empty:
                preferred = [
                    "timestamp",
                    "host",
                    "ip",
                    "os",
                    "environment",
                    "app_group",
                    "server_role",
                    "site",
                    "source",
                    "log",
                ]

                cols = [c for c in preferred if c in df.columns]
                cols += [c for c in df.columns if c not in cols]

                st.dataframe(
                    df[cols],
                    use_container_width=True,
                    hide_index=True,
                    height=650,
                )

                st.download_button(
                    "⬇️ Download Logs CSV",
                    df.to_csv(index=False).encode("utf-8"),
                    "os_logs.csv",
                    "text/csv",
                )
            else:
                st.info("No logs found.")
        else:
            st.error(result["error"])


# ============================================================
# DIAGNOSTICS
# ============================================================
with tab_diag:
    st.subheader("Troubleshooting / Diagnostics")

    st.write("### Connection")
    st.code(
        f"Loki URL: {st.session_state['loki_url']}\n"
        f"Ready URL: {api_url(st.session_state['loki_url'], '/ready')}\n"
        f"Query API: {api_url(st.session_state['loki_url'], '/loki/api/v1/query')}\n"
        f"Query Range API: {api_url(st.session_state['loki_url'], '/loki/api/v1/query_range')}"
    )

    if health["ok"]:
        st.success("Connectivity test passed.")
    else:
        st.error("Connectivity test failed.")

    st.write("### Current generated selectors")
    st.write("**Heartbeat selector**")
    st.code(heartbeat_stream, language="text")

    st.write("**OS log selector**")
    st.code(log_stream, language="text")

    st.write("### Quick diagnostic queries")

    diagnostic_queries = {
        "Heartbeat count, last 5m":
            f'count(sum by (host) (count_over_time({heartbeat_stream}[5m])))',

        "Heartbeat inventory, 30d":
            f'count(sum by (host) (count_over_time({heartbeat_stream}[30d])))',

        "RHEL heartbeat inventory":
            f'count(sum by (host) (count_over_time({heartbeat_stream[:-1]},os="rhel"}}[30d])))',

        "Windows heartbeat inventory":
            f'count(sum by (host) (count_over_time({heartbeat_stream[:-1]},os="windows"}}[30d])))',

        "Log records in selected period":
            f'sum(count_over_time({log_stream}[{max(1, int((end-start).total_seconds()))}s]))',
    }

    for title, q in diagnostic_queries.items():
        st.markdown(f"**{title}**")
        st.code(q, language="text")

        if st.button(
            f"Run: {title}",
            key=f"diag_{title}",
        ):
            rr = run_metric(
                st.session_state["loki_url"],
                q,
                timeout,
            )

            if rr["ok"]:
                st.success(
                    f"OK — {metric_value(rr)} — "
                    f"{rr['elapsed']:.3f}s"
                )
                with st.expander("Raw response"):
                    st.json(rr["data"])
            else:
                st.error(rr["error"])


# -----------------------------
# Footer
# -----------------------------
st.markdown("---")
st.caption(
    "Loki OS Log Management Reporting Console | "
    "Python + Streamlit | Loki only | "
    "Heartbeat excluded from OS log volume statistics"
)
