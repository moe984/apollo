"""
FP Predictor — Alert Data Explorer Dashboard

Visualizes preprocessed Apollo alert data using Plotly Dash.

Usage:
    python dashboard.py                                    # auto-finds latest preprocessed CSV
    python dashboard.py --input preprocessed.csv           # specific file
    python dashboard.py --port 8051                        # custom port
"""

import argparse
import base64
import glob
import io
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import missingno as msno
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from dash import Dash, dash_table, dcc, html


def _fig_to_base64(fig) -> str:
    """Render a matplotlib figure to a base64-encoded PNG string."""
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    buf.seek(0)
    return base64.b64encode(buf.read()).decode("utf-8")


DEFAULT_INPUT = "cust_lsuam_splunk_alert_data_preprocessed.csv"


def find_preprocessed_csv() -> str:
    if os.path.exists(DEFAULT_INPUT):
        return DEFAULT_INPUT
    candidates = glob.glob("*_preprocessed.csv")
    if not candidates:
        raise FileNotFoundError("No preprocessed CSV found. Run preprocess.py first.")
    return max(candidates, key=os.path.getmtime)


def load_data(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    return df


def build_figures(df: pd.DataFrame) -> dict:
    figs = {}
    sev_order = ["INFO", "MEDIUM", "HIGH", "CRITICAL"]
    sev_colors = {"INFO": "#3498db", "MEDIUM": "#f39c12", "HIGH": "#e74c3c", "CRITICAL": "#8e44ad"}

    # --- Exploration tab ---

    # Alert name frequency
    name_counts = df["stix_alert_name"].value_counts().reset_index()
    name_counts.columns = ["stix_alert_name", "count"]
    figs["alert_name"] = px.bar(
        name_counts, x="count", y="stix_alert_name", orientation="h",
        title="stix_alert_name Frequency",
        text="count",
        color="count", color_continuous_scale="Blues",
    ).update_layout(
        yaxis=dict(categoryorder="total ascending"),
        showlegend=False,
        coloraxis_showscale=False,
        height=500,
    ).update_traces(textposition="outside")


    # --- Data Quality tab ---
    total = len(df)
    numeric_cols = df.select_dtypes(include="number").columns.tolist()
    cat_cols = df.select_dtypes(include="object").columns.tolist()
    completely_empty = [col for col in df.columns if df[col].isna().all() or (df[col].astype(str).isin(["", "nan"])).all()]
    high_cardinality = [col for col in df.columns if df[col].nunique() > 10]

    figs["_dq_stats"] = {
        "Total Rows": total,
        "Total Columns": len(df.columns),
        "Numeric Columns": len(numeric_cols),
        "Categorical Columns": len(cat_cols),
        "High Cardinality (>10 unique)": len(high_cardinality),
    }
    figs["_completely_empty"] = completely_empty

    # missingno matrix — render to base64 PNG for embedding in Dash
    # Replace empty strings with NaN so missingno can detect them
    df_nulls = df.replace("", pd.NA)
    fig_matrix = msno.matrix(df_nulls, fontsize=8, sparkline=False, figsize=(20, 10))
    figs["_msno_matrix"] = _fig_to_base64(fig_matrix.figure)
    plt.close("all")

    fig_bar = msno.bar(df_nulls, fontsize=8, figsize=(20, 8))
    figs["_msno_bar"] = _fig_to_base64(fig_bar.figure)
    plt.close("all")

    # --- Temporal tab (all from CSV columns) ---
    day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

    # Alerts per day timeline — from eng_event_date
    date_df = df[df["eng_event_date"].notna() & ~df["eng_event_date"].astype(str).isin(["", "nan"])].copy()
    daily = date_df.groupby("eng_event_date").size().reset_index(name="count")
    daily["eng_event_date"] = pd.to_datetime(daily["eng_event_date"], format="%Y-%m-%d", errors="coerce")
    figs["temporal_daily"] = px.bar(
        daily, x="eng_event_date", y="count",
        title="Alerts Per Day (CDT)",
        text="count",
    ).update_layout(xaxis_title="Date", yaxis_title="Count", height=400)

    # Alerts per hour of day — from eng_hour_of_day
    hour_df = df[df["eng_hour_of_day"].notna() & ~df["eng_hour_of_day"].astype(str).isin(["", "nan"])].copy()
    hour_df["eng_hour_of_day"] = hour_df["eng_hour_of_day"].astype(int)
    hourly = hour_df.groupby("eng_hour_of_day").size().reindex(range(24), fill_value=0).reset_index(name="count")
    hourly.columns = ["hour", "count"]
    hourly["label"] = hourly["hour"].apply(lambda h: f"{h:02d}:00")
    figs["temporal_hourly"] = px.bar(
        hourly, x="label", y="count",
        title="Alerts by Hour of Day (CDT)",
        text="count",
        color="count", color_continuous_scale="YlOrRd",
    ).update_layout(xaxis_title="Hour", yaxis_title="Count", showlegend=False, coloraxis_showscale=False, height=400)

    # Alerts by day of week — from eng_day_of_week
    dow_df = df[df["eng_day_of_week"].notna() & ~df["eng_day_of_week"].astype(str).isin(["", "nan"])].copy()
    dow_df["eng_day_of_week"] = dow_df["eng_day_of_week"].astype(int)
    dow = dow_df.groupby("eng_day_of_week").size().reindex(range(7), fill_value=0).reset_index(name="count")
    dow.columns = ["day", "count"]
    dow["label"] = dow["day"].map(lambda d: day_names[d])
    figs["temporal_dow"] = px.bar(
        dow, x="label", y="count",
        title="Alerts by Day of Week (CDT)",
        text="count",
        color="count", color_continuous_scale="Blues",
    ).update_layout(xaxis_title="Day", yaxis_title="Count", showlegend=False, coloraxis_showscale=False, height=400)

    # Day x Hour heatmap — from eng_day_of_week + eng_hour_of_day
    both_df = df[
        df["eng_day_of_week"].notna() & ~df["eng_day_of_week"].astype(str).isin(["", "nan"]) &
        df["eng_hour_of_day"].notna() & ~df["eng_hour_of_day"].astype(str).isin(["", "nan"])
    ].copy()
    both_df["eng_day_of_week"] = both_df["eng_day_of_week"].astype(int)
    both_df["eng_hour_of_day"] = both_df["eng_hour_of_day"].astype(int)
    heatmap = both_df.groupby(["eng_day_of_week", "eng_hour_of_day"]).size().reset_index(name="count")
    pivot = heatmap.pivot(index="eng_day_of_week", columns="eng_hour_of_day", values="count").reindex(range(7)).fillna(0)
    pivot = pivot.reindex(columns=range(24), fill_value=0)
    figs["temporal_heatmap"] = go.Figure(go.Heatmap(
        z=pivot.values,
        x=[f"{h:02d}:00" for h in range(24)],
        y=[day_names[i] for i in pivot.index],
        colorscale="YlOrRd",
        text=pivot.values.astype(int),
        texttemplate="%{text}",
    )).update_layout(title="Alert Volume — Day x Hour (CDT)", xaxis_title="Hour", yaxis_title="Day", height=350)

    # Alert category over time (daily stacked) — from eng_event_date + eng_alert_category
    cat_date_df = df[df["eng_event_date"].notna() & ~df["eng_event_date"].astype(str).isin(["", "nan"])].copy()
    cat_daily = cat_date_df.groupby(["eng_event_date", "eng_alert_category"]).size().reset_index(name="count")
    cat_daily.columns = ["date", "eng_alert_category", "count"]
    cat_daily["date"] = pd.to_datetime(cat_daily["date"], format="%Y-%m-%d", errors="coerce")
    figs["temporal_cat"] = px.bar(
        cat_daily, x="date", y="count", color="eng_alert_category",
        title="Alert Category Over Time (CDT, daily)",
    ).update_layout(xaxis_title="Date", yaxis_title="Count", barmode="stack", height=450)

    # Business hours vs off-hours — from eng_is_business_hours
    bh_df = df[df["eng_is_business_hours"].notna() & ~df["eng_is_business_hours"].astype(str).isin(["", "nan"])].copy()
    bh_df["eng_is_business_hours"] = bh_df["eng_is_business_hours"].astype(int)
    bh_counts = bh_df["eng_is_business_hours"].value_counts().reset_index()
    bh_counts.columns = ["eng_is_business_hours", "count"]
    bh_counts["label"] = bh_counts["eng_is_business_hours"].map({1: "Business Hours (8-17 M-F)", 0: "Off-Hours"})
    figs["temporal_bh"] = px.pie(
        bh_counts, names="label", values="count",
        title="Business Hours vs Off-Hours (CDT)",
    ).update_layout(height=400)

    # --- User tab (correlations scoped to eng_detection_domain == Identity) ---

    id_df = df[df["eng_detection_domain"] == "Identity"].copy()

    # Correlation 1: Identity alert types x severity
    if not id_df.empty:
        id_type_sev = id_df.groupby(["stix_search_name", "stix_apollo_severity"]).size().reset_index(name="count")
        figs["id_type_sev"] = px.bar(
            id_type_sev, x="count", y="stix_search_name", color="stix_apollo_severity", orientation="h",
            title="Identity: Alert Types by Severity",
            color_discrete_map=sev_colors,
            category_orders={"stix_apollo_severity": sev_order},
        ).update_layout(
            yaxis=dict(categoryorder="total ascending"),
            barmode="stack", height=400,
            legend_title_text="Severity",
        )

    # Correlation 2: Identity user x alert category — what type of identity attack per user
    if not id_df.empty:
        id_user_cat = id_df[id_df["splunk_user"].notna() & ~id_df["splunk_user"].astype(str).isin(["", "nan"])]
        if not id_user_cat.empty:
            uc_agg = id_user_cat.groupby(["eng_alert_category", "splunk_user"]).size().reset_index(name="count")
            figs["id_user_cat"] = px.sunburst(
                uc_agg, path=["eng_alert_category", "splunk_user"], values="count",
                title="Identity: Alert Category → User",
            ).update_layout(height=600)
            figs["id_user_cat"].update_traces(
                textinfo="label+value",
                hovertemplate="<b>%{label}</b><br>Alerts: %{value}<extra></extra>",
            )

    # Correlation 3: Identity country x alert category — where identity attacks come from
    if not id_df.empty:
        id_geo = id_df[id_df["splunk_country"].notna() & ~id_df["splunk_country"].astype(str).isin(["", "nan"])]
        if not id_geo.empty:
            geo_cat = id_geo.groupby(["splunk_country", "eng_alert_category"]).size().reset_index(name="count")
            figs["id_country_cat"] = px.bar(
                geo_cat, x="count", y="splunk_country", color="eng_alert_category", orientation="h",
                title="Identity: Country x Alert Category",
            ).update_layout(
                yaxis=dict(categoryorder="total ascending"),
                barmode="stack", height=max(350, len(geo_cat["splunk_country"].unique()) * 25),
                legend_title_text="Category",
            )

    # Correlation 4: Identity alert category x hour of day — when identity attacks happen
    if not id_df.empty:
        id_hour = id_df[id_df["eng_hour_of_day"].notna() & ~id_df["eng_hour_of_day"].astype(str).isin(["", "nan"])].copy()
        id_hour["eng_hour_of_day"] = id_hour["eng_hour_of_day"].astype(int)
        if not id_hour.empty:
            cat_hour = id_hour.groupby(["eng_alert_category", "eng_hour_of_day"]).size().reset_index(name="count")
            cat_hour_pivot = cat_hour.pivot(index="eng_alert_category", columns="eng_hour_of_day", values="count").fillna(0)
            cat_hour_pivot = cat_hour_pivot.reindex(columns=range(24), fill_value=0)
            figs["id_cat_hour"] = go.Figure(go.Heatmap(
                z=cat_hour_pivot.values,
                x=[f"{h:02d}:00" for h in range(24)],
                y=cat_hour_pivot.index.tolist(),
                colorscale="YlOrRd",
                text=cat_hour_pivot.values.astype(int),
                texttemplate="%{text}",
            )).update_layout(
                title="Identity: Alert Category x Hour of Day (CDT)",
                xaxis_title="Hour", yaxis_title="eng_alert_category",
                height=350,
            )

    # --- User tab (general charts) ---

    # Filter to rows with a user
    user_df = df[df["splunk_user"].notna() & ~df["splunk_user"].astype(str).isin(["", "nan"])].copy()

    # 1. All users treemap — tile size = alert count, color = dominant severity
    user_counts = user_df.groupby("splunk_user").agg(
        alert_count=("splunk_user", "size"),
        dominant_severity=("stix_apollo_severity", lambda x: x.value_counts().index[0]),
    ).reset_index()
    figs["user_treemap"] = px.treemap(
        user_counts, path=["dominant_severity", "splunk_user"], values="alert_count",
        color="dominant_severity", color_discrete_map=sev_colors,
        title="All Users — Alert Volume (tile size) by Severity (color)",
    ).update_layout(height=600)
    figs["user_treemap"].update_traces(
        textinfo="label+value",
        hovertemplate="<b>%{label}</b><br>Alerts: %{value}<extra></extra>",
    )

    top_users = user_df["splunk_user"].value_counts().head(15).index.tolist()

    # 2. User x Alert Category sunburst — all users
    user_cat = user_df.groupby(["eng_alert_category", "splunk_user"]).size().reset_index(name="count")
    figs["user_category_sunburst"] = px.sunburst(
        user_cat, path=["eng_alert_category", "splunk_user"], values="count",
        title="User x Alert Category (all users)",
    ).update_layout(height=700)
    figs["user_category_sunburst"].update_traces(
        textinfo="label+value",
        hovertemplate="<b>%{label}</b><br>Alerts: %{value}<extra></extra>",
    )

    # 3. Brute force: failure count per user
    bf_df = user_df[user_df["eng_alert_category"] == "brute_force"].copy()
    bf_df["splunk_total_failures"] = pd.to_numeric(bf_df["splunk_total_failures"], errors="coerce")
    bf_df["splunk_total_successes"] = pd.to_numeric(bf_df["splunk_total_successes"], errors="coerce")
    if not bf_df.empty and bf_df["splunk_total_failures"].notna().any():
        bf_user = bf_df.groupby("splunk_user").agg(
            total_failures=("splunk_total_failures", "sum"),
            total_successes=("splunk_total_successes", "sum"),
            alert_count=("splunk_total_failures", "count"),
        ).reset_index().sort_values("total_failures", ascending=True)

        figs["user_brute_force"] = go.Figure()
        figs["user_brute_force"].add_trace(go.Bar(
            y=bf_user["splunk_user"], x=bf_user["total_failures"],
            name="Failed Logins", orientation="h", marker_color="#e74c3c",
            text=bf_user["total_failures"].astype(int), textposition="outside",
        ))
        figs["user_brute_force"].add_trace(go.Bar(
            y=bf_user["splunk_user"], x=bf_user["total_successes"],
            name="Successful Logins", orientation="h", marker_color="#2ecc71",
            text=bf_user["total_successes"].astype(int), textposition="outside",
        ))
        figs["user_brute_force"].update_layout(
            title="Brute Force: Failed vs Successful Logins by User",
            barmode="group",
            xaxis_title="Login Attempts",
            yaxis_title="User",
            height=400,
        )

    # --- Risk tab ---

    # 1. Severity x Alert Category — risk profile of each alert type
    sev_cat = df.groupby(["stix_apollo_severity", "eng_alert_category"]).size().reset_index(name="count")
    figs["risk_sev_cat"] = px.bar(
        sev_cat, x="count", y="eng_alert_category", color="stix_apollo_severity", orientation="h",
        title="Alert Categories by Severity",
        color_discrete_map=sev_colors,
        category_orders={"stix_apollo_severity": sev_order},
    ).update_layout(
        yaxis=dict(categoryorder="total ascending"),
        barmode="stack", height=450,
        legend_title_text="Severity",
    )

    # 2. Severity x Security Domain — where risk concentrates
    sec_df = df[df["splunk_orig_security_domain"].notna() & ~df["splunk_orig_security_domain"].astype(str).isin(["", "nan"])].copy()
    if not sec_df.empty:
        sev_domain = sec_df.groupby(["splunk_orig_security_domain", "stix_apollo_severity"]).size().reset_index(name="count")
        figs["risk_sev_domain"] = px.bar(
            sev_domain, x="count", y="splunk_orig_security_domain", color="stix_apollo_severity", orientation="h",
            title="Security Domains by Severity",
            color_discrete_map=sev_colors,
            category_orders={"stix_apollo_severity": sev_order},
        ).update_layout(
            yaxis=dict(categoryorder="total ascending"),
            barmode="stack", height=350,
            legend_title_text="Severity",
        )

    # 3. Detection domain by severity
    det_domain_sev = df.groupby(["eng_detection_domain", "stix_apollo_severity"]).size().reset_index(name="count")
    figs["risk_domain_sev"] = px.bar(
        det_domain_sev, x="count", y="eng_detection_domain", color="stix_apollo_severity", orientation="h",
        title="Detection Domain by Severity",
        color_discrete_map=sev_colors,
        category_orders={"stix_apollo_severity": sev_order},
    ).update_layout(
        yaxis=dict(categoryorder="total ascending"),
        barmode="stack", height=350,
        legend_title_text="Severity",
    )

    # 4. MITRE technique count distribution — alert complexity
    mitre_count_dist = df["stix_mitre_technique_count"].value_counts().sort_index().reset_index()
    mitre_count_dist.columns = ["stix_mitre_technique_count", "alerts"]
    figs["risk_mitre_count"] = px.bar(
        mitre_count_dist, x="stix_mitre_technique_count", y="alerts",
        title="MITRE Techniques per Alert (complexity)",
        text="alerts",
        color="stix_mitre_technique_count", color_continuous_scale="OrRd",
    ).update_layout(
        xaxis_title="Number of MITRE Techniques", yaxis_title="Alert Count",
        showlegend=False, coloraxis_showscale=False, height=350,
    )

    # 6. Observable count distribution — evidence depth
    obs_count_dist = df["stix_observable_count"].value_counts().sort_index().reset_index()
    obs_count_dist.columns = ["stix_observable_count", "alerts"]
    figs["risk_obs_count"] = px.bar(
        obs_count_dist, x="stix_observable_count", y="alerts",
        title="STIX Observables per Alert (evidence depth)",
        text="alerts",
        color="stix_observable_count", color_continuous_scale="Blues",
    ).update_layout(
        xaxis_title="Number of Observables", yaxis_title="Alert Count",
        showlegend=False, coloraxis_showscale=False, height=350,
    )

    # 7. Individual severity distributions
    apollo_sev = df["stix_apollo_severity"].value_counts().reset_index()
    apollo_sev.columns = ["stix_apollo_severity", "count"]
    figs["risk_apollo_sev"] = px.bar(
        apollo_sev, x="stix_apollo_severity", y="count",
        title="stix_apollo_severity",
        text="count",
        color="stix_apollo_severity", color_discrete_map=sev_colors,
    ).update_layout(showlegend=False, height=350, xaxis_title="", yaxis_title="Count")

    vendor_sev = df[df["stix_vendor_severity"].notna() & ~df["stix_vendor_severity"].astype(str).isin(["", "nan"])]
    vendor_sev = vendor_sev["stix_vendor_severity"].value_counts().reset_index()
    vendor_sev.columns = ["stix_vendor_severity", "count"]
    figs["risk_vendor_sev"] = px.bar(
        vendor_sev, x="stix_vendor_severity", y="count",
        title="stix_vendor_severity",
        text="count",
    ).update_layout(height=350, xaxis_title="", yaxis_title="Count")

    # 8. Severity correlation — Apollo vs Vendor
    sev_ct = df.groupby(["stix_apollo_severity", "stix_vendor_severity"]).size().reset_index(name="count")
    sev_pivot = sev_ct.pivot(index="stix_apollo_severity", columns="stix_vendor_severity", values="count").fillna(0)
    apollo_order = [s for s in ["INFO", "MEDIUM", "HIGH", "CRITICAL"] if s in sev_pivot.index]
    sev_pivot = sev_pivot.reindex(apollo_order)
    figs["risk_sev_correlation"] = go.Figure(go.Heatmap(
        z=sev_pivot.values,
        x=sev_pivot.columns.tolist(),
        y=sev_pivot.index.tolist(),
        colorscale="Blues",
        text=sev_pivot.values.astype(int),
        texttemplate="%{text}",
    )).update_layout(
        title="Severity Correlation — Apollo vs Vendor",
        xaxis_title="stix_vendor_severity",
        yaxis_title="stix_apollo_severity",
        height=350,
    )

    # Vendor alert name by severity
    vendor_name_sev = df.groupby(["stix_vendor_alert_name", "stix_apollo_severity"]).size().reset_index(name="count")
    figs["risk_vendor_alert_name"] = px.bar(
        vendor_name_sev, x="count", y="stix_vendor_alert_name", color="stix_apollo_severity", orientation="h",
        title="Vendor Alert Names by Severity",
        color_discrete_map=sev_colors,
        category_orders={"stix_apollo_severity": sev_order},
    ).update_layout(
        yaxis=dict(categoryorder="total ascending"),
        barmode="stack",
        height=max(400, df["stix_vendor_alert_name"].nunique() * 35),
        legend_title_text="Severity",
    )

    # --- Cloud tab (empty — no cloud-specific data in this dataset) ---

    # --- Network tab (correlations scoped to eng_detection_domain == Network) ---

    net_domain_df = df[df["eng_detection_domain"] == "Network"].copy()

    # Correlation 1: Network alert types — search name x severity
    if not net_domain_df.empty:
        net_type_sev = net_domain_df.groupby(["stix_search_name", "stix_apollo_severity"]).size().reset_index(name="count")
        figs["net_type_sev"] = px.bar(
            net_type_sev, x="count", y="stix_search_name", color="stix_apollo_severity", orientation="h",
            title="Network: Alert Types by Severity",
            color_discrete_map=sev_colors,
            category_orders={"stix_apollo_severity": sev_order},
        ).update_layout(
            yaxis=dict(categoryorder="total ascending"),
            barmode="stack", height=300,
            legend_title_text="Severity",
        )

    # Correlation 2: Network source/dest IPs
    net_ip_rows = []
    for _, r in net_domain_df.iterrows():
        src = r.get("splunk_source_address", "")
        dst = r.get("splunk_destination_address", "")
        name = r.get("stix_search_name", "")
        if src and str(src) not in ("", "nan"):
            net_ip_rows.append({"ip": str(src), "direction": "Source", "alert": name})
        if dst and str(dst) not in ("", "nan"):
            net_ip_rows.append({"ip": str(dst), "direction": "Destination", "alert": name})
    if net_ip_rows:
        net_ip_df = pd.DataFrame(net_ip_rows)
        figs["net_ips"] = px.bar(
            net_ip_df.groupby(["ip", "direction"]).size().reset_index(name="count"),
            x="count", y="ip", color="direction", orientation="h",
            title="Network: Source & Destination IPs",
        ).update_layout(
            yaxis=dict(categoryorder="total ascending"),
            barmode="group", height=300,
            legend_title_text="Direction",
        )

    # Correlation 3: Network MITRE techniques
    net_mitre_rows = []
    for _, r in net_domain_df.iterrows():
        techniques = str(r.get("stix_mitre_techniques", ""))
        if techniques and techniques not in ("", "nan"):
            for t in techniques.split(";"):
                t = t.strip()
                if t:
                    net_mitre_rows.append({"technique": t, "alert": r.get("stix_search_name", "")})
    if net_mitre_rows:
        net_mitre_df = pd.DataFrame(net_mitre_rows)
        net_mitre_agg = net_mitre_df.groupby(["technique", "alert"]).size().reset_index(name="count")
        figs["net_domain_mitre"] = px.bar(
            net_mitre_agg, x="count", y="technique", color="alert", orientation="h",
            title="Network: MITRE Techniques by Alert Type",
        ).update_layout(
            yaxis=dict(categoryorder="total ascending"),
            barmode="stack", height=300,
            legend_title_text="Alert",
        )

    # --- Network tab (general charts below) ---

    # 1. IOC type breakdown (using eng_ioc_type — IPv4, IPv6, URL, Email, Hash, Other)
    ioc_df = df[df["eng_ioc_type"].notna() & ~df["eng_ioc_type"].astype(str).isin(["", "nan"])].copy()
    if not ioc_df.empty:
        ioc_type_counts = ioc_df["eng_ioc_type"].value_counts().reset_index()
        ioc_type_counts.columns = ["eng_ioc_type", "count"]
        figs["net_ioc_type"] = px.pie(
            ioc_type_counts, names="eng_ioc_type", values="count",
            title="IOC Type Distribution",
        ).update_layout(height=400)

    # 2. IOC values broken down by type (one chart per eng_ioc_type)
    ioc_all = df[
        df["splunk_ioc"].notna() & ~df["splunk_ioc"].astype(str).isin(["", "nan"]) &
        df["eng_ioc_type"].notna() & ~df["eng_ioc_type"].astype(str).isin(["", "nan"])
    ].copy()
    ioc_colors = {"IPv4": "Reds", "IPv6": "Purples", "URL": "Blues", "Hash": "Greens", "Email": "Oranges", "Other": "Greys"}
    for ioc_type in ["IPv4", "IPv6", "URL", "Hash", "Email", "Other"]:
        subset = ioc_all[ioc_all["eng_ioc_type"] == ioc_type]
        if subset.empty:
            continue
        vals = subset["splunk_ioc"].value_counts().reset_index()
        vals.columns = ["splunk_ioc", "count"]
        vals["label"] = vals["splunk_ioc"].str[:70]
        fig_key = f"net_ioc_{ioc_type.lower()}"
        figs[fig_key] = px.bar(
            vals, x="count", y="label", orientation="h",
            title=f"IOCs — {ioc_type} ({len(vals)} unique)",
            text="count",
            color="count", color_continuous_scale=ioc_colors.get(ioc_type, "Reds"),
        ).update_layout(
            yaxis=dict(categoryorder="total ascending"),
            showlegend=False, coloraxis_showscale=False,
            height=max(250, len(vals) * 28),
        ).update_traces(textposition="outside")

    # 3. Source IPs by country — geographic origin of threats
    geo_df = df[
        df["splunk_country"].notna() & ~df["splunk_country"].astype(str).isin(["", "nan"])
    ].copy()
    if not geo_df.empty:
        country_counts = geo_df["splunk_country"].value_counts().reset_index()
        country_counts.columns = ["splunk_country", "count"]
        figs["net_country"] = px.bar(
            country_counts, x="count", y="splunk_country", orientation="h",
            title="Alert Origin by Country",
            text="count",
            color="count", color_continuous_scale="YlOrRd",
        ).update_layout(
            yaxis=dict(categoryorder="total ascending"),
            showlegend=False, coloraxis_showscale=False,
            height=max(350, len(country_counts) * 28),
        ).update_traces(textposition="outside")

    # 4. Source IPs grouped by country
    src_df = df[df["splunk_source_address"].notna() & ~df["splunk_source_address"].astype(str).isin(["", "nan"])].copy()
    if not src_df.empty:
        src_country = src_df.groupby("splunk_source_address")["splunk_country"].first().reset_index()
        src_counts = src_df.groupby("splunk_source_address").size().reset_index(name="count")
        src_counts = src_counts.merge(src_country, on="splunk_source_address", how="left")
        src_counts["splunk_country"] = src_counts["splunk_country"].fillna("Unknown")
        src_counts = src_counts.sort_values(["splunk_country", "count"], ascending=[True, False])
        figs["net_source_ips"] = px.bar(
            src_counts, x="splunk_country", y="count",
            title="Source IPs by Country",
            text="splunk_source_address",
            color="splunk_source_address",
        ).update_layout(
            xaxis_title="Country",
            yaxis_title="Alert Count",
            height=500,
            showlegend=False,
        ).update_traces(textposition="outside", textangle=-90, textfont_size=10)

    # 5. MITRE ATT&CK technique frequency
    mitre_series = df["stix_mitre_techniques"].dropna()
    mitre_series = mitre_series[~mitre_series.astype(str).isin(["", "nan"])]
    if not mitre_series.empty:
        mitre_exploded = mitre_series.str.split(";").explode().str.strip()
        mitre_exploded = mitre_exploded[mitre_exploded != ""]
        mitre_counts = mitre_exploded.value_counts().reset_index()
        mitre_counts.columns = ["technique", "count"]
        figs["net_mitre"] = px.bar(
            mitre_counts, x="count", y="technique", orientation="h",
            title="MITRE ATT&CK Techniques",
            text="count",
            color="count", color_continuous_scale="Purples",
        ).update_layout(
            yaxis=dict(categoryorder="total ascending"),
            showlegend=False, coloraxis_showscale=False,
            height=max(350, len(mitre_counts) * 30),
        ).update_traces(textposition="outside")

    # 6. Geo choropleth — alert count by country
    geo_df = df[df["splunk_country"].notna() & ~df["splunk_country"].astype(str).isin(["", "nan"])].copy()
    if not geo_df.empty:
        country_agg = geo_df.groupby("splunk_country").size().reset_index(name="count")
        figs["net_geo_map"] = px.choropleth(
            country_agg,
            locations="splunk_country",
            locationmode="country names",
            color="count",
            hover_name="splunk_country",
            hover_data={"count": True},
            title="Geographic Origin of Alerts",
            color_continuous_scale="OrRd",
        ).update_layout(
            height=450,
            margin=dict(l=0, r=0, t=40, b=0),
            geo=dict(projection_type="equirectangular", showframe=False, showcoastlines=True),
        )

    # File name frequency (for Network tab, next to hash)
    file_df = df[df["splunk_file_name"].notna() & ~df["splunk_file_name"].astype(str).isin(["", "nan"])].copy()
    if not file_df.empty:
        file_counts = file_df["splunk_file_name"].value_counts().reset_index()
        file_counts.columns = ["splunk_file_name", "count"]
        figs["net_file_name"] = px.bar(
            file_counts, x="count", y="splunk_file_name", orientation="h",
            title="File Names",
            text="count",
            color="count", color_continuous_scale="Greens",
        ).update_layout(
            yaxis=dict(categoryorder="total ascending"),
            showlegend=False, coloraxis_showscale=False,
            height=max(250, len(file_counts) * 28),
        ).update_traces(textposition="outside")

    # --- Endpoint tab (correlations scoped to eng_detection_domain == Endpoint) ---

    ep_df = df[df["eng_detection_domain"] == "Endpoint"].copy()

    # Correlation 1: Defender Category x Defender Title — what attack types produce which alerts
    if not ep_df.empty:
        cat_title = ep_df.groupby(["splunk_defender_category", "splunk_defender_title"]).size().reset_index(name="count")
        cat_title = cat_title[cat_title["splunk_defender_title"].notna() & ~cat_title["splunk_defender_title"].astype(str).isin(["", "nan"])]
        if not cat_title.empty:
            figs["ep_cat_title"] = px.sunburst(
                cat_title, path=["splunk_defender_category", "splunk_defender_title"], values="count",
                title="Endpoint: Attack Category → Alert Title",
            ).update_layout(height=600)
            figs["ep_cat_title"].update_traces(
                textinfo="label+value",
                hovertemplate="<b>%{label}</b><br>Alerts: %{value}<extra></extra>",
            )

    # Correlation 2: Defender Category x IOC Type — what evidence each attack type produces
    if not ep_df.empty:
        cat_ioc = ep_df[ep_df["eng_ioc_type"].notna() & ~ep_df["eng_ioc_type"].astype(str).isin(["", "nan"])]
        if not cat_ioc.empty:
            cat_ioc_agg = cat_ioc.groupby(["splunk_defender_category", "eng_ioc_type"]).size().reset_index(name="count")
            cat_ioc_pivot = cat_ioc_agg.pivot(index="splunk_defender_category", columns="eng_ioc_type", values="count").fillna(0)
            figs["ep_cat_ioc"] = go.Figure(go.Heatmap(
                z=cat_ioc_pivot.values,
                x=cat_ioc_pivot.columns.tolist(),
                y=cat_ioc_pivot.index.tolist(),
                colorscale="YlOrRd",
                text=cat_ioc_pivot.values.astype(int),
                texttemplate="%{text}",
            )).update_layout(
                title="Endpoint: Attack Category x IOC Type",
                xaxis_title="eng_ioc_type", yaxis_title="splunk_defender_category",
                height=350,
            )

    # Correlation 3: Defender Title x User — which users trigger which endpoint alerts
    if not ep_df.empty:
        title_user = ep_df[ep_df["splunk_user"].notna() & ~ep_df["splunk_user"].astype(str).isin(["", "nan"])]
        if not title_user.empty:
            tu_agg = title_user.groupby(["splunk_defender_title", "splunk_user"]).size().reset_index(name="count")
            figs["ep_title_user"] = px.treemap(
                tu_agg, path=["splunk_defender_title", "splunk_user"], values="count",
                title="Endpoint: Alert Title → User",
            ).update_layout(height=600)
            figs["ep_title_user"].update_traces(
                textinfo="label+value",
                hovertemplate="<b>%{label}</b><br>Alerts: %{value}<extra></extra>",
            )

    # Correlation 4: Defender Category x Hour of Day — when endpoint attacks happen
    if not ep_df.empty:
        ep_hour = ep_df[ep_df["eng_hour_of_day"].notna() & ~ep_df["eng_hour_of_day"].astype(str).isin(["", "nan"])].copy()
        ep_hour["eng_hour_of_day"] = ep_hour["eng_hour_of_day"].astype(int)
        if not ep_hour.empty:
            cat_hour = ep_hour.groupby(["splunk_defender_category", "eng_hour_of_day"]).size().reset_index(name="count")
            cat_hour_pivot = cat_hour.pivot(index="splunk_defender_category", columns="eng_hour_of_day", values="count").fillna(0)
            cat_hour_pivot = cat_hour_pivot.reindex(columns=range(24), fill_value=0)
            figs["ep_cat_hour"] = go.Figure(go.Heatmap(
                z=cat_hour_pivot.values,
                x=[f"{h:02d}:00" for h in range(24)],
                y=cat_hour_pivot.index.tolist(),
                colorscale="YlOrRd",
                text=cat_hour_pivot.values.astype(int),
                texttemplate="%{text}",
            )).update_layout(
                title="Endpoint: Attack Category x Hour of Day (CDT)",
                xaxis_title="Hour", yaxis_title="splunk_defender_category",
                height=350,
            )

    # --- Endpoint tab (original charts below) ---

    defender_df = df[df["splunk_defender_category"].notna() & ~df["splunk_defender_category"].astype(str).isin(["", "nan"])].copy()

    # 1. Defender alert titles — what's actually firing
    title_counts = defender_df["splunk_defender_title"].value_counts().reset_index()
    title_counts.columns = ["splunk_defender_title", "count"]
    figs["endpoint_titles"] = px.bar(
        title_counts, x="count", y="splunk_defender_title", orientation="h",
        title="Defender Alert Titles",
        text="count",
        color="count", color_continuous_scale="Reds",
    ).update_layout(
        yaxis=dict(categoryorder="total ascending"),
        showlegend=False, coloraxis_showscale=False,
        height=max(400, len(title_counts) * 35),
    ).update_traces(textposition="outside")

    # 2. Attack category x detection source — sunburst
    cat_src = defender_df.groupby(["splunk_defender_category", "splunk_defender_detection_source"]).size().reset_index(name="count")
    figs["endpoint_cat_source"] = px.sunburst(
        cat_src, path=["splunk_defender_category", "splunk_defender_detection_source"], values="count",
        title="Attack Category x Detection Source",
    ).update_layout(height=550)
    figs["endpoint_cat_source"].update_traces(
        textinfo="label+value",
        hovertemplate="<b>%{label}</b><br>Alerts: %{value}<extra></extra>",
    )

    # 3. Targeted computers — which domain controllers / machines are hit
    comp_df = df[df["splunk_computer_name"].notna() & ~df["splunk_computer_name"].astype(str).isin(["", "nan"])].copy()
    device_df = df[df["splunk_device_hostname"].notna() & ~df["splunk_device_hostname"].astype(str).isin(["", "nan"])].copy()
    # Combine both into one view
    machines = []
    for _, r in comp_df.iterrows():
        machines.append({"hostname": r["splunk_computer_name"], "source": "splunk_computer_name", "severity": r["stix_apollo_severity"]})
    for _, r in device_df.iterrows():
        machines.append({"hostname": r["splunk_device_hostname"], "source": "splunk_device_hostname", "severity": r["stix_apollo_severity"]})
    if machines:
        machines_df = pd.DataFrame(machines)
        host_counts = machines_df.groupby(["hostname", "severity"]).size().reset_index(name="count")
        figs["endpoint_machines"] = px.bar(
            host_counts, x="count", y="hostname", color="severity", orientation="h",
            title="Targeted Machines (computers + devices)",
            color_discrete_map=sev_colors,
        ).update_layout(
            yaxis=dict(categoryorder="total ascending"),
            barmode="stack", height=max(350, len(host_counts["hostname"].unique()) * 30),
        )

    # 5. Process activity — what's running on endpoints
    proc_df = df[df["splunk_process_command_line"].notna() & ~df["splunk_process_command_line"].astype(str).isin(["", "nan"])].copy()
    if not proc_df.empty:
        proc_detail = proc_df.groupby(["splunk_device_hostname", "splunk_file_name", "splunk_process_command_line"]).size().reset_index(name="count")
        proc_detail = proc_detail.sort_values("count", ascending=True)
        proc_detail["label"] = proc_detail["splunk_device_hostname"] + " → " + proc_detail["splunk_process_command_line"]
        figs["endpoint_processes"] = px.bar(
            proc_detail, x="count", y="label", orientation="h",
            title="Process Command Lines on Endpoints",
            text="count",
            color="splunk_file_name",
        ).update_layout(
            yaxis=dict(categoryorder="total ascending"),
            height=max(350, len(proc_detail) * 40),
            legend_title_text="File",
        ).update_traces(textposition="outside")

    # --- EDA tab ---

    # Raw data — every row, every column, nothing hidden
    figs["_eda_raw"] = df.astype(str)

    # Column profile: column, dtype, non-null, null%, unique, min, max, sample
    info_rows = []
    for col in df.columns:
        non_null = df[col].notna().sum() - (df[col].astype(str).isin(["", "nan"])).sum()
        non_null = max(non_null, 0)
        n_unique = int(df[col].nunique())
        info_rows.append({
            "column": col,
            "dtype": str(df[col].dtype),
            "non_null": int(non_null),
            "null_%": round((1 - non_null / total) * 100, 1),
            "unique": n_unique,
            "high_cardinality": "Yes" if n_unique > 10 else "",
            "sample": str(df[col].dropna().iloc[0])[:80] if df[col].notna().any() else "",
        })
    figs["_eda_info"] = pd.DataFrame(info_rows)

    # Full value counts for every non-unique column — no cap
    all_value_counts = {}
    for col in df.columns:
        n_unique = df[col].nunique()
        if True:
            vc = df[col].value_counts(dropna=False).reset_index()
            vc.columns = ["value", "count"]
            vc["value"] = vc["value"].astype(str)
            vc["pct"] = (vc["count"] / total * 100).round(1)
            all_value_counts[col] = vc
    figs["_eda_value_counts"] = all_value_counts

    return figs


def _build_dash_table(df_table: pd.DataFrame, table_id: str) -> dash_table.DataTable:
    return dash_table.DataTable(
        id=table_id,
        columns=[{"name": c, "id": c} for c in df_table.columns],
        data=df_table.to_dict("records"),
        style_table={"overflowX": "auto"},
        style_cell={"textAlign": "left", "padding": "8px", "fontSize": "13px", "fontFamily": "monospace"},
        style_header={"fontWeight": "bold", "backgroundColor": "#f8f9fa", "borderBottom": "2px solid #dee2e6"},
        style_data_conditional=[{"if": {"row_index": "odd"}, "backgroundColor": "#fafafa"}],
        page_size=20,
        sort_action="native",
        filter_action="native",
    )


def _source_label(*columns: str) -> html.Div:
    """Annotation showing which columns produced a chart."""
    return html.Div(style={"marginTop": "4px", "marginBottom": "20px", "paddingLeft": "8px"}, children=[
        html.Span("Source: ", style={"fontSize": "13px", "color": "#666", "fontWeight": "600"}),
        *[html.Span(children=[
            html.Code(col, style={"fontSize": "13px", "background": "#f0f0f0", "padding": "2px 6px", "borderRadius": "3px"}),
            html.Span(", " if i < len(columns) - 1 else "", style={"color": "#999"}),
        ]) for i, col in enumerate(columns)],
    ])


def _count_filled(df: pd.DataFrame, col: str) -> int:
    """Count non-empty, non-null values for a column."""
    return int(df[col].notna().sum() - (df[col].astype(str).isin(["", "nan"])).sum())


def build_column_cards(df: pd.DataFrame, columns: list[str]) -> html.Div:
    """Build stat cards showing data point count for each column."""
    cards = []
    total = len(df)
    for col in columns:
        filled = _count_filled(df, col)
        pct = round(filled / total * 100) if total else 0
        cards.append(
            html.Div(style={
                "background": "#f8f9fa", "borderRadius": "8px", "padding": "14px",
                "textAlign": "center", "border": "1px solid #e9ecef",
            }, children=[
                html.Div(f"{filled}", style={"fontSize": "24px", "fontWeight": "bold", "color": "#2c3e50"}),
                html.Div(f"/ {total} ({pct}%)", style={"fontSize": "11px", "color": "#999", "marginTop": "2px"}),
                html.Code(col, style={"fontSize": "11px", "background": "#eee", "padding": "1px 5px", "borderRadius": "3px"}),
            ])
        )
    ncols = min(len(columns), 5)
    return html.Div(style={"display": "grid", "gridTemplateColumns": f"repeat({ncols}, 1fr)", "gap": "10px", "marginBottom": "20px"}, children=cards)


def build_stats_cards(stats: dict) -> html.Div:
    cards = []
    for label, value in stats.items():
        cards.append(
            html.Div(style={
                "background": "#f8f9fa", "borderRadius": "8px", "padding": "16px",
                "textAlign": "center", "border": "1px solid #e9ecef",
            }, children=[
                html.Div(str(value), style={"fontSize": "28px", "fontWeight": "bold", "color": "#2c3e50"}),
                html.Div(label, style={"fontSize": "13px", "color": "#666", "marginTop": "4px"}),
            ])
        )
    ncols = len(cards)
    return html.Div(style={"display": "grid", "gridTemplateColumns": f"repeat({ncols}, 1fr)", "gap": "12px", "marginBottom": "20px"}, children=cards)


def build_layout(df: pd.DataFrame, figs: dict, input_path: str) -> html.Div:
    dq_stats = figs.pop("_dq_stats")
    empty_cols = figs.pop("_completely_empty")
    msno_matrix = figs.pop("_msno_matrix")
    msno_bar = figs.pop("_msno_bar")
    eda_raw = figs.pop("_eda_raw")
    eda_info = figs.pop("_eda_info")
    eda_value_counts = figs.pop("_eda_value_counts")

    return html.Div(style={"fontFamily": "system-ui, sans-serif", "margin": "0 auto", "maxWidth": "1400px", "padding": "20px"}, children=[
        html.H1("FP Predictor — Alert Data Explorer", style={"marginBottom": "5px"}),
        html.P(f"{len(df)} alerts from {input_path}", style={"color": "#666", "marginTop": "0"}),

        dcc.Tabs([
            dcc.Tab(label="EDA", children=[
                html.Div(style={"padding": "20px 0"}, children=[
                    build_stats_cards(dq_stats),

                    html.H3("Column Prefix Legend"),
                    dash_table.DataTable(
                        id="prefix-legend",
                        columns=[
                            {"name": "Prefix", "id": "prefix"},
                            {"name": "Source", "id": "source"},
                            {"name": "Description", "id": "description"},
                            {"name": "Count", "id": "count"},
                        ],
                        data=[
                            {"prefix": "org_", "source": "Raw CSV", "description": "Original columns from the Apollo database export — passed through unchanged", "count": sum(1 for c in df.columns if c.startswith("org_"))},
                            {"prefix": "stix_", "source": "stix_features JSON", "description": "Extracted from the STIX 2.1 bundle — alert identity, MITRE techniques, severity, observable counts", "count": sum(1 for c in df.columns if c.startswith("stix_"))},
                            {"prefix": "splunk_", "source": "Splunk notable fields", "description": "Extracted from the Splunk ES notable event fields — network, user, defender, geo, IOC, risk, file/process data", "count": sum(1 for c in df.columns if c.startswith("splunk_"))},
                            {"prefix": "apollo_", "source": "apollo_metadata (DB)", "description": "Apollo ingestion metadata — ingest/normalize timestamps, correlation ID, source alert ID", "count": sum(1 for c in df.columns if c.startswith("apollo_"))},
                            {"prefix": "eng_", "source": "Engineered", "description": "Computed by preprocess.py — severity numeric, alert category, temporal features, binary presence flags", "count": sum(1 for c in df.columns if c.startswith("eng_"))},
                        ],
                        style_table={"overflowX": "auto"},
                        style_cell={"textAlign": "left", "padding": "10px", "fontSize": "13px"},
                        style_header={"fontWeight": "bold", "backgroundColor": "#f8f9fa", "borderBottom": "2px solid #dee2e6"},
                        style_data_conditional=[
                            {"if": {"filter_query": "{prefix} = 'org_'"}, "backgroundColor": "#e8f4fd"},
                            {"if": {"filter_query": "{prefix} = 'stix_'"}, "backgroundColor": "#fef9e7"},
                            {"if": {"filter_query": "{prefix} = 'splunk_'"}, "backgroundColor": "#eafaf1"},
                            {"if": {"filter_query": "{prefix} = 'apollo_'"}, "backgroundColor": "#f4ecf7"},
                            {"if": {"filter_query": "{prefix} = 'eng_'"}, "backgroundColor": "#fdedec"},
                        ],
                    ),

                    html.H3("Raw Data", style={"marginTop": "30px"}),
                    _build_dash_table(eda_raw, "eda-raw"),

                    html.H3("Column Profile", style={"marginTop": "30px"}),
                    _build_dash_table(eda_info, "eda-info"),

                    html.H3("Value Counts (all values, all columns)", style={"marginTop": "30px"}),
                ] + [
                    html.Details(style={"marginBottom": "10px", "border": "1px solid #e9ecef", "borderRadius": "6px", "padding": "10px"}, children=[
                        html.Summary(f"{col}  ({len(vc)} values)", style={"cursor": "pointer", "fontWeight": "bold", "fontSize": "14px"}),
                        _build_dash_table(vc, f"eda-vc-{col}"),
                    ]) for col, vc in eda_value_counts.items()
                ] + [
                    html.H3("Missing Value Matrix", style={"marginTop": "30px"}),
                    html.Img(src=f"data:image/png;base64,{msno_matrix}", style={"width": "100%"}),
                    html.H3("Missing Value Bar", style={"marginTop": "20px"}),
                    html.Img(src=f"data:image/png;base64,{msno_bar}", style={"width": "100%"}),

                    html.H3("Alert Name Frequency", style={"marginTop": "30px"}),
                    dcc.Graph(figure=figs["alert_name"]),
                    _source_label("stix_alert_name"),
                ]),
            ]),
            dcc.Tab(label="User", children=[
                html.Div(style={"padding": "20px 0"}, children=[
                    build_column_cards(df, ["eng_detection_domain", "stix_search_name", "stix_apollo_severity", "eng_alert_category", "splunk_user", "splunk_country", "eng_hour_of_day", "splunk_total_failures", "splunk_total_successes"]),
                ] + ([dcc.Graph(figure=figs["id_type_sev"]), _source_label("stix_search_name", "stix_apollo_severity")] if "id_type_sev" in figs else [])
                + ([dcc.Graph(figure=figs["id_user_cat"]), _source_label("eng_alert_category", "splunk_user")] if "id_user_cat" in figs else [])
                + ([dcc.Graph(figure=figs["id_country_cat"]), _source_label("splunk_country", "eng_alert_category")] if "id_country_cat" in figs else [])
                + ([dcc.Graph(figure=figs["id_cat_hour"]), _source_label("eng_alert_category", "eng_hour_of_day")] if "id_cat_hour" in figs else [])
                + [
                    dcc.Graph(figure=figs["user_treemap"]),
                    _source_label("splunk_user", "stix_apollo_severity"),
                    dcc.Graph(figure=figs["user_category_sunburst"]),
                    _source_label("splunk_user", "eng_alert_category"),
                ] + ([
                    dcc.Graph(figure=figs["user_brute_force"]),
                    _source_label("splunk_user", "eng_alert_category", "splunk_total_failures", "splunk_total_successes"),
                ] if "user_brute_force" in figs else [])),
            ]),
            dcc.Tab(label="Endpoint", children=[
                html.Div(style={"padding": "20px 0"}, children=[
                    build_column_cards(df, ["eng_detection_domain", "splunk_defender_category", "splunk_defender_title", "splunk_defender_detection_source", "eng_ioc_type", "splunk_user", "eng_hour_of_day", "stix_apollo_severity", "splunk_ioc", "splunk_computer_name", "splunk_device_hostname", "splunk_file_name", "splunk_process_command_line"]),
                ] + ([dcc.Graph(figure=figs["ep_cat_title"]), _source_label("splunk_defender_category", "splunk_defender_title")] if "ep_cat_title" in figs else [])
                + ([dcc.Graph(figure=figs["ep_cat_ioc"]), _source_label("splunk_defender_category", "eng_ioc_type")] if "ep_cat_ioc" in figs else [])
                + ([dcc.Graph(figure=figs["ep_title_user"]), _source_label("splunk_defender_title", "splunk_user")] if "ep_title_user" in figs else [])
                + ([dcc.Graph(figure=figs["ep_cat_hour"]), _source_label("splunk_defender_category", "eng_hour_of_day")] if "ep_cat_hour" in figs else [])
                + [
                    dcc.Graph(figure=figs["endpoint_titles"]),
                    _source_label("splunk_defender_title"),
                    dcc.Graph(figure=figs["endpoint_cat_source"]),
                    _source_label("splunk_defender_category", "splunk_defender_detection_source"),
                ] + ([
                    dcc.Graph(figure=figs["endpoint_machines"]),
                    _source_label("splunk_computer_name", "splunk_device_hostname", "stix_apollo_severity"),
                ] if "endpoint_machines" in figs else []) + ([
                    dcc.Graph(figure=figs["endpoint_processes"]),
                    _source_label("splunk_device_hostname", "splunk_file_name", "splunk_process_command_line"),
                ] if "endpoint_processes" in figs else []) + ([
                    html.Div(style={"display": "grid", "gridTemplateColumns": "1fr 1fr", "gap": "20px"}, children=[
                        html.Div([dcc.Graph(figure=figs["net_ioc_hash"]), _source_label("splunk_ioc", "eng_ioc_type")]) if "net_ioc_hash" in figs else html.Div(),
                        html.Div([dcc.Graph(figure=figs["net_file_name"]), _source_label("splunk_file_name")]) if "net_file_name" in figs else html.Div(),
                    ]),
                ] if "net_ioc_hash" in figs or "net_file_name" in figs else [])),
            ]),
            dcc.Tab(label="Network", children=[
                html.Div(style={"padding": "20px 0"}, children=[
                    build_column_cards(df, ["eng_detection_domain", "stix_search_name", "stix_apollo_severity", "splunk_source_address", "splunk_destination_address", "splunk_country", "splunk_ioc", "eng_ioc_type", "stix_mitre_techniques"]),
                ]
                + ([dcc.Graph(figure=figs["net_type_sev"]), _source_label("stix_search_name", "stix_apollo_severity")] if "net_type_sev" in figs else [])
                + ([dcc.Graph(figure=figs["net_ips"]), _source_label("splunk_source_address", "splunk_destination_address")] if "net_ips" in figs else [])
                + ([dcc.Graph(figure=figs["net_domain_mitre"]), _source_label("stix_mitre_techniques", "stix_search_name")] if "net_domain_mitre" in figs else [])
                + ([dcc.Graph(figure=figs["net_geo_map"], style={"width": "100%"}), _source_label("splunk_country")] if "net_geo_map" in figs else [])
                + ([html.Div(style={"display": "grid", "gridTemplateColumns": "1fr 1fr", "gap": "20px"}, children=[
                        html.Div([dcc.Graph(figure=figs["net_ioc_type"]), _source_label("eng_ioc_type")]),
                        html.Div([dcc.Graph(figure=figs["net_country"]), _source_label("splunk_country")]),
                    ])] if "net_ioc_type" in figs and "net_country" in figs else [])
                + ([html.Div(style={"display": "grid", "gridTemplateColumns": "1fr 1fr", "gap": "20px"}, children=[
                        html.Div([dcc.Graph(figure=figs["net_ioc_ipv4"]), _source_label("splunk_ioc", "eng_ioc_type")]) if "net_ioc_ipv4" in figs else html.Div(),
                        html.Div([dcc.Graph(figure=figs["net_ioc_ipv6"]), _source_label("splunk_ioc", "eng_ioc_type")]) if "net_ioc_ipv6" in figs else html.Div(),
                    ])])
                + ([dcc.Graph(figure=figs["net_ioc_url"]), _source_label("splunk_ioc", "eng_ioc_type")] if "net_ioc_url" in figs else [])
                + ([dcc.Graph(figure=figs["net_ioc_email"]), _source_label("splunk_ioc", "eng_ioc_type")] if "net_ioc_email" in figs else [])
                + ([dcc.Graph(figure=figs["net_ioc_other"]), _source_label("splunk_ioc", "eng_ioc_type")] if "net_ioc_other" in figs else [])
                + ([dcc.Graph(figure=figs["net_source_ips"]), _source_label("splunk_source_address", "splunk_country")] if "net_source_ips" in figs else [])
                + ([dcc.Graph(figure=figs["net_mitre"]), _source_label("stix_mitre_techniques")] if "net_mitre" in figs else [])
                ),
            ]),
            dcc.Tab(label="Cloud", children=[
                html.Div(style={"padding": "20px 0"}, children=[
                    # No cloud-specific data in this dataset
                ]),
            ]),
            dcc.Tab(label="Alert", children=[
                html.Div(style={"padding": "20px 0"}, children=[
                    build_column_cards(df, ["stix_apollo_severity", "stix_vendor_severity", "stix_vendor_alert_name", "eng_alert_category", "eng_detection_domain", "splunk_orig_security_domain", "stix_mitre_technique_count", "stix_observable_count"]),
                    html.Div(style={"display": "grid", "gridTemplateColumns": "1fr 1fr 1fr", "gap": "20px"}, children=[
                        html.Div([dcc.Graph(figure=figs["risk_sev_cat"]), _source_label("stix_apollo_severity", "eng_alert_category")]),
                        html.Div([dcc.Graph(figure=figs["risk_sev_domain"]), _source_label("splunk_orig_security_domain", "stix_apollo_severity")]) if "risk_sev_domain" in figs else html.Div(),
                        html.Div([dcc.Graph(figure=figs["risk_domain_sev"]), _source_label("eng_detection_domain", "stix_apollo_severity")]),
                    ]),
                ] + [
                    html.Div(style={"display": "grid", "gridTemplateColumns": "1fr 1fr", "gap": "20px"}, children=[
                        html.Div([dcc.Graph(figure=figs["risk_mitre_count"]), _source_label("stix_mitre_technique_count")]),
                        html.Div([dcc.Graph(figure=figs["risk_obs_count"]), _source_label("stix_observable_count")]),
                    ]),
                    html.Div(style={"display": "grid", "gridTemplateColumns": "1fr 1fr 1fr", "gap": "20px"}, children=[
                        html.Div([dcc.Graph(figure=figs["risk_apollo_sev"]), _source_label("stix_apollo_severity")]),
                        html.Div([dcc.Graph(figure=figs["risk_vendor_sev"]), _source_label("stix_vendor_severity")]),
                        html.Div([dcc.Graph(figure=figs["risk_sev_correlation"]), _source_label("stix_apollo_severity", "stix_vendor_severity")]),
                    ]),
                    dcc.Graph(figure=figs["risk_vendor_alert_name"]),
                    _source_label("stix_vendor_alert_name"),
                ]),
            ]),
            dcc.Tab(label="Temporal", children=[
                html.Div(style={"padding": "20px 0"}, children=[
                    build_column_cards(df, ["eng_event_date", "eng_hour_of_day", "eng_day_of_week", "eng_is_business_hours", "eng_alert_category"]),
                    dcc.Graph(figure=figs["temporal_daily"]),
                    _source_label("eng_event_date"),
                    html.Div(style={"display": "grid", "gridTemplateColumns": "1fr 1fr", "gap": "20px"}, children=[
                        html.Div([dcc.Graph(figure=figs["temporal_hourly"]), _source_label("eng_hour_of_day")]),
                        html.Div([dcc.Graph(figure=figs["temporal_dow"]), _source_label("eng_day_of_week")]),
                    ]),
                    dcc.Graph(figure=figs.get("temporal_heatmap", go.Figure())),
                    _source_label("eng_day_of_week", "eng_hour_of_day"),
                    dcc.Graph(figure=figs["temporal_cat"]),
                    _source_label("eng_event_date", "eng_alert_category"),
                    dcc.Graph(figure=figs["temporal_bh"]),
                    _source_label("eng_is_business_hours"),
                ]),
            ]),
        ]),
    ])


def main():
    parser = argparse.ArgumentParser(description="FP Predictor alert data dashboard")
    parser.add_argument("--input", type=str, default=None, help="Preprocessed CSV path")
    parser.add_argument("--port", type=int, default=8050, help="Port (default: 8050)")
    args = parser.parse_args()

    input_path = args.input or find_preprocessed_csv()
    print(f"Loading {input_path}...")
    df = load_data(input_path)
    print(f"  {len(df)} rows, {len(df.columns)} columns")

    print("Building figures...")
    figs = build_figures(df)

    app = Dash(__name__)
    app.layout = build_layout(df, figs, input_path)

    print(f"Dashboard running at http://localhost:{args.port}")
    app.run(debug=True, port=args.port)


if __name__ == "__main__":
    main()
