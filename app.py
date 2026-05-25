import streamlit as st
import pandas as pd
import numpy as np
from datetime import datetime
import random
import matplotlib.pyplot as plt
import psycopg2
from sqlalchemy import create_engine
from urllib.parse import quote_plus


# ======================================
# FILE CONFIG
# ======================================
EXCEL_FILE = r"D:\Capstone_project\Merged_By_Event_Type.xlsx"

# ======================================
# DATABASE CONFIG
# ======================================
DB_CONFIG = {
    "host": "localhost",
    "database": "notification",
    "user": "postgres",
    "password": "Avinash@12",
    "port": "5432"
}

ENCODED_PASSWORD = quote_plus(DB_CONFIG["password"])
SQLALCHEMY_URL = (
    f"postgresql+psycopg2://{DB_CONFIG['user']}:{ENCODED_PASSWORD}"
    f"@{DB_CONFIG['host']}:{DB_CONFIG['port']}/{DB_CONFIG['database']}"
)


# ======================================
# DATABASE UTILITIES
# ======================================
def get_db_connection():
    return psycopg2.connect(**DB_CONFIG)


def ensure_table_exists():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS notification_log (
            id SERIAL PRIMARY KEY,
            timestamp TIMESTAMP,
            customer_id VARCHAR(50),
            event_type TEXT,
            channel VARCHAR(20),
            status TEXT,
            retries INT,
            spam_score INT,
            spam_status VARCHAR(20)
        );
    """)
    conn.commit()
    cur.close()
    conn.close()


def save_to_db(row):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO notification_log
        (timestamp, customer_id, event_type, channel, status, retries, spam_score, spam_status)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
    """, (
        row["Time"], row["Customer"], row["Event"],
        row["Channel"], row["Status"],
        row["Retries"], row["SpamScore"], row["SpamStatus"]
    ))
    conn.commit()
    cur.close()
    conn.close()


ensure_table_exists()


# ======================================
# LOAD DATA
# ======================================
@st.cache_data
def load_data():
    df = pd.read_excel(EXCEL_FILE)
    df.columns = df.columns.str.lower()
    df["delivered_flag"] = df.get("delivered_yn", "Y").astype(str).str.upper().map({"Y": 1, "N": 0})

    for col in ["retry_percentage", "retry_triggered", "event_count"]:
        if col not in df.columns:
            df[col] = 0

    if "intended_channel" not in df.columns:
        df["intended_channel"] = "SMS"

    return df


df = load_data()


# ======================================
# SESSION STATE
# ======================================
if "event_log" not in st.session_state:
    st.session_state.event_log = pd.DataFrame(columns=[
        "Time", "Customer", "Event", "Channel",
        "Status", "Retries", "SpamScore", "SpamStatus"
    ])


# ======================================
# SPAM ENGINE (FINAL UPDATED)
# ======================================
def spam_detection_engine(customer, event, channel):
    log = st.session_state.event_log
    user_logs = log[log["Customer"] == customer]

    attempts = len(user_logs[user_logs["Event"] == event])
    score = 0
    reasons = []

    # NEW RULES
    if attempts >= 3:
        score += 40
        reasons.append("More than 3 attempts")

    if attempts >= 4:
        score += 40
        reasons.append("Excessive repetition - BLOCK")

    # OLD RULES
    if len(user_logs) > 5:
        score += 30
        reasons.append("High total activity")

    if len(user_logs["Channel"].unique()) > 3:
        score += 20
        reasons.append("Multi-channel misuse")

    if event.upper() in ["OTP", "LOGIN OTP", "FRAUD ALERT", "TRANSACTION OTP"] and attempts >= 2:
        score += 30
        reasons.append("Critical event spam")

    # Status decision
    if score >= 70:
        status = "BLOCKED"
    elif score >= 40:
        status = "WARNING"
    else:
        status = "ALLOW"

    return score, status, reasons


# ======================================
# SMART CHANNEL AI
# ======================================
def smart_channel_selector(event, customer):
    scores = {}

    for ch in ["SMS", "EMAIL", "PUSH", "WHATSAPP"]:
        subset = df[(df["event_type"] == event) & (df["intended_channel"] == ch)]

        if subset.empty:
            scores[ch] = random.randint(55, 65)
            continue

        delivery = subset["delivered_flag"].mean() * 100
        retry = subset["retry_percentage"].mean()
        score = delivery - retry

        # Event sensitivity
        if event.upper() in ["OTP", "LOGIN OTP", "TRANSACTION OTP", "FRAUD ALERT"]:
            if ch in ["SMS", "WHATSAPP"]:
                score += 15
            if ch == "EMAIL":
                score -= 10

        # Time-based logic
        hour = datetime.now().hour
        if ch == "EMAIL" and hour >= 22:
            score -= 10
        if ch == "WHATSAPP" and 9 <= hour <= 23:
            score += 10

        scores[ch] = round(score, 2)

    st.sidebar.subheader("Channel AI Scores")
    st.sidebar.json(scores)

    return max(scores, key=scores.get)


# ======================================
# FALLBACK ENGINE
# ======================================
def get_fallback(primary):
    channels = ["SMS", "EMAIL", "PUSH", "WHATSAPP"]
    channels.remove(primary)
    return random.choice(channels)


# ======================================
# LOGGER
# ======================================
def log_event(event, customer, channel, status, retries, score, spam):
    row = {
        "Time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "Customer": customer,
        "Event": event,
        "Channel": channel,
        "Status": status,
        "Retries": retries,
        "SpamScore": score,
        "SpamStatus": spam
    }

    st.session_state.event_log = pd.concat(
        [st.session_state.event_log, pd.DataFrame([row])],
        ignore_index=True
    )

    save_to_db(row)


# ======================================
# SIMULATION ENGINE
# ======================================
def simulate(event, customer, channel):

    if channel == "AUTO":
        channel = smart_channel_selector(event, customer)
        st.success(f" Selected Channel: {channel}")

    score, spam, reasons = spam_detection_engine(customer, event, channel)

    if spam == "BLOCKED":
        log_event(event, customer, channel, "BLOCKED", 0, score, spam)
        return " BLOCKED — Too many attempts!"

    # Failure probability
    success_rate = df[df["event_type"] == event]["delivered_flag"].mean()
    fail = 1 - success_rate if not np.isnan(success_rate) else 0.25

    if channel == "WHATSAPP":
        fail *= 0.7

    fail = min(max(fail + random.uniform(-0.05, 0.05), 0.03), 0.9)

    retries = 0
    critical = ["OTP", "LOGIN OTP", "FRAUD ALERT", "TRANSACTION OTP", "KYC REMINDER"]

    if random.random() < fail:
        retries = 1
        if event.upper() in critical:
            status = f"DELIVERED (FALLBACK VIA {get_fallback(channel)})"
        else:
            status = (
                f"DELIVERED (RETRY VIA {channel})"
                if random.random() > 0.4 else
                f"DELIVERED (FALLBACK VIA {get_fallback(channel)})"
            )
    else:
        status = "DELIVERED"

    log_event(event, customer, channel, status, retries, score, spam)

    if spam == "WARNING":
        st.warning("⚠ Spam Warning: " + ", ".join(reasons))

    return status


# ======================================
# UI THEME
# ======================================
st.set_page_config("TrustNotify – Secure Notification Delivery", layout="wide")

st.markdown("""
<style>
body{background:#0e1117}
div[data-testid="metric-container"]{
background:#111827;border-radius:12px;padding:15px;
box-shadow:0 0 12px rgba(0,255,255,.2)
}
h1,h2,h3{color:#38bdf8}
section[data-testid="stSidebar"]{background:#020617}
</style>
""", unsafe_allow_html=True)


st.sidebar.title(" TrustNotify AI")
menu = st.sidebar.radio("Menu", [
    "Dashboard", "Simulation", "SLA Reports",
    "Database Logs", "Spam Monitor", "Raw Data"
])


# ======================================
# DASHBOARD
# ======================================
if menu == "Dashboard":
    st.title("Notification Command Center")

    colA, colB = st.columns(2)
    e = colA.selectbox("Event", ["All"] + sorted(df["event_type"].unique()))
    c = colB.selectbox("Channel", ["All"] + sorted(df["intended_channel"].unique()))

    data = df.copy()
    if e != "All":
        data = data[data["event_type"] == e]
    if c != "All":
        data = data[data["intended_channel"] == c]

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Events", len(data))
    m2.metric("Delivery %", f"{data['delivered_flag'].mean() * 100:.1f}%")
    m3.metric("Retry %", f"{(data['retry_triggered'] > 0).mean() * 100:.1f}%")
    m4.metric("Top Channel", data["intended_channel"].mode()[0] if not data.empty else "N/A")

    col1, col2 = st.columns(2)
    with col1:
        st.subheader("Delivery by Event")
        st.bar_chart(data.groupby("event_type")["delivered_flag"].mean() * 100)

    with col2:
        p = data["intended_channel"].value_counts()
        fig, ax = plt.subplots(facecolor="#0e1117")
        ax.set_facecolor("#0e1117")
        ax.pie(p, autopct="%1.1f%%", startangle=90, textprops={"color": "white"})
        ax.legend(p.index, labelcolor="white")
        st.pyplot(fig)

    st.subheader("📡 Live Delivery Feed")
    st.dataframe(
        st.session_state.event_log.sort_values("Time", ascending=False).head(12),
        use_container_width=True
    )


# ======================================
# SIMULATION
# ======================================
elif menu == "Simulation":
    st.title(" Notification Simulator")

    e = st.selectbox("Event", sorted(df["event_type"].unique()))
    c = st.text_input("Customer ID", "1001")
    ch = st.selectbox("Channel", ["AUTO", "SMS", "EMAIL", "PUSH", "WHATSAPP"])

    if st.button("Send Notification"):
        st.success(simulate(e, c, ch))

    st.dataframe(st.session_state.event_log.sort_values("Time", ascending=False).head(15))


# ======================================
# SLA REPORT
# ======================================
elif menu == "SLA Reports":
    st.title(" SLA Compliance Dashboard")

    logs = st.session_state.event_log

    if logs.empty:
        st.warning("No SLA data available.")
    else:
        delivery = logs["Status"].str.contains("DELIVERED").mean() * 100
        retry = (logs["Retries"] > 0).mean() * 100
        blocked = (logs["SpamStatus"] == "BLOCKED").mean() * 100

        sla = pd.DataFrame({
            "Metric": ["Delivery Success", "Retry Rate", "Spam Blocks"],
            "Current %": [f"{delivery:.1f}%", f"{retry:.1f}%", f"{blocked:.1f}%"],
            "SLA Target": [">=99%", "<=5%", "<=1%"],
            "Status": [
                " " if delivery >= 99 else "🔴",
                " " if retry <= 5 else "🔴",
                " " if blocked <= 1 else "🔴"
            ]
        })

        st.dataframe(sla, use_container_width=True)


# ======================================
# DATABASE LOGS
# ======================================
elif menu == "Database Logs":
    engine = create_engine(SQLALCHEMY_URL)
    db = pd.read_sql("SELECT * FROM notification_log ORDER BY timestamp DESC", engine)
    st.dataframe(db, use_container_width=True)


# ======================================
# SPAM MONITOR
# ======================================
elif menu == "Spam Monitor":
    spam = st.session_state.event_log
    spam = spam[spam["SpamStatus"] != "ALLOW"]

    if spam.empty:
        st.success(" No spam detected.")
    else:
        st.dataframe(spam.sort_values("Time", ascending=False))


# ======================================
# RAW DATA
# ======================================
elif menu == "Raw Data":
    st.dataframe(df, use_container_width=True)
